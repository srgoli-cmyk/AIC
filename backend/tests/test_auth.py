"""Tests for the Google OAuth 2.0 / OIDC login flow, @ucsd.edu domain enforcement,
and session lifecycle (web cookie and Chrome extension bearer token)."""

import base64
import dataclasses
import hashlib
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, update
from sqlalchemy.orm import Session

from app import auth, auth_db, db
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


def _new_nonce() -> tuple[str, str]:
    """A raw nonce and its challenge, made the way the extension makes them."""
    nonce = secrets.token_urlsafe(32)
    digest = hashlib.sha256(nonce.encode()).digest()
    return nonce, base64.urlsafe_b64encode(digest).decode().rstrip("=")


# A well-formed challenge for tests that never exchange.
CHALLENGE = _new_nonce()[1]


def _mock_google(monkeypatch, sub: str, email: str, name: str = "Test Student"):
    """Make the callback's token request return a valid ID token for this student."""
    _configure_login(monkeypatch)
    id_token = _make_jwt(
        {"sub": sub, "email": email, "email_verified": True, "hd": "ucsd.edu", "name": name}
    )

    class MockResponse:
        status_code = 200

        def json(self):
            return {"id_token": id_token}

    async def mock_post(self, url, **kwargs):
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)


def _extension_login(monkeypatch, challenge: str, sub: str = "google-sub-ext") -> str:
    """Run login and callback for client=extension; returns the one-time code."""
    _mock_google(monkeypatch, sub, f"{sub}@ucsd.edu")
    client.cookies.clear()
    r = client.get(f"/auth/google/login?client=extension&nonce={challenge}", follow_redirects=False)
    assert r.status_code == 302
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = client.get(f"/auth/google/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/auth/extension/done?code=")
    return loc.split("code=")[1]


def _exchange(code: str, nonce):
    body = {"code": code} if nonce is None else {"code": code, "nonce": nonce}
    return client.post("/auth/extension/exchange", json=body)


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

    r = client.get(f"/auth/google/login?client=extension&nonce={CHALLENGE}", follow_redirects=False)
    assert r.status_code == 302
    target = r.headers["location"]
    assert "https://accounts.google.com/o/oauth2/v2/auth?" in target
    assert "client_id=test-client-id" in target
    assert "hd=ucsd.edu" in target
    assert "code_challenge=" in target
    assert "code_challenge_method=S256" in target
    assert auth.OAUTH_STATE_COOKIE in r.cookies
    # The challenge travels in the signed state, never to Google.
    assert CHALLENGE not in target
    state = auth.verify_oauth_state(r.cookies[auth.OAUTH_STATE_COOKIE])
    assert state is not None
    assert state["challenge"] == CHALLENGE


def _configure_login(monkeypatch):
    _mock_settings(
        monkeypatch,
        google_client_id="client-id",
        google_client_secret="client-secret",  # noqa: S106
        allowed_email_domain="ucsd.edu",
    )


def test_extension_login_without_challenge_is_refused(monkeypatch):
    _configure_login(monkeypatch)
    r = client.get("/auth/google/login?client=extension", follow_redirects=False)
    assert r.status_code == 422
    assert auth.OAUTH_STATE_COOKIE not in r.cookies


@pytest.mark.parametrize(
    "bad",
    ["", "short", CHALLENGE + "A", CHALLENGE[:-1], CHALLENGE[:-1] + "=", CHALLENGE[:-1] + "+"],
)
def test_extension_login_with_malformed_challenge_is_refused(monkeypatch, bad):
    _configure_login(monkeypatch)
    r = client.get(
        "/auth/google/login", params={"client": "extension", "nonce": bad}, follow_redirects=False
    )
    assert r.status_code == 422
    assert bad not in r.text or bad == ""  # the value is not echoed back


def test_web_login_is_unchanged_and_refuses_a_challenge(monkeypatch):
    _configure_login(monkeypatch)
    r = client.get("/auth/google/login?client=web", follow_redirects=False)
    assert r.status_code == 302
    state = auth.verify_oauth_state(r.cookies[auth.OAUTH_STATE_COOKIE])
    assert state is not None
    assert "challenge" not in state
    r = client.get(f"/auth/google/login?client=web&nonce={CHALLENGE}", follow_redirects=False)
    assert r.status_code == 422


