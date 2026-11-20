"""Database models and persistence helpers for UCSD Google authentication.

Kept separate from app/db.py so auth logic stays self-contained. Only SHA-256 hashes of session
tokens and single-use extension exchange codes are stored in Postgres; raw tokens are returned once
on creation and never logged or persisted.
"""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Text, delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import db
from .config import settings

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
    nonce_hash: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    return _insert_session(user_id, kind, timedelta(days=ttl_days))


def _insert_session(user_id: str, kind: SessionKind, ttl: timedelta) -> tuple[str, datetime]:
    """Insert a session that lives for `ttl` and return (raw_token, expires_at)."""
    db.ensure_schema()
    now = datetime.now(UTC)
    expires_at = now + ttl
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


def nonce_challenge(nonce: str) -> str:
    """The challenge for a raw nonce: base64url (no padding) of its SHA-256. The extension sends
    this at login and the raw nonce at exchange."""
    digest = hashlib.sha256(nonce.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_extension_code(user_id: str, challenge: str, ttl_seconds: int = 60) -> str:
    """Create a short-lived single-use code for the Chrome extension login handoff, bound to the
    extension's nonce challenge. Only the SHA-256 hash of the code is stored; the raw code is
    returned once for the redirect URL."""
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    if not challenge:
        raise ValueError("challenge is required")
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
                nonce_hash=challenge,
            )
        )
    return raw_code


def consume_extension_code(raw_code: str, nonce: str) -> UserRecord | None:
    """Atomically consume a single-use extension exchange code and return its student, or None if
    the code is unknown, expired or used, or the nonce does not match the code's challenge.
    DELETE ... RETURNING prevents replay under concurrency. The row is deleted on any attempt
    that finds it, so a wrong nonce cannot be retried: the nonce lives as long as the code."""
    if not raw_code or "\x00" in raw_code:
        return None
    db.ensure_schema()
    now = datetime.now(UTC)
    with Session(db.engine) as session, session.begin():
        row = session.execute(
            delete(AuthExchangeCode)
            .where(
                AuthExchangeCode.code_hash == hash_token(raw_code),
                AuthExchangeCode.expires_at > now,
            )
            .returning(AuthExchangeCode.user_id, AuthExchangeCode.nonce_hash)
        ).one_or_none()
        if row is None:
            return None
        user_id, stored_challenge = row
        # Old rows have no challenge and are never accepted.
        if not stored_challenge:
            return None
        # Constant-time comparison on bytes (compare_digest rejects non-ASCII str).
        if not hmac.compare_digest(
            nonce_challenge(nonce).encode("ascii"), stored_challenge.encode("utf-8")
        ):
            return None
        user = session.get(User, user_id)
        return _to_record(user) if user else None


class LocalSessionRefused(Exception):
    """A local session was refused. The message is fixed text, safe to print."""


MAX_LOCAL_TTL_HOURS = 24


def _local_email(email: str) -> str:
    """The lowercased address if it is a plain address in the allowed domain, else a refusal.
    Same suffix rule as login; whitespace and control characters are refused outright."""
    wanted = email.strip().lower()
    suffix = "@" + settings.allowed_email_domain
    local = wanted[: -len(suffix)]
    if (
        not wanted.endswith(suffix)
        or not local
        or "@" in local
        or not wanted.isprintable()
        or any(ch.isspace() for ch in wanted)
    ):
        raise LocalSessionRefused(f"Only addresses ending in {suffix} are accepted.")
    return wanted


