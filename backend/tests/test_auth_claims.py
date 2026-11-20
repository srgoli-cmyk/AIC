"""Pure tests (no database, no network) for ID token claim checks, the SESSION_SECRET rules for the
signed OAuth state, and the rule that rejected logins log no email or domain."""

import base64
import dataclasses
import json
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import settings
from app.main import app

CLIENT_ID = "client-id.apps.googleusercontent.com"
NOW = 1_800_000_000.0
SECRET_A = "a" * 40  # noqa: S105
SECRET_B = "b" * 40  # noqa: S105


def good_claims(**over) -> dict:
    claims = {
        "aud": CLIENT_ID,
        "iss": "https://accounts.google.com",
        "iat": NOW - 10,
        "exp": NOW + 3000,
        "sub": "123",
        "email": "triton@ucsd.edu",
        "email_verified": True,
        "hd": "ucsd.edu",
    }
    claims.update(over)
    return claims


def check(claims: dict, now: float = NOW):
    return auth.check_id_token_claims(
        claims, client_id=CLIENT_ID, allowed_domain="ucsd.edu", now=now
    )


def test_good_claims_pass_and_email_is_normalized():
    assert check(good_claims()) == (None, "triton@ucsd.edu")
    assert check(good_claims(email=" Triton@UCSD.edu ", hd="UCSD.EDU")) == (
        None,
        "triton@ucsd.edu",
    )


@pytest.mark.parametrize("iss", ["https://accounts.google.com", "accounts.google.com"])
def test_both_google_issuers_accepted(iss):
    assert check(good_claims(iss=iss))[0] is None


@pytest.mark.parametrize(
    "iss",
    ["https://evil.example", "http://accounts.google.com", "", None, 5, ["accounts.google.com"]],
)
def test_bad_issuer_rejected(iss):
    assert check(good_claims(iss=iss))[0] == "claims"


def test_missing_issuer_rejected():
    claims = good_claims()
    del claims["iss"]
    assert check(claims)[0] == "claims"


def test_aud_string_must_equal_client_id():
    assert check(good_claims(aud="someone-else"))[0] == "claims"


def test_missing_or_odd_aud_rejected():
    claims = good_claims()
    del claims["aud"]
    assert check(claims)[0] == "claims"
    assert check(good_claims(aud=None))[0] == "claims"
    assert check(good_claims(aud=7))[0] == "claims"


def test_aud_list_needs_client_id_and_matching_azp():
    assert check(good_claims(aud=[CLIENT_ID, "other"], azp=CLIENT_ID))[0] is None
    # client id absent from the list
    assert check(good_claims(aud=["other"], azp=CLIENT_ID))[0] == "claims"
    # wrong or missing azp
    assert check(good_claims(aud=[CLIENT_ID, "other"], azp="other"))[0] == "claims"
    assert check(good_claims(aud=[CLIENT_ID, "other"]))[0] == "claims"
    assert check(good_claims(aud=[]))[0] == "claims"


def test_expiry_and_skew():
    assert check(good_claims(exp=NOW + 1))[0] is None
    assert check(good_claims(exp=NOW - 59))[0] is None  # inside the 60 s skew
    assert check(good_claims(exp=NOW - 60))[0] == "claims"
    assert check(good_claims(exp=NOW - 3600))[0] == "claims"


def test_iat_skew():
    assert check(good_claims(iat=NOW + 59))[0] is None
    assert check(good_claims(iat=NOW + 61))[0] == "claims"


@pytest.mark.parametrize("bad", [None, "1800000000", True, [], {}])
def test_time_claims_must_be_numbers(bad):
    assert check(good_claims(exp=bad))[0] == "claims"
    assert check(good_claims(iat=bad))[0] == "claims"


def test_missing_time_claims_rejected():
    for key in ("exp", "iat"):
        claims = good_claims()
        del claims[key]
        assert check(claims)[0] == "claims"


@pytest.mark.parametrize("verified", [False, None, "true", 1])
def test_email_must_be_verified_true(verified):
    assert check(good_claims(email_verified=verified)) == ("unverified", None)


def test_domain_rules():
    assert check(good_claims(hd="gmail.com")) == ("domain", None)
    assert check(good_claims(hd=None)) == ("domain", None)
    assert check(good_claims(email="x@gmail.com")) == ("domain", None)
    # suffix trick: the domain must follow the single @
    assert check(good_claims(email="x@evilucsd.edu")) == ("domain", None)
    claims = good_claims()
    del claims["hd"]
    assert check(claims) == ("domain", None)


@pytest.mark.parametrize(
    "email",
    [
        "a@b@ucsd.edu",  # two @
        "triton.ucsd.edu",  # none
        "@ucsd.edu",  # empty local part
        "tritón@ucsd.edu",  # non-ASCII
        "triton@ucsd.edu‮",  # hidden character
        "",
        None,
        5,
    ],
)
def test_malformed_email_rejected(email):
    assert check(good_claims(email=email)) == ("claims", None)


def test_unicode_case_tricks_rejected():
    # Kelvin sign casefolds to ASCII "k"; non-ASCII is refused before any folding matters.
    assert check(good_claims(email="Kelvin@ucsd.edu")) == ("claims", None)


