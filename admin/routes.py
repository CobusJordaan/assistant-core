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
from admin.git_manager import get_status as git_status, pull as git_pull, get_update_status
from admin.service_manager import (
    get_all_statuses, get_service_status, restart_service, restart_container,
    get_container_detail,
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

    from admin.docker_manager import get_open_webui_version, has_compose_file, COMPOSE_FILE
    owui_version = get_open_webui_version()

    return templates.TemplateResponse(request, "admin/open-webui.html", {
        "active_page": "open-webui",
        "container": detail,
        "webui_url": webui_url,
        "owui_version": owui_version,
        "compose_available": has_compose_file(),
        "compose_file_path": COMPOSE_FILE,
        "session_role": session.get("role", "admin"),
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


@router.post("/api/open-webui/check-update")
async def api_open_webui_check_update(request: Request, csrf_token: str = Form(...)):
    """Check if a new Open WebUI Docker image is available."""
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    from admin.docker_manager import check_update, get_open_webui_version
    _audit(request, session, "owui_check_update", result="STARTED")

    result = check_update()
    result["current_version"] = get_open_webui_version()

    _audit(request, session, "owui_check_update",
           result="UPDATE_AVAILABLE" if result.get("update_available") else "UP_TO_DATE")

    return result


@router.post("/api/open-webui/backup")
async def api_open_webui_backup(request: Request, csrf_token: str = Form(...)):
    """Backup Open WebUI data before update."""
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    if not _require_owner(session):
        return JSONResponse(status_code=403, content={"success": False, "message": "Owner role required"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    from admin.docker_manager import backup_data
    result = backup_data(user=session.get("user", "admin"))

    _audit(request, session, "owui_backup",
           target=result.get("path", ""),
           result="SUCCESS" if result["success"] else "FAILED")

    return result


@router.post("/api/open-webui/update")
async def api_open_webui_update(request: Request, csrf_token: str = Form(...)):
    """Update Open WebUI: pull image + recreate container via Compose."""
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    if not _require_owner(session):
        return JSONResponse(status_code=403, content={"success": False, "message": "Owner role required"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    from admin.docker_manager import update_open_webui

    _audit(request, session, "owui_update", result="STARTED")
    result = update_open_webui(user=session.get("user", "admin"))
    _audit(request, session, "owui_update",
           result="SUCCESS" if result["success"] else "FAILED",
           details=str(result.get("steps", [])))

    return result


@router.get("/api/open-webui/backups")
async def api_open_webui_backups(request: Request):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    from admin.docker_manager import list_backups
    return {"backups": list_backups()}


@router.delete("/api/open-webui/backups/{filename}")
async def api_open_webui_delete_backup(request: Request, filename: str):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    if not _require_owner(session):
        return JSONResponse(status_code=403, content={"success": False, "message": "Owner role required"})

    csrf_token = request.query_params.get("csrf_token", "")
    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    from admin.docker_manager import delete_backup
    result = delete_backup(filename)
    if result["success"]:
        _audit(request, session, "owui_delete_backup", target=filename, result="SUCCESS")
    return result


# ---------------------------------------------------------------------------
# Image Bridge management page
# ---------------------------------------------------------------------------

@router.get("/image-bridge", response_class=HTMLResponse)
async def image_bridge_page(request: Request):
    session = _require_session(request)
    if not session:
        return RedirectResponse("/admin/login", status_code=302)

    svc_status = get_service_status("image-bridge")

    admin_db = _get_admin_db(request)
    settings = {}
    key_prefix = None
    if admin_db and admin_db.available:
        for key in ("forge_base_url", "image_bridge_port", "default_width",
                     "default_height", "default_steps", "default_cfg_scale",
                     "default_sampler_name", "default_scheduler", "default_model",
                     "default_checkpoint", "default_negative_prompt", "output_dir",
                     "public_base_url",
                     "enable_adetailer", "adetailer_model", "adetailer_prompt",
                     "adetailer_negative_prompt"):
            s = admin_db.get_setting(key)
            if s:
                settings[key] = s["value"]
        pfx = admin_db.get_setting("image_bridge_api_key_prefix")
        if pfx and pfx["value"]:
            key_prefix = pfx["value"]

    return templates.TemplateResponse(request, "admin/image-bridge.html", {
        "active_page": "image-bridge",
        "service": svc_status,
        "settings": settings,
        "key_prefix": key_prefix,
        "bridge_port": settings.get("image_bridge_port", "5000"),
        "session_role": session.get("role", "admin"),
        "csrf_token": session.get("csrf", ""),
    })


@router.get("/api/image-bridge/status")
async def api_image_bridge_status(request: Request):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    svc = get_service_status("image-bridge")

    admin_db = _get_admin_db(request)
    port = "5000"
    forge_url = "http://127.0.0.1:7860"
    key_configured = False
    if admin_db and admin_db.available:
        p = admin_db.get_setting("image_bridge_port")
        if p:
            port = p["value"]
        f = admin_db.get_setting("forge_base_url")
        if f:
            forge_url = f["value"]
        k = admin_db.get_setting("image_bridge_api_key_prefix")
        if k and k["value"]:
            key_configured = True

    return {
        "service": svc,
        "port": port,
        "forge_url": forge_url,
        "key_configured": key_configured,
    }


@router.post("/api/image-bridge/settings")
async def api_image_bridge_settings(
    request: Request,
    csrf_token: str = Form(...),
    forge_base_url: str = Form(""),
    default_width: str = Form(""),
    default_height: str = Form(""),
    default_steps: str = Form(""),
    default_cfg_scale: str = Form(""),
    default_sampler_name: str = Form(""),
    default_scheduler: str = Form(""),
    default_model: str = Form(""),
    default_checkpoint: str = Form(""),
    default_negative_prompt: str = Form(""),
    output_dir: str = Form(""),
    public_base_url: str = Form(""),
    enable_adetailer: str = Form("false"),
    adetailer_model: str = Form(""),
    adetailer_prompt: str = Form(""),
    adetailer_negative_prompt: str = Form(""),
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

    user = session.get("user", "admin")
    updates = {
        "forge_base_url": ("string", forge_base_url),
        "default_width": ("int", default_width),
        "default_height": ("int", default_height),
        "default_steps": ("int", default_steps),
        "default_cfg_scale": ("string", default_cfg_scale),
        "default_sampler_name": ("string", default_sampler_name),
        "default_scheduler": ("string", default_scheduler),
        "default_model": ("string", default_model),
        "default_checkpoint": ("string", default_checkpoint),
        "default_negative_prompt": ("string", default_negative_prompt),
        "output_dir": ("string", output_dir),
        "public_base_url": ("string", public_base_url),
        "enable_adetailer": ("bool", enable_adetailer),
        "adetailer_model": ("string", adetailer_model),
        "adetailer_prompt": ("string", adetailer_prompt),
        "adetailer_negative_prompt": ("string", adetailer_negative_prompt),
    }
    import logging
    _ib_log = logging.getLogger("admin.routes.ib")
    saved_count = 0
    for key, (vtype, value) in updates.items():
        # Save even empty strings (to clear a field)
        if value is not None:
            admin_db.set_setting(key, value, vtype, 0, user)
            saved_count += 1

    # Verify a few key settings were persisted
    verify = {}
    for vk in ("default_negative_prompt", "default_width", "default_height", "adetailer_prompt"):
        s = admin_db.get_setting(vk)
        verify[vk] = s["value"][:50] if s else "MISSING"
    _ib_log.info("Saved %d settings. Verify: %s", saved_count, verify)

    _audit(request, session, "update_image_bridge_settings", result="SUCCESS")
    return {"success": True, "saved": saved_count, "verify": verify}


@router.post("/api/image-bridge/generate-key")
async def api_image_bridge_generate_key(
    request: Request,
    csrf_token: str = Form(...),
):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    if not _require_owner(session) and session.get("role") != "admin":
        return JSONResponse(status_code=403, content={"success": False, "message": "Admin role required"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    admin_db = _get_admin_db(request)
    if not admin_db or not admin_db.available:
        return JSONResponse(status_code=500, content={"success": False, "message": "Database not available"})

    import hashlib
    import secrets

    raw_key = "imgbr_" + secrets.token_hex(32)
    salt = secrets.token_hex(16)
    key_hash = hashlib.sha256((salt + raw_key).encode()).hexdigest()
    prefix = raw_key[:8]
    user = session.get("user", "admin")

    admin_db.set_setting("image_bridge_api_key_hash", key_hash, "string", 1, user)
    admin_db.set_setting("image_bridge_api_key_salt", salt, "string", 1, user)
    admin_db.set_setting("image_bridge_api_key_prefix", prefix, "string", 0, user)

    _audit(request, session, "generate_image_bridge_key", result="SUCCESS")

    return {"success": True, "raw_key": raw_key, "prefix": prefix}


@router.post("/api/image-bridge/test-forge")
async def api_image_bridge_test_forge(
    request: Request,
    csrf_token: str = Form(...),
):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    admin_db = _get_admin_db(request)
    forge_url = "http://127.0.0.1:7860"
    if admin_db and admin_db.available:
        s = admin_db.get_setting("forge_base_url")
        if s:
            forge_url = s["value"]

    connected = False
    models_count = 0
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{forge_url.rstrip('/')}/sdapi/v1/sd-models")
            if resp.status_code == 200:
                connected = True
                models = resp.json()
                models_count = len(models) if isinstance(models, list) else 0
            else:
                # Fallback: try base URL
                resp2 = await client.get(forge_url)
                connected = resp2.status_code < 500
    except Exception:
        connected = False

    _audit(request, session, "test_forge_connection",
           target=forge_url, result="OK" if connected else "FAILED")

    return {"success": connected, "forge_url": forge_url, "models_count": models_count}


@router.get("/api/image-bridge/models")
async def api_image_bridge_models(request: Request):
    """Proxy Forge model list for checkpoint dropdown."""
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    admin_db = _get_admin_db(request)
    forge_url = "http://127.0.0.1:7860"
    if admin_db and admin_db.available:
        s = admin_db.get_setting("forge_base_url")
        if s:
            forge_url = s["value"]

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{forge_url.rstrip('/')}/sdapi/v1/sd-models")
            if resp.status_code == 200:
                models = resp.json()
                return {"success": True, "models": [
                    {"title": m.get("title", ""), "model_name": m.get("model_name", "")}
                    for m in models
                ]}
    except Exception:
        pass

    return {"success": False, "models": []}


@router.get("/api/image-bridge/health")
async def api_image_bridge_health(request: Request):
    """Proxy health check to the image-bridge service (server-side)."""
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    admin_db = _get_admin_db(request)
    port = "5000"
    if admin_db and admin_db.available:
        p = admin_db.get_setting("image_bridge_port")
        if p:
            port = p["value"]

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"http://127.0.0.1:{port}/health")
            if resp.status_code == 200:
                data = resp.json()
                # Normalize: ensure status field is present
                if "status" not in data:
                    data["status"] = "ok"
                return data
            return {"status": "error", "code": resp.status_code}
    except Exception:
        return {"status": "unreachable"}


@router.post("/api/image-bridge/test-generate")
async def api_image_bridge_test_generate(
    request: Request,
    prompt: str = Form(...),
    negative_prompt: str = Form(""),
    width: str = Form(""),
    height: str = Form(""),
    csrf_token: str = Form(...),
):
    """Test image generation by calling the image-bridge service."""
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"success": False, "message": "Unauthorized"})

    csrf_err = _check_csrf(request, session, csrf_token)
    if csrf_err:
        return csrf_err

    admin_db = _get_admin_db(request)
    port = "5000"
    w = width or "512"
    h = height or "640"
    if admin_db and admin_db.available:
        p = admin_db.get_setting("image_bridge_port")
        if p:
            port = p["value"]
        if not width:
            ws = admin_db.get_setting("default_width")
            if ws:
                w = ws["value"]
        if not height:
            hs = admin_db.get_setting("default_height")
            if hs:
                h = hs["value"]

    bridge_url = f"http://127.0.0.1:{port}"
    body = {"prompt": prompt, "n": 1, "size": f"{w}x{h}", "response_format": "b64_json"}
    if negative_prompt:
        body["negative_prompt"] = negative_prompt

    try:
        import httpx
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{bridge_url}/v1/images/generations",
                json=body,
                headers={"X-Admin-Test": "true"},
            )
            if resp.status_code == 200:
                data = resp.json()
                _audit(request, session, "test_image_generate", target=prompt[:50], result="SUCCESS")
                return {"success": True, "data": data}
            else:
                return {"success": False, "message": f"Bridge returned {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


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


@router.get("/api/update-status")
async def api_update_status(request: Request):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    return get_update_status()


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
    "image-bridge": ["/usr/bin/journalctl", "-u", "image-bridge", "--no-pager", "-n"],
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


@router.get("/api/audit/actions")
async def api_audit_actions(request: Request):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    admin_db = _get_admin_db(request)
    if not admin_db or not admin_db.available:
        return {"actions": []}

    return {"actions": admin_db.get_distinct_actions()}


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
