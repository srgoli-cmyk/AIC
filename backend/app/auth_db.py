"""Database models and persistence helpers for UCSD Google authentication.

Kept separate from app/db.py so auth logic stays self-contained. Only SHA-256 hashes of session
tokens and single-use extension exchange codes are stored in Postgres; raw tokens are returned once
on creation and never logged or persisted.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Text, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import db

SessionKind = Literal["web", "extension"]
VALID_SESSION_KINDS: tuple[str, ...] = ("web", "extension")


class User(db.Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    google_sub: Mapped[str] = mapped_column(Text, unique=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserSession(db.Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuthExchangeCode(db.Base):
    __tablename__ = "auth_exchange_codes"
    code_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True)
class UserRecord:
    """Public user identity passed through FastAPI dependencies (excludes internal google_sub)."""

    id: str
    email: str
    name: str


def hash_token(raw: str) -> str:
    """SHA-256 hex digest of a high-entropy random token or single-use code."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    # Opaque random identifier (96 bits of entropy) so sequential user/session counts are never
    # exposed in API responses.
    return f"{prefix}_{secrets.token_hex(12)}"


def _to_record(user: User) -> UserRecord:
    return UserRecord(id=user.id, email=user.email, name=user.name)


def upsert_user(google_sub: str, email: str, name: str) -> UserRecord:
    """Create a UCSD student account on first login, or update email/name/last_login_at on repeat
    logins. Keyed by Google's permanent `sub` claim."""
    db.ensure_schema()
    now = datetime.now(UTC)
    normalized_email = email.strip().lower()
    display_name = name.strip() or normalized_email.split("@", 1)[0]
    with Session(db.engine) as session, session.begin():
        stmt = (
            insert(User)
            .values(
                id=_new_id("usr"),
                google_sub=google_sub,
                email=normalized_email,
                name=display_name,
                created_at=now,
                last_login_at=now,
            )
            .on_conflict_do_update(
                index_elements=[User.google_sub],
                set_={
                    "email": normalized_email,
                    "name": display_name,
                    "last_login_at": now,
                },
            )
            .returning(User.id, User.email, User.name)
        )
        row = session.execute(stmt).one()
        return UserRecord(id=row[0], email=row[1], name=row[2])


def get_user(user_id: str) -> UserRecord | None:
    """Look up a student by internal user id, or None if unknown."""
    if not user_id or "\x00" in user_id:
        return None
    db.ensure_schema()
    with Session(db.engine) as session:
        user = session.get(User, user_id)
        return _to_record(user) if user else None


def create_session(user_id: str, kind: SessionKind, ttl_days: int) -> tuple[str, datetime]:
    """Mint a 256-bit random session token, store only its SHA-256 hash, and return
    (raw_token, expires_at). The caller sends raw_token once (cookie or bearer response)."""
    if kind not in VALID_SESSION_KINDS:
        raise ValueError(f"unsupported session kind: {kind!r}")
    if ttl_days <= 0:
        raise ValueError("ttl_days must be positive")
    db.ensure_schema()
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=ttl_days)
    raw_token = secrets.token_urlsafe(32)
    with Session(db.engine) as session, session.begin():
        session.execute(
            insert(UserSession).values(
                id=_new_id("ses"),
                user_id=user_id,
                token_hash=hash_token(raw_token),
                kind=kind,
                expires_at=expires_at,
                created_at=now,
            )
        )
    return raw_token, expires_at


def get_user_by_session_token(
    raw_token: str, *, kind: SessionKind | None = None
) -> UserRecord | None:
    """Resolve a raw cookie or bearer token to its student if the session exists and has not
    expired. Optionally restrict to a specific session kind ('web' or 'extension')."""
    if not raw_token or "\x00" in raw_token:
        return None
    db.ensure_schema()
    now = datetime.now(UTC)
    stmt = (
        select(User.id, User.email, User.name)
        .join(UserSession, UserSession.user_id == User.id)
        .where(
            UserSession.token_hash == hash_token(raw_token),
            UserSession.expires_at > now,
        )
    )
    if kind is not None:
        stmt = stmt.where(UserSession.kind == kind)
    with Session(db.engine) as session:
        row = session.execute(stmt).one_or_none()
        return UserRecord(id=row[0], email=row[1], name=row[2]) if row else None


def revoke_session(raw_token: str) -> bool:
    """Revoke a session by its raw token. Returns True if a matching session was removed."""
    if not raw_token or "\x00" in raw_token:
        return False
    db.ensure_schema()
    with Session(db.engine) as session, session.begin():
        removed = session.execute(
            delete(UserSession).where(UserSession.token_hash == hash_token(raw_token))
        ).rowcount
    return bool(removed)


def create_extension_code(user_id: str, ttl_seconds: int = 60) -> str:
    """Create a short-lived single-use code for the Chrome extension login handoff. Only the
    SHA-256 hash is stored; the raw code is returned once for the redirect URL."""
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    db.ensure_schema()
    now = datetime.now(UTC)
    raw_code = secrets.token_urlsafe(32)
    with Session(db.engine) as session, session.begin():
        session.execute(
            insert(AuthExchangeCode).values(
                code_hash=hash_token(raw_code),
                user_id=user_id,
                expires_at=now + timedelta(seconds=ttl_seconds),
                created_at=now,
            )
        )
    return raw_code


def consume_extension_code(raw_code: str) -> UserRecord | None:
    """Atomically consume a single-use extension exchange code and return its student, or None if
    unknown, expired, or already used. DELETE ... RETURNING prevents replay under concurrency."""
    if not raw_code or "\x00" in raw_code:
        return None
    db.ensure_schema()
    now = datetime.now(UTC)
    with Session(db.engine) as session, session.begin():
        user_id = session.execute(
            delete(AuthExchangeCode)
            .where(
                AuthExchangeCode.code_hash == hash_token(raw_code),
                AuthExchangeCode.expires_at > now,
            )
            .returning(AuthExchangeCode.user_id)
        ).scalar_one_or_none()
        if user_id is None:
            return None
        user = session.get(User, user_id)
        return _to_record(user) if user else None


def purge_expired() -> tuple[int, int]:
    """Delete expired sessions and unused extension exchange codes. Returns (sessions, codes)."""
    db.ensure_schema()
    now = datetime.now(UTC)
    with Session(db.engine) as session, session.begin():
        s_count = session.execute(delete(UserSession).where(UserSession.expires_at <= now)).rowcount
        c_count = session.execute(
            delete(AuthExchangeCode).where(AuthExchangeCode.expires_at <= now)
        ).rowcount
    return int(s_count or 0), int(c_count or 0)
