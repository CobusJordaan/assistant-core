"""Admin dashboard routes — login, dashboard, logs, actions, API."""

import asyncio
import logging
import os
import threading

from fastapi import APIRouter, Request, Response, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from admin.auth import (
    is_configured, verify_password, create_session_cookie, get_session,
    clear_session, validate_csrf, check_rate_limit, record_failed_attempt,
    clear_attempts, get_lockout_remaining, ADMIN_USERNAME,
)
from admin.system_info import (
    get_cpu_info, get_cpu_temp, get_memory_info, get_disk_info,
    get_uptime, get_gpu_info, get_db_info, get_version_info,
)
from admin.git_manager import get_status as git_status, pull as git_pull
from admin.service_manager import (
    get_all_statuses, restart_service, restart_container,
)

logger = logging.getLogger("admin.routes")

DATABASE_PATH = os.getenv("DATABASE_PATH", "memory.db")

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


def _require_session(request: Request):
    """Return session dict or None (caller should redirect)."""
    if not is_configured():
        return None
    return get_session(request)


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
    }


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not is_configured():
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "Admin not configured. Set ADMIN_PASSWORD_HASH and ADMIN_SECRET_KEY in .env",
            "lockout_remaining": 0,
            "info": None,
        })

    # Already logged in?
    session = get_session(request)
    if session:
        return RedirectResponse("/admin", status_code=302)

    ip = _get_client_ip(request)
    lockout = get_lockout_remaining(ip)

    return templates.TemplateResponse("admin/login.html", {
        "request": request,
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
    if not is_configured():
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "Admin not configured.",
            "lockout_remaining": 0,
            "info": None,
        })

    ip = _get_client_ip(request)

    # Rate limit check
    if not check_rate_limit(ip):
        lockout = get_lockout_remaining(ip)
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": None,
            "lockout_remaining": lockout,
            "info": None,
        })

    # Verify credentials
    if username != ADMIN_USERNAME or not verify_password(password):
        record_failed_attempt(ip)
        await asyncio.sleep(1)  # Slow down brute force
        lockout = get_lockout_remaining(ip)
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "Invalid username or password.",
            "lockout_remaining": lockout,
            "info": None,
        })

    # Success
    clear_attempts(ip)
    response = RedirectResponse("/admin", status_code=302)
    csrf_token = create_session_cookie(response)
    logger.info("Admin login from %s", ip)
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
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
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

    return templates.TemplateResponse("admin/logs.html", {
        "request": request,
        "active_page": "logs",
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

    return _collect_status()


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
# Actions (POST, CSRF-protected, background threads)
# ---------------------------------------------------------------------------

def _check_csrf(request: Request, session: dict, csrf_token: str) -> JSONResponse | None:
    """Return error response if CSRF fails, else None."""
    if not validate_csrf(session, csrf_token):
        return JSONResponse(status_code=403, content={"success": False, "message": "CSRF validation failed"})
    return None


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
    thread = threading.Thread(target=restart_service, args=(name, user), daemon=True)
    thread.start()

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
    return result
