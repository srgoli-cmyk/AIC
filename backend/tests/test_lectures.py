"""GET /courses/{course_id}/lectures: which stored lectures have captions."""

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app import db
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / "contracts/fixtures/sync_sample.json").read_text())
CID = "canvas-999999"

client = TestClient(app)


def _sync(payload):
    assert client.post("/sync", json=payload).status_code == 200


@pytest.mark.integration
def test_lists_synced_lectures_with_exact_shape_ordered_by_entry_id():
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"].reverse()  # stored in a different order than the id order
    _sync(payload)
    r = client.get(f"/courses/{CID}/lectures")
    assert r.status_code == 200
    assert r.json() == [
        {"entry_id": "1_fake0001", "has_captions": True},
        {"entry_id": "1_fake0002", "has_captions": True},
    ]


@pytest.mark.integration
def test_response_contains_no_caption_text():
    _sync(SAMPLE)
    body = client.get(f"/courses/{CID}/lectures").text
    for lecture in SAMPLE["lectures"]:
        assert lecture["captions_srt"][:40] not in body
    assert "derivatives" not in body and "00:00" not in body


@pytest.mark.integration
def test_unknown_course_is_404():
    r = client.get("/courses/canvas-1/lectures")
    assert r.status_code == 404
    assert r.json() == {"detail": "course not synced"}


@pytest.mark.integration
def test_course_without_lectures_gives_an_empty_list():
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"] = []
    _sync(payload)
    r = client.get(f"/courses/{CID}/lectures")
    assert r.status_code == 200 and r.json() == []


@pytest.mark.integration
def test_lecture_stored_under_another_course_is_not_listed():
    _sync(SAMPLE)
    other = copy.deepcopy(SAMPLE)
    other["course"]["canvas_course_id"] = 99999
    other["lectures"] = [copy.deepcopy(other["lectures"][0])]
    other["lectures"][0]["entry_id"] = "1_other001"
    _sync(other)
    # 1_fake0001 is also in `other`'s payload, but it stays with the first course.
    assert [e["entry_id"] for e in client.get(f"/courses/{CID}/lectures").json()] == [
        "1_fake0001",
        "1_fake0002",
    ]
    assert client.get("/courses/canvas-99999/lectures").json() == [
        {"entry_id": "1_other001", "has_captions": True}
    ]


@pytest.mark.integration
def test_has_captions_is_false_for_a_row_with_empty_captions():
    _sync(SAMPLE)
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lectures (entry_id, course_id, title, duration_sec, media_url,"
                " captions_srt) VALUES ('1_empty001', :c, 't', 1, 'u', '')"
            ),
            {"c": CID},
        )
    got = {e["entry_id"]: e["has_captions"] for e in client.get(f"/courses/{CID}/lectures").json()}
    assert got == {"1_empty001": False, "1_fake0001": True, "1_fake0002": True}


@pytest.mark.integration
def test_query_does_not_load_caption_text():
    _sync(SAMPLE)
    seen = []
    from sqlalchemy import event

    def record(conn, cursor, statement, *args):
        seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        db.list_lecture_statuses(CID)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    selects = [q.split(" FROM ")[0] for q in seen if "FROM lectures" in q and "ORDER BY" in q]
    assert selects
    for select_list in selects:
        # captions_srt may only appear inside length(...), never as a selected column.
        assert "captions_srt" not in select_list.replace("length(lectures.captions_srt)", "")


def test_database_down_is_503(monkeypatch):
    monkeypatch.setattr(db, "_schema_ready", False)
    monkeypatch.setattr(db, "engine", create_engine("postgresql+psycopg://u:hunter2@127.0.0.1:1/x"))
    r = client.get(f"/courses/{CID}/lectures")
    assert r.status_code == 503
    assert r.json() == {"detail": "database unavailable"}
