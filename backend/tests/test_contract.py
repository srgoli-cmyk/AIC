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


def test_openapi_reports_the_app_title_the_extension_checks():
    assert client.get("/openapi.json").json()["info"]["title"] == "AI Lecture Search"


def test_api_md_documents_the_openapi_title_check():
    """Guard: the extension relies on this title, so the contract must keep documenting it."""
    text = " ".join((ROOT / "contracts/api.md").read_text().split())
    assert "## GET /openapi.json" in text
    assert "`info.title` is `AI Lecture Search`" in text


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


def test_api_md_documents_the_lectures_endpoint():
    """Guard: the endpoint and its response shape must not be dropped from the contract."""
    text = " ".join((ROOT / "contracts/api.md").read_text().split())
    assert "## GET /courses/{course_id}/lectures" in text
    assert '[{"entry_id": "1_fake0001", "has_captions": true}]' in text


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


MEDIA_URL_MESSAGE = (
    "media_url must be https://canvaskaf.ucsd.edu/media/t/<entryId>/<categoryId> "
    "or https://podcast.ucsd.edu/watch/<term>/<section>/<n>, with no query or fragment"
)
PODCAST = "https://podcast.ucsd.edu/watch/fa26/dsc010_002-000-le/3"
GOOD = "https://canvaskaf.ucsd.edu/media/t/1_fake0001/111111"


def test_pydantic_media_url_pattern_matches_the_json_schema():
    from app.schemas import MEDIA_URL_PATTERN

    schema_pattern = SCHEMA["properties"]["lectures"]["items"]["properties"]["media_url"]["pattern"]
    assert MEDIA_URL_PATTERN == schema_pattern


@pytest.mark.parametrize(
    "bad",
    [
        "https://canvaskaf.ucsd.edu/media/t/1_fake0001",  # no category
        f"{GOOD}?st=5",  # query
        f"{GOOD}#t=5",  # fragment
        f"{GOOD}/",  # trailing slash
        f"{GOOD}\n",  # trailing newline
        "https://canvaskaf.ucsd.edu/media/t/1_fake0001/abc",  # non-numeric category
        "https://canvaskaf.ucsd.edu/media/t/1_FAKE0001/111111",  # malformed entry id
        "https://canvaskaf.ucsd.edu/media/t/fake0001/111111",  # malformed entry id
        "https://canvaskaf.ucsd.edu/media/other",
        "https://evil.example.com/media/t/1_fake0001/111111",  # another host
        "http://canvaskaf.ucsd.edu/media/t/1_fake0001/111111",  # not https
        f"{PODCAST}#57",  # podcast with a fragment
        f"{PODCAST}?x=1",  # podcast with a query
        "https://podcast.ucsd.edu/watch/fa26/dsc010_002-000-le",  # a section, not a lecture
        "https://podcast.ucsd.edu/watch/fa26/dsc010_002-000-le/3/kaltura",  # another page
        "https://podcast.ucsd.edu/watch/fa26/../admin/3",  # not a section
        "https://podcast.ucsd.edu.evil.example.com/watch/fa26/dsc010_002-000-le/3",  # another host
    ],
)
def test_sync_rejects_a_bad_media_url_and_stores_nothing(bad, monkeypatch):
    def must_not_save(_payload):
        raise AssertionError("a rejected sync must not reach the database")

    monkeypatch.setattr("app.main.db.save_sync", must_not_save)
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"][0]["media_url"] = bad
    r = client.post("/sync", json=payload)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert [d["loc"] for d in detail] == [["body", "lectures", 0, "media_url"]]
    assert detail[0]["msg"] == f"Value error, {MEDIA_URL_MESSAGE}"
    # The body holds no caption text and does not echo the rejected value.
    assert payload["lectures"][0]["captions_srt"] not in r.text
    assert set(detail[0]) == {"type", "loc", "msg"}


@pytest.mark.parametrize(
    "good", [GOOD, PODCAST, "https://podcast.ucsd.edu/watch/fa26/math018_004-000-le/13"]
)
def test_media_url_accepts_gallery_and_podcast_lectures(good):
    from app.schemas import MEDIA_URL_RE

    assert MEDIA_URL_RE.fullmatch(good)
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"][0]["media_url"] = good
    jsonschema.validate(payload, SCHEMA)


@pytest.mark.integration
def test_sync_accepts_a_valid_media_url():
    payload = copy.deepcopy(SAMPLE)
    payload["lectures"][0]["media_url"] = GOOD
    assert client.post("/sync", json=payload).status_code == 200


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
