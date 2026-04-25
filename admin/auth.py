"""Authentication: bcrypt verification, signed session cookies, CSRF, rate limiting."""

import os
import time
import secrets
import logging

from fastapi import Request, Response
from passlib.hash import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

logger = logging.getLogger("admin.auth")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "").strip()
SESSION_MAX_AGE = 8 * 3600  # 8 hours
COOKIE_NAME = "admin_session"

# ---------------------------------------------------------------------------
# Rate limiting (in-memory)
# ---------------------------------------------------------------------------

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60  # 15 minutes

_attempts: dict[str, dict] = {}  # ip -> {"count": int, "locked_until": float}


def check_rate_limit(ip: str) -> bool:
    """Return True if login is allowed, False if locked out."""
    entry = _attempts.get(ip)
    if not entry:
        return True
    if entry.get("locked_until", 0) > time.time():
        return False
    if entry["count"] >= MAX_ATTEMPTS:
        return False
    return True


def record_failed_attempt(ip: str):
    """Record a failed login attempt."""
    entry = _attempts.setdefault(ip, {"count": 0, "locked_until": 0})
    entry["count"] += 1
    if entry["count"] >= MAX_ATTEMPTS:
        entry["locked_until"] = time.time() + LOCKOUT_SECONDS
        logger.warning("Login locked out for IP %s (%d attempts)", ip, entry["count"])


def clear_attempts(ip: str):
    """Clear rate limit state on successful login."""
    _attempts.pop(ip, None)


def get_lockout_remaining(ip: str) -> int:
    """Return seconds remaining on lockout, or 0."""
    entry = _attempts.get(ip)
    if not entry:
        return 0
    remaining = entry.get("locked_until", 0) - time.time()
    return max(0, int(remaining))


# ---------------------------------------------------------------------------
# Configuration check
# ---------------------------------------------------------------------------

def is_configured() -> bool:
    """Return True if admin credentials are set up."""
    return bool(ADMIN_PASSWORD_HASH) and bool(ADMIN_SECRET_KEY)


# ---------------------------------------------------------------------------
# Password verification
# ---------------------------------------------------------------------------

def verify_password(plain: str) -> bool:
    """Check plain password against stored bcrypt hash."""
    if not ADMIN_PASSWORD_HASH:
        return False
    try:
        return bcrypt.verify(plain, ADMIN_PASSWORD_HASH)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Session cookies
# ---------------------------------------------------------------------------

def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(ADMIN_SECRET_KEY, salt="admin-session")


def create_session_cookie(response: Response) -> str:
    """Create a signed session cookie. Returns the CSRF token."""
    csrf_token = secrets.token_hex(32)
    serializer = _get_serializer()
    cookie_value = serializer.dumps({"user": ADMIN_USERNAME, "csrf": csrf_token})
    response.set_cookie(
        COOKIE_NAME,
        cookie_value,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="strict",
        path="/admin",
    )
    return csrf_token


def get_session(request: Request) -> dict | None:
    """Read and validate the session cookie. Returns {"user", "csrf"} or None."""
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    if not ADMIN_SECRET_KEY:
        return None
    serializer = _get_serializer()
    try:
        return serializer.loads(cookie, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def clear_session(response: Response):
    """Delete the session cookie."""
    response.delete_cookie(COOKIE_NAME, path="/admin")


# ---------------------------------------------------------------------------
# CSRF validation
# ---------------------------------------------------------------------------

def validate_csrf(session: dict, form_token: str) -> bool:
    """Compare CSRF token from form with the one in the session cookie."""
    expected = session.get("csrf", "")
    if not expected or not form_token:
        return False
    return secrets.compare_digest(expected, form_token)
