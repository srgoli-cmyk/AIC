"""db/init.sql must work on a fresh database, on a database built from main's older schema (no
user_id, single-column keys) and on one that is already migrated, losing no rows or embeddings.

The old schema is tests/fixtures/init_main.sql, an exact copy of backend/db/init.sql at origin/main
commit 3afdcc49bb62504409cfca46154bc26530629686 (`git show origin/main:backend/db/init.sql`).
The auth PR's schema is tests/fixtures/init_pr.sql, an exact copy of backend/db/init.sql at
origin/feat/ucsd-google-auth commit fa45f14ed1bbde04d4cad629140e0ad40b294164 (it has per-user keys
but no auth_exchange_codes.nonce_hash).
Scratch databases end in `_test` and are dropped at the end, even when a test fails."""

from pathlib import Path

import dbtools
import pytest
from sqlalchemy import create_engine, text

from app import auth_db, db

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures"
MAIN_SCHEMA = (FIXTURES / "init_main.sql").read_text()
PR_SCHEMA = (FIXTURES / "init_pr.sql").read_text()
NEW_SCHEMA = db.INIT_SQL.read_text()
DIM = 384

TABLES = ("courses", "lectures", "files", "chunks", "documents", "doc_chunks", "course_sites")

_COURSE_FK = (
    "FOREIGN KEY (user_id, course_id) REFERENCES courses(user_id, course_id) ON DELETE CASCADE"
)
EXPECTED_KEYS = {
    "courses": {
        "PRIMARY KEY (user_id, course_id)",
        "UNIQUE (user_id, canvas_course_id)",
        "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE",
    },
    "lectures": {"PRIMARY KEY (user_id, entry_id)", _COURSE_FK},
    "files": {"PRIMARY KEY (user_id, canvas_file_id)", _COURSE_FK},
    "chunks": {
        "PRIMARY KEY (id)",
        "FOREIGN KEY (user_id, entry_id) REFERENCES lectures(user_id, entry_id) ON DELETE CASCADE",
    },
    "documents": {"PRIMARY KEY (user_id, doc_id)", _COURSE_FK},
    "doc_chunks": {
        "PRIMARY KEY (id)",
        "FOREIGN KEY (user_id, doc_id) REFERENCES documents(user_id, doc_id) ON DELETE CASCADE",
    },
    "course_sites": {"PRIMARY KEY (user_id, course_id)", _COURSE_FK},
}


def _vec(i: int) -> str:
    v = [0.0] * DIM
    v[i] = 1.0
    return "[" + ",".join(str(x) for x in v) + "]"


class Scratch:
    """A scratch database plus an engine for it."""

    def __init__(self, name: str):
        main_url, _ = dbtools.resolve_urls()
        self.main_url = main_url
        self.name = name
        self.url = dbtools.with_db_name(main_url, name)
        self.engine = None

    def create(self) -> None:
        self.drop()  # a leftover from an interrupted run
        dbtools.create_database_if_missing(self.main_url, self.url)
        self.engine = db.make_engine(self.url)

    def drop(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None
        assert self.name.endswith("_test")  # never drop anything else
        admin = create_engine(db.normalize_url(self.main_url), isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)'))
        finally:
            admin.dispose()

    def run_sql(self, sql: str) -> None:
        with self.engine.begin() as conn:
            conn.exec_driver_sql(sql)

    def key_defs(self, table: str) -> set[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = CAST(:t AS regclass) AND contype IN ('p', 'u', 'f')"
                ),
                {"t": table},
            )
            return {r[0] for r in rows}

    def count(self, table: str) -> int:
        with self.engine.connect() as conn:
            return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()  # noqa: S608


@pytest.fixture
def scratch_factory():
    made: list[Scratch] = []

    def make(name: str) -> Scratch:
        s = Scratch(name)
        made.append(s)  # registered before create, so a failed create is still cleaned up
        s.create()
        return s

    try:
        yield make
    finally:
        for s in made:
            s.drop()


