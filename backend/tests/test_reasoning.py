"""The per-model `reasoning` setting: the value mapping and the payload sent to OpenRouter. The
study writer and check settings are tested in test_study.py. No network."""

import asyncio
import json
import logging
from dataclasses import replace

import httpx
import pytest

from app import answer, config
from app.config import reasoning_payload


@pytest.fixture(autouse=True)
def _fresh_warnings(monkeypatch):
    """The warning is once per setting name per process, so each test starts with none given."""
    monkeypatch.setattr(config, "_warned", set())


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", None),
        ("   ", None),
        ("off", {"enabled": False}),
        ("OFF", {"enabled": False}),
        ("  Off ", {"enabled": False}),
        ("minimal", {"effort": "minimal"}),
        ("low", {"effort": "low"}),
        ("medium", {"effort": "medium"}),
        ("high", {"effort": "high"}),
        ("xhigh", {"effort": "xhigh"}),
        (" XHigh\t", {"effort": "xhigh"}),
    ],
)
def test_reasoning_payload_maps_every_value(value, expected, caplog):
    with caplog.at_level(logging.WARNING, logger="aic"):
        assert reasoning_payload(value, "OPENROUTER_REASONING") == expected
    assert caplog.text == "", "a known value is not warned about"


def test_an_unknown_value_warns_once_with_the_setting_name_not_the_value(caplog):
    with caplog.at_level(logging.WARNING, logger="aic"):
        assert reasoning_payload("sideways", "OPENROUTER_CHECK_REASONING") is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "OPENROUTER_CHECK_REASONING" in warnings[0].getMessage()
    assert "sideways" not in caplog.text


def test_the_warning_comes_once_per_setting_name(caplog):
    with caplog.at_level(logging.WARNING, logger="aic"):
        for _ in range(3):
            assert reasoning_payload("bogus", "OPENROUTER_REASONING") is None
        assert reasoning_payload("bogus", "OPENROUTER_STUDY_REASONING") is None
        assert reasoning_payload("bogus", "OPENROUTER_STUDY_REASONING") is None
    names = [r.getMessage().split()[0] for r in caplog.records if r.levelno == logging.WARNING]
    assert names == ["OPENROUTER_REASONING", "OPENROUTER_STUDY_REASONING"]


# --- the payload of call_llm and stream_llm ---


def _with_settings(monkeypatch, **changes):
    monkeypatch.setattr(
        answer, "settings", replace(answer.settings, openrouter_api_key="k", **changes)
    )


def _call_body(monkeypatch, **kwargs) -> dict:
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: handler(request)
    )
    answer.call_llm([], **kwargs)
    return seen


def _stream_body(monkeypatch, **kwargs) -> dict:
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        data = json.dumps({"choices": [{"delta": {"content": "hi"}}]})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"data: {data}\n\ndata: [DONE]\n\n".encode(),
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        answer.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )

    async def run():
        return [piece async for piece in answer.stream_llm([], **kwargs)]

    assert asyncio.run(run()) == ["hi"]
    return seen


@pytest.mark.parametrize("send", [_call_body, _stream_body])
def test_no_reasoning_key_by_default(monkeypatch, send):
    _with_settings(monkeypatch, openrouter_reasoning="")
    assert "reasoning" not in send(monkeypatch)


@pytest.mark.parametrize("send", [_call_body, _stream_body])
@pytest.mark.parametrize(
    ("value", "expected"), [("off", {"enabled": False}), ("low", {"effort": "low"})]
)
def test_the_answer_setting_is_used_by_default(monkeypatch, send, value, expected):
    _with_settings(monkeypatch, openrouter_reasoning=value)
    assert send(monkeypatch)["reasoning"] == expected


@pytest.mark.parametrize("send", [_call_body, _stream_body])
def test_an_explicit_value_beats_the_setting(monkeypatch, send):
    _with_settings(monkeypatch, openrouter_reasoning="off")
    assert send(monkeypatch, reasoning="low")["reasoning"] == {"effort": "low"}


@pytest.mark.parametrize("send", [_call_body, _stream_body])
def test_an_explicit_empty_value_means_the_default(monkeypatch, send):
    _with_settings(monkeypatch, openrouter_reasoning="off")
    assert "reasoning" not in send(monkeypatch, reasoning="")


def test_an_unknown_setting_sends_no_key(monkeypatch):
    _with_settings(monkeypatch, openrouter_reasoning="bogus")
    assert "reasoning" not in _call_body(monkeypatch)
