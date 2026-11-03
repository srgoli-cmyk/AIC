"""Ingestion: captions become embedded chunks after /sync. A fake model is used (see conftest)."""

import asyncio
import copy
import json
import logging
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import chunking, db, embeddings, ingest
from app.chunking import chunk_cues
from app.main import app, sync
from app.schemas import SyncPayload
from app.srt import parse_srt

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / "contracts/fixtures/sync_sample.json").read_text())
CID = "canvas-999999"
E1 = SAMPLE["lectures"][0]["entry_id"]
E2 = SAMPLE["lectures"][1]["entry_id"]

client = TestClient(app)
run_ingestion = ingest.ingest_course  # the no_ingestion fixture replaces ingest.ingest_course


def _query(sql, params=None):
    with db.engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), params or {}).all()]


def _sync(payload):
    r = client.post("/sync", json=payload)
    assert r.status_code == 200, r.text
    return r


def _indexed(course_id=CID):
    return {c["course_id"]: c["indexed"] for c in client.get("/courses").json()}[course_id]


def _chunks(entry_id):
    return _query(
        "SELECT start_sec, end_sec FROM chunks WHERE entry_id = :e ORDER BY start_sec",
        {"e": entry_id},
    )


def _chunk_ids():
    return _query("SELECT id FROM chunks ORDER BY id")


@pytest.fixture
def no_ingestion(monkeypatch):
    """Let /sync store data without the background ingestion, to drive it by hand."""
    monkeypatch.setattr(ingest, "ingest_course", lambda course_id: None)


@pytest.mark.integration
def test_sync_chunks_fixture_with_correct_timestamps_and_marks_course_indexed():
    r = _sync(SAMPLE)
    assert r.json()["status"] == "accepted"
    assert _chunks(E1) == [(0.0, 30.0), (60.0, 95.0)]
    assert _chunks(E2) == [(0.0, 40.0)]
    assert _indexed() is True
    assert _query("SELECT DISTINCT vector_dims(embedding) FROM chunks") == [(384,)]
    texts = _query("SELECT text FROM chunks WHERE entry_id = :e ORDER BY start_sec", {"e": E1})
    assert texts[0][0].startswith("Okay, today we look at derivatives of products.")


@pytest.mark.integration
def test_unchanged_resync_does_not_rechunk(fake_model):
    _sync(SAMPLE)
    ids, calls = _chunk_ids(), len(fake_model.calls)
    _sync(SAMPLE)
    assert _chunk_ids() == ids
    assert len(fake_model.calls) == calls
    assert _indexed() is True


@pytest.mark.integration
def test_changed_captions_rechunk_only_that_lecture():
    _sync(SAMPLE)
    old_e1 = _query("SELECT id FROM chunks WHERE entry_id = :e", {"e": E1})
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"][1]["captions_srt"] = "1\n00:00:00,000 --> 00:00:10,000\nNew words here.\n"
    _sync(payload)
    assert _query("SELECT id FROM chunks WHERE entry_id = :e", {"e": E1}) == old_e1
    assert _chunks(E2) == [(0.0, 10.0)]
    assert _query("SELECT text FROM chunks WHERE entry_id = :e", {"e": E2}) == [
        ("New words here.",)
    ]
    assert _indexed() is True


@pytest.mark.integration
def test_indexed_is_false_while_lectures_are_due_and_resync_keeps_it_true(no_ingestion):
    _sync(SAMPLE)
    assert _indexed() is False  # stored but not chunked
    run_ingestion(CID)
    assert _indexed() is True
    _sync(SAMPLE)
    assert _indexed() is True  # unchanged re-sync does not reset it
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"][0]["captions_srt"] += "\n4\n00:02:00,000 --> 00:02:02,000\nMore.\n"
    _sync(payload)
    assert _indexed() is False  # changed captions are due again
    run_ingestion(CID)
    assert _indexed() is True


@pytest.mark.integration
def test_new_lecture_in_a_resync_marks_course_not_indexed_until_chunked(no_ingestion):
    _sync(SAMPLE)
    run_ingestion(CID)
    payload = copy.deepcopy(SAMPLE)
    extra = copy.deepcopy(payload["lectures"][0])
    extra["entry_id"] = "1_fake0003"
    payload["lectures"].append(extra)
    _sync(payload)
    assert _indexed() is False
    run_ingestion(CID)
    assert _indexed() is True
    assert _chunks("1_fake0003") == [(0.0, 30.0), (60.0, 95.0)]


@pytest.mark.integration
def test_course_without_lectures_is_never_indexed():
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"] = []
    _sync(payload)
    assert _indexed() is False


