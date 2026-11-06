"""GET /courses/{course_id}: course details and its lectures, never captions."""

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / "contracts/fixtures/sync_sample.json").read_text())
CID = "canvas-999999"

client = TestClient(app)


def _sync(payload):
    assert client.post("/sync", json=payload).status_code == 200


@pytest.mark.integration
def test_returns_course_details_and_lectures_in_order():
    _sync(SAMPLE)
    r = client.get(f"/courses/{CID}")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "MATH 20A"
    assert body["term"] == "FA26"
    assert body["term_start"] == "2026-09-24"
    assert body["term_end"] is None
    assert body["instructors"] == ["Synthetic Instructor"]
    assert body["sections"] == ["MATH 20A - A00"]
    assert [lec["entry_id"] for lec in body["lectures"]] == ["1_fake0001", "1_fake0002"]
    first = body["lectures"][0]
    assert first["lecture_no"] == 8
    assert first["recorded_start"].startswith("2026-10-12T17:00:00")
    assert set(first) == {
        "entry_id",
        "title",
        "lecture_no",
        "section",
        "recorded_at",
        "recorded_start",
        "duration_sec",
        "media_url",
        "indexed",
    }


@pytest.mark.integration
def test_response_contains_no_caption_text():
    _sync(SAMPLE)
    body = client.get(f"/courses/{CID}").text
    for lecture in SAMPLE["lectures"]:
        assert lecture["captions_srt"][:40] not in body


@pytest.mark.integration
def test_lectures_are_indexed_after_ingestion():
    _sync(SAMPLE)  # TestClient runs the ingestion background task before returning
    body = client.get(f"/courses/{CID}").json()
    assert body["indexed"] is True
    assert all(lec["indexed"] for lec in body["lectures"])


@pytest.mark.integration
def test_resync_without_details_keeps_stored_ones():
    _sync(SAMPLE)
    bare = copy.deepcopy(SAMPLE)
    for key in ("term_start", "instructors", "sections"):
        del bare["course"][key]
    del bare["lectures"][0]["recorded_start"]
    _sync(bare)
    body = client.get(f"/courses/{CID}").json()
    assert body["instructors"] == ["Synthetic Instructor"]
    assert body["sections"] == ["MATH 20A - A00"]
    assert body["term_start"] == "2026-09-24"
    assert body["lectures"][0]["recorded_start"].startswith("2026-10-12T17:00:00")


@pytest.mark.integration
def test_course_synced_before_details_existed_gives_empty_lists():
    bare = copy.deepcopy(SAMPLE)
    for key in ("term_start", "instructors", "sections"):
        del bare["course"][key]
    _sync(bare)
    body = client.get(f"/courses/{CID}").json()
    assert body["instructors"] == [] and body["sections"] == []


@pytest.mark.integration
def test_unknown_course_is_404():
    r = client.get("/courses/canvas-1")
    assert r.status_code == 404
    assert r.json() == {"detail": "course not synced"}


@pytest.mark.integration
def test_search_can_be_limited_to_one_lecture():
    _sync(SAMPLE)
    vector = [0.0] * db.EMBEDDING_DIM
    vector[0] = 1.0
    everything = {h.entry_id for h in db.search_chunks(CID, vector, 40)}
    assert everything == {"1_fake0001", "1_fake0002"}
    only = {h.entry_id for h in db.search_chunks(CID, vector, 40, entry_id="1_fake0002")}
    assert only == {"1_fake0002"}


def test_sync_rejects_too_many_instructors():
    payload = copy.deepcopy(SAMPLE)
    payload["course"]["instructors"] = [f"Person {i}" for i in range(21)]
    assert client.post("/sync", json=payload).status_code == 422


def test_ask_rejects_a_malformed_entry_id():
    r = client.post("/ask", json={"course_id": CID, "question": "q", "entry_id": "not-an-id"})
    assert r.status_code == 422
