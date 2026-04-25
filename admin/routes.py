"""Admin dashboard routes — login, dashboard, logs, actions, API."""

import asyncio
import logging
import os
import threading

import bcrypt as _bcrypt
from fastapi import APIRouter, Request, Response, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from admin.auth import (
    is_configured, verify_credentials, create_session_cookie, get_session,
    clear_session, validate_csrf, check_rate_limit, record_failed_attempt,
    clear_attempts, get_lockout_remaining, refresh_session_cookie,
    ADMIN_USERNAME,
)
from admin.system_info import (
    get_cpu_info, get_cpu_temp, get_memory_info, get_disk_info,
    get_uptime, get_gpu_info, get_db_info, get_version_info,
    get_system_sensors,
)
from admin.git_manager import get_status as git_status, pull as git_pull
from admin.service_manager import (
    get_all_statuses, restart_service, restart_container, get_container_detail,
)

logger = logging.getLogger("admin.routes")

DATABASE_PATH = os.getenv("DATABASE_PATH", "memory.db")
ADMIN_BACKUP_DIR = os.getenv("ADMIN_BACKUP_DIR", "/opt/ai-assistant/data/backups")

OPEN_WEBUI_HOST = os.getenv("OPEN_WEBUI_HOST", "172.18.2.195")
OPEN_WEBUI_PORT = os.getenv("OPEN_WEBUI_PORT", "3000")

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_admin_db(request: Request):
    return getattr(request.app.state, "admin_db", None)


def _require_session(request: Request):
    """Return session dict or None (caller should redirect)."""
    admin_db = _get_admin_db(request)
    if not is_configured(admin_db):
        return None
    return get_session(request, admin_db)


def _require_owner(session: dict) -> bool:
    """Check if session user has owner role."""
    return session.get("role") == "owner"


def _collect_status() -> dict:
    """Gather all system status data."""
    return {
        "cpu": get_cpu_info(),
        "cpu_temp": get_cpu_temp(),
        "memory": get_memory_info(),
        "disks": get_disk_info(),
        "gpu": get_gpu_info(),
        "services": get_all_statuses(),
        "git": git_status(),
        "databases": get_db_info(DATABASE_PATH),
        "uptime": get_uptime(),
        "version": get_version_info(),
        "sensors": get_system_sensors(),
    }


def _check_csrf(request: Request, session: dict, csrf_token: str) -> JSONResponse | None:
    """Return error response if CSRF fails, else None."""
    if not validate_csrf(session, csrf_token):
        return JSONResponse(status_code=403, content={"success": False, "message": "CSRF validation failed"})
    return None


def _audit(request: Request, session: dict, action: str, target: str = "",
           result: str = "", details: str = ""):
    """Log an admin action to DB and file."""
    from admin.security import log_admin_action
    admin_db = _get_admin_db(request)
    log_admin_action(
        user=session.get("user", "admin"),
        action=action,
        result=result,
        ip_address=_get_client_ip(request),
        admin_db=admin_db,
        user_id=session.get("user_id"),
        target=target,
        user_agent=request.headers.get("User-Agent", ""),
        details=details,
    )


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    admin_db = _get_admin_db(request)
    if not is_configured(admin_db):
        return templates.TemplateResponse(request, "admin/login.html", {
            "error": "Admin not configured. Set ADMIN_PASSWORD_HASH and ADMIN_SECRET_KEY in .env",
            "lockout_remaining": 0,
            "info": None,
        })

    # Already logged in?
    session = get_session(request, admin_db)
    if session:
        return RedirectResponse("/admin", status_code=302)

    ip = _get_client_ip(request)
    lockout = get_lockout_remaining(ip)

    return templates.TemplateResponse(request, "admin/login.html", {
        "error": None,
        "lockout_remaining": lockout,
        "info": None,
    })


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    admin_db = _get_admin_db(request)
    if not is_configured(admin_db):
        return templates.TemplateResponse(request, "admin/login.html", {
            "error": "Admin not configured.",
            "lockout_remaining": 0,
            "info": None,
        })

    ip = _get_client_ip(request)

    # Rate limit check
    if not check_rate_limit(ip):
        lockout = get_lockout_remaining(ip)
        return templates.TemplateResponse(request, "admin/login.html", {
            "error": None,
            "lockout_remaining": lockout,
            "info": None,
        })

    # Verify credentials (DB first, then env fallback)
    user_info = verify_credentials(username, password, admin_db)
    if not user_info:
        record_failed_attempt(ip)
        # Record in DB too
        if admin_db and admin_db.available:
            admin_db.record_attempt(username, ip, False)
        await asyncio.sleep(1)  # Slow down brute force
        lockout = get_lockout_remaining(ip)
        return templates.TemplateResponse(request, "admin/login.html", {
            "error": "Invalid username or password.",
            "lockout_remaining": lockout,
            "info": None,
        })

    # Success
    clear_attempts(ip)
    if admin_db and admin_db.available:
        admin_db.record_attempt(username, ip, True)
        if user_info.get("user_id"):
            admin_db.record_login(user_info["user_id"])

    response = RedirectResponse("/admin", status_code=302)
    create_session_cookie(response, user_info)

    from admin.security import log_admin_action
    log_admin_action(
        user=username, action="login", result="SUCCESS",
        ip_address=ip, admin_db=admin_db,
        user_id=user_info.get("user_id"),
        user_agent=request.headers.get("User-Agent", ""),
    )
    logger.info("Admin login from %s (source=%s)", ip, user_info.get("source"))
    return response


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse("/admin/login", status_code=302)
    clear_session(response)
    return response


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request):
    session = _require_session(request)
    if not session:
        return RedirectResponse("/admin/login", status_code=302)

    status = _collect_status()
    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "active_page": "dashboard",
        "status": status,
        "csrf_token": session.get("csrf", ""),
    })


