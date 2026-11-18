"""Google OAuth 2.0 (OpenID Connect) authentication with @ucsd.edu domain enforcement.

Provides server-side sign-in endpoints for both the web interface (HttpOnly session cookies)
and the Chrome extension (revocable bearer tokens via single-use exchange codes).
No passwords or Google OAuth access/refresh tokens are ever stored in the database.
"""

import base64
import hashlib
import hmac
import html
import json
import logging
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth_db
from .config import settings
from .schemas import AuthUser, ExtensionExchangeRequest, ExtensionExchangeResponse

log = logging.getLogger("aic")

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105

SESSION_COOKIE_NAME = "aic_session"
OAUTH_STATE_COOKIE = "aic_oauth_state"
OAUTH_STATE_MAX_AGE = 600  # 10 minutes to finish the Google sign-in flow


# --- PKCE and anti-CSRF state helpers ---


def generate_code_verifier() -> str:
    """Cryptographically random 256-bit URL-safe verifier for PKCE."""
    return secrets.token_urlsafe(32)


def generate_code_challenge(verifier: str) -> str:
    """SHA-256 base64url-encoded challenge (no padding) matching RFC 7636 S256."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _state_secret() -> bytes:
    # Use the Google client secret to sign state cookies; fall back to a fixed derivation if unset.
    seed = settings.google_client_secret or "aic-auth-state-signing-seed"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def sign_oauth_state(payload: dict) -> str:
    """HMAC-SHA256 signature over JSON state payload: `<b64_payload>.<b64_sig>`."""
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64_data = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = hmac.new(_state_secret(), b64_data.encode("ascii"), hashlib.sha256).digest()
    b64_sig = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{b64_data}.{b64_sig}"


def verify_oauth_state(token: str) -> dict | None:
    """Verify state cookie signature and age (<= 10 min). Returns payload dict or None."""
    if not token or "." not in token:
        return None
    b64_data, b64_sig = token.split(".", 1)
    try:
        expected_sig = hmac.new(_state_secret(), b64_data.encode("ascii"), hashlib.sha256).digest()
        actual_sig = base64.urlsafe_b64decode(b64_sig + "=" * (-len(b64_sig) % 4))
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        raw = base64.urlsafe_b64decode(b64_data + "=" * (-len(b64_data) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    ts = payload.get("ts")
    if not isinstance(ts, (int, float)) or (time.time() - ts) > OAUTH_STATE_MAX_AGE:
        return None
    return payload


def decode_jwt_payload(id_token: str) -> dict:
    """Extract claims dictionary from an unencrypted JWT without signature validation.

    The ID token was received directly from Google's TLS token endpoint authenticated with the
    client secret, so the transport channel validates its authenticity."""
    parts = id_token.split(".")
    if len(parts) < 2:
        raise ValueError("malformed ID token")
    payload_b64 = parts[1]
    try:
        raw = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        claims = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("malformed ID token payload") from exc
    if not isinstance(claims, dict):
        raise ValueError("ID token payload must be a JSON object")
    return claims


# --- Request user extraction ---


def extract_session_token(request: Request) -> str | None:
    """Extract raw session token from either Authorization header or aic_session cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer ") :].strip()
        if token:
            return token
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        return cookie_token.strip()
    return None


def get_current_user(request: Request) -> auth_db.UserRecord | None:
    """Resolve the authenticated student from the request, or None if unauthenticated."""
    token = extract_session_token(request)
    if not token:
        return None
    return auth_db.get_user_by_session_token(token)


# --- Endpoints ---


@router.get("/google/login")
def google_login(client: str = Query(default="web", pattern=r"^(web|extension)$")):
    """Redirect to Google's OAuth 2.0 authorization endpoint with PKCE and hd=ucsd.edu."""
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=503, detail="auth service not configured")

    state_nonce = secrets.token_urlsafe(16)
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)

    state_cookie_val = sign_oauth_state(
        {"state": state_nonce, "verifier": code_verifier, "client": client, "ts": time.time()}
    )

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.auth_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "hd": settings.allowed_email_domain,
        "state": state_nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "online",
        "prompt": "select_account",
    }
    redirect_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    response = RedirectResponse(url=redirect_url, status_code=302)
    response.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=state_cookie_val,
        max_age=OAUTH_STATE_MAX_AGE,
        path="/auth/google/callback",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
    return response