@pytest.mark.integration
def test_failed_ingestion_does_not_fail_sync_logs_type_only_and_is_retried(monkeypatch, caplog):
    working = embeddings.embed

    def broken(texts):
        raise RuntimeError("SECRET-CAPTION-TEXT")

    monkeypatch.setattr(embeddings, "embed", broken)
    with caplog.at_level(logging.DEBUG):
        _sync(SAMPLE)
    assert _indexed() is False
    assert _query("SELECT count(*) FROM chunks") == [(0,)]
    assert _query("SELECT count(*) FROM lectures WHERE chunked_hash IS NOT NULL") == [(0,)]
    messages = [r.getMessage() for r in caplog.records if r.name == "aic"]
    assert f"ingestion of lecture {E1} failed: RuntimeError" in messages
    assert "SECRET-CAPTION-TEXT" not in caplog.text and "derivatives" not in caplog.text

    monkeypatch.setattr(embeddings, "embed", working)
    _sync(SAMPLE)  # unchanged payload: the failed lectures are still due and are retried
    assert _indexed() is True
    assert _chunks(E1) == [(0.0, 30.0), (60.0, 95.0)]


@pytest.mark.integration
def test_missing_ml_dependencies_stop_the_run_with_one_log_line(monkeypatch, caplog):
    monkeypatch.setattr(embeddings, "_load", embeddings._load.real)
    monkeypatch.setattr(embeddings, "_model", None)
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # the import fails
    with caplog.at_level(logging.ERROR, logger="aic"):
        _sync(SAMPLE)
    errors = [r.getMessage() for r in caplog.records if r.name == "aic"]
    assert errors == ["ingestion stopped: embedding model not loaded (ModuleNotFoundError)"]
    assert _indexed() is False
    assert _query("SELECT count(*) FROM lectures WHERE chunked_hash IS NULL") == [(2,)]


@pytest.mark.integration
def test_model_load_failure_is_not_retried_for_every_lecture(monkeypatch, caplog):
    calls = []

    def broken(texts):
        calls.append(1)
        raise embeddings.ModelLoadError("OSError")

    monkeypatch.setattr(embeddings, "embed", broken)
    with caplog.at_level(logging.ERROR, logger="aic"):
        _sync(SAMPLE)
    assert len(calls) == 1  # the second lecture was never tried
    assert "embedding model not loaded (OSError)" in caplog.text
    assert _indexed() is False


@pytest.mark.integration
def test_dropped_caption_blocks_are_counted_in_a_warning(caplog):
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"] = payload["lectures"][:1]
    payload["lectures"][0]["captions_srt"] += "\n6\n00:00:xx --> 00:01:50,000\nSECRET-BROKEN-TEXT\n"
    with caplog.at_level(logging.WARNING, logger="aic"):
        _sync(payload)
    assert f"lecture {E1}: 1 malformed caption blocks dropped" in caplog.text
    assert "SECRET-BROKEN-TEXT" not in caplog.text
    assert _chunks(E1) == [(0.0, 30.0), (60.0, 95.0)]


@pytest.mark.integration
def test_ingestion_is_queued_not_run_inline(no_ingestion):
    """sync() only queues the work: the response is ready while nothing is chunked yet."""
    background = BackgroundTasks()
    response = sync(SyncPayload.model_validate(SAMPLE), background)
    assert response.status == "accepted"
    assert len(background.tasks) == 1
    task = background.tasks[0]
    assert task.func is ingest.ingest_course and task.args == (CID,)
    assert _query("SELECT count(*) FROM chunks") == [(0,)]
    assert _indexed() is False


@pytest.mark.integration
def test_queued_ingestion_runs_when_the_background_tasks_run(monkeypatch):
    background = BackgroundTasks()
    sync(SyncPayload.model_validate(SAMPLE), background)
    assert _indexed() is False
    asyncio.run(background())
    assert _indexed() is True


@pytest.mark.integration
def test_second_replace_for_the_same_captions_changes_nothing():
    _sync(SAMPLE)
    ids = _chunk_ids()
    captions = db.lecture_captions(E1)
    chunks = chunk_cues(parse_srt(captions))
    vectors = [[1.0] + [0.0] * 383] * len(chunks)
    assert db.replace_chunks(E1, db.captions_hash(captions), chunks, vectors) is False
    assert _chunk_ids() == ids