def test_callback_refuses_extension_state_without_challenge():
    # Cannot happen through /login; guards a state signed without one.
    state_cookie = auth.sign_oauth_state(
        {"state": "s", "verifier": "v", "client": "extension", "ts": time.time()}
    )
    client.cookies.set(auth.OAUTH_STATE_COOKIE, state_cookie)
    r = client.get("/auth/google/callback?code=c&state=s", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/auth/extension/done?error=login_failed"


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
    nonce, challenge = _new_nonce()

    # 1. Login then callback redirects to the landing page with a one-time code
    one_time_code = _extension_login(monkeypatch, challenge, sub="ada")

    # Landing page renders successfully
    landing = client.get(f"/auth/extension/done?code={one_time_code}")
    assert landing.status_code == 200
    assert "Signed in" in landing.text

    # 2. Extension exchanges single-use code plus its raw nonce for a persistent bearer token
    exchange_resp = _exchange(one_time_code, nonce)
    assert exchange_resp.status_code == 200
    data = exchange_resp.json()
    assert "token" in data
    assert data["user"]["email"] == "ada@ucsd.edu"
    bearer_token = data["token"]

    # Replay of the good pair fails
    replay_resp = _exchange(one_time_code, nonce)
    assert replay_resp.status_code == 401
    assert replay_resp.json() == INVALID

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


# --- Extension exchange is bound to the extension's nonce ---

INVALID = {"detail": "invalid or expired code"}


@pytest.mark.integration
def test_code_from_another_login_is_rejected(monkeypatch):
    """The hijack: the attacker's code, sent to a victim whose extension holds another nonce."""
    _, attacker_challenge = _new_nonce()
    victim_nonce, _ = _new_nonce()
    attacker_code = _extension_login(monkeypatch, attacker_challenge, sub="attacker")

    r = _exchange(attacker_code, victim_nonce)
    assert r.status_code == 401
    assert r.json() == INVALID


@pytest.mark.integration
@pytest.mark.parametrize("bad", ["", "wrong", "A" * 43, "x" * 200, "é中"])
def test_wrong_or_malformed_nonce_is_rejected_and_burns_the_code(monkeypatch, bad):
    nonce, challenge = _new_nonce()
    code = _extension_login(monkeypatch, challenge, sub="burn")

    assert _exchange(code, bad).status_code == 401
    # The right nonce no longer helps: the code went with the failed attempt.
    r = _exchange(code, nonce)
    assert r.status_code == 401
    assert r.json() == INVALID


@pytest.mark.integration
def test_missing_nonce_is_rejected(monkeypatch):
    nonce, challenge = _new_nonce()
    code = _extension_login(monkeypatch, challenge, sub="missing")
    assert _exchange(code, None).status_code == 422
    # Unknown fields stay refused too.
    assert (
        client.post(
            "/auth/extension/exchange", json={"code": code, "nonce": nonce, "extra": 1}
        ).status_code
        == 422
    )


@pytest.mark.integration
def test_expired_code_is_rejected(monkeypatch):
    nonce, challenge = _new_nonce()
    code = _extension_login(monkeypatch, challenge, sub="expired")
    with Session(db.engine) as session, session.begin():
        session.execute(
            update(auth_db.AuthExchangeCode)
            .where(auth_db.AuthExchangeCode.code_hash == auth_db.hash_token(code))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    r = _exchange(code, nonce)
    assert r.status_code == 401
    assert r.json() == INVALID


@pytest.mark.integration
def test_code_without_stored_challenge_is_never_accepted():
    """Rows made before the column existed have no challenge."""
    user = auth_db.upsert_user("sub-old-row", "oldrow@ucsd.edu", "Old Row")
    raw = "old-code-" + secrets.token_urlsafe(8)
    with Session(db.engine) as session, session.begin():
        session.execute(
            insert(auth_db.AuthExchangeCode).values(
                code_hash=auth_db.hash_token(raw),
                user_id=user.id,
                expires_at=datetime.now(UTC) + timedelta(seconds=60),
                created_at=datetime.now(UTC),
                nonce_hash=None,
            )
        )
    for guess in ("", "anything", auth_db.nonce_challenge("anything")):
        assert _exchange(raw, guess).status_code == 401
