"""All database access: engine, models and queries. SQLAlchemy 2.0 (sync) on the psycopg 3 driver.

db/init.sql is the only schema source. The models below only map to the tables it creates;
never call `Base.metadata.create_all()`.

Never log SQLAlchemy exceptions: their text can include bound parameters (captions).
"""

import hashlib
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    and_,
    cast,
    create_engine,
    delete,
    exists,
    func,
    literal,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DataError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import NullPool

from . import chunking
from .chunking import Chunk as ChunkData
from .config import settings
from .schemas import (
    CourseDetail,
    CourseSummary,
    DocumentInfo,
    LectureInfo,
    LectureStatus,
    SyncPayload,
    course_id_for,
)
from .titles import parse_title

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
    term_start: Mapped[date | None] = mapped_column(Date)
    term_end: Mapped[date | None] = mapped_column(Date)
    instructors: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    sections: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    indexed: Mapped[bool] = mapped_column(Boolean, server_default="false")


class Lecture(Base):
    __tablename__ = "lectures"
    entry_id: Mapped[str] = mapped_column(Text, primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.course_id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[date | None] = mapped_column(Date)
    recorded_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_sec: Mapped[float] = mapped_column(Float)
    media_url: Mapped[str] = mapped_column(Text)
    captions_srt: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    lecture_no: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(Text)
    # Hash of what the current chunks were built from (see captions_hash); NULL = not chunked.
    chunked_hash: Mapped[str | None] = mapped_column(Text)


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


class Document(Base):
    __tablename__ = "documents"
    doc_id: Mapped[str] = mapped_column(Text, primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.course_id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    pages: Mapped[int] = mapped_column(Integer)
    lecture_no: Mapped[int | None] = mapped_column(Integer)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocChunk(Base):
    __tablename__ = "doc_chunks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("documents.doc_id", ondelete="CASCADE"))
    page: Mapped[int] = mapped_column(Integer)
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


def _hash_prefix() -> str:
    # Read at call time, so a changed version or model makes every stored lecture due.
    return f"{chunking.CHUNKER_VERSION}\n{settings.embedding_model}\n"


def captions_hash(captions: str) -> str:
    """sha256 (hex) of UTF-8 `version \\n model \\n captions`: what the chunks depend on."""
    return hashlib.sha256((_hash_prefix() + captions).encode("utf-8")).hexdigest()


def _current_hash():
    """The same hash computed by Postgres, so "which lectures are due" is one query."""
    prefix = cast(literal(_hash_prefix()), Text)
    return func.encode(
        func.sha256(func.convert_to(prefix.op("||")(Lecture.captions_srt), "UTF8")), "hex"
    )


def _is_due():
    return Lecture.chunked_hash.is_distinct_from(_current_hash())


def _has_due_lecture():
    return exists().where(Lecture.course_id == Course.course_id, _is_due())


def save_sync(payload: SyncPayload) -> tuple[str, int]:
    """Upsert the course, its lectures and its files in ONE transaction. Returns the course_id and
    the number of distinct lectures skipped because they belong to another course.

    Nothing is ever deleted: lectures or files missing from the payload stay stored, and
    `files: []` therefore leaves stored files alone. Optional columns use COALESCE so an omitted
    value (e.g. `term`) never erases one stored earlier. `indexed` is only ever set to false here
    (when a lecture is new or its captions changed), never to true.
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
                term_start=payload.course.term_start,
                term_end=payload.course.term_end,
                instructors=payload.course.instructors,
                sections=payload.course.sections,
                synced_at=payload.synced_at,
            )
            session.execute(
                course.on_conflict_do_update(
                    index_elements=[Course.course_id],
                    set_={
                        "code": course.excluded.code,
                        "name": course.excluded.name,
                        "term": func.coalesce(course.excluded.term, Course.term),
                        "term_start": func.coalesce(course.excluded.term_start, Course.term_start),
                        "term_end": func.coalesce(course.excluded.term_end, Course.term_end),
                        "instructors": func.coalesce(
                            course.excluded.instructors, Course.instructors
                        ),
                        "sections": func.coalesce(course.excluded.sections, Course.sections),
                        "synced_at": course.excluded.synced_at,
                    },
                )
            )

            # An empty list must not be executed (it would be an INSERT with no values).
            if payload.lectures:
                # One row per entry_id (the last one wins), so a duplicate inside the payload
                # neither errors nor counts twice.
                titles = {lec.entry_id: parse_title(lec.title) for lec in payload.lectures}
                rows = {
                    lec.entry_id: {
                        "entry_id": lec.entry_id,
                        "course_id": cid,
                        "title": lec.title,
                        "lecture_no": titles[lec.entry_id][0],
                        "section": titles[lec.entry_id][1],
                        "recorded_at": lec.recorded_at,
                        "recorded_start": lec.recorded_start,
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
                            "lecture_no": lecture.excluded.lecture_no,
                            "section": lecture.excluded.section,
                            "recorded_at": func.coalesce(
                                lecture.excluded.recorded_at, Lecture.recorded_at
                            ),
                            "recorded_start": func.coalesce(
                                lecture.excluded.recorded_start, Lecture.recorded_start
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

            # Never claim "indexed" while a lecture is new or has changed captions. An unchanged
            # re-sync has nothing due, so `indexed` is left as it was.
            session.execute(
                update(Course)
                .where(Course.course_id == cid, _has_due_lecture())
                .values(indexed=False)
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


def list_lecture_statuses(course_id: str) -> list[LectureStatus]:
    """Each stored lecture of the course with whether it has captions, ordered by entry_id. The
    caption text itself is never loaded: Postgres computes the flag."""
    ensure_schema()
    has_captions = (func.coalesce(func.length(Lecture.captions_srt), 0) > 0).label("has_captions")
    stmt = (
        select(Lecture.entry_id, has_captions)
        .where(Lecture.course_id == course_id)
        .order_by(Lecture.entry_id.collate("C"))
    )
    with Session(engine) as session:
        return [LectureStatus(entry_id=r[0], has_captions=r[1]) for r in session.execute(stmt)]


def get_course(course_id: str) -> CourseDetail | None:
    """The course with its details and every stored lecture (no captions), or None. Lectures are
    ordered by number, then recording date, then entry_id; `indexed` is per lecture: its chunks
    were built from its current captions."""
    if "\x00" in course_id:
        return None
    ensure_schema()
    lecture_indexed = Lecture.chunked_hash.is_not_distinct_from(_current_hash()).label("indexed")
    with Session(engine) as session:
        course = session.get(Course, course_id)
        if course is None:
            return None
        rows = session.execute(
            select(
                Lecture.entry_id,
                Lecture.title,
                Lecture.lecture_no,
                Lecture.section,
                Lecture.recorded_at,
                Lecture.recorded_start,
                Lecture.duration_sec,
                Lecture.media_url,
                lecture_indexed,
            )
            .where(Lecture.course_id == course_id)
            .order_by(
                Lecture.lecture_no.asc().nulls_last(),
                Lecture.recorded_start.asc().nulls_last(),
                Lecture.recorded_at.asc().nulls_last(),
                Lecture.entry_id.collate("C"),
            )
        ).all()
        return CourseDetail(
            course_id=course.course_id,
            canvas_course_id=course.canvas_course_id,
            code=course.code,
            name=course.name,
            term=course.term,
            term_start=course.term_start,
            term_end=course.term_end,
            instructors=course.instructors or [],
            sections=course.sections or [],
            synced_at=course.synced_at.astimezone(UTC),
            indexed=course.indexed,
            lectures=[
                LectureInfo(
                    entry_id=r[0],
                    title=r[1],
                    lecture_no=r[2],
                    section=r[3],
                    recorded_at=r[4],
                    # Postgres returns the session's time zone; the contract promises UTC.
                    recorded_start=r[5].astimezone(UTC) if r[5] else None,
                    duration_sec=r[6],
                    media_url=r[7],
                    indexed=bool(r[8]),
                )
                for r in rows
            ],
        )


def course_exists(course_id: str) -> bool:
    if "\x00" in course_id:
        return False  # cannot exist, and the driver refuses to send it; answered as not found
    ensure_schema()
    with Session(engine) as session:
        found = session.execute(select(Course.course_id).where(Course.course_id == course_id))
        return found.first() is not None


# --- ingestion ---


def due_lecture_ids(course_id: str) -> list[str]:
    """Lectures of the course whose chunks are missing or were built from other captions."""
    ensure_schema()
    with Session(engine) as session:
        stmt = select(Lecture.entry_id).where(Lecture.course_id == course_id, _is_due())
        return list(session.execute(stmt.order_by(Lecture.entry_id)).scalars())


def lecture_captions(entry_id: str) -> str | None:
    with Session(engine) as session:
        stmt = select(Lecture.captions_srt).where(Lecture.entry_id == entry_id)
        return session.execute(stmt).scalar_one_or_none()


def replace_chunks(
    entry_id: str, captions_hash_: str, chunks: list[ChunkData], vectors: list[list[float]]
) -> bool:
    """Replace a lecture's chunks in one transaction. Returns False (and writes nothing) if the
    chunks are already current, or if the captions changed since they were read; in the second
    case the lecture stays due for the next run.

    The lecture row is locked first, so overlapping ingestions of the same lecture run one after
    the other and the second one sees the first one's result instead of adding duplicates."""
    with Session(engine) as session, session.begin():
        row = session.execute(
            select(Lecture.chunked_hash, _current_hash())
            .where(Lecture.entry_id == entry_id)
            .with_for_update()
        ).one_or_none()
        if row is None:
            return False
        stored_hash, current_hash = row
        if current_hash != captions_hash_ or stored_hash == captions_hash_:
            return False
        session.execute(delete(Chunk).where(Chunk.entry_id == entry_id))
        if chunks:
            session.execute(
                insert(Chunk),
                [
                    {
                        "entry_id": entry_id,
                        "start_sec": c.start,
                        "end_sec": c.end,
                        "text": c.text,
                        "embedding": v,
                    }
                    for c, v in zip(chunks, vectors, strict=True)
                ],
            )
        session.execute(
            update(Lecture).where(Lecture.entry_id == entry_id).values(chunked_hash=captions_hash_)
        )
    return True


def refresh_indexed(course_id: str) -> None:
    """indexed = the course has lectures and none of them is due."""
    has_lectures = exists().where(Lecture.course_id == Course.course_id)
    with Session(engine) as session, session.begin():
        # Wait for a sync that is still writing this course, THEN look at the lectures. A single
        # UPDATE that blocks on the row would resume with its old view of the lectures and could
        # mark the course indexed while a newly synced lecture is due.
        session.execute(
            select(Course.course_id).where(Course.course_id == course_id).with_for_update()
        ).first()
        session.execute(
            update(Course)
            .where(Course.course_id == course_id)
            .values(indexed=and_(has_lectures, ~_has_due_lecture()))
        )


# --- search ---


@dataclass(frozen=True)
class ChunkHit:
    entry_id: str
    lecture_title: str
    lecture_no: int | None
    media_url: str
    start_sec: float
    end_sec: float
    text: str
    distance: float  # cosine distance to the question; smaller is closer
    # Set for a page of a document (slides). Then entry_id is the doc_id, lecture_title the
    # document's name, media_url its link, and start_sec = end_sec = the page number.
    page: int | None = None


# --- documents (slides) ---


def save_document(
    course_id: str,
    doc_id: str,
    *,
    source: str,
    name: str,
    url: str,
    version: str,
    content_hash: str,
    pages: int,
    chunks: list[tuple[int, str]],
    lecture_no: int | None = None,
) -> None:
    """Store a document and replace its chunks (without embeddings) in one transaction. A
    doc_id that belongs to another course is refused with ValueRejected."""
    ensure_schema()
    values = {
        "course_id": course_id,
        "source": source,
        "name": name,
        "url": url,
        "version": version,
        "content_hash": content_hash,
        "pages": pages,
        "lecture_no": lecture_no,
        "synced_at": datetime.now(UTC),
    }
    try:
        with Session(engine) as session, session.begin():
            owner = session.execute(
                select(Document.course_id).where(Document.doc_id == doc_id).with_for_update()
            ).scalar_one_or_none()
            if owner is not None and owner != course_id:
                raise ValueRejected
            stmt = insert(Document).values(doc_id=doc_id, **values)
            session.execute(stmt.on_conflict_do_update(index_elements=["doc_id"], set_=values))
            session.execute(delete(DocChunk).where(DocChunk.doc_id == doc_id))
            if chunks:
                session.execute(
                    insert(DocChunk),
                    [{"doc_id": doc_id, "page": page, "text": text_} for page, text_ in chunks],
                )
    except DataError:
        raise ValueRejected from None


def delete_document(course_id: str, doc_id: str) -> bool:
    """Remove one of the course's documents and its chunks. False if the course has no such
    document (another course's document is never touched)."""
    ensure_schema()
    with Session(engine) as session, session.begin():
        gone = session.execute(
            delete(Document).where(Document.doc_id == doc_id, Document.course_id == course_id)
        ).rowcount
    return bool(gone)


def list_documents(course_id: str) -> list[DocumentInfo]:
    """The course's documents, by name. `indexed` is true when every chunk has its embedding."""
    ensure_schema()
    pending = (
        exists().where(DocChunk.doc_id == Document.doc_id, DocChunk.embedding.is_(None))
    ).label("pending")
    stmt = (
        select(Document, pending)
        .where(Document.course_id == course_id)
        .order_by(Document.name, Document.doc_id)
    )
    with Session(engine) as session:
        return [
            DocumentInfo(
                doc_id=d.doc_id,
                source=d.source,
                name=d.name,
                url=d.url,
                pages=d.pages,
                version=d.version,
                lecture_no=d.lecture_no,
                indexed=not is_pending,
            )
            for d, is_pending in session.execute(stmt).all()
        ]


def doc_chunks_without_embedding(doc_id: str, limit: int) -> list[tuple[int, str]]:
    stmt = (
        select(DocChunk.id, DocChunk.text)
        .where(DocChunk.doc_id == doc_id, DocChunk.embedding.is_(None))
        .order_by(DocChunk.id)
        .limit(limit)
    )
    with Session(engine) as session:
        return [(r[0], r[1]) for r in session.execute(stmt).all()]


def set_doc_embeddings(rows: list[tuple[int, list[float]]]) -> None:
    with Session(engine) as session, session.begin():
        for chunk_id, vector in rows:
            session.execute(
                update(DocChunk).where(DocChunk.id == chunk_id).values(embedding=vector)
            )


def search_doc_chunks(course_id: str, query_vector: list[float], limit: int) -> list[ChunkHit]:
    """The `limit` document chunks of one course closest to the vector, closest first. An exact
    scan: a course has a few thousand slide chunks at most, far fewer than caption chunks."""
    ensure_schema()
    distance = DocChunk.embedding.cosine_distance(query_vector).label("distance")
    stmt = (
        select(
            Document.doc_id,
            Document.name,
            Document.url,
            DocChunk.page,
            DocChunk.text,
            distance,
        )
        .join(Document, Document.doc_id == DocChunk.doc_id)
        .where(Document.course_id == course_id, DocChunk.embedding.is_not(None))
        .order_by(distance)
        .limit(limit)
    )
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT set_config('enable_indexscan', 'off', true)"))
        rows = session.execute(stmt).all()
    return [
        ChunkHit(doc_id, name, None, url, float(page), float(page), text_, dist, page=page)
        for doc_id, name, url, page, text_, dist in rows
    ]


def lecture_chunks(entry_id: str) -> list[ChunkHit]:
    """Every chunk of one lecture, in the order it was said. For the study tools, which read a
    whole lecture instead of searching it; `distance` is 0 because nothing was searched."""
    ensure_schema()
    stmt = (
        select(
            Chunk.entry_id,
            Lecture.title,
            Lecture.lecture_no,
            Lecture.media_url,
            Chunk.start_sec,
            Chunk.end_sec,
            Chunk.text,
        )
        .join(Lecture, Lecture.entry_id == Chunk.entry_id)
        .where(Chunk.entry_id == entry_id)
        .order_by(Chunk.start_sec)
    )
    with Session(engine) as session:
        return [ChunkHit(*row, 0.0) for row in session.execute(stmt).all()]


def lecture_exists(entry_id: str) -> bool:
    ensure_schema()
    with Session(engine) as session:
        return session.get(Lecture, entry_id) is not None


def _version_at_least(version: str, major: int, minor: int) -> bool:
    try:
        parts = tuple(int(p) for p in version.split(".")[:2])
    except ValueError:
        return False
    return parts >= (major, minor)


_warned_old_pgvector = False


def _search_statement(
    course_id: str, query_vector: list[float], limit: int, entry_id: str | None = None
):
    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
    return (
        select(
            Chunk.entry_id,
            Lecture.title,
            Lecture.lecture_no,
            Lecture.media_url,
            Chunk.start_sec,
            Chunk.end_sec,
            Chunk.text,
            distance,
        )
        .join(Lecture, Lecture.entry_id == Chunk.entry_id)
        .where(
            Lecture.course_id == course_id,
            Chunk.embedding.is_not(None),
            *([Chunk.entry_id == entry_id] if entry_id else []),
        )
        .order_by(distance)
        .limit(limit)
    )


def _to_hits(rows) -> list[ChunkHit]:
    # An index scan with iterative scanning can return rows slightly out of order, so sort.
    return sorted((ChunkHit(*r) for r in rows), key=lambda h: h.distance)


def search_chunks(
    course_id: str, query_vector: list[float], limit: int, entry_id: str | None = None
) -> list[ChunkHit]:
    """The `limit` chunks of one course closest to the vector (cosine), closest first. With
    `entry_id`, only that lecture's chunks."""
    global _warned_old_pgvector
    ensure_schema()
    stmt = _search_statement(course_id, query_vector, limit, entry_id)
    with Session(engine) as session, session.begin():
        # Settings last for this transaction only. The index scan reads ef_search candidates and
        # the course filter is applied afterwards, so a small ef_search under-returns.
        ef = min(max(limit, 40), 1000)
        session.execute(text("SELECT set_config('hnsw.ef_search', :v, true)"), {"v": str(ef)})
        version = session.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar()
        if version and _version_at_least(version, 0, 8):
            # Keep scanning the index until `limit` rows pass the course filter (pgvector 0.8+).
            session.execute(text("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)"))
        elif not _warned_old_pgvector:
            _warned_old_pgvector = True
            log.warning(
                "pgvector %s has no iterative index scan; searches may under-return", version
            )
        rows = session.execute(stmt).all()
        if len(rows) < limit:
            # Either the course has fewer chunks than asked for (the exact scan is cheap), or the
            # index gave up early (hnsw.max_scan_tuples) in a database with many courses. Scan
            # exactly, so "no rows" really means no embedded chunks.
            session.execute(text("SELECT set_config('enable_indexscan', 'off', true)"))
            exact = session.execute(stmt).all()
            if len(exact) > len(rows):
                log.info(
                    "vector index returned %d of %d rows; used an exact scan", len(rows), limit
                )
            rows = exact
    return _to_hits(rows)
