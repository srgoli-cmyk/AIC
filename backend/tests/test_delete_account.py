"""DELETE /auth/account removes the student and all their data, and only theirs. The public pages
at / and /privacy need no login."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import auth_db, db, main, slides
from app.main import app

client = TestClient(app)

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / "contracts/fixtures/sync_sample.json").read_text())
CID = "canvas-999999"

# Every table that holds a student's data, with its user column.
USER_TABLES = [
    "courses",
    "lectures",
    "files",
    "chunks",
    "documents",
    "doc_chunks",
    "course_sites",
    "sessions",
    "auth_exchange_codes",
]


def _student(name: str) -> dict:
    user = auth_db.upsert_user(f"sub-{name}", f"{name}@ucsd.edu", name.title())
    raw, _ = auth_db.create_session(user.id, kind="web", ttl_days=1)
    return {"id": user.id, "token": raw, "bearer": {"Authorization": f"Bearer {raw}"}}


def _fill(student: dict) -> None:
    """A synced course with chunks, a stored and embedded slide deck, a course site and an
    exchange code, so every table has a row for this student."""
    r = client.post("/sync", json=SAMPLE, headers=student["bearer"])
    assert r.status_code == 200, r.text
    slides.store(
        CID,
        "canvas-file-1",
        source="canvas",
        name="Deck.pdf",
        url="https://canvas.ucsd.edu/courses/999999/files/1",
        version="1",
        data=b"deck",
        pages=["first page text about limits", "second page text about series"],
        user_id=student["id"],
    )
    slides.embed_document("canvas-file-1", user_id=student["id"])
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO course_sites (user_id, course_id, url, title, chosen_by, status, "
                "updated_at) VALUES (:u, :c, 'https://math.ucsd.edu/x', 'Site', 'search', "
                "'done', now())"
            ),
            {"u": student["id"], "c": CID},
        )
    auth_db.create_extension_code(student["id"], "A" * 43)


def _counts(user_id: str) -> dict[str, int]:
    with db.engine.connect() as conn:
        counts = {
            t: conn.execute(
                text(f"SELECT count(*) FROM {t} WHERE user_id = :u"),  # noqa: S608 (fixed names)
                {"u": user_id},
            ).scalar_one()
            for t in USER_TABLES
        }
        counts["users"] = conn.execute(
            text("SELECT count(*) FROM users WHERE id = :u"), {"u": user_id}
        ).scalar_one()
    return counts


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_delete_removes_everything_of_that_user_and_nothing_else():
    alice, bob = _student("alice"), _student("bob")
    _fill(alice)
    _fill(bob)
    assert all(n > 0 for n in _counts(alice["id"]).values()), _counts(alice["id"])
    bob_before = _counts(bob["id"])

    r = client.delete("/auth/account", headers=alice["bearer"])

    assert r.status_code == 204
    assert r.content == b""
    assert all(n == 0 for n in _counts(alice["id"]).values()), _counts(alice["id"])
    assert _counts(bob["id"]) == bob_before
    # Bob still works.
    assert client.get("/courses", headers=bob["bearer"]).status_code == 200


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_cookie_works_clears_the_cookie_and_session_is_dead_afterwards(caplog):
    alice = _student("alice")
    web = TestClient(app)
    web.cookies.set("aic_session", alice["token"])

    with caplog.at_level("INFO", logger="aic"):
        r = web.delete("/auth/account")

    assert r.status_code == 204
    set_cookie = r.headers.get("set-cookie", "")
    assert "aic_session=" in set_cookie
    assert "Max-Age=0" in set_cookie
    # Fixed log line, no email or name.
    text_logged = " ".join(rec.getMessage() for rec in caplog.records)
    assert "account deleted" in text_logged
    assert "alice" not in text_logged.lower()
    # The old token (cookie and Bearer) no longer authenticates.
    again = TestClient(app)
    assert again.get("/auth/me", headers=alice["bearer"]).status_code == 401
    assert again.get("/courses", headers=alice["bearer"]).status_code == 401
    assert _counts(alice["id"])["users"] == 0


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_anonymous_request_is_401():
    assert client.delete("/auth/account").status_code == 401
    bad = {"Authorization": "Bearer not-a-real-token"}
    assert client.delete("/auth/account", headers=bad).status_code == 401


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_foreign_origin_with_a_cookie_is_403_and_nothing_is_deleted():
    alice = _student("alice")
    web = TestClient(app)
    web.cookies.set("aic_session", alice["token"])

    r = web.delete("/auth/account", headers={"Origin": "https://evil.example"})

    assert r.status_code == 403
    assert _counts(alice["id"])["users"] == 1
    assert web.get("/auth/me").status_code == 200


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_own_origin_with_a_cookie_is_allowed():
    alice = _student("alice")
    web = TestClient(app)
    web.cookies.set("aic_session", alice["token"])

    r = web.delete("/auth/account", headers={"Origin": "http://testserver"})

    assert r.status_code == 204


@pytest.mark.integration
def test_usr_local_cannot_be_deleted():
    # The default test student is usr_local (see conftest); the account must survive.
    r = client.delete("/auth/account")
    assert r.status_code == 403
    assert _counts(db.LOCAL_USER_ID)["users"] == 1
    with pytest.raises(auth_db.AccountNotDeletable):
        auth_db.delete_user(db.LOCAL_USER_ID)


@pytest.mark.integration
def test_delete_user_unknown_id_is_false():
    assert auth_db.delete_user("usr_does_not_exist") is False


# --- public pages ---


@pytest.mark.no_auth_override
def test_landing_and_privacy_pages_are_public_html(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<!doctype html><title>home</title>")
    (tmp_path / "privacy.html").write_text("<!doctype html><title>privacy</title>")
    monkeypatch.setattr(main, "SITE_DIR", tmp_path)

    home = client.get("/")
    privacy = client.get("/privacy")

    assert home.status_code == 200
    assert home.headers["content-type"].startswith("text/html")
    assert "home" in home.text
    assert privacy.status_code == 200
    assert privacy.headers["content-type"].startswith("text/html")
    assert "privacy" in privacy.text


@pytest.mark.no_auth_override
def test_missing_page_files_are_404(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "SITE_DIR", tmp_path)
    assert client.get("/").status_code == 404
    assert client.get("/privacy").status_code == 404


@pytest.mark.no_auth_override
def test_health_openapi_and_app_are_unchanged():
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/openapi.json").status_code == 200
    # The public pages stay out of the API schema.
    paths = client.get("/openapi.json").json()["paths"]
    assert "/" not in paths
    assert "/privacy" not in paths
    assert "/auth/account" in paths
    if main.WEB_DIR.is_dir():
        assert client.get("/app/").status_code == 200


@pytest.mark.no_auth_override
def test_site_assets_are_mounted_under_site():
    assert any(getattr(r, "path", None) == "/site" for r in app.routes)
