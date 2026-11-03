"""All database access: engine, models and queries. SQLAlchemy 2.0 (sync) on the psycopg 3 driver.

db/init.sql is the only schema source. The models below only map to the tables it creates;
never call `Base.metadata.create_all()`.

Never log SQLAlchemy exceptions: their text can include bound parameters (captions).
"""

import logging
import threading
from datetime import date, datetime
from pathlib import Path

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Text,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DataError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import NullPool

from .config import settings
from .schemas import CourseSummary, SyncPayload, course_id_for

log = logging.getLogger("aic")

INIT_SQL = Path(__file__).resolve().parents[1] / "db" / "init.sql"
EMBEDDING_DIM = settings.embedding_dim  # single source; init.sql has vector(384), change together

# Schema apply is serialized across processes with an advisory lock, and gives up on a table lock
# instead of hanging behind another transaction (e.g. a long query on `chunks`).
SCHEMA_LOCK_KEY = 727_001
LOCK_TIMEOUT = "5s"


def normalize_url(url: str) -> URL:
    """.env uses `postgresql://`, which SQLAlchemy would send to psycopg2. Select psycopg 3."""
    parsed = make_url(url)
    if parsed.drivername in ("postgresql", "postgres"):
        parsed = parsed.set(drivername="postgresql+psycopg")
    return parsed


class SchemaBusy(Exception):
    """Another thread is applying the schema; the caller answers 503 instead of waiting."""


class ValueRejected(Exception):
    """The database or driver refused a value in a sync. Carries nothing from the original error:
    its text can contain SQL and the offending values (captions)."""


def make_engine(url: str = settings.database_url, lock_timeout: str = LOCK_TIMEOUT):
    # NullPool: one connection per use, closed afterwards. echo stays off and hide_parameters
    # keeps bound values (captions) out of exception text.
    return create_engine(
        normalize_url(url),
        poolclass=NullPool,
        echo=False,
        hide_parameters=True,
        connect_args={
            "connect_timeout": 5,  # seconds; a dead host must not hang a request
            # Every statement gives up on a table lock after this long (a 503 for the caller)
            # instead of waiting behind another transaction indefinitely.
            "options": f"-c lock_timeout={lock_timeout}",
        },
    )


engine = make_engine()


class Base(DeclarativeBase):
    pass


class Course(Base):
    __tablename__ = "courses"
    course_id: Mapped[str] = mapped_column(Text, primary_key=True)
    canvas_course_id: Mapped[int] = mapped_column(unique=True)  # INTEGER in init.sql
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    term: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    indexed: Mapped[bool] = mapped_column(Boolean, server_default="false")


class Lecture(Base):
    __tablename__ = "lectures"
    entry_id: Mapped[str] = mapped_column(Text, primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.course_id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[date | None] = mapped_column(Date)
    duration_sec: Mapped[float] = mapped_column(Float)
    media_url: Mapped[str] = mapped_column(Text)
    captions_srt: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)


class CanvasFile(Base):
    __tablename__ = "files"
    canvas_file_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.course_id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(Text)


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entry_id: Mapped[str] = mapped_column(ForeignKey("lectures.entry_id", ondelete="CASCADE"))
    start_sec: Mapped[float] = mapped_column(Float)
    end_sec: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))


def apply_schema() -> None:
    """Apply db/init.sql. It is idempotent (IF NOT EXISTS everywhere), so it is safe to repeat.
    This is needed because Postgres only runs init.sql when the data volume is first created, so
    tables added later would otherwise be missing on an existing volume."""
    with engine.begin() as conn:
        # Both settings are transaction-local. The lock timeout also bounds the advisory lock wait.
        conn.execute(text("SELECT set_config('lock_timeout', :t, true)"), {"t": LOCK_TIMEOUT})
        # Two processes creating the same missing tables at once would collide (IntegrityError
        # on the catalog); the advisory lock makes the second one wait, then find them present.
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SCHEMA_LOCK_KEY})
        # Raw driver SQL: the file holds several statements (extension, tables, indexes).
        conn.exec_driver_sql(INIT_SQL.read_text())


_schema_ready = False
_schema_lock = threading.Lock()


def ensure_schema() -> None:
    """Apply the schema until one apply has succeeded, then never again. Called at startup and
    before every database use, so if Postgres was down at startup the first request after it
    returns creates the missing tables. Errors propagate (callers map them to 503).

    Only one thread applies at a time; the others fail fast with SchemaBusy rather than queue up
    behind a slow or timing-out apply."""
    global _schema_ready
    if _schema_ready:
        return
    if not _schema_lock.acquire(blocking=False):
        if _schema_ready:  # the other thread finished between the two checks
            return
        raise SchemaBusy
    try:
        if not _schema_ready:
            apply_schema()
            _schema_ready = True
    finally:
        _schema_lock.release()