def _error_redirect(client: str, error_code: str) -> RedirectResponse:
    """Build redirect response to the web app or extension landing page on auth error."""
    if client == "extension":
        url = f"/auth/extension/done?error={error_code}"
    else:
        url = f"/app/?auth_error={error_code}"
    res = RedirectResponse(url=url, status_code=302)
    res.delete_cookie(key=OAUTH_STATE_COOKIE, path="/auth/google/callback")
    return res


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Handle Google OAuth callback, enforce UCSD claims, and establish student session."""
    raw_state_cookie = request.cookies.get(OAUTH_STATE_COOKIE, "")
    stored_state = verify_oauth_state(raw_state_cookie)
    client_type = stored_state.get("client", "web") if stored_state else "web"

    if error or not stored_state or not state or state != stored_state.get("state"):
        log.warning("oauth callback state mismatch or error from Google: %s", error or "bad_state")
        return _error_redirect(client_type, "login_failed")

    code_verifier = stored_state.get("verifier", "")
    if not code:
        return _error_redirect(client_type, "login_failed")

    # Exchange authorization code for tokens directly with Google
    token_data = {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": settings.auth_redirect_uri,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            token_res = await http.post(GOOGLE_TOKEN_URL, data=token_data)
            if token_res.status_code != 200:
                log.warning("google token exchange failed with status %d", token_res.status_code)
                return _error_redirect(client_type, "login_failed")
            tokens = token_res.json()
            id_token = tokens.get("id_token")
            if not id_token:
                return _error_redirect(client_type, "login_failed")
            claims = decode_jwt_payload(id_token)
    except Exception as exc:
        log.warning("failed to exchange code or decode ID token: %s", type(exc).__name__)
        return _error_redirect(client_type, "login_failed")

    # Strictly enforce @ucsd.edu domain and verification claims on the server
    email = str(claims.get("email", "")).strip().lower()
    email_verified = claims.get("email_verified") is True
    hd_claim = str(claims.get("hd", "")).strip().lower()
    allowed_domain = settings.allowed_email_domain.lower()

    if not email_verified or hd_claim != allowed_domain or not email.endswith(f"@{allowed_domain}"):
        log.info("rejected non-ucsd or unverified login: email=%s hd=%s", email, hd_claim)
        return _error_redirect(client_type, "ucsd_only")

    google_sub = str(claims.get("sub", "")).strip()
    if not google_sub:
        return _error_redirect(client_type, "login_failed")

    name = str(claims.get("name", "")).strip() or email.split("@")[0]
    user = auth_db.upsert_user(google_sub=google_sub, email=email, name=name)

    # Success: extension gets a short-lived one-time code; web gets a session cookie.
    if client_type == "extension":
        one_time_code = auth_db.create_extension_code(user.id, ttl_seconds=60)
        res = RedirectResponse(url=f"/auth/extension/done?code={one_time_code}", status_code=302)
    else:
        raw_session_token, _ = auth_db.create_session(
            user.id, kind="web", ttl_days=settings.session_ttl_days
        )
        res = RedirectResponse(url="/app/", status_code=302)
        res.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=raw_session_token,
            max_age=settings.session_ttl_days * 86400,
            path="/",
            httponly=True,
            samesite="lax",
            secure=settings.cookie_secure,
        )

    res.delete_cookie(key=OAUTH_STATE_COOKIE, path="/auth/google/callback")
    return res


@router.get("/extension/done", response_class=HTMLResponse)
def extension_done(code: str | None = None, error: str | None = None):
    """Clean landing page for the Chrome extension OAuth popup tab."""
    safe_code = html.escape(code or "")
    safe_error = html.escape(error or "")

    if safe_error == "ucsd_only":
        headline = "Sign in rejected"
        badge_class = "bad"
        message = (
            "Only verified <strong>@ucsd.edu</strong> Google accounts are allowed. "
            "Please return to the extension and select your UCSD email."
        )
    elif safe_error:
        headline = "Sign in failed"
        badge_class = "bad"
        message = "Authentication could not be completed. Please try again from the extension."
    elif safe_code:
        headline = "Signed in"
        badge_class = "ok"
        message = (
            "Your UCSD account is verified. The extension is completing your sign-in automatically."
        )
    else:
        headline = "Extension Auth"
        badge_class = ""
        message = "Ready to connect with the Chrome extension."

    html_content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{headline} · AI Lecture Search</title>
  <style>
    :root {{
      --ground: #0c0c10; --surface: #16161c; --line: #24242d;
      --ink: #f2f1ec; --muted: #9a99a6; --accent: #c8f560; --bad: #f57a60;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; min-height: 100vh; background: var(--ground); color: var(--ink);
      font-family: system-ui, -apple-system, sans-serif;
      display: flex; align-items: center; justify-content: center; padding: 24px;
    }}
    .card {{
      background: var(--surface); border: 1px solid var(--line);
      border-radius: 16px; padding: 32px; max-width: 420px; width: 100%; text-align: center;
    }}
    .status-dot {{
      display: inline-block; width: 12px; height: 12px;
      border-radius: 50%; background: var(--muted); margin-bottom: 12px;
    }}
    .status-dot.ok {{ background: var(--accent); }}
    .status-dot.bad {{ background: var(--bad); }}
    h1 {{ font-size: 20px; margin: 0 0 10px; font-weight: 600; }}
    p {{ color: var(--muted); font-size: 14px; line-height: 1.5; margin: 0 0 20px; }}
    .foot {{
      font-size: 12px; color: var(--muted); margin-top: 24px;
      border-top: 1px solid var(--line); padding-top: 16px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="status-dot {badge_class}"></div>
    <h1>{headline}</h1>
    <p>{message}</p>
    <div class="foot">You can close this tab if it doesn't close automatically.</div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html_content)


@router.post("/extension/exchange", response_model=ExtensionExchangeResponse)
def extension_exchange(body: ExtensionExchangeRequest) -> ExtensionExchangeResponse:
    """Exchange a short-lived single-use code for a persistent extension bearer token."""
    user = auth_db.consume_extension_code(body.code)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid or expired code")

    token, _ = auth_db.create_session(
        user.id, kind="extension", ttl_days=settings.extension_token_ttl_days
    )
    return ExtensionExchangeResponse(
        token=token, user=AuthUser(id=user.id, email=user.email, name=user.name)
    )


@router.get("/me", response_model=AuthUser)
def auth_me(request: Request) -> AuthUser:
    """Return the profile of the currently logged-in student (via cookie or bearer token)."""
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return AuthUser(id=user.id, email=user.email, name=user.name)


@router.post("/logout", status_code=204)
def auth_logout(request: Request) -> Response:
    """Revoke the current session and clear session cookies."""
    token = extract_session_token(request)
    if token:
        auth_db.revoke_session(token)
    res = Response(status_code=204)
    res.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return res