def create_local_session(email: str, ttl_hours: float = 1) -> tuple[str, datetime]:
    """Find or create the user with this exact lowercased email and mint an `extension` session.

    For the local command `python -m app.dev_session` only; no route calls this. A created user
    gets the placeholder google_sub `local-cli:<random hex>`. Returns (raw_token, expires_at);
    only the token's hash is stored. Raises LocalSessionRefused for a bad address or TTL."""
    wanted = _local_email(email)
    if not 0 < ttl_hours <= MAX_LOCAL_TTL_HOURS:
        raise LocalSessionRefused(f"--ttl-hours must be above 0 and at most {MAX_LOCAL_TTL_HOURS}.")
    db.ensure_schema()
    now = datetime.now(UTC)
    with Session(db.engine) as session, session.begin():
        # ON CONFLICT DO NOTHING on the unique email keeps this safe if two runs race.
        session.execute(
            insert(User)
            .values(
                id=_new_id("usr"),
                google_sub=f"local-cli:{secrets.token_hex(16)}",
                email=wanted,
                name=wanted.split("@", 1)[0],
                created_at=now,
                last_login_at=now,
            )
            .on_conflict_do_nothing(index_elements=[User.email])
        )
        user_id = session.execute(select(User.id).where(User.email == wanted)).scalar_one()
    return _insert_session(user_id, "extension", timedelta(hours=ttl_hours))


def revoke_all_sessions(email: str) -> int:
    """Delete every session of the user with this email (any kind). Returns how many were removed;
    an unknown user is 0. Same address rule as create_local_session."""
    wanted = _local_email(email)
    db.ensure_schema()
    with Session(db.engine) as session, session.begin():
        user_id = session.execute(select(User.id).where(User.email == wanted)).scalar_one_or_none()
        if user_id is None:
            return 0
        removed = session.execute(delete(UserSession).where(UserSession.user_id == user_id))
    return int(removed.rowcount or 0)


class ClaimRefused(Exception):
    """The claim was refused and nothing was changed. The message is safe to print: it holds only
    fixed text, the email the operator typed, and course ids."""


# Tables moved by claim_local_data, in the order they are reported.
CLAIM_TABLES = ("courses", "lectures", "files", "documents", "course_sites", "chunks", "doc_chunks")

# Copy statements in foreign-key order (parents before children). Every column is listed so the
# copy keeps all data; `chunks` and `doc_chunks` omit `id`, so the copies get fresh ids.
_CLAIM_COPY_SQL = {
    "courses": """
        INSERT INTO courses (user_id, course_id, canvas_course_id, code, name, term, term_start,
                             term_end, instructors, sections, synced_at, indexed)
        SELECT :target, course_id, canvas_course_id, code, name, term, term_start,
               term_end, instructors, sections, synced_at, indexed
        FROM courses WHERE user_id = :local""",
    "lectures": """
        INSERT INTO lectures (user_id, entry_id, course_id, title, lecture_no, section,
                              recorded_at, recorded_start, duration_sec, media_url, captions_srt,
                              summary, chunked_hash)
        SELECT :target, entry_id, course_id, title, lecture_no, section, recorded_at,
               recorded_start, duration_sec, media_url, captions_srt, summary, chunked_hash
        FROM lectures WHERE user_id = :local""",
    "files": """
        INSERT INTO files (user_id, canvas_file_id, course_id, name, content_type)
        SELECT :target, canvas_file_id, course_id, name, content_type
        FROM files WHERE user_id = :local""",
    "documents": """
        INSERT INTO documents (user_id, doc_id, course_id, source, name, url, version,
                               content_hash, pages, lecture_no, synced_at)
        SELECT :target, doc_id, course_id, source, name, url, version, content_hash, pages,
               lecture_no, synced_at
        FROM documents WHERE user_id = :local""",
    "course_sites": """
        INSERT INTO course_sites (user_id, course_id, url, title, chosen_by, status, found,
                                  stored, unchanged, unreadable, failed, updated_at)
        SELECT :target, course_id, url, title, chosen_by, status, found, stored, unchanged,
               unreadable, failed, updated_at
        FROM course_sites WHERE user_id = :local""",
    "chunks": """
        INSERT INTO chunks (user_id, entry_id, start_sec, end_sec, text, embedding)
        SELECT :target, entry_id, start_sec, end_sec, text, embedding
        FROM chunks WHERE user_id = :local ORDER BY id""",
    "doc_chunks": """
        INSERT INTO doc_chunks (user_id, doc_id, page, text, embedding)
        SELECT :target, doc_id, page, text, embedding
        FROM doc_chunks WHERE user_id = :local ORDER BY id""",
}

