"""Single shared-password login for a public deployment.

Enabled only when FIP_APP_PASSWORD is set (local use stays password-free). Sessions are
HMAC-signed cookies valid for 30 days; failed logins are slowed down and rate-limited per IP.
"""
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

COOKIE = "fip_session"
MAX_AGE = 30 * 24 * 3600
PUBLIC = {"/login.html", "/api/login", "/app.css", "/i18n.js", "/logo.svg", "/favicon.ico",
          "/about", "/privacy", "/terms"}  # public pages required by Google OAuth verification
_failures: dict = defaultdict(deque)


def password() -> str:
    return os.getenv("FIP_APP_PASSWORD", "")


def enabled() -> bool:
    return bool(password())


def _secret() -> bytes:
    # FIP_SESSION_SECRET keeps sessions valid across restarts; otherwise a per-process secret.
    global _SECRET
    try:
        return _SECRET
    except NameError:
        _SECRET = (os.getenv("FIP_SESSION_SECRET") or secrets.token_hex(32)).encode()
        return _SECRET


def _sign(expiry: int) -> str:
    # The password is part of the key: changing it logs everybody out.
    key = _secret() + hashlib.sha256(password().encode()).digest()
    return hmac.new(key, str(expiry).encode(), hashlib.sha256).hexdigest()


def make_token() -> str:
    expiry = int(time.time()) + MAX_AGE
    return f"{expiry}.{_sign(expiry)}"


def valid(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp, sig = token.split(".", 1)
    return exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(sig, _sign(int(exp)))


def check_password(request: Request, candidate: str) -> bool | None:
    """True/False, or None when this client is temporarily blocked."""
    ip = request.client.host if request.client else "?"
    q = _failures[ip]
    now = time.time()
    while q and now - q[0] > 600:
        q.popleft()
    if len(q) >= 8:  # 8 failures in 10 minutes
        return None
    ok = hmac.compare_digest(candidate.encode(), password().encode())
    if not ok:
        q.append(now)
        time.sleep(1.0)
    return ok


def set_cookie(response, request: Request):
    response.set_cookie(COOKIE, make_token(), max_age=MAX_AGE, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")  # https behind Caddy (proxy headers)


async def middleware(request: Request, call_next):
    if not enabled() or request.url.path in PUBLIC or valid(request.cookies.get(COOKIE)):
        return await call_next(request)
    if request.url.path.startswith(("/api/", "/media/")):
        return JSONResponse({"detail": "Please sign in."}, status_code=401)
    return RedirectResponse("/login.html")
