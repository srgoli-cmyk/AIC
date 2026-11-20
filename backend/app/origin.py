"""Origin check for every request that changes something (anything but GET, HEAD, OPTIONS).

A browser attaches the `aic_session` cookie to a request from any page on the same site, and
every localhost port counts as one site. A POST with a simple body needs no CORS preflight, so
CORS alone does not stop a page on localhost:3000 from starting a paid call with the student's
cookie. This check does.

It is an ASGI middleware, not a route dependency, for two reasons: FastAPI reads and parses the
request body before it runs dependencies (the check must come first), and a new route is covered
without anyone remembering to add it.

A request is allowed when
  1. it has no `Origin` header (curl, the smoke test, a same-origin request that omits it), or
  2. `Origin` is this backend's own origin (scheme and Host of the request: the page at /app/), or
  3. it carries a valid `Authorization: Bearer` token. A page cannot add that header cross-origin
     without a CORS preflight, and only our extension passes the preflight (see main.py).
Otherwise: 403 {"detail": "origin not allowed"}.

Order with `require_user`: this runs first, so a foreign page gets 403 whether or not it is signed
in (no hint about the session), and nothing downstream runs. Rule 3 looks only at the Bearer
header, never the cookie, and `extract_session_token` also prefers Bearer over the cookie and
does not fall back to it. So a request is let through by Bearer only when `require_user` will
authenticate it by that same Bearer token; it cannot ride a cookie behind a Bearer header.
An invalid Bearer from a foreign Origin is 403 here; from no or our own Origin it passes and
`require_user` answers 401.
"""

from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from . import auth, auth_db, db

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# No cookie and no Bearer token here: the one-time code and the nonce are the protection, and the
# extension's own Origin must not be refused. Exact path, nothing else is exempt.
EXEMPT_PATHS = frozenset({"/auth/extension/exchange"})

REFUSED = {"detail": "origin not allowed"}


def own_origin(request: Request) -> str | None:
    """`scheme://host[:port]` as the browser sees us, from the request's scheme and Host."""
    host = request.headers.get("host")
    if not host:
        return None
    return f"{request.url.scheme}://{host}".lower()


async def _has_valid_bearer(request: Request) -> bool:
    token = auth.bearer_token(request)
    if not token:
        return False
    # The lookup is a synchronous database call: keep it off the event loop.
    return await run_in_threadpool(auth_db.get_user_by_session_token, token) is not None


async def origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True
    if origin.lower() == own_origin(request):
        return True
    return await _has_valid_bearer(request)


class OriginCheckMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] in SAFE_METHODS
            or scope["path"] in EXEMPT_PATHS
        ):
            await self.app(scope, receive, send)
            return
        # Request(scope) reads only headers here; the body stays unread.
        try:
            allowed = await origin_allowed(Request(scope))
        except (OperationalError, InterfaceError, db.SchemaBusy):
            allowed = None  # the Bearer lookup could not reach the database
        if allowed is None:
            response = JSONResponse(status_code=503, content={"detail": "database unavailable"})
        elif allowed:
            await self.app(scope, receive, send)
            return
        else:
            response = JSONResponse(status_code=403, content=REFUSED)
        await response(scope, receive, send)