# Course ids (of usr_local rows) that would collide with a row the target user already has.
# Checks every key the copy could violate: the course key, the (user, canvas course) uniqueness,
# and the lecture, document and file keys.
_CLAIM_CONFLICT_SQL = """
    SELECT l.course_id FROM courses l JOIN courses t
        ON t.user_id = :target AND (t.course_id = l.course_id
                                    OR t.canvas_course_id = l.canvas_course_id)
        WHERE l.user_id = :local
    UNION
    SELECT l.course_id FROM lectures l JOIN lectures t
        ON t.user_id = :target AND t.entry_id = l.entry_id WHERE l.user_id = :local
    UNION
    SELECT l.course_id FROM documents l JOIN documents t
        ON t.user_id = :target AND t.doc_id = l.doc_id WHERE l.user_id = :local
    UNION
    SELECT l.course_id FROM files l JOIN files t
        ON t.user_id = :target AND t.canvas_file_id = l.canvas_file_id WHERE l.user_id = :local
    ORDER BY 1"""


def claim_local_data(email: str, *, dry_run: bool) -> dict:
    """Move everything owned by `usr_local` (data from before login) to the user with this email.

    One transaction: any refusal or error changes nothing. Raises ClaimRefused when the email has
    no account (the student must sign in once first), when it is usr_local itself, or when the
    target already holds a course, lecture, document or file with the same key. Returns
    {"dry_run": bool, "email": str, "user_id": str, "moved": {table: count}}; with dry_run the
    counts are what would move and nothing is written.

    The foreign keys have no ON UPDATE CASCADE, so rows cannot simply be re-keyed. Instead each
    table is copied in foreign-key order, then the usr_local courses are deleted and ON DELETE
    CASCADE removes every remaining usr_local row. Chunks keep text, times and embeddings."""
    db.ensure_schema()
    wanted = email.lower()
    params = {"local": db.LOCAL_USER_ID}
    with Session(db.engine) as session, session.begin():
        target = session.execute(select(User.id).where(User.email == wanted)).scalar_one_or_none()
        if target is None:
            raise ClaimRefused(f"No account for {email}. Sign in once with that address first.")
        if target == db.LOCAL_USER_ID:
            raise ClaimRefused("That address is the placeholder local account. Use your own.")
        params["target"] = target

        # Lock the courses being moved so a sync or ingest cannot change them mid-copy.
        session.execute(
            text("SELECT 1 FROM courses WHERE user_id = :local FOR UPDATE"), params
        ).all()
        clashes = session.execute(text(_CLAIM_CONFLICT_SQL), params).scalars().all()
        if clashes:
            raise ClaimRefused(
                "Nothing moved. This account already has data with the same keys as: "
                + ", ".join(clashes)
            )

        moved: dict[str, int] = {}
        for table in CLAIM_TABLES:
            if dry_run:
                # Table names come from the fixed tuple above, never from input.
                count_sql = f"SELECT count(*) FROM {table} WHERE user_id = :local"  # noqa: S608
                moved[table] = session.execute(text(count_sql), params).scalar_one()
            else:
                moved[table] = session.execute(text(_CLAIM_COPY_SQL[table]), params).rowcount
        if not dry_run and moved["courses"]:
            # Cascades to lectures, files, documents, course_sites, chunks and doc_chunks.
            session.execute(delete(db.Course).where(db.Course.user_id == db.LOCAL_USER_ID))
    return {"dry_run": dry_run, "email": wanted, "user_id": target, "moved": moved}


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
