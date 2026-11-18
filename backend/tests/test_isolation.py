"""Integration and isolation tests verifying that endpoints are strictly protected by
authentication and that student course data, documents, and queries are strictly isolated
between students.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth, auth_db
from app.main import app

client = TestClient(app)

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PAYLOAD = json.loads((ROOT / "contracts/fixtures/sync_sample.json").read_text())


# --- Authentication enforcement tests (HTTP 401 on unauthenticated access) ---


@pytest.mark.no_auth_override
def test_endpoints_require_authentication_unauthenticated_returns_401():
    """All core data endpoints must reject unauthenticated requests with HTTP 401."""
    routes = [
        ("GET", "/courses"),
        ("GET", "/courses/canvas-999999"),
        ("GET", "/courses/canvas-999999/lectures"),
        ("POST", "/sync", {"json": SAMPLE_PAYLOAD}),
        ("POST", "/ask", {"json": {"course_id": "canvas-999999", "question": "What is arch?"}}),
        (
            "POST",
            "/ask/stream",
            {"json": {"course_id": "canvas-999999", "question": "What is arch?"}},
        ),
        ("GET", "/courses/canvas-999999/documents"),
        (
            "PUT",
            "/courses/canvas-999999/documents/canvas/101?name=lec1.pdf&version=v1",
            {"content": b"%PDF-1.4\n"},
        ),
        ("DELETE", "/courses/canvas-999999/documents/canvas/101"),
        ("GET", "/courses/canvas-999999/site"),
        ("POST", "/courses/canvas-999999/site/search"),
        ("PUT", "/courses/canvas-999999/site", {"json": {"url": "https://example.com"}}),
        ("POST", "/courses/canvas-999999/site/sync"),
        ("DELETE", "/courses/canvas-999999/site"),
        ("POST", "/study", {"json": {"entry_id": "1_fake0001", "kind": "teach"}}),
    ]

    for item in routes:
        method = item[0]
        url = item[1]
        kwargs = item[2] if len(item) > 2 else {}
        resp = client.request(method, url, **kwargs)
        assert resp.status_code == 401, f"{method} {url} returned {resp.status_code}, expected 401"
        assert resp.json() == {"detail": "not authenticated"}


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_authentication_accepts_valid_cookie_and_bearer_token():
    """Endpoints accept both web session cookie and extension Authorization: Bearer token."""
    # Create test users and sessions in database
    user_cookie = auth_db.upsert_user(
        google_sub="sub-cookie-test", email="cookie@ucsd.edu", name="Cookie User"
    )
    cookie_token, _ = auth_db.create_session(user_cookie.id, kind="web", ttl_days=1)

    user_bearer = auth_db.upsert_user(
        google_sub="sub-bearer-test", email="bearer@ucsd.edu", name="Bearer User"
    )
    bearer_token, _ = auth_db.create_session(user_bearer.id, kind="extension", ttl_days=1)

    # 1. Access using session cookie
    client.cookies.set(auth.SESSION_COOKIE_NAME, cookie_token)
    r1 = client.get("/courses")
    assert r1.status_code == 200
    client.cookies.clear()

    # 2. Access using Authorization: Bearer header
    r2 = client.get("/courses", headers={"Authorization": f"Bearer {bearer_token}"})
    assert r2.status_code == 200

    # 3. Access using invalid/expired token returns 401
    r3 = client.get("/courses", headers={"Authorization": "Bearer invalid-or-expired-token"})
    assert r3.status_code == 401


# --- Data Isolation tests between students ---


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_student_data_isolation_between_two_students():
    """Verify that User A cannot see, query, or mutate User B's synced courses or documents."""
    # Create User A (Alice) and User B (Bob)
    user_alice = auth_db.upsert_user(
        google_sub="sub-alice", email="alice@ucsd.edu", name="Alice Triton"
    )
    alice_token, _ = auth_db.create_session(user_alice.id, kind="web", ttl_days=1)
    alice_headers = {"Authorization": f"Bearer {alice_token}"}

    user_bob = auth_db.upsert_user(google_sub="sub-bob", email="bob@ucsd.edu", name="Bob Triton")
    bob_token, _ = auth_db.create_session(user_bob.id, kind="web", ttl_days=1)
    bob_headers = {"Authorization": f"Bearer {bob_token}"}

    # Step 1: Alice syncs MATH 20A
    sync_resp = client.post("/sync", json=SAMPLE_PAYLOAD, headers=alice_headers)
    assert sync_resp.status_code == 200
    course_id = sync_resp.json()["course_id"]
    assert course_id == "canvas-999999"

    # Step 2: Alice can see her course in /courses and get its details
    alice_courses = client.get("/courses", headers=alice_headers).json()
    assert len(alice_courses) == 1
    assert alice_courses[0]["course_id"] == "canvas-999999"

    alice_detail = client.get(f"/courses/{course_id}", headers=alice_headers)
    assert alice_detail.status_code == 200
    assert alice_detail.json()["code"] == "MATH 20A"

    alice_lectures = client.get(f"/courses/{course_id}/lectures", headers=alice_headers)
    assert alice_lectures.status_code == 200
    assert len(alice_lectures.json()) == 2

    # Step 3: Bob calls /courses -> returns EMPTY list (Bob cannot see Alice's course)
    bob_courses = client.get("/courses", headers=bob_headers).json()
    assert bob_courses == []

    # Step 4: Bob attempts to access Alice's course endpoints -> returns 404 course not synced
    bob_detail = client.get(f"/courses/{course_id}", headers=bob_headers)
    assert bob_detail.status_code == 404
    assert bob_detail.json() == {"detail": "course not synced"}

    bob_lectures = client.get(f"/courses/{course_id}/lectures", headers=bob_headers)
    assert bob_lectures.status_code == 404
    assert bob_lectures.json() == {"detail": "course not synced"}

    bob_ask = client.post(
        "/ask",
        json={"course_id": course_id, "question": "What is software architecture?"},
        headers=bob_headers,
    )
    assert bob_ask.status_code == 404
    assert bob_ask.json() == {"detail": "course not synced"}

    bob_stream = client.post(
        "/ask/stream",
        json={"course_id": course_id, "question": "What is software architecture?"},
        headers=bob_headers,
    )
    assert bob_stream.status_code == 404
    assert bob_stream.json() == {"detail": "course not synced"}

    bob_docs = client.get(f"/courses/{course_id}/documents", headers=bob_headers)
    assert bob_docs.status_code == 404
    assert bob_docs.json() == {"detail": "course not synced"}

    bob_del_doc = client.delete(f"/courses/{course_id}/documents/canvas/999", headers=bob_headers)
    assert bob_del_doc.status_code == 404

    bob_site = client.get(f"/courses/{course_id}/site", headers=bob_headers)
    assert bob_site.status_code == 404
    assert bob_site.json() == {"detail": "course not synced"}

    bob_del_site = client.delete(f"/courses/{course_id}/site", headers=bob_headers)
    assert bob_del_site.status_code == 404
    assert bob_del_site.json() == {"detail": "course not synced"}

    # Bob attempts to study Alice's synced lecture -> returns 404 lecture not synced
    bob_study = client.post(
        "/study", json={"entry_id": "1_fake0001", "kind": "teach"}, headers=bob_headers
    )
    assert bob_study.status_code == 404
    assert bob_study.json() == {"detail": "lecture not synced"}

    # Step 5: Bob syncs his own course (even same Canvas course ID)
    bob_sync = client.post("/sync", json=SAMPLE_PAYLOAD, headers=bob_headers)
    assert bob_sync.status_code == 200

    # Now both have their own isolated record
    assert len(client.get("/courses", headers=alice_headers).json()) == 1
    assert len(client.get("/courses", headers=bob_headers).json()) == 1
