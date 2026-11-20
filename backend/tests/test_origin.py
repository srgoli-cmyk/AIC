"""CORS (only our extension) and the Origin check on every state-changing route."""

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import answer, auth_db, config, db, study
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / "contracts/fixtures/sync_sample.json").read_text())

EXT_ID = config.settings.extension_id
EXT = f"chrome-extension://{EXT_ID}"
OTHER_EXT = "chrome-extension://" + "a" * 32
EVIL = "https://evil.example"
LOCALHOST_PAGE = "http://localhost:3000"
OWN = "http://testserver"  # the TestClient's scheme and Host

ENTRY = "0_abcd1234"
QUESTION = {"course_id": "canvas-999999", "question": "What is a loop?"}

# Every state-changing route: (method, path, request keywords). A bad JSON body on purpose where
# there is one: a refusal must come before the body is parsed, so it is 403, never 422.
ROUTES = [
    ("POST", "/sync", {"json": SAMPLE}),
    ("POST", "/ask", {"json": QUESTION}),
    ("POST", "/ask/stream", {"json": QUESTION}),
    ("POST", "/study", {"json": {"entry_id": ENTRY, "kind": "teach"}}),
    ("POST", "/auth/logout", {}),
    ("POST", "/courses/canvas-1/site/search", {}),
    ("PUT", "/courses/canvas-1/site", {"json": {"url": "https://dsc10.com/"}}),
    ("POST", "/courses/canvas-1/site/sync", {}),
    ("DELETE", "/courses/canvas-1/site", {}),
    ("PUT", "/courses/canvas-1/documents/canvas/7?name=a&version=b", {"content": b"%PDF"}),
    ("DELETE", "/courses/canvas-1/documents/canvas/7", {}),
]
ROUTE_IDS = [f"{m} {p.split('?')[0]}" for m, p, _ in ROUTES]


# --- CORS ---


def test_cors_allows_only_our_extension_and_no_credentials():
    c = TestClient(app)
    ok = c.get("/health", headers={"Origin": EXT})
    assert ok.headers["access-control-allow-origin"] == EXT
    assert "access-control-allow-credentials" not in ok.headers
    for origin in (OTHER_EXT, LOCALHOST_PAGE, "http://127.0.0.1:5173", EVIL):
        r = c.get("/health", headers={"Origin": origin})
        assert "access-control-allow-origin" not in r.headers, origin
        assert "access-control-allow-credentials" not in r.headers, origin


def test_cors_preflight_is_answered_for_our_extension_only():
    c = TestClient(app)
    ask = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization",
    }
    ok = c.options("/sync", headers={"Origin": EXT, **ask})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == EXT
    assert "access-control-allow-credentials" not in ok.headers
    assert "authorization" in ok.headers["access-control-allow-headers"].lower()
    for origin in (OTHER_EXT, LOCALHOST_PAGE, EVIL):
        bad = c.options("/sync", headers={"Origin": origin, **ask})
        assert bad.status_code == 400, origin
        assert "access-control-allow-origin" not in bad.headers, origin


def test_extension_id_default_and_validation(caplog):
    assert config.Settings.extension_id == "gdgflcpoijokbdnckcojphfoinhkbbmd"
    assert config.extension_origin(EXT_ID) == EXT
    with caplog.at_level(logging.WARNING, logger="aic"):
        for bad in ("", "short", "A" * 32, "q" * 32, "a" * 33, EXT):
            assert config.extension_origin(bad) is None, bad
    assert "EXTENSION_ID" in caplog.text
    assert "q" * 32 not in caplog.text  # the value is not logged


# --- the Origin check ---


def _token(sub="origin-user", kind="web"):
    user = auth_db.upsert_user(google_sub=sub, email=f"{sub}@ucsd.edu", name="Origin User")
    token, _ = auth_db.create_session(user.id, kind=kind, ttl_days=1)
    return user, token


def _send(client, method, path, kwargs, headers):
    kwargs = dict(kwargs)
    if "json" in kwargs:  # a broken body, to show the body is not parsed before the check
        kwargs = {"content": b"{", "headers": {"Content-Type": "application/json"}}
    extra = kwargs.pop("headers", {})
    return client.request(method, path, headers={**extra, **headers}, **kwargs)


@pytest.mark.integration
@pytest.mark.no_auth_override
@pytest.mark.parametrize("origin", [EVIL, LOCALHOST_PAGE, OTHER_EXT, "null"])
@pytest.mark.parametrize(("method", "path", "kwargs"), ROUTES, ids=ROUTE_IDS)
def test_cookie_from_a_foreign_origin_is_refused_before_anything_runs(method, path, kwargs, origin):
    _, token = _token()
    client = TestClient(app)
    client.cookies.set("aic_session", token)
    r = _send(client, method, path, kwargs, {"Origin": origin})
    assert r.status_code == 403
    assert r.json() == {"detail": "origin not allowed"}


@pytest.mark.integration
@pytest.mark.no_auth_override
@pytest.mark.parametrize(
    "headers",
    [{}, {"Origin": OWN}, {"Origin": OWN.upper()}],
    ids=["no origin", "own origin", "own origin, other case"],
)
@pytest.mark.parametrize(("method", "path", "kwargs"), ROUTES, ids=ROUTE_IDS)
def test_cookie_with_no_or_own_origin_is_not_refused(method, path, kwargs, headers):
    _, token = _token()
    client = TestClient(app)
    client.cookies.set("aic_session", token)
    r = _send(client, method, path, kwargs, headers)
    assert r.status_code != 403  # whatever the route says next (422 for the broken body, ...)


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_own_origin_follows_the_hosts_scheme_and_port():
    _, token = _token()
    client = TestClient(app, base_url="http://localhost:8000")
    client.cookies.set("aic_session", token)
    ok = client.post("/auth/logout", headers={"Origin": "http://localhost:8000"})
    assert ok.status_code == 204
    # The same host on another port, or over https, is another origin.
    for origin in ("http://localhost:3000", "https://localhost:8000"):
        assert client.post("/auth/logout", headers={"Origin": origin}).status_code == 403