def _insert_old_data(s: Scratch) -> None:
    """Rows in main's schema (no user_id): two courses with lectures, chunks, files, a document
    with chunks and a course site."""
    with s.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO courses (course_id, canvas_course_id, code, name, term, synced_at) "
                "VALUES ('canvas-1', 1, 'MATH 20A', 'Calc', 'FA25', now()), "
                "('canvas-2', 2, 'CSE 8A', 'Intro', 'FA25', now())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO lectures (entry_id, course_id, title, duration_sec, media_url, "
                "captions_srt) VALUES "
                "('e1', 'canvas-1', 'Lecture 1', 60, 'https://x/e1', "
                "'1\n00:00:00,000 --> 00:00:02,000\nhello\n'), "
                "('e2', 'canvas-1', 'Lecture 2', 60, 'https://x/e2', "
                "'1\n00:00:00,000 --> 00:00:02,000\nworld\n'), "
                "('e3', 'canvas-2', 'Lecture 1', 60, 'https://x/e3', "
                "'1\n00:00:00,000 --> 00:00:02,000\nother\n')"
            )
        )
        for i, entry in enumerate(["e1", "e1", "e2", "e3"]):
            conn.execute(
                text(
                    "INSERT INTO chunks (entry_id, start_sec, end_sec, text, embedding) "
                    "VALUES (:e, 0, 10, :t, CAST(:v AS vector))"
                ),
                {"e": entry, "t": f"chunk {i}", "v": _vec(i)},
            )
        conn.execute(
            text(
                "INSERT INTO files (canvas_file_id, course_id, name) "
                "VALUES (10, 'canvas-1', 'a.pdf'), (11, 'canvas-2', 'b.pdf')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO documents (doc_id, course_id, source, name, url, version, "
                "content_hash, pages, synced_at) VALUES "
                "('canvas-file-10', 'canvas-1', 'canvas', 'a.pdf', 'https://x/a', 'v1', 'h', 2, "
                "now()), "
                "('canvas-file-11', 'canvas-2', 'canvas', 'b.pdf', 'https://x/b', 'v1', 'h', 1, "
                "now())"
            )
        )
        for i, doc in enumerate(["canvas-file-10", "canvas-file-10", "canvas-file-11"]):
            conn.execute(
                text(
                    "INSERT INTO doc_chunks (doc_id, page, text, embedding) "
                    "VALUES (:d, 1, :t, CAST(:v AS vector))"
                ),
                {"d": doc, "t": f"page {i}", "v": _vec(100 + i)},
            )
        conn.execute(
            text(
                "INSERT INTO course_sites (course_id, url, title, chosen_by, status, updated_at) "
                "VALUES ('canvas-1', 'https://site', 'Site', 'student', 'done', now())"
            )
        )


