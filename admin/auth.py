"""Authentication: bcrypt verification, signed session cookies, CSRF, rate limiting."""

import os
import time
import secrets
import logging

from fastapi import Request, Response
import bcrypt as _bcrypt
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

def is_configured(admin_db=None) -> bool:
    """Return True if admin credentials are set up (DB or env)."""
    if admin_db and admin_db.available:
        try:
            users = admin_db.list_users()
            if users:
                return True
        except Exception:
            pass
    return bool(ADMIN_PASSWORD_HASH) and bool(ADMIN_SECRET_KEY)


# ---------------------------------------------------------------------------
# Password verification
# ---------------------------------------------------------------------------

def verify_password(plain: str) -> bool:
    """Check plain password against stored bcrypt hash (env-based)."""
    if not ADMIN_PASSWORD_HASH:
        return False
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), ADMIN_PASSWORD_HASH.encode("utf-8"))
    except Exception:
        return False


def verify_credentials(username: str, password: str, admin_db=None) -> dict | None:
    """Verify login credentials. Try DB first, fall back to env.

    Returns {"user_id", "username", "role", "source", "password_changed_at"} or None.
    """
    # Try DB users first
    if admin_db and admin_db.available:
        try:
            user = admin_db.get_user_by_username(username)
            if user and user.get("is_active"):
                stored_hash = user["password_hash"]
                if _bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
                    return {
                        "user_id": user["id"],
                        "username": user["username"],
                        "role": user["role"],
                        "source": "db",
                        "password_changed_at": user.get("password_changed_at", ""),
                    }
        except Exception as e:
            logger.error("DB credential check failed: %s", e)

    # Fall back to env
    if username == ADMIN_USERNAME and verify_password(password):
        return {
            "user_id": None,
            "username": ADMIN_USERNAME,
            "role": "owner",
            "source": "env",
            "password_changed_at": "",
        }

    return None


# ---------------------------------------------------------------------------
# Session cookies
# ---------------------------------------------------------------------------

def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(ADMIN_SECRET_KEY, salt="admin-session")


def create_session_cookie(response: Response, user_info: dict | None = None) -> str:
    """Create a signed session cookie. Returns the CSRF token."""
    csrf_token = secrets.token_hex(32)
    payload = {
        "user": (user_info or {}).get("username", ADMIN_USERNAME),
        "csrf": csrf_token,
        "last_active": time.time(),
    }
    if user_info:
        payload["user_id"] = user_info.get("user_id")
        payload["role"] = user_info.get("role", "admin")
        payload["source"] = user_info.get("source", "env")
        payload["password_changed_at"] = user_info.get("password_changed_at", "")

    serializer = _get_serializer()
    cookie_value = serializer.dumps(payload)
    response.set_cookie(
        COOKIE_NAME,
        cookie_value,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="strict",
        path="/admin",
    )
    return csrf_token


def get_session(request: Request, admin_db=None) -> dict | None:
    """Read and validate the session cookie.

    Returns session dict or None. Checks:
    - Cookie signature and max age
    - Session inactivity timeout (8 hours)
    - Password change invalidation (DB users only)
    """
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    if not ADMIN_SECRET_KEY:
        return None
    serializer = _get_serializer()
    try:
        session = serializer.loads(cookie, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None

    # Inactivity timeout check
    last_active = session.get("last_active", 0)
    if last_active and (time.time() - last_active) > SESSION_MAX_AGE:
        return None

    # Password change invalidation (DB users only)
    if admin_db and admin_db.available and session.get("source") == "db":
        pw_changed = session.get("password_changed_at", "")
        user_id = session.get("user_id")
        if pw_changed and user_id:
            try:
                user = admin_db.get_user_by_id(user_id)
                if user and user.get("password_changed_at", "") > pw_changed:
                    return None
                if user and not user.get("is_active"):
                    return None
            except Exception:
                pass

    return session


def refresh_session_cookie(request: Request, response: Response):
    """Refresh the last_active timestamp in the session cookie (rolling window)."""
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie or not ADMIN_SECRET_KEY:
        return
    serializer = _get_serializer()
    try:
        session = serializer.loads(cookie, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return

    session["last_active"] = time.time()
    new_cookie = serializer.dumps(session)
    response.set_cookie(
        COOKIE_NAME,
        new_cookie,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="strict",
        path="/admin",
    )


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