@pytest.mark.integration
@pytest.mark.no_auth_override
@pytest.mark.parametrize("origin", [EXT, EVIL, LOCALHOST_PAGE, None])
@pytest.mark.parametrize(("method", "path", "kwargs"), ROUTES, ids=ROUTE_IDS)
def test_a_valid_bearer_token_is_allowed_from_any_origin(method, path, kwargs, origin):
    # The browser cannot send an Authorization header cross-origin without passing the CORS
    # preflight, and only our extension passes it, so the check lets a Bearer token through.
    _, token = _token(kind="extension")
    headers = {"Authorization": f"Bearer {token}"}
    if origin:
        headers["Origin"] = origin
    r = _send(TestClient(app), method, path, kwargs, headers)
    assert r.status_code != 403


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_a_bearer_token_that_is_not_valid_does_not_open_a_foreign_origin():
    user, cookie = _token()
    client = TestClient(app)
    client.cookies.set("aic_session", cookie)  # a valid cookie behind a bad Bearer must not help
    for bearer in ("nonsense", "", " "):
        r = client.post(
            "/sync",
            json=SAMPLE,
            headers={"Authorization": f"Bearer {bearer}", "Origin": EVIL},
        )
        assert r.status_code == 403, bearer
    # With a revoked token as well.
    _, token = _token("revoked")
    auth_db.revoke_session(token)
    r = TestClient(app).post(
        "/sync", json=SAMPLE, headers={"Authorization": f"Bearer {token}", "Origin": EVIL}
    )
    assert r.status_code == 403
    # Without a foreign Origin the same bad token is the usual 401.
    r = TestClient(app).post("/sync", json=SAMPLE, headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_a_foreign_origin_gets_403_even_when_not_signed_in():
    r = TestClient(app).post("/sync", json=SAMPLE, headers={"Origin": EVIL})
    assert r.status_code == 403
    assert TestClient(app).post("/sync", json=SAMPLE).status_code == 401


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_reads_are_not_checked():
    # GET changes nothing and a page cannot read the answer (no CORS); the cookie still works.
    _, token = _token()
    client = TestClient(app)
    client.cookies.set("aic_session", token)
    assert client.get("/courses", headers={"Origin": EVIL}).status_code == 200


# --- no side effects behind a refusal ---


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_a_refused_sync_stores_nothing_and_an_allowed_one_does():
    user, token = _token()
    client = TestClient(app)
    client.cookies.set("aic_session", token)
    refused = client.post("/sync", json=SAMPLE, headers={"Origin": EVIL})
    assert refused.status_code == 403
    assert not db.course_exists("canvas-999999", user_id=user.id)

    bearer = {"Authorization": f"Bearer {token}", "Origin": EXT}
    allowed = TestClient(app).post("/sync", json=SAMPLE, headers=bearer)
    assert allowed.status_code == 200
    assert db.course_exists("canvas-999999", user_id=user.id)


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_a_refused_ask_or_study_makes_no_model_call(monkeypatch):
    user, token = _token()
    # A synced course and lecture, so that a request that got through would reach the model.
    sync = TestClient(app).post("/sync", json=SAMPLE, headers={"Authorization": f"Bearer {token}"})
    assert sync.status_code == 200
    entry = SAMPLE["lectures"][0]["entry_id"]

    calls = []

    def tripwire(*args, **kwargs):
        calls.append(1)
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(answer, "answer_question", tripwire)
    monkeypatch.setattr(answer, "prepare", tripwire)
    monkeypatch.setattr(answer, "call_llm", tripwire)
    monkeypatch.setattr(study, "study_lecture", tripwire)

    client = TestClient(app)
    client.cookies.set("aic_session", token)
    evil = {"Origin": EVIL}
    ask = {"course_id": QUESTION["course_id"], "question": "What is a loop?"}
    assert client.post("/ask", json=ask, headers=evil).status_code == 403
    assert client.post("/ask/stream", json=ask, headers=evil).status_code == 403
    study_body = {"entry_id": entry, "kind": "teach"}
    assert client.post("/study", json=study_body, headers=evil).status_code == 403
    assert calls == []


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_a_refused_logout_keeps_the_session():
    user, token = _token()
    client = TestClient(app)
    client.cookies.set("aic_session", token)
    assert client.post("/auth/logout", headers={"Origin": EVIL}).status_code == 403
    assert auth_db.get_user_by_session_token(token) is not None
    assert client.post("/auth/logout").status_code == 204
    assert auth_db.get_user_by_session_token(token) is None


# --- the extension's login exchange ---


@pytest.mark.integration
@pytest.mark.no_auth_override
@pytest.mark.parametrize("origin", [EXT, EVIL, None])
def test_the_extension_exchange_is_not_affected(origin):
    # No cookie and no Bearer token: the code and nonce protect it. A wrong pair is the usual 401,
    # never this check's 403.
    headers = {"Origin": origin} if origin else {}
    r = TestClient(app).post(
        "/auth/extension/exchange",
        json={"code": "x" * 43, "nonce": "y" * 43},
        headers=headers,
    )
    assert r.status_code == 401
    assert r.json() == {"detail": "invalid or expired code"}
