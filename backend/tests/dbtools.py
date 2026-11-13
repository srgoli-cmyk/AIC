"""Test-database helpers. Importing this must not import the app's settings-dependent modules:
conftest uses it to choose the database BEFORE the app is loaded."""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TABLES = ("doc_chunks", "documents", "chunks", "files", "lectures", "courses")


def db_name(url: str) -> str:
    return make_url(url).database or ""


def with_db_name(url: str, name: str) -> str:
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def resolve_urls() -> tuple[str, str]:
    """Return (main_url, test_url). Same lookup order as app/config.py: an existing env var wins
    over .env."""
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    main = os.getenv("DATABASE_URL", "postgresql://aic:aic@localhost:5432/aic")
    test = os.getenv("TEST_DATABASE_URL") or with_db_name(main, "aic_test")
    return main, test


def truncate_all(conn) -> None:
    """Empty every table. Hard-fails unless connected to a database whose name ends in `_test`."""
    name = conn.execute(text("SELECT current_database()")).scalar()
    if not name.endswith("_test"):
        raise RuntimeError(f"refusing to truncate database {name!r}: name must end in '_test'")
    conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))  # noqa: S608


def create_database_if_missing(main_url: str, test_url: str) -> None:
    """The only place tests connect to the main database: CREATE DATABASE for the test one."""
    from app.db import normalize_url  # pure helper; imported late so env is already set

    name = db_name(test_url)
    if not name.endswith("_test") or not name.replace("_", "").isalnum():
        raise RuntimeError(f"test database name {name!r} must be alphanumeric and end in '_test'")
    admin = create_engine(normalize_url(main_url), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
            ).first()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{name}"'))  # name validated above
    finally:
        admin.dispose()