# ---------------------------------------------------------------------------
# Logs page
# ---------------------------------------------------------------------------

@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    session = _require_session(request)
    if not session:
        return RedirectResponse("/admin/login", status_code=302)

    return templates.TemplateResponse(request, "admin/logs.html", {
        "active_page": "logs",
        "csrf_token": session.get("csrf", ""),
    })


# ---------------------------------------------------------------------------
# Open WebUI management page
# ---------------------------------------------------------------------------

@router.get("/open-webui", response_class=HTMLResponse)
async def open_webui_page(request: Request):
    session = _require_session(request)
    if not session:
        return RedirectResponse("/admin/login", status_code=302)

    detail = get_container_detail("open-webui")
    port = detail.get("host_port") or OPEN_WEBUI_PORT
    webui_url = f"http://{OPEN_WEBUI_HOST}:{port}"

    return templates.TemplateResponse(request, "admin/open-webui.html", {
        "active_page": "open-webui",
        "container": detail,
        "webui_url": webui_url,
        "csrf_token": session.get("csrf", ""),
    })


@router.get("/api/open-webui/status")
async def api_open_webui_status(request: Request):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    detail = get_container_detail("open-webui")
    port = detail.get("host_port") or OPEN_WEBUI_PORT
    return {**detail, "webui_url": f"http://{OPEN_WEBUI_HOST}:{port}"}


@router.get("/api/open-webui/health")
async def api_open_webui_health(request: Request):
    """Check Open WebUI HTTP health (best-effort)."""
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    detail = get_container_detail("open-webui")
    port = detail.get("host_port") or OPEN_WEBUI_PORT
    url = f"http://{OPEN_WEBUI_HOST}:{port}"

    healthy = False
    if detail.get("running"):
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url)
                healthy = resp.status_code < 500
        except Exception:
            healthy = False

    return {"running": detail.get("running", False), "healthy": healthy, "url": url}


# ---------------------------------------------------------------------------
# Users page
# ---------------------------------------------------------------------------

@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    session = _require_session(request)
    if not session:
        return RedirectResponse("/admin/login", status_code=302)

    admin_db = _get_admin_db(request)
    users = admin_db.list_users() if admin_db and admin_db.available else []
    owner_count = admin_db.count_owners() if admin_db and admin_db.available else 0

    return templates.TemplateResponse(request, "admin/users.html", {
        "active_page": "users",
        "users": users,
        "owner_count": owner_count,
        "session_role": session.get("role", "admin"),
        "csrf_token": session.get("csrf", ""),
    })


# ---------------------------------------------------------------------------
# API Keys page
# ---------------------------------------------------------------------------

