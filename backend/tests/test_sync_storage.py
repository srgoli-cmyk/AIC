"""Storage behaviour of /sync, /courses and /ask. Integration tests need Postgres (`make db`)."""

import copy
import json
import logging
import threading
import time
from pathlib import Path

import dbtools
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DataError

from app import db
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / "contracts/fixtures/sync_sample.json").read_text())
CID = "canvas-999999"

client = TestClient(app)


def _query(sql, params=None):
    with db.engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), params or {}).all()]


def _sync(payload):
    r = client.post("/sync", json=payload)
    assert r.status_code == 200, r.text
    return r


@pytest.mark.integration
def test_sync_twice_gives_one_course_with_two_lectures():
    _sync(SAMPLE)
    _sync(SAMPLE)
    assert client.get("/courses").json() == [
        {
            "course_id": CID,
            "code": SAMPLE["course"]["code"],
            "name": SAMPLE["course"]["name"],
            "lectures": 2,
            "indexed": True,  # chunked by the background ingestion
        }
    ]
    assert _query("SELECT count(*) FROM files") == [(1,)]


@pytest.mark.integration
def test_resync_with_empty_files_keeps_stored_files():
    _sync(SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    payload["files"] = []
    _sync(payload)
    assert _query("SELECT canvas_file_id FROM files") == [(SAMPLE["files"][0]["canvas_file_id"],)]


@pytest.mark.integration
def test_nonempty_files_upsert_by_canvas_file_id_and_keep_others():
    _sync(SAMPLE)
    fid = SAMPLE["files"][0]["canvas_file_id"]
    payload = copy.deepcopy(SAMPLE)
    payload["files"] = [{"canvas_file_id": fid + 1, "name": "other.pdf"}]
    _sync(payload)
    payload["files"] = [{"canvas_file_id": fid, "name": "renamed.pdf"}]
    _sync(payload)
    rows = _query("SELECT canvas_file_id, name FROM files ORDER BY canvas_file_id")
    assert rows == [(fid, "renamed.pdf"), (fid + 1, "other.pdf")]


@pytest.mark.integration
def test_lecture_missing_from_later_payload_stays_stored():
    _sync(SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"] = payload["lectures"][:1]
    _sync(payload)
    assert client.get("/courses").json()[0]["lectures"] == 2


@pytest.mark.integration
def test_resync_updates_lecture_fields():
    _sync(SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"][0]["title"] = "Renamed lecture"
    _sync(payload)
    entry = payload["lectures"][0]["entry_id"]
    assert _query("SELECT title FROM lectures WHERE entry_id = :e", {"e": entry}) == [
        ("Renamed lecture",)
    ]


@pytest.mark.integration
def test_omitted_term_does_not_erase_stored_term():
    payload = copy.deepcopy(SAMPLE)
    payload["course"]["term"] = "SP26"
    _sync(payload)
    del payload["course"]["term"]
    _sync(payload)
    assert _query("SELECT term FROM courses") == [("SP26",)]


@pytest.mark.integration
def test_omitted_recorded_at_and_content_type_are_kept():
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"][0]["recorded_at"] = "2026-04-01"
    payload["files"] = [{"canvas_file_id": 5, "name": "a.pdf", "content_type": "application/pdf"}]
    _sync(payload)
    del payload["lectures"][0]["recorded_at"]
    payload["files"] = [{"canvas_file_id": 5, "name": "a.pdf"}]
    _sync(payload)
    entry = payload["lectures"][0]["entry_id"]
    rec = _query("SELECT recorded_at::text FROM lectures WHERE entry_id = :e", {"e": entry})
    assert rec == [("2026-04-01",)]
    assert _query("SELECT content_type FROM files") == [("application/pdf",)]


@pytest.mark.integration
def test_resync_does_not_reset_indexed():
    _sync(SAMPLE)
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE courses SET indexed = TRUE"))
    _sync(SAMPLE)
    assert client.get("/courses").json()[0]["indexed"] is True


@pytest.mark.integration
def test_course_without_lectures_reports_zero():
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"] = []
    _sync(payload)
    assert client.get("/courses").json()[0]["lectures"] == 0


@pytest.mark.integration
def test_lecture_of_another_course_is_left_untouched_and_counted(caplog):
    _sync(SAMPLE)  # course A (canvas-999999): 2 lectures, 1 file
    other = copy.deepcopy(SAMPLE)
    other["course"]["canvas_course_id"] = 99999
    other["lectures"] = other["lectures"][:1]  # same entry_id as one already in A
    before = _query("SELECT * FROM lectures ORDER BY entry_id")
    other["lectures"][0].update(
        title="Other title TITLE-MARKER",
        media_url="https://canvaskaf.ucsd.edu/media/t/1_other001/222222",
        captions_srt="1\n00:00:00,000 --> 00:00:01,000\nCAPTION-MARKER\n",
    )
    other["files"][0]["name"] = "other-name.pdf"
    with caplog.at_level(logging.WARNING, logger="aic"):
        body = _sync(other).json()
    entry = other["lectures"][0]["entry_id"]
    fid = other["files"][0]["canvas_file_id"]
    # Nothing about the lecture changed, including its course.
    assert _query("SELECT * FROM lectures ORDER BY entry_id") == before
    assert body["lectures_received"] == 1
    assert body["lectures_skipped_other_course"] == 1
    warnings = [r.getMessage() for r in caplog.records if r.name == "aic"]
    assert warnings == [
        f"lecture {entry} belongs to course {CID}; sync of course canvas-99999 skipped it"
    ]
    assert "MARKER" not in caplog.text
    # Files are different: course_id stays, other fields still update.
    assert _query("SELECT course_id, name FROM files WHERE canvas_file_id = :f", {"f": fid}) == [
        (CID, "other-name.pdf")
    ]
    counts = {c["course_id"]: c["lectures"] for c in client.get("/courses").json()}
    assert counts == {CID: 2, "canvas-99999": 0}


@pytest.mark.integration
def test_duplicate_skipped_lecture_in_one_payload_is_counted_once():
    _sync(SAMPLE)
    other = copy.deepcopy(SAMPLE)
    other["course"]["canvas_course_id"] = 99999
    other["lectures"] = [other["lectures"][0], copy.deepcopy(other["lectures"][0])]
    body = _sync(other).json()
    assert body["lectures_received"] == 2
    assert body["lectures_skipped_other_course"] == 1


@pytest.mark.integration
def test_normal_resync_skips_nothing():
    _sync(SAMPLE)
    assert _sync(SAMPLE).json()["lectures_skipped_other_course"] == 0


def _rejected_values():
    def with_(edit):
        payload = copy.deepcopy(SAMPLE)
        edit(payload)
        return payload

    # The contract allows each of these, but storage cannot hold them. For the NUL byte the
    # driver itself refuses; for the others Postgres does. The course row is written first, so
    # in every case except course_id_over_int32 (which fails on that first statement) the empty
    # tables below also prove the rollback.
    return {
        "course_id_over_int32": with_(lambda p: p["course"].update(canvas_course_id=2**31)),
        "file_id_over_int64": with_(lambda p: p["files"][0].update(canvas_file_id=2**63)),
        "duration_over_real": with_(lambda p: p["lectures"][1].update(duration_sec=1e39)),
        "nul_byte": with_(lambda p: p["lectures"][1].update(title="bad\x00title")),
    }


@pytest.mark.integration
@pytest.mark.parametrize("case", list(_rejected_values()))
def test_value_rejected_by_storage_gives_422_and_leaves_nothing_written(case, caplog):
    payload = _rejected_values()[case]
    r = client.post("/sync", json=payload)
    assert r.status_code == 422
    assert r.json() == {"detail": "a value was rejected by the database"}
    # Exactly one warning, carrying only the sqlstate (None for the driver-side NUL refusal).
    messages = [rec.getMessage() for rec in caplog.records if rec.name == "aic"]
    assert len(messages) == 1
    assert messages[0].startswith("value rejected by the database (sqlstate=")
    assert _query("SELECT count(*) FROM courses") == [(0,)]
    assert _query("SELECT count(*) FROM lectures") == [(0,)]
    assert _query("SELECT count(*) FROM files") == [(0,)]


@pytest.mark.integration
def test_duplicate_ids_in_one_payload_last_wins_and_one_row_is_stored():
    payload = copy.deepcopy(SAMPLE)
    first = copy.deepcopy(payload["lectures"][0])
    first["title"] = "first"
    last = copy.deepcopy(first)
    last["title"] = "last"
    payload["lectures"] = [first, last]
    payload["files"] = [
        {"canvas_file_id": 7, "name": "first.pdf"},
        {"canvas_file_id": 7, "name": "last.pdf"},
    ]
    r = _sync(payload)
    assert r.json()["lectures_received"] == 2
    assert _query("SELECT title FROM lectures") == [("last",)]
    assert _query("SELECT name FROM files") == [("last.pdf",)]


@pytest.mark.integration
def test_ask_unknown_course_is_404():
    r = client.post("/ask", json={"course_id": "canvas-1", "question": "anything?"})
    assert r.status_code == 404


@pytest.mark.integration
def test_ask_known_course_without_a_key_is_503():
    _sync(SAMPLE)
    r = client.post("/ask", json={"course_id": CID, "question": "anything?"})
    assert r.status_code == 503


@pytest.mark.integration
def test_startup_applies_schema_to_a_database_without_files_table(monkeypatch):
    monkeypatch.setattr(db, "_schema_ready", False)
    with db.engine.begin() as conn:
        conn.execute(text("DROP TABLE files"))
    with TestClient(app):  # entering the context runs the lifespan
        pass
    assert _query("SELECT count(*) FROM files") == [(0,)]


@pytest.mark.integration
def test_schema_is_applied_on_first_use_after_a_down_start(monkeypatch):
    """Postgres down at startup, then back: the first request creates the missing table."""
    with db.engine.begin() as conn:
        conn.execute(text("DROP TABLE files"))
    real_engine = db.engine
    monkeypatch.setattr(db, "_schema_ready", False)
    monkeypatch.setattr(db, "engine", _unreachable_engine())
    with TestClient(app) as c:
        assert c.get("/courses").status_code == 503  # still down
        monkeypatch.setattr(db, "engine", real_engine)  # Postgres is back
        assert c.get("/courses").status_code == 200
    assert _query("SELECT count(*) FROM files") == [(0,)]


def _within(seconds, fn):
    """Run fn in a thread and fail (instead of hanging the suite) if it takes longer."""
    box = {}
    thread = threading.Thread(target=lambda: box.update(result=fn()), daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        pytest.fail(f"still waiting after {seconds}s: a lock timeout is not being applied")
    return box["result"]


@pytest.mark.integration
def test_schema_lock_timeout_warns_at_startup_and_retries_later(monkeypatch, caplog):
    monkeypatch.setattr(db, "_schema_ready", False)
    monkeypatch.setattr(db, "LOCK_TIMEOUT", "200ms")
    # Another transaction holds `chunks`; CREATE INDEX IF NOT EXISTS has to wait for it.
    holder = db.engine.connect()
    try:
        holder.execute(text("LOCK TABLE chunks IN ACCESS EXCLUSIVE MODE"))
        with caplog.at_level(logging.WARNING, logger="aic"):
            c = TestClient(app)
            _within(5, c.__enter__)  # startup runs the lifespan
            try:
                assert "schema will be applied on first use" in caplog.text
                assert _within(5, lambda: c.get("/courses")).status_code == 503
                holder.rollback()
                assert _within(5, lambda: c.get("/courses")).status_code == 200
            finally:
                c.__exit__(None, None, None)
    finally:
        holder.close()


@pytest.mark.integration
def test_request_waiting_on_a_table_lock_gives_503_in_bounded_time(monkeypatch):
    db.ensure_schema()  # so the request below is not stopped by the schema step
    monkeypatch.setattr(db, "engine", db.make_engine(lock_timeout="200ms"))
    holder = create_engine(db.engine.url).connect()
    try:
        holder.execute(text("LOCK TABLE courses IN ACCESS EXCLUSIVE MODE"))
        start = time.monotonic()
        r = _within(5, lambda: client.get("/courses"))
        assert time.monotonic() - start < 3
        assert r.status_code == 503
        assert r.json() == {"detail": "database unavailable"}
    finally:
        holder.close()


@pytest.mark.integration
def test_concurrent_schema_applies_do_not_collide():
    errors = []
    barrier = threading.Barrier(6)

    def apply():
        barrier.wait()
        try:
            db.apply_schema()
        except Exception as exc:  # noqa: BLE001
            errors.append(type(exc).__name__)

    try:
        with db.engine.begin() as conn:
            conn.execute(text("DROP TABLE doc_chunks, documents, chunks, files, lectures, courses"))
        threads = [threading.Thread(target=apply) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    finally:
        db.apply_schema()  # leave the tables in place for the next test
    assert errors == []


@pytest.mark.integration
def test_connection_lost_mid_request_gives_503():
    _sync(SAMPLE)  # also makes sure the schema step is done
    state = {"done": False}

    def kill_own_backend(conn, cursor, statement, *_args):
        if state["done"] or "FROM courses" not in statement:
            return
        state["done"] = True
        pid = conn.connection.dbapi_connection.info.backend_pid
        with db.engine.connect() as other:
            other.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
        time.sleep(0.2)

    event.listen(db.engine, "before_cursor_execute", kill_own_backend)
    try:
        r = client.get("/courses")
    finally:
        event.remove(db.engine, "before_cursor_execute", kill_own_backend)
    assert state["done"]
    assert r.status_code == 503
    assert r.json() == {"detail": "database unavailable"}


# --- no database needed ---


def _unreachable_engine():
    return create_engine("postgresql+psycopg://user:hunter2@127.0.0.1:1/secretdb")


def test_database_down_gives_503_without_leaking_details(monkeypatch, caplog):
    monkeypatch.setattr(db, "_schema_ready", False)
    monkeypatch.setattr(db, "engine", _unreachable_engine())
    caption = SAMPLE["lectures"][0]["captions_srt"]
    with caplog.at_level(logging.DEBUG):
        responses = (
            client.get("/courses"),
            client.post("/sync", json=SAMPLE),
            client.post("/ask", json={"course_id": CID, "question": "anything?"}),
        )
    for r in responses:
        assert r.status_code == 503
        assert r.json() == {"detail": "database unavailable"}
        assert "hunter2" not in r.text
    assert "hunter2" not in caplog.text
    assert caption[:30] not in caplog.text


def test_startup_continues_with_a_warning_when_database_is_unreachable(monkeypatch, caplog):
    monkeypatch.setattr(db, "_schema_ready", False)
    monkeypatch.setattr(db, "engine", _unreachable_engine())
    with caplog.at_level(logging.DEBUG), TestClient(app) as c:
        assert c.get("/health").status_code == 200
    assert "database unavailable at startup" in caplog.text
    for secret in ("hunter2", "127.0.0.1", "secretdb", "postgresql"):
        assert secret not in caplog.text


def test_unknown_field_value_is_not_echoed_in_422(caplog):
    payload = copy.deepcopy(SAMPLE)
    payload["token"] = "TOP-SECRET-VALUE"  # noqa: S105
    with caplog.at_level(logging.DEBUG):
        r = client.post("/sync", json=payload)
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)
    assert "input" not in r.json()["detail"][0]
    assert "TOP-SECRET-VALUE" not in r.text + caplog.text


def test_over_limit_caption_is_not_echoed_in_422(caplog):
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"][0]["captions_srt"] = "CAPTION-MARKER " * 140_000  # over 2,000,000
    with caplog.at_level(logging.DEBUG):
        r = client.post("/sync", json=payload)
    assert r.status_code == 422
    assert "CAPTION-MARKER" not in r.text + caplog.text


def test_lone_surrogate_in_rejected_input_gives_422_and_logs_no_caption(caplog):
    # json.loads accepts "\ud83d" (a lone surrogate); encoding it back used to crash the handler.
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"][0]["captions_srt"] = "\ud83d" + "x" * 2_000_000
    body = json.dumps(payload)  # ensure_ascii writes the surrogate as a \\ud83d escape
    payload2 = copy.deepcopy(SAMPLE)
    payload2["\ud83dkey"] = "\ud83dvalue"
    with caplog.at_level(logging.DEBUG):
        r1 = client.post("/sync", content=body, headers={"Content-Type": "application/json"})
        r2 = client.post(
            "/sync",
            content=json.dumps(payload2),
            headers={"Content-Type": "application/json"},
        )
    assert r1.status_code == 422
    assert r2.status_code == 422
    assert "xxxxxxxx" not in caplog.text


def test_repr_of_settings_hides_secrets():
    shown = repr(db.settings)
    # Compute a bool first: a failing `assert` on the strings would print the secret repr.
    hidden = (
        db.settings.database_url not in shown
        and "database_url" not in shown
        and "openrouter_api_key" not in shown
    )
    assert hidden


def test_engine_hides_bound_parameters():
    assert db.engine.hide_parameters


@pytest.mark.integration
def test_requests_use_a_table_lock_timeout():
    with db.engine.connect() as conn:
        assert conn.execute(text("SHOW lock_timeout")).scalar() == "5s"


def test_second_thread_fails_fast_while_schema_is_being_applied(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow_apply():
        started.set()
        release.wait(10)

    monkeypatch.setattr(db, "_schema_ready", False)
    monkeypatch.setattr(db, "apply_schema", slow_apply)
    first = threading.Thread(target=db.ensure_schema, daemon=True)
    first.start()
    try:
        assert started.wait(5)
        t0 = time.monotonic()
        r = client.get("/courses")
        assert time.monotonic() - t0 < 2
        assert r.status_code == 503
        assert r.json() == {"detail": "database unavailable"}
    finally:
        release.set()
        first.join(5)
    assert db._schema_ready is True


def test_dataerror_outside_sync_is_not_reported_as_422(monkeypatch):
    def boom():
        raise DataError("SELECT 1/0", None, Exception("division by zero"))

    monkeypatch.setattr(db, "list_courses", boom)
    r = TestClient(app, raise_server_exceptions=False).get("/courses")
    assert r.status_code == 500


def test_ask_with_nul_byte_in_course_id_is_404_without_touching_the_database():
    r = client.post("/ask", json={"course_id": "canvas-1\x00", "question": "anything?"})
    assert r.status_code == 404


def test_long_unknown_key_is_truncated_in_422():
    payload = copy.deepcopy(SAMPLE)
    payload["k" * 5000] = 1
    r = client.post("/sync", json=payload)
    assert r.status_code == 422
    assert len(r.text) < 1000
    assert all(len(p) <= 100 for e in r.json()["detail"] for p in e["loc"] if isinstance(p, str))


def test_url_normalization_selects_psycopg3_and_keeps_password():
    url = db.normalize_url("postgresql://u:pw@localhost:5432/x")
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "pw"  # noqa: S105
    assert db.normalize_url("postgresql+psycopg://u@h/x").drivername == "postgresql+psycopg"


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _FakeConn:
    def __init__(self, name):
        self.name = name
        self.statements = []

    def execute(self, stmt):
        self.statements.append(str(stmt))
        return _Result(self.name)


def test_truncate_refuses_database_not_ending_in_test():
    conn = _FakeConn("aic")
    with pytest.raises(RuntimeError, match="_test"):
        dbtools.truncate_all(conn)
    assert not any("TRUNCATE" in s for s in conn.statements)


def test_truncate_runs_on_test_database():
    conn = _FakeConn("aic_test")
    dbtools.truncate_all(conn)
    assert any("TRUNCATE" in s for s in conn.statements)


def test_tests_use_a_test_database():
    name = dbtools.db_name(db.settings.database_url)  # compare a local, never `settings` itself
    assert name.endswith("_test")
