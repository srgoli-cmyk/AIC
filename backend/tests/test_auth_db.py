"""Unit and integration tests for auth_db (users, hashed sessions, single-use extension codes)
and per-student composite-key data isolation in db.py."""

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import auth_db, db
from app.schemas import SyncPayload

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / "contracts/fixtures/sync_sample.json").read_text())


# --- pure unit tests (no Postgres required) ---


def test_hash_token_is_deterministic_sha256_and_never_plaintext():
    raw = "example-random-token-value"
    digest = auth_db.hash_token(raw)
    assert len(digest) == 64
    assert digest == auth_db.hash_token(raw)
    assert raw not in digest


def test_create_session_validates_kind_and_ttl():
    with pytest.raises(ValueError, match="unsupported session kind"):
        auth_db.create_session("usr_1", "invalid", 30)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="ttl_days"):
        auth_db.create_session("usr_1", "web", 0)


def test_create_extension_code_validates_ttl():
    with pytest.raises(ValueError, match="ttl_seconds"):
        auth_db.create_extension_code("usr_1", ttl_seconds=0)


def test_empty_and_nul_inputs_short_circuit_without_db(monkeypatch):
    def fail_db():
        raise AssertionError("invalid empty/NUL inputs must not hit the database")

    monkeypatch.setattr(db, "ensure_schema", fail_db)
    for bad in ("", "abc\x00def"):
        assert auth_db.get_user(bad) is None
        assert auth_db.get_user_by_session_token(bad) is None
        assert auth_db.revoke_session(bad) is False
        assert auth_db.consume_extension_code(bad) is None


def test_course_tables_use_user_scoped_composite_primary_keys():
    """Guard: every student-owned table includes `user_id` in its primary key so two students
    taking the same Canvas class can never collide."""
    for model, expected_pk in (
        (db.Course, ("user_id", "course_id")),
        (db.Lecture, ("user_id", "entry_id")),
        (db.CanvasFile, ("user_id", "canvas_file_id")),
        (db.Document, ("user_id", "doc_id")),
        (db.CourseSite, ("user_id", "course_id")),
    ):
        pk_cols = tuple(col.name for col in model.__table__.primary_key.columns)
        assert pk_cols == expected_pk


# --- Postgres integration tests ---


@pytest.mark.integration
def test_upsert_user_creates_and_updates_by_google_sub():
    first = auth_db.upsert_user("google-sub-1", "Ada@UCSD.EDU", "Ada Lovelace")
    assert first.id.startswith("usr_")
    assert first.email == "ada@ucsd.edu"
    assert first.name == "Ada Lovelace"

    # Repeat login with same google_sub updates profile without changing internal user id.
    second = auth_db.upsert_user("google-sub-1", "ada.l@ucsd.edu", "Ada Byron")
    assert second.id == first.id
    assert second.email == "ada.l@ucsd.edu"
    assert second.name == "Ada Byron"
    assert auth_db.get_user(first.id) == second