def test_migrates_main_schema_keeping_every_row(scratch_factory, monkeypatch):
    s = scratch_factory("aic_migration_test")
    s.run_sql(MAIN_SCHEMA)
    _insert_old_data(s)
    before = {t: s.count(t) for t in TABLES}
    assert before == {
        "courses": 2,
        "lectures": 3,
        "files": 2,
        "chunks": 4,
        "documents": 2,
        "doc_chunks": 3,
        "course_sites": 1,
    }

    s.run_sql(NEW_SCHEMA)
    s.run_sql(NEW_SCHEMA)  # a second startup must change nothing and not fail

    # Rows survive, all belong to the local student, and embeddings are intact.
    assert {t: s.count(t) for t in TABLES} == before
    with s.engine.connect() as conn:
        for t in TABLES:
            owners = conn.execute(text(f"SELECT DISTINCT user_id FROM {t}")).all()  # noqa: S608
            assert owners == [("usr_local",)], t
        embedded = conn.execute(text("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL"))
        assert embedded.scalar() == 4
        nearest = conn.execute(
            text("SELECT text FROM chunks ORDER BY embedding <=> CAST(:v AS vector) LIMIT 1"),
            {"v": _vec(2)},
        ).scalar()
        assert nearest == "chunk 2"
        indexes = dict(
            conn.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE indexname IN "
                    "('chunks_entry_idx', 'documents_course_idx', 'doc_chunks_doc_idx')"
                )
            ).all()
        )
    assert len(indexes) == 3
    assert all("user_id" in d for d in indexes.values()), indexes

    # Composite keys exist; the old single-column ones are gone.
    for table in TABLES:
        assert s.key_defs(table) == EXPECTED_KEYS[table], table

    # The app's own queries work against the migrated database.
    monkeypatch.setattr(db, "engine", s.engine)
    monkeypatch.setattr(db, "_schema_ready", True)
    courses = db.list_courses(user_id="usr_local")
    assert sorted(c.course_id for c in courses) == ["canvas-1", "canvas-2"]
    detail = db.get_course("canvas-1", user_id="usr_local")
    assert detail is not None and len(detail.lectures) == 2
    hits = db.search_chunks("canvas-1", [1.0] + [0.0] * (DIM - 1), 5, user_id="usr_local")
    assert [h.text for h in hits][0] == "chunk 0"
    assert {h.entry_id for h in hits} == {"e1", "e2"}  # nothing from canvas-2
    assert db.search_chunks("canvas-1", [1.0] + [0.0] * (DIM - 1), 5, user_id="usr_other") == []


def test_cascade_delete_uses_the_new_keys(scratch_factory):
    s = scratch_factory("aic_migration_cascade_test")
    s.run_sql(MAIN_SCHEMA)
    _insert_old_data(s)
    s.run_sql(NEW_SCHEMA)

    with s.engine.begin() as conn:
        conn.execute(
            text("DELETE FROM courses WHERE user_id = 'usr_local' AND course_id = 'canvas-1'")
        )

    # Everything under canvas-1 is gone; canvas-2 is untouched.
    assert {t: s.count(t) for t in TABLES} == {
        "courses": 1,
        "lectures": 1,
        "files": 1,
        "chunks": 1,
        "documents": 1,
        "doc_chunks": 1,
        "course_sites": 0,
    }


def test_same_canvas_course_for_two_students_after_migration(scratch_factory):
    """The point of the new keys: UNIQUE(canvas_course_id) alone must no longer apply."""
    s = scratch_factory("aic_migration_two_users_test")
    s.run_sql(MAIN_SCHEMA)
    _insert_old_data(s)
    s.run_sql(NEW_SCHEMA)
    with s.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, google_sub, email, name, created_at, last_login_at) "
                "VALUES ('usr_b', 'sub_b', 'b@ucsd.edu', 'B', now(), now())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO courses (user_id, course_id, canvas_course_id, code, name, synced_at) "
                "VALUES ('usr_b', 'canvas-1', 1, 'MATH 20A', 'Calc', now())"
            )
        )
    assert s.count("courses") == 3