@pytest.mark.integration
def test_refresh_waits_for_a_sync_that_is_still_writing_and_sees_its_lecture():
    """Staged interleaving: a sync holds the course row and adds a due lecture; refresh_indexed
    must wait for it and then leave `indexed` false, not overwrite it from an old view."""
    _sync(SAMPLE)
    assert _indexed() is True
    writer = db.engine.connect()
    try:
        writer.execute(text("UPDATE courses SET indexed = FALSE WHERE course_id = :c"), {"c": CID})
        writer.execute(
            text(
                "INSERT INTO lectures (entry_id, course_id, title, duration_sec, media_url,"
                " captions_srt) VALUES ('1_fake0003', :c, 't', 1, 'u', 'x')"
            ),
            {"c": CID},
        )
        refresher = threading.Thread(target=db.refresh_indexed, args=(CID,))
        refresher.start()
        time.sleep(0.5)  # refresh_indexed is now waiting on the course row
        assert refresher.is_alive()
        writer.commit()
    finally:
        writer.close()
    refresher.join(10)
    assert not refresher.is_alive()
    assert _indexed() is False


@pytest.mark.integration
def test_wrong_vector_dimension_is_a_failure_not_a_client_error(monkeypatch, caplog):
    monkeypatch.setattr(embeddings, "embed", lambda texts: [[1.0, 2.0]] * len(texts))
    with caplog.at_level(logging.ERROR, logger="aic"):
        r = client.post("/sync", json=SAMPLE)
    assert r.status_code == 200
    assert "EmbeddingDimensionError" in caplog.text
    assert _indexed() is False


@pytest.mark.integration
def test_captions_without_cues_count_as_chunked_with_zero_chunks(caplog):
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"] = payload["lectures"][:1]
    payload["lectures"][0]["captions_srt"] = "UNPARSEABLE-MARKER, no timings"
    with caplog.at_level(logging.WARNING, logger="aic"):
        _sync(payload)
    assert _chunks(E1) == []
    assert _query("SELECT chunked_hash IS NOT NULL FROM lectures") == [(True,)]
    assert _indexed() is True
    assert f"lecture {E1}: captions have no cues" in caplog.text
    assert "UNPARSEABLE-MARKER" not in caplog.text


@pytest.mark.integration
def test_lecture_skipped_for_another_course_is_not_ingested_by_it():
    _sync(SAMPLE)
    ids = _chunk_ids()
    other = copy.deepcopy(SAMPLE)
    other["course"]["canvas_course_id"] = 99999
    other["lectures"] = other["lectures"][:1]
    _sync(other)
    assert _chunk_ids() == ids
    assert _indexed("canvas-99999") is False  # it owns no lectures
    assert _indexed() is True


@pytest.mark.integration
def test_captions_changed_while_embedding_are_not_stored_under_the_new_hash(
    monkeypatch, no_ingestion
):
    _sync(SAMPLE)
    old = embeddings.embed

    def embed_then_edit(texts):
        vectors = old(texts)
        with db.engine.begin() as conn:  # a sync lands while the embedding runs
            conn.execute(
                text("UPDATE lectures SET captions_srt = 'changed' WHERE entry_id = :e"), {"e": E1}
            )
        return vectors

    monkeypatch.setattr(embeddings, "embed", embed_then_edit)
    ingest.ingest_lecture(E1)
    assert _chunks(E1) == []
    assert _query("SELECT chunked_hash FROM lectures WHERE entry_id = :e", {"e": E1}) == [(None,)]
    assert E1 in db.due_lecture_ids(CID)


@pytest.mark.integration
def test_overlapping_ingestions_of_one_lecture_do_not_duplicate_chunks(monkeypatch, no_ingestion):
    _sync(SAMPLE)
    old = embeddings.embed

    def slow(texts):
        time.sleep(0.3)  # both threads are inside embedding at the same time
        return old(texts)

    monkeypatch.setattr(embeddings, "embed", slow)
    barrier = threading.Barrier(2)
    errors = []

    def run():
        barrier.wait()
        try:
            ingest.ingest_lecture(E1)
        except Exception as exc:  # noqa: BLE001
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    assert _chunks(E1) == [(0.0, 30.0), (60.0, 95.0)]


@pytest.mark.integration
def test_lecture_number_and_section_are_stored_from_the_title():
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"][0]["title"] = "Lecture 17 - The Chain Rule - LE [002-000-LE]"
    _sync(payload)
    rows = _query("SELECT entry_id, lecture_no, section FROM lectures ORDER BY entry_id")
    assert rows == [(E1, 17, "002"), (E2, 9, None)]
    payload["lectures"][0]["title"] = "Review"  # a later title without them clears the values
    _sync(payload)
    assert _query("SELECT lecture_no, section FROM lectures WHERE entry_id = :e", {"e": E1}) == [
        (None, None)
    ]


