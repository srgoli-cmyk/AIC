"""Default model settings. Each test loads a private copy of app/config.py (so the shared
`settings` is untouched) with .env loading turned off and the environment set by the test."""

import importlib.util
from pathlib import Path

import dotenv
import pytest

from app import config

CONFIG_PATH = Path(config.__file__)


def _fresh_settings(monkeypatch, **env):
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)  # ignore the real .env
    for name in ("OPENROUTER_MODEL", "OPENROUTER_REASONING", "ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location("config_under_test", CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_answer_model_defaults_to_haiku_with_reasoning_off(monkeypatch):
    module = _fresh_settings(monkeypatch)
    assert module.settings.openrouter_model == "anthropic/claude-haiku-5.5"
    assert module.settings.openrouter_reasoning == "off"
    assert module.reasoning_payload(module.settings.openrouter_reasoning, "X") == {"enabled": False}


def test_explicitly_empty_reasoning_means_no_reasoning_key(monkeypatch):
    module = _fresh_settings(monkeypatch, OPENROUTER_REASONING="")
    assert module.settings.openrouter_reasoning == ""
    assert module.reasoning_payload(module.settings.openrouter_reasoning, "X") is None


def test_allowed_hosts_default_is_strict_loopback_only(monkeypatch):
    module = _fresh_settings(monkeypatch)
    assert module.settings.allowed_hosts == "localhost,127.0.0.1"
    assert module.allowed_host_list(module.settings.allowed_hosts) == ["localhost", "127.0.0.1"]


@pytest.mark.parametrize("value", ["high", "low"])
def test_set_reasoning_and_model_win_over_the_defaults(monkeypatch, value):
    module = _fresh_settings(
        monkeypatch, OPENROUTER_REASONING=value, OPENROUTER_MODEL="some/other-model"
    )
    assert module.settings.openrouter_reasoning == value
    assert module.settings.openrouter_model == "some/other-model"