@pytest.mark.integration
def test_session_stores_only_hash_and_respects_kind_expiry_and_revocation():
    user = auth_db.upsert_user("google-sub-2", "student@ucsd.edu", "Student")
    raw_token, expires_at = auth_db.create_session(user.id, "web", ttl_days=7)
    assert expires_at > datetime.now(UTC)

    # Confirm the database stores the SHA-256 hash, never the raw token.
    with Session(db.engine) as session:
        stored_hash = session.execute(
            select(auth_db.UserSession.token_hash).where(auth_db.UserSession.user_id == user.id)
        ).scalar_one()
    assert stored_hash == auth_db.hash_token(raw_token)
    assert stored_hash != raw_token

    # Lookup works for matching kind and rejects wrong kind.
    assert auth_db.get_user_by_session_token(raw_token) == user
    assert auth_db.get_user_by_session_token(raw_token, kind="web") == user
    assert auth_db.get_user_by_session_token(raw_token, kind="extension") is None

    # Expired session is rejected and cleaned up by purge_expired.
    with Session(db.engine) as session, session.begin():
        session.execute(
            update(auth_db.UserSession)
            .where(auth_db.UserSession.token_hash == stored_hash)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert auth_db.get_user_by_session_token(raw_token) is None
    purged_sessions, _ = auth_db.purge_expired()
    assert purged_sessions == 1

    # Revoking a live session removes it immediately.
    ext_token, _ = auth_db.create_session(user.id, "extension", ttl_days=30)
    assert auth_db.get_user_by_session_token(ext_token, kind="extension") == user
    assert auth_db.revoke_session(ext_token) is True
    assert auth_db.revoke_session(ext_token) is False
    assert auth_db.get_user_by_session_token(ext_token) is None


@pytest.mark.integration
def test_extension_exchange_code_is_single_use_and_expires():
    user = auth_db.upsert_user("google-sub-3", "ext@ucsd.edu", "Ext Student")
    code = auth_db.create_extension_code(user.id, ttl_seconds=60)

    # First exchange succeeds; replay with the same code fails.
    assert auth_db.consume_extension_code(code) == user
    assert auth_db.consume_extension_code(code) is None

    # Expired code cannot be consumed.
    expired_code = auth_db.create_extension_code(user.id, ttl_seconds=60)
    with Session(db.engine) as session, session.begin():
        session.execute(
            update(auth_db.AuthExchangeCode)
            .where(auth_db.AuthExchangeCode.code_hash == auth_db.hash_token(expired_code))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert auth_db.consume_extension_code(expired_code) is None


@pytest.mark.integration
def test_two_students_can_sync_the_same_canvas_course_independently():
    alice = auth_db.upsert_user("sub-alice", "alice@ucsd.edu", "Alice")
    bob = auth_db.upsert_user("sub-bob", "bob@ucsd.edu", "Bob")

    alice_payload = SyncPayload.model_validate(copy.deepcopy(SAMPLE))
    bob_raw = copy.deepcopy(SAMPLE)
    bob_raw["course"]["name"] = "Bob's Calculus Section"
    bob_raw["lectures"] = [bob_raw["lectures"][0]]
    bob_payload = SyncPayload.model_validate(bob_raw)

    cid_a, skipped_a = db.save_sync(alice_payload, user_id=alice.id)
    cid_b, skipped_b = db.save_sync(bob_payload, user_id=bob.id)
    assert cid_a == cid_b == "canvas-999999"
    assert skipped_a == skipped_b == 0

    # Each student sees only their own course details and lecture count.
    alice_courses = db.list_courses(user_id=alice.id)
    bob_courses = db.list_courses(user_id=bob.id)
    assert [(c.course_id, c.name, c.lectures) for c in alice_courses] == [
        ("canvas-999999", "Calculus for Science and Engineering", 2)
    ]
    assert [(c.course_id, c.name, c.lectures) for c in bob_courses] == [
        ("canvas-999999", "Bob's Calculus Section", 1)
    ]

    # Documents with the same Canvas file id stay isolated per student.
    db.save_document(
        cid_a,
        "canvas-file-7",
        source="canvas",
        name="Alice Slides",
        url="https://canvas.ucsd.edu/courses/999999/files/7",
        version="v1",
        content_hash="aaa",
        pages=1,
        chunks=[(1, "alice text")],
        user_id=alice.id,
    )
    db.save_document(
        cid_b,
        "canvas-file-7",
        source="canvas",
        name="Bob Slides",
        url="https://canvas.ucsd.edu/courses/999999/files/7",
        version="v1",
        content_hash="bbb",
        pages=1,
        chunks=[(1, "bob text")],
        user_id=bob.id,
    )
    assert [d.name for d in db.list_documents(cid_a, user_id=alice.id)] == ["Alice Slides"]
    assert [d.name for d in db.list_documents(cid_b, user_id=bob.id)] == ["Bob Slides"]
