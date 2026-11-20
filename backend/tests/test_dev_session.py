"""The local session command: a Bearer token for a test user, never reachable over HTTP."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import auth_db, dev_session
from app.main import app

# no_auth_override: real token checks, not the suite's default fake user.
pytestmark = [pytest.mark.integration, pytest.mark.no_auth_override]

EMAIL = "smoke-test@ucsd.edu"


def _get_courses(token: str) -> int:
    with TestClient(app) as client:
        return client.get("/courses", headers={"Authorization": f"Bearer {token}"}).status_code


def _count(sql: str, **params) -> int:
    with auth_db.db.engine.begin() as conn:
        return conn.execute(text(sql), params).scalar_one()


def test_created_user_and_token_work_as_bearer_on_courses():
    token, _ = auth_db.create_local_session(EMAIL)
    with TestClient(app) as client:
        assert client.get("/courses").status_code == 401  # control: no token, no access
    assert _get_courses(token) == 200
    row = _row("SELECT google_sub, name FROM users WHERE email = :e")
    assert row[0].startswith("local-cli:")
    assert row[1] == "smoke-test"
    assert _row("SELECT kind FROM sessions")[0] == "extension"


def _row(sql: str):
    with auth_db.db.engine.begin() as conn:
        return conn.execute(text(sql), {"e": EMAIL}).one()


def test_existing_user_is_reused_and_email_is_lowercased():
    existing = auth_db.upsert_user("sub_real", EMAIL, "Test Person")
    token, _ = auth_db.create_local_session("  Smoke-Test@UCSD.edu ")
    assert _count("SELECT count(*) FROM users WHERE email = :e", e=EMAIL) == 1
    user = auth_db.get_user_by_session_token(token)
    assert user is not None and user.id == existing.id
    # The real google_sub is untouched.
    assert _row("SELECT google_sub FROM users WHERE email = :e")[0] == "sub_real"


@pytest.mark.parametrize(
    "email",
    [
        "someone@gmail.com",
        "someone@ucsd.edu.evil.com",
        "someone@evil.com@ucsd.edu",
        "@ucsd.edu",
        "a b@ucsd.edu",
    ],
)
def test_non_ucsd_email_is_refused(email):
    with pytest.raises(auth_db.LocalSessionRefused):
        auth_db.create_local_session(email)
    assert _count("SELECT count(*) FROM users WHERE email <> 'local@ucsd.edu'") == 0


@pytest.mark.parametrize("hours", [0, -1, 24.01, 100])
def test_ttl_out_of_bounds_is_refused(hours):
    with pytest.raises(auth_db.LocalSessionRefused):
        auth_db.create_local_session(EMAIL, hours)
    assert _count("SELECT count(*) FROM sessions") == 0


def test_ttl_bounds_are_accepted_and_sets_expiry():
    _, short = auth_db.create_local_session(EMAIL, 0.5)
    _, longest = auth_db.create_local_session(EMAIL, 24)
    assert 0 < (longest - short).total_seconds() < 24 * 3600


def test_cli_prints_exactly_the_token_on_stdout(capsys):
    assert dev_session.main(["--email", EMAIL]) == 0
    out, err = capsys.readouterr()
    assert out.endswith("\n") and out.count("\n") == 1
    token = out.strip()
    assert token and not any(ch.isspace() for ch in token)
    assert err == ""
    assert _get_courses(token) == 200
    # Only the hash is stored.
    assert _count("SELECT count(*) FROM sessions WHERE token_hash = :t", t=token) == 0


def test_cli_refusal_exits_nonzero_with_fixed_message_and_empty_stdout(capsys):
    assert dev_session.main(["--email", "x@gmail.com"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "REFUSED: Only addresses ending in @ucsd.edu are accepted.\n"
    assert dev_session.main(["--email", EMAIL, "--ttl-hours", "25"]) == 1
    assert capsys.readouterr().out == ""


def test_revoke_all_makes_tokens_fail_with_401(capsys):
    first, _ = auth_db.create_local_session(EMAIL)
    second, _ = auth_db.create_local_session(EMAIL)
    other, _ = auth_db.create_local_session("other-test@ucsd.edu")
    assert dev_session.main(["--email", EMAIL, "--revoke-all"]) == 0
    out, err = capsys.readouterr()
    assert out == ""
    assert "2" in err
    assert _get_courses(first) == 401
    assert _get_courses(second) == 401
    assert _get_courses(other) == 200  # another user is untouched


def test_revoke_all_for_unknown_user_is_zero(capsys):
    assert dev_session.main(["--email", "nobody-test@ucsd.edu", "--revoke-all"]) == 0
    assert "0" in capsys.readouterr().err


def test_no_route_exposes_session_creation():
    paths = [getattr(route, "path", "") for route in app.routes]
    assert not [p for p in paths if "dev" in p.lower()]
    # The only paths with "session" would be session creation; there must be none.
    assert not [p for p in paths if "session" in p.lower()]