@pytest.mark.integration
def test_huge_lecture_number_is_stored_as_null():
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"][0]["title"] = "Lecture 99999999999 - Overflow"
    _sync(payload)
    assert _query("SELECT lecture_no FROM lectures WHERE entry_id = :e", {"e": E1}) == [(None,)]


# --- no database needed ---


def test_embed_sends_one_normalized_batch(fake_model):
    vectors = embeddings.embed(["a", "b", "c"])
    assert len(vectors) == 3 and all(len(v) == 384 for v in vectors)
    assert len(fake_model.calls) == 1
    assert fake_model.calls[0][1]["normalize_embeddings"] is True


def test_embed_of_nothing_does_not_load_the_model(monkeypatch):
    def fail():
        raise AssertionError("model loaded")

    monkeypatch.setattr(embeddings, "_load", fail)
    assert embeddings.embed([]) == []


def test_importing_the_app_does_not_import_sentence_transformers():
    import subprocess

    code = "import sys, app.main; sys.exit('sentence_transformers' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0  # noqa: S603


def test_ingest_course_never_raises_when_the_database_is_down(monkeypatch, caplog):
    def down(course_id):
        raise ConnectionError("postgresql://user:hunter2@host/db")

    monkeypatch.setattr(db, "due_lecture_ids", down)
    with caplog.at_level(logging.DEBUG):
        run_ingestion(CID)
    assert f"ingestion of course {CID} failed: ConnectionError" in caplog.text
    assert "hunter2" not in caplog.text


def test_model_is_built_once_with_the_configured_name_and_normalized_output(monkeypatch):
    built = []

    class StubModel:
        def __init__(self, name):
            built.append(name)
            self.kwargs = []

        def encode(self, texts, **kwargs):
            self.kwargs.append(kwargs)
            return [[0.0] * 384 for _ in texts]

    stub = type(sys)("sentence_transformers")
    stub.SentenceTransformer = StubModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", stub)
    monkeypatch.setattr(embeddings, "_load", embeddings._load.real)
    monkeypatch.setattr(embeddings, "_model", None)

    embeddings.embed(["one"])
    embeddings.embed(["two", "three"])
    assert built == [embeddings.settings.embedding_model]
    assert embeddings._model.kwargs[0]["normalize_embeddings"] is True
    assert len(embeddings._model.kwargs) == 2


# --- the re-chunk hash covers captions, chunker version and model name ---


@pytest.mark.integration
def test_python_and_postgres_hashes_agree_including_non_ascii():
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"] = payload["lectures"][:1]
    captions = "1\n00:00:00,000 --> 00:00:02,000\nCafé naïve — 日本語 😀 ü\n"
    payload["lectures"][0]["captions_srt"] = captions
    _sync(payload)
    with db.engine.connect() as conn:
        in_sql = conn.execute(select(db._current_hash()).select_from(db.Lecture)).scalar()
    assert in_sql == db.captions_hash(captions)
    # The stored hash written by ingestion is the same value, so nothing is due afterwards.
    assert _query("SELECT chunked_hash FROM lectures") == [(in_sql,)]
    assert db.due_lecture_ids(CID) == []


@pytest.mark.integration
def test_changed_chunker_version_rechunks_on_the_next_sync(monkeypatch):
    _sync(SAMPLE)
    ids = _chunk_ids()
    monkeypatch.setattr(chunking, "CHUNKER_VERSION", "next-version")
    _sync(SAMPLE)  # same payload
    new_ids = _chunk_ids()
    assert new_ids and set(new_ids).isdisjoint(ids)
    assert _indexed() is True


@pytest.mark.integration
def test_changed_model_name_rechunks_on_the_next_sync(monkeypatch):
    _sync(SAMPLE)
    ids = _chunk_ids()
    monkeypatch.setattr(db, "settings", replace(db.settings, embedding_model="other/model"))
    _sync(SAMPLE)
    new_ids = _chunk_ids()
    assert new_ids and set(new_ids).isdisjoint(ids)
    assert _indexed() is True


@pytest.mark.integration
def test_same_version_and_model_do_not_rechunk(fake_model):
    _sync(SAMPLE)
    ids, calls = _chunk_ids(), len(fake_model.calls)
    _sync(SAMPLE)
    assert _chunk_ids() == ids and len(fake_model.calls) == calls


@pytest.mark.integration
def test_rows_with_the_old_captions_only_hash_are_due(no_ingestion):
    _sync(SAMPLE)
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE lectures SET chunked_hash ="
                " encode(sha256(convert_to(captions_srt, 'UTF8')), 'hex')"
            )
        )
    assert sorted(db.due_lecture_ids(CID)) == [E1, E2]
    run_ingestion(CID)
    assert db.due_lecture_ids(CID) == []
    assert _indexed() is True