def test_migrates_a_database_built_by_the_auth_pr(scratch_factory, monkeypatch):
    """A database created from the auth PR's init.sql has per-user keys but no
    auth_exchange_codes.nonce_hash. The current init.sql must add that column, keep every row and
    leave the old code (no challenge) unusable."""
    s = scratch_factory("aic_migration_pr_test")
    s.run_sql(PR_SCHEMA)
    with s.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, google_sub, email, name, created_at, last_login_at) "
                "VALUES ('usr_a', 'sub_a', 'a@ucsd.edu', 'A', now(), now())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sessions (id, user_id, token_hash, kind, expires_at, created_at) "
                "VALUES ('ses_1', 'usr_a', 'tokhash', 'web', now() + interval '1 day', now())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO auth_exchange_codes (code_hash, user_id, expires_at, created_at) "
                "VALUES (:h, 'usr_a', now() + interval '1 day', now())"
            ),
            {"h": auth_db.hash_token("old-raw-code")},
        )
        # Per-user data for two students; the same Canvas course for both.
        for user in ("usr_local", "usr_a"):
            conn.execute(
                text(
                    "INSERT INTO courses (user_id, course_id, canvas_course_id, code, name, "
                    "synced_at) VALUES (:u, 'canvas-1', 1, 'MATH 20A', 'Calc', now())"
                ),
                {"u": user},
            )
            conn.execute(
                text(
                    "INSERT INTO lectures (user_id, entry_id, course_id, title, duration_sec, "
                    "media_url, captions_srt) VALUES (:u, 'e1', 'canvas-1', 'L1', 60, "
                    "'https://x/e1', 'cap')"
                ),
                {"u": user},
            )
            conn.execute(
                text(
                    "INSERT INTO chunks (user_id, entry_id, start_sec, end_sec, text, embedding) "
                    "VALUES (:u, 'e1', 0, 10, 'chunk', CAST(:v AS vector))"
                ),
                {"u": user, "v": _vec(3)},
            )
            conn.execute(
                text(
                    "INSERT INTO files (user_id, canvas_file_id, course_id, name) "
                    "VALUES (:u, 10, 'canvas-1', 'a.pdf')"
                ),
                {"u": user},
            )
            conn.execute(
                text(
                    "INSERT INTO documents (user_id, doc_id, course_id, source, name, url, "
                    "version, content_hash, pages, synced_at) VALUES (:u, 'canvas-file-10', "
                    "'canvas-1', 'canvas', 'a.pdf', 'https://x/a', 'v1', 'h', 1, now())"
                ),
                {"u": user},
            )
            conn.execute(
                text(
                    "INSERT INTO doc_chunks (user_id, doc_id, page, text, embedding) "
                    "VALUES (:u, 'canvas-file-10', 1, 'page', CAST(:v AS vector))"
                ),
                {"u": user, "v": _vec(7)},
            )
            conn.execute(
                text(
                    "INSERT INTO course_sites (user_id, course_id, url, title, chosen_by, "
                    "status, updated_at) VALUES (:u, 'canvas-1', 'https://site', 'Site', "
                    "'student', 'done', now())"
                ),
                {"u": user},
            )
    tables = (*TABLES, "users", "sessions", "auth_exchange_codes")
    before = {t: s.count(t) for t in tables}
    assert before["courses"] == 2 and before["chunks"] == 2 and before["auth_exchange_codes"] == 1
    assert before["users"] == 2  # usr_local (seeded by the schema) and usr_a

    s.run_sql(NEW_SCHEMA)
    s.run_sql(NEW_SCHEMA)  # a second startup must change nothing and not fail

    assert {t: s.count(t) for t in tables} == before
    for table in TABLES:
        assert s.key_defs(table) == EXPECTED_KEYS[table], table
    with s.engine.connect() as conn:
        columns = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'auth_exchange_codes'"
                )
            )
        }
        assert "nonce_hash" in columns
        challenge = conn.execute(text("SELECT nonce_hash FROM auth_exchange_codes")).scalar()
        assert challenge is None  # the old row has no challenge

    # The old code is refused whatever nonce is sent, and is burned by the attempt.
    monkeypatch.setattr(db, "engine", s.engine)
    monkeypatch.setattr(db, "_schema_ready", True)
    assert auth_db.consume_extension_code("old-raw-code", "any-nonce") is None
    assert s.count("auth_exchange_codes") == 0


def test_init_sql_on_an_empty_database_twice(scratch_factory):
    s = scratch_factory("aic_migration_fresh_test")
    s.run_sql(NEW_SCHEMA)
    s.run_sql(NEW_SCHEMA)
    for table in TABLES:
        assert s.key_defs(table) == EXPECTED_KEYS[table], table
        assert s.count(table) == 0
    with s.engine.connect() as conn:
        seeded = conn.execute(text("SELECT count(*) FROM users WHERE id = 'usr_local'")).scalar()
    assert seeded == 1
