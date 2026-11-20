"""Starting the app deletes expired sessions and exchange codes, and a failing purge never stops
the app."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import auth_db, db
from app.main import app

LEAK = "DB-ERROR-TEXT"  # must never reach the log


def _backdate(model, column, key_column, key_value, delta: timedelta) -> None:
    with Session(db.engine) as session, session.begin():
        session.execute(
            update(model).where(key_column == key_value).values({column: datetime.now(UTC) + delta})
        )


def _session_hashes() -> set[str]:
    with Session(db.engine) as session:
        return set(session.execute(select(auth_db.UserSession.token_hash)).scalars())


def _code_hashes() -> set[str]:
    with Session(db.engine) as session:
        return set(session.execute(select(auth_db.AuthExchangeCode.code_hash)).scalars())


@pytest.mark.integration
def test_startup_deletes_expired_sessions_and_codes_and_keeps_live_ones():
    user = auth_db.upsert_user("sub-purge", "purge@ucsd.edu", "Purge")
    challenge = auth_db.nonce_challenge("n")

    live_token, _ = auth_db.create_session(user.id, "web", ttl_days=1)
    old_token, _ = auth_db.create_session(user.id, "extension", ttl_days=1)
    live_code = auth_db.create_extension_code(user.id, challenge, ttl_seconds=60)
    old_code = auth_db.create_extension_code(user.id, challenge, ttl_seconds=60)

    old_session_hash = auth_db.hash_token(old_token)
    old_code_hash = auth_db.hash_token(old_code)
    _backdate(
        auth_db.UserSession,
        "expires_at",
        auth_db.UserSession.token_hash,
        old_session_hash,
        timedelta(seconds=-5),
    )
    _backdate(
        auth_db.AuthExchangeCode,
        "expires_at",
        auth_db.AuthExchangeCode.code_hash,
        old_code_hash,
        timedelta(seconds=-5),
    )
    # Control: the rows are there before the app starts.
    assert old_session_hash in _session_hashes()
    assert old_code_hash in _code_hashes()

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert _session_hashes() == {auth_db.hash_token(live_token)}
    assert _code_hashes() == {auth_db.hash_token(live_code)}
    # The live session still works.
    assert auth_db.get_user_by_session_token(live_token) == user


@pytest.mark.parametrize("error", [RuntimeError(LEAK), db.SchemaBusy(LEAK)])
def test_a_failing_purge_is_logged_by_type_and_the_app_starts(monkeypatch, caplog, error):
    def failing():
        raise error

    monkeypatch.setattr(auth_db, "purge_expired", failing)
    with caplog.at_level(logging.DEBUG, logger="aic"), TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
    assert "purge of expired sessions failed" in caplog.text
    assert type(error).__name__ in caplog.text
    assert LEAK not in caplog.text


def test_purge_is_called_once_per_start(monkeypatch):
    calls = []
    monkeypatch.setattr(auth_db, "purge_expired", lambda: calls.append(1) or (0, 0))
    with TestClient(app):
        assert calls == [1]
