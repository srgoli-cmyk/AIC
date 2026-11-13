"""The course website endpoints: searching for the site, the student's pick, progress, and
forgetting a site. Search, fetching and reading the site are replaced; no test reaches the
network."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import coursesite as cs
from app import db
from app import main as app_main
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / "contracts/fixtures/sync_sample.json").read_text())
CID = "canvas-999999"
client = TestClient(app)

SITE = "https://dsc10.com/"
OLD = "https://old.ucsd.edu/dsc10/"


def verdict(url, decision, title="DSC 10"):
    return cs.Verdict(url=url, decision=decision, score=8, title=title, reasons=["names DSC 10"])


@pytest.fixture
def course(monkeypatch):
    assert client.post("/sync", json=SAMPLE).status_code == 200
    key = replace(app_main.settings, openrouter_api_key="sk-or-FAKE-KEY-0123456789")
    monkeypatch.setattr(app_main, "settings", key)
    synced = []

    def fake_sync(course_id, url, progress=None):
        out = cs.SiteSync(found=3, stored=2, unreadable=1)
        if progress:
            progress(out)
        db.save_document(
            course_id,
            cs.doc_id_for(url + "lec1.pdf"),
            source="web",
            name="Lecture 1",
            url=url + "lec1.pdf",
            version="v1",
            content_hash="h",
            pages=1,
            chunks=[(1, "Arrays hold values of one type.")],
            lecture_no=1,
        )
        synced.append(url)
        return out

    monkeypatch.setattr(cs, "sync_site", fake_sync)
    return synced


@pytest.mark.integration
def test_a_matching_site_is_used_and_synced_without_asking(course, monkeypatch):
    monkeypatch.setattr(
        cs, "find_course_site", lambda facts: [verdict(SITE, cs.ACCEPT), verdict(OLD, cs.ASK)]
    )
    r = client.post(f"/courses/{CID}/site/search")
    assert r.status_code == 200
    body = r.json()
    assert [c["decision"] for c in body["candidates"]] == ["accept", "ask"]
    assert body["site"]["url"] == SITE and body["site"]["chosen_by"] == "search"
    assert course == [SITE]
    site = client.get(f"/courses/{CID}/site").json()
    assert (site["status"], site["found"], site["stored"], site["unreadable"]) == ("done", 3, 2, 1)
    assert [d["source"] for d in client.get(f"/courses/{CID}/documents").json()] == ["web"]


@pytest.mark.integration
def test_a_site_that_only_might_be_it_waits_for_the_student(course, monkeypatch):
    monkeypatch.setattr(cs, "find_course_site", lambda facts: [verdict(OLD, cs.ASK)])
    body = client.post(f"/courses/{CID}/site/search").json()
    assert body["site"] is None and body["candidates"][0]["url"] == OLD
    assert course == []
    assert client.get(f"/courses/{CID}/site").json() is None


@pytest.mark.integration
def test_the_students_pick_is_checked_and_never_overridden_by_a_search(course, monkeypatch):
    monkeypatch.setattr(cs, "check_site", lambda facts, url: verdict(url, cs.ASK))
    r = client.put(f"/courses/{CID}/site", json={"url": OLD})
    assert r.status_code == 200 and r.json()["chosen_by"] == "student"
    monkeypatch.setattr(cs, "find_course_site", lambda facts: [verdict(SITE, cs.ACCEPT)])
    assert client.post(f"/courses/{CID}/site/search").json()["site"]["url"] == OLD
    assert course == [OLD]


@pytest.mark.integration
def test_a_page_that_is_not_the_course_is_refused(course, monkeypatch):
    monkeypatch.setattr(cs, "check_site", lambda facts, url: verdict(url, cs.REJECT))
    r = client.put(f"/courses/{CID}/site", json={"url": "https://blog.example/"})
    assert r.status_code == 422 and "not this course" in r.json()["detail"]
    assert client.put(f"/courses/{CID}/site", json={"url": "http://x.example/"}).status_code == 422
    assert client.get(f"/courses/{CID}/site").json() is None


@pytest.mark.integration
def test_forgetting_the_site_removes_what_came_from_it_but_not_canvas_files(course, monkeypatch):
    monkeypatch.setattr(cs, "check_site", lambda facts, url: verdict(url, cs.ACCEPT))
    client.put(f"/courses/{CID}/site", json={"url": SITE})
    db.save_document(
        CID,
        "canvas-file-7",
        source="canvas",
        name="Week 3",
        url="https://canvas.ucsd.edu/courses/999999/files/7",
        version="v",
        content_hash="h",
        pages=1,
        chunks=[(1, "Canvas page text here.")],
    )
    assert client.delete(f"/courses/{CID}/site").status_code == 204
    assert [d["doc_id"] for d in client.get(f"/courses/{CID}/documents").json()] == [
        "canvas-file-7"
    ]
    assert client.get(f"/courses/{CID}/site").json() is None


@pytest.mark.integration
def test_choosing_another_site_drops_the_old_sites_documents(course, monkeypatch):
    monkeypatch.setattr(cs, "check_site", lambda facts, url: verdict(url, cs.ACCEPT))
    client.put(f"/courses/{CID}/site", json={"url": SITE})
    client.put(f"/courses/{CID}/site", json={"url": OLD})
    docs = client.get(f"/courses/{CID}/documents").json()
    assert [d["url"] for d in docs] == [OLD + "lec1.pdf"]


@pytest.mark.integration
def test_unknown_course_and_missing_key(course, monkeypatch):
    assert client.get("/courses/canvas-1/site").status_code == 404
    assert client.post("/courses/canvas-1/site/search").status_code == 404
    monkeypatch.setattr(app_main, "settings", replace(app_main.settings, openrouter_api_key=""))
    assert client.post(f"/courses/{CID}/site/search").status_code == 503


def test_course_facts_come_from_the_synced_course(monkeypatch):
    from datetime import date

    monkeypatch.setattr(
        db,
        "course_facts",
        lambda cid: ("DSC010_FA26_001", "DSC 10", "Fall 2026", ["Peter Chi"], [date(2026, 9, 25)]),
    )
    f = cs.facts_for(CID)
    assert f.subject_number == ("DSC", "10") and f.surnames == ("Chi",)
    assert f.lecture_dates == (date(2026, 9, 25),)


@pytest.mark.integration
def test_other_web_pages_cannot_start_a_search_or_delete(course, monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "find_course_site", lambda facts: calls.append(1) or [])
    evil = {"Origin": "https://evil.example"}
    assert client.post(f"/courses/{CID}/site/search", headers=evil).status_code == 403
    assert client.delete(f"/courses/{CID}/site", headers=evil).status_code == 403
    assert client.delete(f"/courses/{CID}/documents/canvas/7", headers=evil).status_code == 403
    assert calls == []
    ext = {"Origin": "chrome-extension://" + "a" * 32}
    assert client.post(f"/courses/{CID}/site/search", headers=ext).status_code == 200
    local = {"Origin": "http://localhost:8000"}
    assert client.post(f"/courses/{CID}/site/search", headers=local).status_code == 200
