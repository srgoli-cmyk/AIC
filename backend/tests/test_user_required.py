"""The user is always explicit: no function that reads or writes per-user data may fall back to a
default user, and a background job writes under the user who started it."""

import inspect
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import answer, auth_db, coursesite, db, ingest, main, slidematch, slides, study

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / "contracts/fixtures/sync_sample.json").read_text())

MODULES = [db, ingest, slides, study, slidematch, coursesite, answer, main]


def _functions_of(module):
    """Every function defined in the module itself (public or private), not imported ones."""
    for name, fn in vars(module).items():
        if inspect.isfunction(fn) and fn.__module__ == module.__name__:
            yield name, fn


def test_no_function_has_a_default_for_user_id():
    offenders = []
    for module in MODULES:
        for name, fn in _functions_of(module):
            param = inspect.signature(fn).parameters.get("user_id")
            if param is not None and param.default is not inspect.Parameter.empty:
                offenders.append(f"{module.__name__}.{name}")
    assert offenders == []


def test_user_id_is_keyword_only_in_public_functions():
    # A positional user_id is easy to swap with another string argument (course_id, doc_id).
    # Private helpers (`_ensure_local_user`, `_facts_or_404`) may take it positionally.
    offenders = []
    for module in MODULES:
        for name, fn in _functions_of(module):
            if name.startswith("_"):
                continue
            param = inspect.signature(fn).parameters.get("user_id")
            if param is not None and param.kind is not inspect.Parameter.KEYWORD_ONLY:
                offenders.append(f"{module.__name__}.{name}")
    assert offenders == []


def test_routes_have_no_default_user_and_the_argument_dropping_helper_is_gone():
    assert not hasattr(main, "_DEFAULT_USER")
    assert not hasattr(main, "_call_fn")
    assert not hasattr(answer, "_call")
    assert not hasattr(study, "_call_fn")
    for name, fn in _functions_of(main):
        param = inspect.signature(fn).parameters.get("current_user")
        if param is not None:
            assert param.default is inspect.Parameter.empty, name


def test_the_job_wrappers_take_the_user():
    for fn in (main._sync_site_job, main._embed_document_job, main._store_canvas_document):
        assert "user_id" in inspect.signature(fn).parameters, fn.__name__


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_a_background_ingest_after_a_sync_writes_chunks_under_that_user_only():
    client = TestClient(main.app)
    user_b = auth_db.upsert_user(google_sub="sub-user-b", email="b@ucsd.edu", name="User B")
    token, _ = auth_db.create_session(user_b.id, kind="web", ttl_days=1)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.post("/sync", json=SAMPLE, headers=headers).status_code == 200

    # The TestClient has run the background ingest by now.
    with db.engine.connect() as conn:
        owners = {r[0] for r in conn.execute(text("SELECT DISTINCT user_id FROM chunks"))}
        indexed = conn.execute(
            text("SELECT user_id, indexed FROM courses WHERE course_id = :c"),
            {"c": "canvas-999999"},
        ).all()
    assert owners == {user_b.id}
    assert [(u, i) for u, i in indexed] == [(user_b.id, True)]
