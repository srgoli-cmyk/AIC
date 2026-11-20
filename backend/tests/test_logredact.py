"""The uvicorn access log keeps its lines but loses the values of secret query parameters."""

import logging

import pytest
from fastapi.testclient import TestClient

from app import logredact
from app.logredact import REDACTED, RedactAccessLog, redact_path
from app.main import app


def _record(*args) -> logging.LogRecord:
    return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s - %s %s %s %s", args, None)


def _filtered(*args) -> logging.LogRecord:
    record = _record(*args)
    assert RedactAccessLog().filter(record) is True  # a record is never dropped
    return record


# --- redact_path ---


def test_code_and_state_are_redacted_and_other_parts_stay():
    out = redact_path("/auth/google/callback?code=4%2Fabc&state=xyz&scope=email")
    assert out == f"/auth/google/callback?code={REDACTED}&state={REDACTED}&scope=email"


@pytest.mark.parametrize("name", ["code", "state", "nonce", "token", "error", "id_token"])
def test_every_listed_name_is_redacted(name):
    assert redact_path(f"/x?{name}=secret") == f"/x?{name}={REDACTED}"


def test_access_token_is_redacted():
    assert redact_path("/x?access_token=secret") == f"/x?access_token={REDACTED}"


def test_names_match_case_insensitively():
    assert redact_path("/x?CODE=a&State=b&Id_Token=c") == (
        f"/x?CODE={REDACTED}&State={REDACTED}&Id_Token={REDACTED}"
    )


def test_repeated_parameters_are_all_redacted():
    out = redact_path("/x?code=one&keep=1&code=two")
    assert out == f"/x?code={REDACTED}&keep=1&code={REDACTED}"


def test_encoded_names_are_decoded_before_matching():
    out = redact_path("/x?%63ode=secret&st%61te=secret2&to+ken=nope&id%5Ftoken=t")
    # "to+ken" decodes to "to ken", which is not a listed name; the others are.
    assert out == f"/x?%63ode={REDACTED}&st%61te={REDACTED}&to+ken=nope&id%5Ftoken={REDACTED}"


def test_empty_secret_value_is_still_marked():
    assert redact_path("/x?code=&a=b") == f"/x?code={REDACTED}&a=b"


def test_parameter_without_equals_has_no_value_to_hide():
    assert redact_path("/x?code&a=b") == "/x?code&a=b"


def test_a_value_with_an_equals_sign_is_hidden_whole():
    assert redact_path("/x?code=a=b=c&k=v") == f"/x?code={REDACTED}&k=v"


def test_similar_names_are_left_alone():
    path = "/x?codes=1&xcode=2&tokens=3&state_id=4"
    assert redact_path(path) == path


def test_path_without_query_is_unchanged():
    assert redact_path("/auth/me") == "/auth/me"
    assert redact_path("/") == "/"


def test_secret_words_in_the_path_are_not_touched():
    assert redact_path("/code=1/state=2?a=b") == "/code=1/state=2?a=b"


def test_empty_query_and_odd_separators():
    assert redact_path("/x?") == "/x?"
    assert redact_path("/x?&&code=1&") == f"/x?&&code={REDACTED}&"


def test_fragment_is_kept_apart():
    assert redact_path("/x?code=1#frag") == f"/x?code={REDACTED}#frag"


# --- the filter on records ---


def test_filter_rewrites_the_path_argument():
    record = _filtered("1.2.3.4:5", "GET", "/auth/extension/done?code=abc", "1.1", 200)
    assert record.args == ("1.2.3.4:5", "GET", f"/auth/extension/done?code={REDACTED}", "1.1", 200)
    line = record.getMessage()
    assert "abc" not in line
    assert line.endswith("200")


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("only one",),
        ("a", "b", "c"),
        ("a", "b", 3, "d", 5),  # path is not a string
        ("a", "b", None, "d", 5),
    ],
)
def test_odd_args_pass_through_unchanged(args):
    record = _record(*args) if args else logging.LogRecord("n", 20, "", 0, "plain", (), None)
    before = record.args
    assert RedactAccessLog().filter(record) is True
    assert record.args == before


def test_dict_args_pass_through_unchanged():
    record = logging.LogRecord("n", 20, "", 0, "%(a)s", ({"a": 1},), None)
    assert RedactAccessLog().filter(record) is True
    assert record.args == {"a": 1}


def test_install_is_idempotent():
    access = logging.getLogger("uvicorn.access")
    logredact.install()
    logredact.install()
    assert sum(isinstance(f, RedactAccessLog) for f in access.filters) == 1


# --- end to end ---


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def test_main_installs_the_filter_on_import():
    # app.main was imported at the top of this file.
    access = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, RedactAccessLog) for f in access.filters)


def test_logged_access_line_through_the_logger_is_redacted():
    # TestClient does not run uvicorn, so emit the record the way uvicorn's access logger does.
    # The filter installed by app.main sits on the logger, so the handler sees the redacted line.
    assert app  # imported: the filter is installed
    access = logging.getLogger("uvicorn.access")
    handler = _Capture()
    access.addHandler(handler)
    old_level = access.level
    access.setLevel(logging.INFO)
    try:
        with TestClient(app) as client:
            client.get("/health?code=zzz")  # a real request, to show the app itself still works
        access.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:1",
            "GET",
            "/auth/google/callback?code=SECRETCODE&state=SECRETSTATE&scope=a",
            "1.1",
            302,
        )
    finally:
        access.removeHandler(handler)
        access.setLevel(old_level)
    assert len(handler.lines) == 1
    line = handler.lines[0]
    assert "SECRETCODE" not in line
    assert "SECRETSTATE" not in line
    assert f"code={REDACTED}&state={REDACTED}&scope=a" in line
    assert line.endswith("302")