@router.get("/api-keys", response_class=HTMLResponse)
async def api_keys_page(request: Request):
    session = _require_session(request)
    if not session:
        return RedirectResponse("/admin/login", status_code=302)

    admin_db = _get_admin_db(request)
    keys = admin_db.list_api_keys() if admin_db and admin_db.available else []

    return templates.TemplateResponse(request, "admin/api-keys.html", {
        "active_page": "api-keys",
        "api_keys": keys,
        "csrf_token": session.get("csrf", ""),
    })


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    session = _require_session(request)
    if not session:
        return RedirectResponse("/admin/login", status_code=302)

    admin_db = _get_admin_db(request)
    settings = admin_db.get_all_settings() if admin_db and admin_db.available else []

    db_health = None
    if admin_db and admin_db.available:
        db_health = {
            "size": admin_db.get_db_size(),
            "tables": admin_db.get_table_counts(),
            "wal_mode": admin_db.get_wal_mode(),
        }

    # Gather backups list
    import re as _re
    from pathlib import Path as _Path
    backup_dir = _Path(ADMIN_BACKUP_DIR)
    backups = []
    if backup_dir.is_dir():
        for f in sorted(backup_dir.glob("admin_*.db"), reverse=True):
            try:
                stat = f.stat()
                m = _re.match(r"admin_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2})\.db", f.name)
                created = f"{m.group(1)} {m.group(2).replace('-', ':')}" if m else ""
                backups.append({
                    "filename": f.name,
                    "size_kb": round(stat.st_size / 1024, 1),
                    "created": created,
                })
            except OSError:
                pass

    return templates.TemplateResponse(request, "admin/settings.html", {
        "active_page": "settings",
        "settings": settings,
        "db_health": db_health,
        "backups": backups,
        "session_role": session.get("role", "admin"),
        "csrf_token": session.get("csrf", ""),
    })


# ---------------------------------------------------------------------------
# Audit log page
# ---------------------------------------------------------------------------

@router.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request):
    session = _require_session(request)
    if not session:
        return RedirectResponse("/admin/login", status_code=302)

    return templates.TemplateResponse(request, "admin/audit.html", {
        "active_page": "audit",
        "csrf_token": session.get("csrf", ""),
    })


# ---------------------------------------------------------------------------
# API: status JSON (auto-refresh)
# ---------------------------------------------------------------------------

@router.get("/api/status")
async def api_status(request: Request):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    data = _collect_status()
    return data


# ---------------------------------------------------------------------------
# API: health (unauthenticated)
# ---------------------------------------------------------------------------

@router.get("/api/health")
async def api_health():
    """Unauthenticated health overview."""
    services = get_all_statuses()
    gpu = get_gpu_info()
    disks = get_disk_info()

    svc_ok = all(s["active"] for s in services.get("services", {}).values())
    ctr_ok = all(c["running"] for c in services.get("containers", {}).values())
    disk_ok = all(not d.get("critical") for d in disks)

    db_path = os.getenv("DATABASE_PATH", "memory.db")
    db_ok = os.path.exists(db_path)

    return {
        "assistant_core": svc_ok,
        "ollama": services.get("services", {}).get("ollama", {}).get("active", False),
        "gpu_available": gpu is not None,
        "db_ok": db_ok,
        "disk_ok": disk_ok,
    }


# ---------------------------------------------------------------------------
# API: DB health
# ---------------------------------------------------------------------------

@router.get("/api/db-health")
async def api_db_health(request: Request):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    admin_db = _get_admin_db(request)
    if not admin_db or not admin_db.available:
        return {"available": False}

    return {
        "available": True,
        "size": admin_db.get_db_size(),
        "tables": admin_db.get_table_counts(),
        "wal_mode": admin_db.get_wal_mode(),
        "path": admin_db._db_path,
    }


