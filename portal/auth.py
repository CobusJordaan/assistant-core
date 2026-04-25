"""Portal authentication — session cookies for family users."""

import os
import time
import secrets
import logging

from fastapi import Request, Response
import bcrypt as _bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

logger = logging.getLogger("portal.auth")

SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "").strip()
SESSION_MAX_AGE = 12 * 3600  # 12 hours
COOKIE_NAME = "portal_session"

# Rate limiting
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60
_attempts: dict[str, dict] = {}


def check_rate_limit(ip: str) -> bool:
    entry = _attempts.get(ip)
    if not entry:
        return True
    if entry.get("locked_until", 0) > time.time():
        return False
    if entry["count"] >= MAX_ATTEMPTS:
        return False
    return True


def record_failed_attempt(ip: str):
    entry = _attempts.setdefault(ip, {"count": 0, "locked_until": 0})
    entry["count"] += 1
    if entry["count"] >= MAX_ATTEMPTS:
        entry["locked_until"] = time.time() + LOCKOUT_SECONDS


def clear_attempts(ip: str):
    _attempts.pop(ip, None)


def get_lockout_remaining(ip: str) -> int:
    entry = _attempts.get(ip)
    if not entry:
        return 0
    remaining = entry.get("locked_until", 0) - time.time()
    return max(0, int(remaining))


def verify_portal_user(username: str, password: str, admin_db) -> dict | None:
    """Verify portal user credentials. Returns user dict or None."""
    if not admin_db or not admin_db.available:
        return None
    user = admin_db.get_portal_user(username)
    if not user or not user.get("is_active"):
        return None
    try:
        if _bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
            return user
    except Exception as e:
        logger.error("Password check failed: %s", e)
    return None


def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(SECRET_KEY, salt="portal-session")


def create_portal_session(response: Response, user: dict) -> str:
    """Create session cookie. Returns CSRF token."""
    csrf_token = secrets.token_hex(32)
    payload = {
        "user": user["username"],
        "user_id": user["id"],
        "display_name": user["display_name"],
        "role": user["role"],
        "csrf": csrf_token,
        "last_active": time.time(),
    }
    cookie_value = _get_serializer().dumps(payload)
    response.set_cookie(
        COOKIE_NAME,
        cookie_value,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="strict",
        path="/portal",
    )
    return csrf_token


def get_portal_session(request: Request) -> dict | None:
    """Validate and return session data, or None."""
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    try:
        data = _get_serializer().loads(cookie, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not data.get("user") or not data.get("user_id"):
        return None
    # Check idle timeout (2 hours)
    if time.time() - data.get("last_active", 0) > 2 * 3600:
        return None
    return data


def refresh_portal_session(request: Request, response: Response):
    """Update last_active timestamp."""
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return
    try:
        data = _get_serializer().loads(cookie, max_age=SESSION_MAX_AGE)
        data["last_active"] = time.time()
        response.set_cookie(
            COOKIE_NAME,
            _get_serializer().dumps(data),
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="strict",
            path="/portal",
        )
    except (BadSignature, SignatureExpired):
        pass


def clear_portal_session(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/portal")
