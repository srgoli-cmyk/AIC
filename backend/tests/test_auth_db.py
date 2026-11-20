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
        auth_db.create_extension_code("usr_1", "c" * 43, ttl_seconds=0)


def test_empty_and_nul_inputs_short_circuit_without_db(monkeypatch):
    def fail_db():
        raise AssertionError("invalid empty/NUL inputs must not hit the database")

    monkeypatch.setattr(db, "ensure_schema", fail_db)
    for bad in ("", "abc\x00def"):
        assert auth_db.get_user(bad) is None
        assert auth_db.get_user_by_session_token(bad) is None
        assert auth_db.revoke_session(bad) is False
        assert auth_db.consume_extension_code(bad, "nonce") is None


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


def _users() -> list[tuple]:
    with Session(db.engine) as session:
        rows = session.execute(
            select(
                auth_db.User.id,
                auth_db.User.google_sub,
                auth_db.User.email,
                auth_db.User.name,
                auth_db.User.last_login_at,
            ).order_by(auth_db.User.id)
        ).all()
    return [tuple(r) for r in rows]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("email", "make_owner"),
    [
        ("taken@ucsd.edu", lambda: auth_db.upsert_user("old-sub", "taken@ucsd.edu", "Old")),
        # The usr_local seed, which the test setup (and a real database) already has.
        ("local@ucsd.edu", lambda: None),
        # What `python -m app.dev_session` makes: a user with a placeholder sub.
        ("taken@ucsd.edu", lambda: auth_db.create_local_session("taken@ucsd.edu")),
    ],
    ids=["recycled", "usr_local", "local-cli"],
)
def test_a_new_sub_with_an_email_that_belongs_to_another_user_is_refused(email, make_owner):
    make_owner()
    before = _users()
    assert any(row[2] == email for row in before)
    with pytest.raises(auth_db.EmailInUse) as caught:
        auth_db.upsert_user("brand-new-sub", email.upper(), "New Person")
    assert email not in repr(caught.value) and not str(caught.value)
    # Nothing was created or changed: not the other user, not a login time, no new row.
    assert _users() == before
    assert all(row[1] != "brand-new-sub" for row in before)


@pytest.mark.integration
def test_a_known_sub_that_now_reports_another_users_email_is_refused_and_unchanged():
    auth_db.upsert_user("sub-first", "first@ucsd.edu", "First")
    second = auth_db.upsert_user("sub-second", "second@ucsd.edu", "Second")
    before = _users()
    with pytest.raises(auth_db.EmailInUse):
        auth_db.upsert_user("sub-second", "first@ucsd.edu", "Second Renamed")
    assert _users() == before
    assert auth_db.get_user(second.id) == second


@pytest.mark.integration
def test_a_local_cli_placeholder_is_not_adopted_by_a_real_login():
    token, _ = auth_db.create_local_session("placeholder@ucsd.edu")
    placeholder = auth_db.get_user_by_session_token(token)
    assert placeholder is not None
    with pytest.raises(auth_db.EmailInUse):
        auth_db.upsert_user("real-google-sub", "placeholder@ucsd.edu", "Real Person")
    # The placeholder keeps its sub and its session; the real person got no account.
    assert auth_db.get_user_by_session_token(token) == placeholder
    owner = [r for r in _users() if r[2] == "placeholder@ucsd.edu"]
    assert len(owner) == 1 and owner[0][1].startswith("local-cli:")


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
    challenge = auth_db.nonce_challenge("raw-nonce")
    code = auth_db.create_extension_code(user.id, challenge, ttl_seconds=60)

    # First exchange succeeds; replay with the same code fails.
    assert auth_db.consume_extension_code(code, "raw-nonce") == user
    assert auth_db.consume_extension_code(code, "raw-nonce") is None

    # Expired code cannot be consumed.
    expired_code = auth_db.create_extension_code(user.id, challenge, ttl_seconds=60)
    with Session(db.engine) as session, session.begin():
        session.execute(
            update(auth_db.AuthExchangeCode)
            .where(auth_db.AuthExchangeCode.code_hash == auth_db.hash_token(expired_code))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert auth_db.consume_extension_code(expired_code, "raw-nonce") is None

    # A wrong nonce fails and burns the code, so the right nonce afterwards fails too.
    burnt = auth_db.create_extension_code(user.id, challenge, ttl_seconds=60)
    assert auth_db.consume_extension_code(burnt, "other-nonce") is None
    assert auth_db.consume_extension_code(burnt, "raw-nonce") is None


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
