"""Tests for the Google OAuth 2.0 / OIDC login flow, @ucsd.edu domain enforcement,
and session lifecycle (web cookie and Chrome extension bearer token)."""

import base64
import dataclasses
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import settings
from app.main import app

client = TestClient(app)


TEST_SESSION_SECRET = "test-session-secret-0123456789-abcdefghij"  # noqa: S105


def _mock_settings(monkeypatch, **kwargs):
    kwargs.setdefault("session_secret", TEST_SESSION_SECRET)
    new_settings = dataclasses.replace(settings, **kwargs)
    monkeypatch.setattr("app.auth.settings", new_settings)
    monkeypatch.setattr("app.config.settings", new_settings)


@pytest.fixture(autouse=True)
def _session_secret(monkeypatch):
    # State cookies need a SESSION_SECRET; the test environment has none.
    _mock_settings(monkeypatch)


def _make_jwt(payload: dict) -> str:
    """Helper creating a dummy unencrypted JWT string (`header.payload.sig`). Fills in the
    audience, issuer and time claims for the tests' client id unless the payload sets them."""
    now = int(time.time())
    defaults = {
        "aud": "client-id",
        "iss": "https://accounts.google.com",
        "iat": now,
        "exp": now + 3600,
    }
    payload = {**defaults, **payload}
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').decode("ascii").rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    sig = base64.urlsafe_b64encode(b"dummy-signature").decode("ascii").rstrip("=")
    return f"{header}.{body}.{sig}"


# --- Login endpoint tests ---


def test_google_login_answers_503_when_not_configured(monkeypatch):
    _mock_settings(monkeypatch, google_client_id="", google_client_secret="")
    r = client.get("/auth/google/login", follow_redirects=False)
    assert r.status_code == 503
    assert r.json() == {"detail": "auth service not configured"}


def test_google_login_rejects_invalid_client_query():
    r = client.get("/auth/google/login?client=unknown", follow_redirects=False)
    assert r.status_code == 422


def test_google_login_redirects_to_google_with_pkce_and_hd(monkeypatch):
    _mock_settings(
        monkeypatch,
        google_client_id="test-client-id.apps.googleusercontent.com",
        google_client_secret="test-secret",  # noqa: S106
        allowed_email_domain="ucsd.edu",
    )

    r = client.get("/auth/google/login?client=extension", follow_redirects=False)
    assert r.status_code == 302
    target = r.headers["location"]
    assert "https://accounts.google.com/o/oauth2/v2/auth?" in target
    assert "client_id=test-client-id" in target
    assert "hd=ucsd.edu" in target
    assert "code_challenge=" in target
    assert "code_challenge_method=S256" in target
    assert auth.OAUTH_STATE_COOKIE in r.cookies


# --- Callback endpoint tests ---


