"""Test setup. Tests never touch the dev database: DATABASE_URL is pointed at a `*_test`
database before the app is imported (settings are read at import time)."""

import os

import dbtools
import netguard
import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.exc import InterfaceError, OperationalError

MAIN_URL, TEST_URL = dbtools.resolve_urls()
os.environ["DATABASE_URL"] = TEST_URL  # must happen before any `import app...`
# An existing variable (even an empty one) wins over .env, so no real key is ever loaded in tests.
os.environ["OPENROUTER_API_KEY"] = ""
# Cap libpq connection timeout in tests so unreachable mock hosts (127.0.0.1:1) fail fast on Windows
os.environ.setdefault("PGCONNECT_TIMEOUT", "2")

from app import db  # noqa: E402
from app.config import settings  # noqa: E402

# Explicit raise (not assert): survives `python -O`, and a failing assert would print `settings`.
if not dbtools.db_name(settings.database_url).endswith("_test"):
    raise SystemExit("test database name must end in _test")


def _prepare_test_database() -> None:
    """Create the test database if needed and apply the schema. Raises OperationalError or
    InterfaceError when Postgres is unreachable."""
    try:
        db.apply_schema()
    except (OperationalError, InterfaceError):
        # Probably the test database does not exist yet; try to create it.
        dbtools.create_database_if_missing(MAIN_URL, TEST_URL)
        db.apply_schema()


def pytest_sessionstart(session):
    """Prepare the database before any test runs. Without this, a test that is not marked
    `integration` but still calls the database (test_ask.py) fails on the first run after
    `aic_test` was dropped. If Postgres is unreachable, do nothing here: the `_test_database`
    fixture below fails the integration tests, and the others fail on their own database call."""
    try:
        _prepare_test_database()
    except (OperationalError, InterfaceError):
        pass


@pytest.fixture(scope="session")
def _test_database() -> None:
    """Make sure the test database exists with the schema. Fails (never skips) when Postgres
    is unreachable, so the gate cannot pass silently."""
    try:
        _prepare_test_database()
    except (OperationalError, InterfaceError):
        pytest.fail("Postgres is not reachable: run `make db` and retry.", pytrace=False)


@pytest.fixture(autouse=True)
def _clean_database(request):
    """Empty the test database before every test marked `integration`."""
    if request.node.get_closest_marker("integration") is not None:
        request.getfixturevalue("_test_database")
        with db.engine.begin() as conn:
            dbtools.truncate_all(conn)
            # Re-seed default test student to satisfy foreign keys and backwards-compatible tests
            conn.execute(
                sa_text(
                    "INSERT INTO users (id, google_sub, email, name, created_at, last_login_at) "
                    "VALUES ('usr_local', 'sub_local', 'local@ucsd.edu', "
                    "'Local Student', now(), now()) "
                    "ON CONFLICT (id) DO NOTHING"
                )
            )
    yield


def pytest_collection_modifyitems(config, items):
    """Tests marked `slow` (they load the real embedding model) only run with `-m slow`. Other
    `-m` expressions, such as `-m "not integration"`, leave them out too."""
    if "slow" in (config.getoption("markexpr") or "").replace("not slow", ""):
        return
    kept = [i for i in items if i.get_closest_marker("slow") is None]
    config.hook.pytest_deselected(items=[i for i in items if i not in kept])
    items[:] = kept


class FakeModel:
    """Stands in for the sentence-transformers model: deterministic one-hot vectors."""

    def __init__(self):
        self.calls = []  # (number of texts, kwargs) per encode call
        self.texts = []  # every text encoded, in order

    def encode(self, texts, **kwargs):
        self.calls.append((len(texts), kwargs))
        self.texts.extend(texts)
        vectors = []
        for t in texts:
            v = [0.0] * settings.embedding_dim
            v[sum(map(ord, t)) % settings.embedding_dim] = 1.0
            vectors.append(v)
        return vectors


@pytest.fixture(autouse=True)
def fake_model(request, monkeypatch):
    """Ingestion runs after every /sync. Use a cheap fake model so tests never load the real one
    (tests marked `slow` keep the real one)."""
    from app import embeddings

    model = FakeModel()
    if request.node.get_closest_marker("slow") is None:
        real_load = embeddings._load
        monkeypatch.setattr(embeddings, "_load", lambda: model)
        embeddings._load.real = real_load  # for tests of the lazy import itself
    return model


@pytest.fixture(autouse=True)
def _no_real_http(request, monkeypatch):
    """Any HTTP request that a test did not mock fails that test, and so does a connection to a
    non-local address. Tests marked `slow` load the real model, which downloads it."""
    if request.node.get_closest_marker("slow") is None:
        netguard.block_network(monkeypatch)


@pytest.fixture(autouse=True)
def _default_auth_user(request):
    """Authenticate requests automatically in tests as LOCAL_USER_ID.

    Existing test suites assume an active user context; this fixture supplies a mock
    UserRecord for `auth.require_user`. Tests testing raw unauthenticated status or multi-user
    isolation can mark themselves with `@pytest.mark.no_auth_override` or override
    `app.dependency_overrides[auth.require_user]` directly.
    """
    if request.node.get_closest_marker("no_auth_override") is not None:
        yield None
        return

    from app import auth, auth_db
    from app.main import app

    default_user = auth_db.UserRecord(
        id=db.LOCAL_USER_ID,
        email="student@ucsd.edu",
        name="Test Student",
    )
    app.dependency_overrides[auth.require_user] = lambda: default_user
    try:
        yield default_user
    finally:
        app.dependency_overrides.pop(auth.require_user, None)