@router.post("/api/db-backup")
async def api_db_backup(request: Request, csrf_token: str = Form(...)):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    if not _require_owner(session):
        return JSONResponse(status_code=403, content={"success": False, "message": "Owner role required"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    admin_db = _get_admin_db(request)
    if not admin_db or not admin_db.available:
        return JSONResponse(status_code=500, content={"success": False, "message": "Database not available"})

    try:
        backup_path = admin_db.backup(ADMIN_BACKUP_DIR)
        _audit(request, session, "db_backup", target=backup_path, result="SUCCESS")
        return {"success": True, "path": backup_path}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@router.get("/api/backups")
async def api_list_backups(request: Request):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    import re
    from pathlib import Path

    backup_dir = Path(ADMIN_BACKUP_DIR)
    backups = []
    if backup_dir.is_dir():
        for f in sorted(backup_dir.glob("admin_*.db"), reverse=True):
            try:
                stat = f.stat()
                # Parse date from filename: admin_YYYY-MM-DD_HH-MM.db
                m = re.match(r"admin_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2})\.db", f.name)
                created = m.group(1).replace("_", " ").replace("-", "-", 2) if m else ""
                backups.append({
                    "filename": f.name,
                    "size_kb": round(stat.st_size / 1024, 1),
                    "created": created,
                })
            except OSError:
                pass

    return {"backups": backups}


@router.delete("/api/backups/{filename}")
async def api_delete_backup(request: Request, filename: str):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    if not _require_owner(session):
        return JSONResponse(status_code=403, content={"success": False, "message": "Owner role required"})

    csrf_token = request.query_params.get("csrf_token", "")
    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    import re
    from pathlib import Path

    # Validate filename format to prevent path traversal
    if not re.match(r"^admin_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.db$", filename):
        return JSONResponse(status_code=400, content={"success": False, "message": "Invalid backup filename"})

    backup_path = Path(ADMIN_BACKUP_DIR) / filename
    if not backup_path.exists():
        return JSONResponse(status_code=404, content={"success": False, "message": "Backup not found"})

    try:
        backup_path.unlink()
        _audit(request, session, "delete_backup", target=filename, result="SUCCESS")
        return {"success": True}
    except OSError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


# ---------------------------------------------------------------------------
# API: logs
# ---------------------------------------------------------------------------

_LOG_COMMANDS = {
    "assistant-core": ["/usr/bin/journalctl", "-u", "assistant-core", "--no-pager", "-n"],
    "ollama": ["/usr/bin/journalctl", "-u", "ollama", "--no-pager", "-n"],
    "open-webui": ["/usr/bin/docker", "logs", "--tail"],
}


@router.get("/api/logs/{source}")
async def api_logs(request: Request, source: str, lines: int = 200):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    if source not in _LOG_COMMANDS:
        return JSONResponse(status_code=400, content={"error": "Invalid log source"})

    lines = min(lines, 500)  # Cap at 500

    from admin.security import run_command

    base_cmd = _LOG_COMMANDS[source]
    if source == "open-webui":
        cmd = base_cmd + [str(lines), "open-webui"]
    else:
        cmd = base_cmd + [str(lines)]

    result = run_command(cmd, timeout=10)
    return {"source": source, "lines": lines, "output": result["output"]}


# ---------------------------------------------------------------------------
# API: Users CRUD
# ---------------------------------------------------------------------------

@router.post("/api/users")
async def api_create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("admin"),
    csrf_token: str = Form(...),
):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    if not _require_owner(session):
        return JSONResponse(status_code=403, content={"success": False, "message": "Owner role required"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    if role not in ("owner", "admin", "viewer"):
        return JSONResponse(status_code=400, content={"success": False, "message": "Invalid role"})

    admin_db = _get_admin_db(request)
    if not admin_db or not admin_db.available:
        return JSONResponse(status_code=500, content={"success": False, "message": "Database not available"})

    # Check if user already exists
    existing = admin_db.get_user_by_username(username)
    if existing:
        return JSONResponse(status_code=400, content={"success": False, "message": "Username already exists"})

    pw_hash = _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
    user_id = admin_db.create_user(username, pw_hash, role)

    _audit(request, session, "create_user", target=username, result="SUCCESS",
           details=f"role={role}")

    return {"success": True, "user_id": user_id}


@router.post("/api/users/{user_id}/password")
async def api_reset_password(
    request: Request,
    user_id: int,
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    # Owners can reset anyone; others can only reset themselves
    if not _require_owner(session) and session.get("user_id") != user_id:
        return JSONResponse(status_code=403, content={"success": False, "message": "Owner role required"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    admin_db = _get_admin_db(request)
    if not admin_db or not admin_db.available:
        return JSONResponse(status_code=500, content={"success": False, "message": "Database not available"})

    user = admin_db.get_user_by_id(user_id)
    if not user:
        return JSONResponse(status_code=404, content={"success": False, "message": "User not found"})

    pw_hash = _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
    admin_db.update_user_password(user_id, pw_hash)

    _audit(request, session, "reset_password", target=user["username"], result="SUCCESS")

    return {"success": True}


@router.post("/api/users/{user_id}/toggle")
async def api_toggle_user(
    request: Request,
    user_id: int,
    csrf_token: str = Form(...),
):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    if not _require_owner(session):
        return JSONResponse(status_code=403, content={"success": False, "message": "Owner role required"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    admin_db = _get_admin_db(request)
    if not admin_db or not admin_db.available:
        return JSONResponse(status_code=500, content={"success": False, "message": "Database not available"})

    user = admin_db.get_user_by_id(user_id)
    if not user:
        return JSONResponse(status_code=404, content={"success": False, "message": "User not found"})

    # Don't deactivate last owner
    if user["is_active"] and user["role"] == "owner" and admin_db.count_owners() <= 1:
        return JSONResponse(status_code=400, content={"success": False, "message": "Cannot deactivate the last owner"})

    if user["is_active"]:
        admin_db.deactivate_user(user_id)
        action = "deactivate_user"
    else:
        admin_db.activate_user(user_id)
        action = "activate_user"

    _audit(request, session, action, target=user["username"], result="SUCCESS")

    return {"success": True, "is_active": not user["is_active"]}


@router.post("/api/users/{user_id}/role")
async def api_change_role(
    request: Request,
    user_id: int,
    role: str = Form(...),
    csrf_token: str = Form(...),
):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    if not _require_owner(session):
        return JSONResponse(status_code=403, content={"success": False, "message": "Owner role required"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    if role not in ("owner", "admin", "viewer"):
        return JSONResponse(status_code=400, content={"success": False, "message": "Invalid role"})

    admin_db = _get_admin_db(request)
    if not admin_db or not admin_db.available:
        return JSONResponse(status_code=500, content={"success": False, "message": "Database not available"})

    user = admin_db.get_user_by_id(user_id)
    if not user:
        return JSONResponse(status_code=404, content={"success": False, "message": "User not found"})

    # Protect last owner from demotion
    if user["role"] == "owner" and role != "owner" and admin_db.count_owners() <= 1:
        return JSONResponse(status_code=400, content={"success": False, "message": "Cannot demote the last owner"})

    admin_db.update_user_role(user_id, role)
    _audit(request, session, "change_role", target=user["username"], result="SUCCESS",
           details=f"{user['role']} -> {role}")

    return {"success": True}


@router.delete("/api/users/{user_id}")
async def api_delete_user(
    request: Request,
    user_id: int,
):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    if not _require_owner(session):
        return JSONResponse(status_code=403, content={"success": False, "message": "Owner role required"})

    # CSRF from query param for DELETE
    csrf_token = request.query_params.get("csrf_token", "")
    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    admin_db = _get_admin_db(request)
    if not admin_db or not admin_db.available:
        return JSONResponse(status_code=500, content={"success": False, "message": "Database not available"})

    user = admin_db.get_user_by_id(user_id)
    if not user:
        return JSONResponse(status_code=404, content={"success": False, "message": "User not found"})

    # Protect last owner
    if user["role"] == "owner" and admin_db.count_owners() <= 1:
        return JSONResponse(status_code=400, content={"success": False, "message": "Cannot delete the last owner"})

    # Don't allow self-deletion
    if session.get("user_id") == user_id:
        return JSONResponse(status_code=400, content={"success": False, "message": "Cannot delete your own account"})

    admin_db.delete_user(user_id)
    _audit(request, session, "delete_user", target=user["username"], result="SUCCESS")

    return {"success": True}


# ---------------------------------------------------------------------------
# API: API Keys CRUD
# ---------------------------------------------------------------------------

@router.post("/api/api-keys")
async def api_create_key(
    request: Request,
    name: str = Form(...),
    scope: str = Form(""),
    csrf_token: str = Form(...),
):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    admin_db = _get_admin_db(request)
    if not admin_db or not admin_db.available:
        return JSONResponse(status_code=500, content={"success": False, "message": "Database not available"})

    raw_key, key_id = admin_db.create_api_key(name, scope, session.get("user", "admin"))
    _audit(request, session, "create_api_key", target=name, result="SUCCESS")

    return {"success": True, "key_id": key_id, "raw_key": raw_key}


@router.delete("/api/api-keys/{key_id}")
async def api_revoke_key(request: Request, key_id: int):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    csrf_token = request.query_params.get("csrf_token", "")
    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    admin_db = _get_admin_db(request)
    if not admin_db or not admin_db.available:
        return JSONResponse(status_code=500, content={"success": False, "message": "Database not available"})

    admin_db.revoke_api_key(key_id)
    _audit(request, session, "revoke_api_key", target=str(key_id), result="SUCCESS")

    return {"success": True}


# ---------------------------------------------------------------------------
# API: Settings CRUD
# ---------------------------------------------------------------------------

@router.post("/api/settings")
async def api_upsert_setting(
    request: Request,
    key: str = Form(...),
    value: str = Form(...),
    value_type: str = Form("string"),
    is_secret: int = Form(0),
    csrf_token: str = Form(...),
):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    if value_type not in ("string", "int", "bool", "json"):
        return JSONResponse(status_code=400, content={"success": False, "message": "Invalid value_type"})

    admin_db = _get_admin_db(request)
    if not admin_db or not admin_db.available:
        return JSONResponse(status_code=500, content={"success": False, "message": "Database not available"})

    admin_db.set_setting(key, value, value_type, is_secret, session.get("user", "admin"))
    _audit(request, session, "update_setting", target=key, result="SUCCESS")

    return {"success": True}


@router.delete("/api/settings/{key}")
async def api_delete_setting(request: Request, key: str):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    csrf_token = request.query_params.get("csrf_token", "")
    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    admin_db = _get_admin_db(request)
    if not admin_db or not admin_db.available:
        return JSONResponse(status_code=500, content={"success": False, "message": "Database not available"})

    admin_db.delete_setting(key)
    _audit(request, session, "delete_setting", target=key, result="SUCCESS")

    return {"success": True}


# ---------------------------------------------------------------------------
# API: Audit log
# ---------------------------------------------------------------------------

@router.get("/api/audit")
async def api_audit_log(
    request: Request,
    page: int = Query(1, ge=1),
    user: str = Query(""),
    action: str = Query(""),
):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    admin_db = _get_admin_db(request)
    if not admin_db or not admin_db.available:
        return {"entries": [], "total": 0, "page": 1, "pages": 1}

    limit = 50
    offset = (page - 1) * limit
    entries = admin_db.get_audit_log(limit, offset, user, action)
    total = admin_db.get_audit_log_count(user, action)
    pages = max(1, (total + limit - 1) // limit)

    return {"entries": entries, "total": total, "page": page, "pages": pages}


# ---------------------------------------------------------------------------
# Actions (POST, CSRF-protected, background threads)
# ---------------------------------------------------------------------------

@router.post("/action/restart-service")
async def action_restart_service(
    request: Request,
    name: str = Form(...),
    csrf_token: str = Form(...),
):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    user = session.get("user", "admin")
    admin_db = _get_admin_db(request)
    thread = threading.Thread(target=restart_service, args=(name, user), daemon=True)
    thread.start()

    _audit(request, session, "restart_service", target=name, result="STARTED")

    return {"success": True, "message": f"Restart of {name} started"}


@router.post("/action/restart-container")
async def action_restart_container(
    request: Request,
    name: str = Form(...),
    csrf_token: str = Form(...),
):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    user = session.get("user", "admin")
    thread = threading.Thread(target=restart_container, args=(name, user), daemon=True)
    thread.start()

    _audit(request, session, "restart_container", target=name, result="STARTED")

    return {"success": True, "message": f"Restart of {name} container started"}


@router.post("/action/git-pull")
async def action_git_pull(
    request: Request,
    csrf_token: str = Form(...),
):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    user = session.get("user", "admin")
    result = git_pull(user)

    _audit(request, session, "git_pull", result="SUCCESS" if result.get("success") else "FAILED")

    return result