def test_callback_handles_google_error():
    state_cookie = auth.sign_oauth_state(
        {"state": "test_state", "verifier": "v", "client": "web", "ts": time.time()}
    )
    client.cookies.set(auth.OAUTH_STATE_COOKIE, state_cookie)
    r = client.get(
        "/auth/google/callback?error=access_denied&state=test_state", follow_redirects=False
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/app/?auth_error=login_failed"


def test_callback_rejects_missing_or_tampered_state():
    # No state cookie provided
    r = client.get("/auth/google/callback?code=abc&state=xyz", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/app/?auth_error=login_failed"

    # Tampered state cookie
    client.cookies.set(auth.OAUTH_STATE_COOKIE, "tampered.state.cookie")
    r2 = client.get("/auth/google/callback?code=abc&state=xyz", follow_redirects=False)
    assert r2.status_code == 302
    assert r2.headers["location"] == "/app/?auth_error=login_failed"


@pytest.mark.integration
def test_callback_rejects_non_ucsd_accounts(monkeypatch):
    _mock_settings(
        monkeypatch,
        google_client_id="client-id",
        google_client_secret="client-secret",  # noqa: S106
        allowed_email_domain="ucsd.edu",
    )

    # Mock Google token response returning personal @gmail.com
    gmail_jwt = _make_jwt(
        {
            "sub": "google-user-123",
            "email": "student@gmail.com",
            "email_verified": True,
            "hd": "gmail.com",
            "name": "Jane Doe",
        }
    )

    class MockResponse:
        status_code = 200

        def json(self):
            return {"id_token": gmail_jwt}

    async def mock_post(self, url, **kwargs):
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    state_cookie = auth.sign_oauth_state(
        {"state": "good_state", "verifier": "v123", "client": "web", "ts": time.time()}
    )
    client.cookies.set(auth.OAUTH_STATE_COOKIE, state_cookie)

    r = client.get("/auth/google/callback?code=good_code&state=good_state", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/app/?auth_error=ucsd_only"


@pytest.mark.integration
def test_callback_rejects_unverified_email(monkeypatch):
    _mock_settings(
        monkeypatch,
        google_client_id="client-id",
        google_client_secret="client-secret",  # noqa: S106
        allowed_email_domain="ucsd.edu",
    )

    unverified_jwt = _make_jwt(
        {
            "sub": "google-user-456",
            "email": "student@ucsd.edu",
            "email_verified": False,
            "hd": "ucsd.edu",
            "name": "John Doe",
        }
    )

    class MockResponse:
        status_code = 200

        def json(self):
            return {"id_token": unverified_jwt}

    async def mock_post(self, url, **kwargs):
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    state_cookie = auth.sign_oauth_state(
        {"state": "good_state", "verifier": "v123", "client": "web", "ts": time.time()}
    )
    client.cookies.set(auth.OAUTH_STATE_COOKIE, state_cookie)

    r = client.get("/auth/google/callback?code=good_code&state=good_state", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/app/?auth_error=ucsd_only"


@pytest.mark.integration
def test_callback_succeeds_for_web_client(monkeypatch):
    _mock_settings(
        monkeypatch,
        google_client_id="client-id",
        google_client_secret="client-secret",  # noqa: S106
        allowed_email_domain="ucsd.edu",
    )

    valid_jwt = _make_jwt(
        {
            "sub": "google-sub-web-1",
            "email": "triton@ucsd.edu",
            "email_verified": True,
            "hd": "ucsd.edu",
            "name": "King Triton",
        }
    )

    class MockResponse:
        status_code = 200

        def json(self):
            return {"id_token": valid_jwt}

    async def mock_post(self, url, **kwargs):
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    state_cookie = auth.sign_oauth_state(
        {"state": "web_state", "verifier": "v456", "client": "web", "ts": time.time()}
    )
    client.cookies.set(auth.OAUTH_STATE_COOKIE, state_cookie)

    r = client.get("/auth/google/callback?code=code1&state=web_state", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/app/"
    assert auth.SESSION_COOKIE_NAME in r.cookies

    # The session cookie grants authenticated access to /auth/me
    session_cookie = r.cookies[auth.SESSION_COOKIE_NAME]
    client.cookies.set(auth.SESSION_COOKIE_NAME, session_cookie)
    me_resp = client.get("/auth/me")
    assert me_resp.status_code == 200
    assert me_resp.json() == {
        "id": me_resp.json()["id"],
        "email": "triton@ucsd.edu",
        "name": "King Triton",
    }


@pytest.mark.integration
def test_callback_and_extension_exchange_flow(monkeypatch):
    _mock_settings(
        monkeypatch,
        google_client_id="client-id",
        google_client_secret="client-secret",  # noqa: S106
        allowed_email_domain="ucsd.edu",
    )

    valid_jwt = _make_jwt(
        {
            "sub": "google-sub-ext-1",
            "email": "ada@ucsd.edu",
            "email_verified": True,
            "hd": "ucsd.edu",
            "name": "Ada Lovelace",
        }
    )

    class MockResponse:
        status_code = 200

        def json(self):
            return {"id_token": valid_jwt}

    async def mock_post(self, url, **kwargs):
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    state_cookie = auth.sign_oauth_state(
        {"state": "ext_state", "verifier": "vext", "client": "extension", "ts": time.time()}
    )
    client.cookies.set(auth.OAUTH_STATE_COOKIE, state_cookie)

    # 1. Callback redirects to extension/done landing page with one-time code
    r = client.get("/auth/google/callback?code=code2&state=ext_state", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/auth/extension/done?code=")
    one_time_code = loc.split("code=")[1]

    # Landing page renders successfully
    landing = client.get(loc)
    assert landing.status_code == 200
    assert "Signed in" in landing.text

    # 2. Extension exchanges single-use code for persistent bearer token
    exchange_resp = client.post("/auth/extension/exchange", json={"code": one_time_code})
    assert exchange_resp.status_code == 200
    data = exchange_resp.json()
    assert "token" in data
    assert data["user"]["email"] == "ada@ucsd.edu"
    bearer_token = data["token"]

    # Replay with same code fails
    replay_resp = client.post("/auth/extension/exchange", json={"code": one_time_code})
    assert replay_resp.status_code == 401
    assert replay_resp.json() == {"detail": "invalid or expired code"}

    # 3. Extension can use bearer token to query /auth/me
    me_resp = client.get("/auth/me", headers={"Authorization": f"Bearer {bearer_token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "ada@ucsd.edu"

    # 4. Logout revokes the token
    logout_resp = client.post("/auth/logout", headers={"Authorization": f"Bearer {bearer_token}"})
    assert logout_resp.status_code == 204

    # Revoked token is no longer authenticated
    me_after = client.get("/auth/me", headers={"Authorization": f"Bearer {bearer_token}"})
    assert me_after.status_code == 401
