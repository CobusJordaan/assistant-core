"""Service status and restart for systemd services and Docker containers."""

import logging

from admin.security import run_command, log_admin_action

logger = logging.getLogger("admin.services")

ALLOWED_SERVICES = frozenset({"assistant-core", "ollama", "image-bridge"})
ALLOWED_CONTAINERS = frozenset({"open-webui"})

# Detect systemctl path (varies by distro)
_SYSTEMCTL = "/bin/systemctl"


def get_service_status(name: str) -> dict:
    """Get systemd service status."""
    if name not in ALLOWED_SERVICES:
        return {"name": name, "active": False, "status_text": "not allowed"}
    result = run_command([_SYSTEMCTL, "is-active", name], timeout=5)
    status_text = result["output"].strip().split("\n")[0] if result["output"] else "unknown"
    return {
        "name": name,
        "active": status_text == "active",
        "status_text": status_text,
    }


def restart_service(name: str, user: str = "admin") -> dict:
    """Restart a whitelisted systemd service via sudo."""
    if name not in ALLOWED_SERVICES:
        return {"success": False, "message": "Service not allowed", "output": ""}

    result = run_command(["/usr/bin/sudo", _SYSTEMCTL, "restart", name], timeout=10)

    # Detect permission errors
    if not result["success"] and "permission" in result["output"].lower():
        result["message"] = "Permission denied. Check sudoers config."

    status = "SUCCESS" if result["success"] else "FAILED"
    log_admin_action(user, f"restart {name}", status)

    return result


def get_container_status(name: str) -> dict:
    """Get Docker container status."""
    if name not in ALLOWED_CONTAINERS:
        return {"name": name, "running": False, "status_text": "not allowed"}
    result = run_command(
        ["/usr/bin/docker", "ps", "--filter", f"name=^/{name}$", "--format", "{{.Status}}"],
        timeout=5,
    )
    status_text = result["output"].strip().split("\n")[0] if result["output"] else "not found"
    return {
        "name": name,
        "running": "Up" in status_text if result["success"] else False,
        "status_text": status_text or "not found",
    }


def restart_container(name: str, user: str = "admin") -> dict:
    """Restart a whitelisted Docker container."""
    if name not in ALLOWED_CONTAINERS:
        return {"success": False, "message": "Container not allowed", "output": ""}

    result = run_command(["/usr/bin/docker", "restart", name], timeout=30)

    status = "SUCCESS" if result["success"] else "FAILED"
    log_admin_action(user, f"restart container {name}", status)

    return result


def get_container_detail(name: str) -> dict:
    """Get detailed Docker container info via docker inspect."""
    if name not in ALLOWED_CONTAINERS:
        return {"name": name, "running": False, "error": "not allowed"}

    result = run_command(
        ["/usr/bin/docker", "inspect", "--format",
         "{{.State.Status}}|{{.State.StartedAt}}|{{.Config.Image}}|{{.Id}}|{{.State.Running}}|{{.NetworkSettings.Ports}}",
         name],
        timeout=5,
    )

    if not result["success"] or not result["output"].strip():
        return {
            "name": name,
            "running": False,
            "status": "not found",
            "started_at": None,
            "image": None,
            "container_id": None,
            "ports": None,
        }

    parts = result["output"].strip().split("|", 5)
    status = parts[0] if len(parts) > 0 else "unknown"
    started_at = parts[1] if len(parts) > 1 else None
    image = parts[2] if len(parts) > 2 else None
    container_id = parts[3][:12] if len(parts) > 3 else None
    running = parts[4].lower() == "true" if len(parts) > 4 else False
    ports_raw = parts[5] if len(parts) > 5 else ""

    # Parse uptime from started_at
    uptime = None
    if started_at and running:
        try:
            from datetime import datetime, timezone
            # Docker returns ISO format like 2026-04-25T10:00:00.123456789Z
            clean = started_at.split(".")[0] + "+00:00"
            start_dt = datetime.fromisoformat(clean.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - start_dt
            days = delta.days
            hours, rem = divmod(delta.seconds, 3600)
            minutes = rem // 60
            if days > 0:
                uptime = f"{days}d {hours}h {minutes}m"
            elif hours > 0:
                uptime = f"{hours}h {minutes}m"
            else:
                uptime = f"{minutes}m"
        except Exception:
            uptime = None

    # Try to detect host port
    host_port = None
    if ports_raw:
        # Format: map[3000/tcp:[{0.0.0.0 3000}]] or similar
        import re
        port_match = re.search(r"\{[^}]*\s(\d+)\}", ports_raw)
        if port_match:
            host_port = port_match.group(1)

    return {
        "name": name,
        "running": running,
        "status": status,
        "started_at": started_at,
        "uptime": uptime,
        "image": image,
        "container_id": container_id,
        "host_port": host_port,
    }


def get_all_statuses() -> dict:
    """Get status of all monitored services and containers."""
    return {
        "services": {name: get_service_status(name) for name in sorted(ALLOWED_SERVICES)},
        "containers": {name: get_container_status(name) for name in sorted(ALLOWED_CONTAINERS)},
    }