def test_empty_client_id_rejects_everything():
    reason, _ = auth.check_id_token_claims(
        good_claims(aud=""), client_id="", allowed_domain="ucsd.edu", now=NOW
    )
    assert reason == "claims"


# --- SESSION_SECRET and the signed state ---


def use_settings(monkeypatch, **kwargs):
    new = dataclasses.replace(settings, **kwargs)
    monkeypatch.setattr("app.auth.settings", new)


def fresh_state() -> dict:
    return {"state": "s", "verifier": "v", "client": "web", "ts": time.time()}


def test_state_signed_with_one_secret_fails_under_another(monkeypatch):
    use_settings(monkeypatch, session_secret=SECRET_A)
    token = auth.sign_oauth_state(fresh_state())
    assert auth.verify_oauth_state(token) is not None
    use_settings(monkeypatch, session_secret=SECRET_B)
    assert auth.verify_oauth_state(token) is None


def test_tampered_state_refused(monkeypatch):
    use_settings(monkeypatch, session_secret=SECRET_A)
    data, sig = auth.sign_oauth_state(fresh_state()).split(".")
    forged = {**fresh_state(), "client": "extension"}
    forged_b64 = base64.urlsafe_b64encode(json.dumps(forged).encode()).decode().rstrip("=")
    assert auth.verify_oauth_state(f"{forged_b64}.{sig}") is None
    assert auth.verify_oauth_state(f"{data}.{sig[:-2]}AA") is None
    assert auth.verify_oauth_state("garbage") is None


def test_expired_state_refused(monkeypatch):
    use_settings(monkeypatch, session_secret=SECRET_A)
    old = {**fresh_state(), "ts": time.time() - auth.OAUTH_STATE_MAX_AGE - 5}
    assert auth.verify_oauth_state(auth.sign_oauth_state(old)) is None


@pytest.mark.parametrize("secret", ["", "short", "x" * 31])
def test_login_503_when_secret_missing_or_short(monkeypatch, caplog, secret):
    use_settings(
        monkeypatch,
        google_client_id=CLIENT_ID,
        google_client_secret="gsecret-value",  # noqa: S106
        session_secret=secret,
    )
    with caplog.at_level(logging.WARNING, logger="aic"):
        r = TestClient(app).get("/auth/google/login", follow_redirects=False)
    assert r.status_code == 503
    assert r.json() == {"detail": "auth service not configured"}
    assert "SESSION_SECRET" in caplog.text
    # names the setting only, never a value
    if len(secret) > 8:
        assert secret not in caplog.text
    assert "gsecret-value" not in caplog.text


def test_no_signing_without_a_valid_secret(monkeypatch):
    use_settings(monkeypatch, session_secret="short")  # noqa: S106
    with pytest.raises(RuntimeError):
        auth.sign_oauth_state(fresh_state())
    assert auth.verify_oauth_state("a.b") is None


def test_login_works_with_valid_secret(monkeypatch):
    use_settings(
        monkeypatch,
        google_client_id=CLIENT_ID,
        google_client_secret="gsecret-value",  # noqa: S106
        session_secret=SECRET_A,
    )
    r = TestClient(app).get("/auth/google/login", follow_redirects=False)
    assert r.status_code == 302
    assert auth.OAUTH_STATE_COOKIE in r.cookies


# --- Rejected logins log no email or domain ---


def _jwt(claims: dict) -> str:
    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{b64(b'{}')}.{b64(json.dumps(claims).encode())}.{b64(b'sig')}"


@pytest.mark.parametrize(
    ("override", "location", "reason"),
    [
        (
            {"hd": "private-school.example", "email": "pat@private-school.example"},
            "ucsd_only",
            "domain",
        ),
        ({"email_verified": False}, "ucsd_only", "unverified"),
        ({"aud": "someone-else"}, "login_failed", "claims"),
    ],
)
def test_rejected_login_logs_no_email_or_domain(monkeypatch, caplog, override, location, reason):
    use_settings(
        monkeypatch,
        google_client_id=CLIENT_ID,
        google_client_secret="gsecret-value",  # noqa: S106
        session_secret=SECRET_A,
        allowed_email_domain="ucsd.edu",
    )
    claims = good_claims(iat=time.time(), exp=time.time() + 3000, **override)
    claims["email"] = override.get("email", "secret.person@ucsd.edu")
    if "hd" not in override:
        claims["hd"] = "ucsd.edu"

    class Resp:
        status_code = 200

        def json(self):
            return {"id_token": _jwt(claims)}

    async def fake_post(self, url, **kwargs):
        return Resp()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    c = TestClient(app)
    c.cookies.set(auth.OAUTH_STATE_COOKIE, auth.sign_oauth_state(fresh_state()))
    with caplog.at_level(logging.DEBUG, logger="aic"):
        r = c.get("/auth/google/callback?code=c&state=s", follow_redirects=False)
    assert r.headers["location"] == f"/app/?auth_error={location}"
    assert f"reason={reason}" in caplog.text
    for forbidden in ("secret.person", "pat@", "private-school", "ucsd.edu", "@"):
        assert forbidden not in caplog.text
