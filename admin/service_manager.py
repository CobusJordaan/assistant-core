"""Service status and restart for systemd services and Docker containers."""

import logging

from admin.security import run_command, log_admin_action

logger = logging.getLogger("admin.services")

ALLOWED_SERVICES = frozenset({"assistant-core", "ollama"})
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


def get_all_statuses() -> dict:
    """Get status of all monitored services and containers."""
    return {
        "services": {name: get_service_status(name) for name in sorted(ALLOWED_SERVICES)},
        "containers": {name: get_container_status(name) for name in sorted(ALLOWED_CONTAINERS)},
    }
