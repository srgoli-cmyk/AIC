"""Redacts secrets from uvicorn's access log lines.

URLs such as `/auth/extension/done?code=...` and `/auth/google/callback?code=...&state=...` carry
one-time codes and OAuth state. The access log is useful (status codes, paths), so instead of
switching it off the values of the sensitive query parameters are replaced with REDACTED.
Everything else, the path and the other parameters, is left as it is.
"""

import logging
from urllib.parse import unquote_plus

REDACTED = "REDACTED"

# Matched case-insensitively, after percent-decoding the name (so `%63ode=` is caught too).
SENSITIVE_PARAMS = frozenset(
    {"code", "state", "nonce", "token", "error", "id_token", "access_token"}
)


def _param_name(pair: str) -> str:
    return unquote_plus(pair.split("=", 1)[0]).strip().casefold()


def redact_path(full_path: str) -> str:
    """`full_path` is the request target as uvicorn logs it (path plus optional `?query`). The
    value of each sensitive parameter becomes REDACTED; names and order are kept, and so are
    parameters without a value (`?code` stays `?code`)."""
    path, sep, query = full_path.partition("?")
    if not sep:
        return full_path
    # A fragment is never sent by browsers, but keep it out of the parsing if one shows up.
    query, hash_sep, fragment = query.partition("#")
    pairs = []
    for pair in query.split("&"):
        if "=" in pair and _param_name(pair) in SENSITIVE_PARAMS:
            pair = pair.split("=", 1)[0] + "=" + REDACTED
        pairs.append(pair)
    return path + "?" + "&".join(pairs) + hash_sep + fragment


class RedactAccessLog(logging.Filter):
    """Rewrites the path argument of a `uvicorn.access` record. Never drops a record and never
    raises: a record with unexpected args is passed on unchanged."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn: (client_addr, method, full_path, http_version, status_code)
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (*args[:2], redact_path(args[2]), *args[3:])
        return True


def install() -> None:
    """Attach the filter to the `uvicorn.access` logger once (importing the app twice is safe)."""
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactAccessLog) for f in access.filters):
        access.addFilter(RedactAccessLog())