def save_sync(payload: SyncPayload) -> tuple[str, int]:
    """Upsert the course, its lectures and its files in ONE transaction. Returns the course_id and
    the number of distinct lectures skipped because they belong to another course.

    Nothing is ever deleted: lectures or files missing from the payload stay stored, and
    `files: []` therefore leaves stored files alone. Optional columns use COALESCE so an omitted
    value (e.g. `term`) never erases one stored earlier. `indexed` is not touched here.
    A lecture already stored under a different course is not touched at all (no field changes).
    A file's course_id is never overwritten on conflict, but its other fields still update.
    """
    ensure_schema()
    cid = course_id_for(payload.course.canvas_course_id)
    skipped = 0
    # session.begin() commits on success and rolls back on any exception.
    try:
        with Session(engine) as session, session.begin():
            course = insert(Course).values(
                course_id=cid,
                canvas_course_id=payload.course.canvas_course_id,
                code=payload.course.code,
                name=payload.course.name,
                term=payload.course.term,
                synced_at=payload.synced_at,
            )
            session.execute(
                course.on_conflict_do_update(
                    index_elements=[Course.course_id],
                    set_={
                        "code": course.excluded.code,
                        "name": course.excluded.name,
                        "term": func.coalesce(course.excluded.term, Course.term),
                        "synced_at": course.excluded.synced_at,
                    },
                )
            )

            # An empty list must not be executed (it would be an INSERT with no values).
            if payload.lectures:
                # One row per entry_id (the last one wins), so a duplicate inside the payload
                # neither errors nor counts twice.
                rows = {
                    lec.entry_id: {
                        "entry_id": lec.entry_id,
                        "course_id": cid,
                        "title": lec.title,
                        "recorded_at": lec.recorded_at,
                        "duration_sec": lec.duration_sec,
                        "media_url": lec.media_url,
                        "captions_srt": lec.captions_srt,
                    }
                    for lec in payload.lectures
                }
                lecture = insert(Lecture)
                stored = session.execute(
                    lecture.on_conflict_do_update(
                        index_elements=[Lecture.entry_id],
                        # A lecture stored under another course is left completely untouched.
                        # Such rows are not updated, so they are absent from RETURNING.
                        where=Lecture.course_id == lecture.excluded.course_id,
                        set_={
                            "title": lecture.excluded.title,
                            "recorded_at": func.coalesce(
                                lecture.excluded.recorded_at, Lecture.recorded_at
                            ),
                            "duration_sec": lecture.excluded.duration_sec,
                            "media_url": lecture.excluded.media_url,
                            "captions_srt": lecture.excluded.captions_srt,
                        },
                    ).returning(Lecture.entry_id),
                    list(rows.values()),
                )
                skipped_ids = rows.keys() - set(stored.scalars().all())
                skipped = len(skipped_ids)
                if skipped_ids:
                    owners = session.execute(
                        select(Lecture.entry_id, Lecture.course_id).where(
                            Lecture.entry_id.in_(skipped_ids)
                        )
                    ).all()
                    for entry_id, owner in owners:
                        # Ids only: never titles or captions.
                        log.warning(
                            "lecture %s belongs to course %s; sync of course %s skipped it",
                            entry_id,
                            owner,
                            cid,
                        )

            # `files: []` skips this block, which is what keeps stored files.
            if payload.files:
                file = insert(CanvasFile)
                session.execute(
                    file.on_conflict_do_update(
                        index_elements=[CanvasFile.canvas_file_id],
                        set_={
                            "name": file.excluded.name,
                            "content_type": func.coalesce(
                                file.excluded.content_type, CanvasFile.content_type
                            ),
                        },
                    ),
                    [
                        {
                            "canvas_file_id": f.canvas_file_id,
                            "course_id": cid,
                            "name": f.name,
                            "content_type": f.content_type,
                        }
                        for f in payload.files
                    ],
                )
    except DataError as exc:
        # Only sqlstate is logged; the rest of the exception can hold captions. A driver-side
        # refusal (NUL byte) has no sqlstate.
        log.warning(
            "value rejected by the database (sqlstate=%s)", getattr(exc.orig, "sqlstate", None)
        )
        raise ValueRejected from None
    return cid, skipped


def list_courses() -> list[CourseSummary]:
    ensure_schema()
    stmt = (
        select(
            Course.course_id,
            Course.code,
            Course.name,
            func.count(Lecture.entry_id),
            Course.indexed,
        )
        .outerjoin(Lecture, Lecture.course_id == Course.course_id)
        .group_by(Course.course_id)
        .order_by(Course.course_id)
    )
    with Session(engine) as session:
        rows = session.execute(stmt).all()
    return [
        CourseSummary(course_id=r[0], code=r[1], name=r[2], lectures=r[3], indexed=r[4])
        for r in rows
    ]


def course_exists(course_id: str) -> bool:
    if "\x00" in course_id:
        return False  # cannot exist, and the driver refuses to send it; answered as not found
    ensure_schema()
    with Session(engine) as session:
        found = session.execute(select(Course.course_id).where(Course.course_id == course_id))
        return found.first() is not None
