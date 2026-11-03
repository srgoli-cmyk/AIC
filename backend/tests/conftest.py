"""Test setup. Tests never touch the dev database: DATABASE_URL is pointed at a `*_test`
database before the app is imported (settings are read at import time)."""

import os

import dbtools
import pytest
from sqlalchemy.exc import InterfaceError, OperationalError

MAIN_URL, TEST_URL = dbtools.resolve_urls()
os.environ["DATABASE_URL"] = TEST_URL  # must happen before any `import app...`

from app import db  # noqa: E402
from app.config import settings  # noqa: E402

# Explicit raise (not assert): survives `python -O`, and a failing assert would print `settings`.
if not dbtools.db_name(settings.database_url).endswith("_test"):
    raise SystemExit("test database name must end in _test")


@pytest.fixture(scope="session")
def _test_database() -> None:
    """Create the test database if needed and apply the schema. Fails (never skips) when Postgres
    is unreachable, so the gate cannot pass silently."""
    try:
        try:
            db.apply_schema()
        except (OperationalError, InterfaceError):
            # Probably the test database does not exist yet; try to create it.
            dbtools.create_database_if_missing(MAIN_URL, TEST_URL)
            db.apply_schema()
    except (OperationalError, InterfaceError):
        pytest.fail("Postgres is not reachable: run `make db` and retry.", pytrace=False)


@pytest.fixture(autouse=True)
def _clean_database(request):
    """Empty the test database before every test marked `integration`."""
    if request.node.get_closest_marker("integration") is not None:
        request.getfixturevalue("_test_database")
        with db.engine.begin() as conn:
            dbtools.truncate_all(conn)
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

    def encode(self, texts, **kwargs):
        self.calls.append((len(texts), kwargs))
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
