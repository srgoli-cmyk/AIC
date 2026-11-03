"""Contract tests: the backend must accept exactly what contracts/ describes, and nothing more."""

import copy
import json
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "contracts/sync_payload.schema.json").read_text())
SAMPLE = json.loads((ROOT / "contracts/fixtures/sync_sample.json").read_text())
ASK_SAMPLE = json.loads((ROOT / "contracts/fixtures/ask_response_sample.json").read_text())

client = TestClient(app)


def test_fixture_matches_json_schema():
    jsonschema.validate(SAMPLE, SCHEMA, format_checker=jsonschema.FormatChecker())


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.integration
def test_sync_accepts_fixture():
    r = client.post("/sync", json=SAMPLE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "course_id": "canvas-999999",
        "lectures_received": 2,
        "files_received": 1,
        "lectures_skipped_other_course": 0,
        "status": "accepted",
    }
    assert any(c["course_id"] == "canvas-999999" for c in client.get("/courses").json())


@pytest.mark.integration
def test_sync_accepts_empty_files_twice():
    """The console snippet always sends `files: []`; it must be valid, also on a re-sync."""
    payload = copy.deepcopy(SAMPLE)
    payload["files"] = []
    jsonschema.validate(payload, SCHEMA, format_checker=jsonschema.FormatChecker())
    for _ in range(2):
        r = client.post("/sync", json=payload)
        assert r.status_code == 200, r.text
        assert r.json()["files_received"] == 0


def test_api_md_documents_empty_files_rule():
    """Guard: the `files: []` re-sync rule must not be dropped from the contract unnoticed."""
    text = " ".join((ROOT / "contracts/api.md").read_text().split())
    assert "`files: []` on a re-sync leaves stored files alone and never deletes them" in text
    assert "Only a non-empty `files` list upserts, by `canvas_file_id`" in text


@pytest.mark.parametrize(
    "path",
    [
        ("ks",),  # top-level token
        ("course", "session_cookie"),
        ("lectures", 0, "kaltura_ks"),
        ("files", 0, "download_url_with_token"),
    ],
)
def test_sync_rejects_unknown_fields(path):
    """Security: no field outside the contract (e.g. a session token) may ever be accepted."""
    payload = copy.deepcopy(SAMPLE)
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = "SECRET"
    assert client.post("/sync", json=payload).status_code == 422
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, SCHEMA)


def test_sync_rejects_foreign_media_url():
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"][0]["media_url"] = "https://evil.example.com/media/x"
    assert client.post("/sync", json=payload).status_code == 422


def test_ask_response_fixture_matches_model():
    from app.schemas import AskResponse

    AskResponse.model_validate(ASK_SAMPLE)


@pytest.mark.integration
def test_ask_without_an_api_key_is_a_503():
    """Tests run with no API key; a synced course answers 503 until one is configured."""
    client.post("/sync", json=SAMPLE)
    r = client.post("/ask", json={"course_id": "canvas-999999", "question": "product rule?"})
    assert r.status_code == 503
    assert r.json() == {"detail": "answer service not configured"}
