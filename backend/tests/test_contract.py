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


def test_sync_accepts_fixture():
    r = client.post("/sync", json=SAMPLE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "course_id": "canvas-78600",
        "lectures_received": 2,
        "files_received": 1,
        "status": "accepted",
    }
    assert any(c["course_id"] == "canvas-78600" for c in client.get("/courses").json())


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


def test_ask_is_stubbed_until_step_4():
    client.post("/sync", json=SAMPLE)
    r = client.post("/ask", json={"course_id": "canvas-78600", "question": "product rule?"})
    assert r.status_code == 501
