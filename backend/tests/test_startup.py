"""The embedding model is loaded at startup, and a failed load never stops the app."""

import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import answer, embeddings
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / "contracts/fixtures/sync_sample.json").read_text())
LEAK = "MODEL-ERROR-TEXT"  # must never reach the log


def _broken_load():
    # The real _load drops the message of the underlying error; mimic that.
    raise embeddings.ModelLoadError("OSError")


def test_lifespan_calls_warm_up(monkeypatch):
    calls = []
    monkeypatch.setattr(embeddings, "warm_up", lambda: calls.append(1))
    with TestClient(app) as c:
        assert calls == [1]
        assert c.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("error", [embeddings.ModelLoadError("OSError"), RuntimeError(LEAK)])
def test_failed_warm_up_is_logged_by_type_and_the_app_starts(monkeypatch, caplog, error):
    def failing():
        raise error

    monkeypatch.setattr(embeddings, "warm_up", failing)
    with caplog.at_level(logging.DEBUG, logger="aic"), TestClient(app) as c:
        assert c.get("/health").json() == {"status": "ok"}
    assert "warm-up failed" in caplog.text
    assert LEAK not in caplog.text


def test_warm_up_loads_the_model_once(monkeypatch):
    loads = []
    monkeypatch.setattr(embeddings, "_load", lambda: loads.append(1))
    embeddings.warm_up()
    assert loads == [1]


def test_embed_query_still_raises_after_a_failed_warm_up(monkeypatch):
    monkeypatch.setattr(embeddings, "_load", _broken_load)
    with TestClient(app) as c:  # the lifespan's warm-up fails here
        assert c.get("/health").status_code == 200
    with pytest.raises(embeddings.ModelLoadError):
        embeddings.embed_query("anything?")


@pytest.mark.integration
def test_ask_is_503_after_a_failed_warm_up(monkeypatch):
    cid = "canvas-999999"
    TestClient(app).post("/sync", json=SAMPLE)  # indexed with the fake model
    monkeypatch.setattr(answer, "settings", replace(answer.settings, openrouter_api_key="sk-fake"))
    monkeypatch.setattr(embeddings, "_load", _broken_load)
    with TestClient(app) as c:
        r = c.post("/ask", json={"course_id": cid, "question": "anything?"})
    assert r.status_code == 503
    assert r.json() == {"detail": "search service unavailable"}
