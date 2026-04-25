"""Open WebUI Docker image update management.

Handles checking for updates, pulling new images, backing up data,
and safely recreating the container via Docker Compose.
"""

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from admin.security import run_command, log_admin_action

logger = logging.getLogger("admin.docker")

OPEN_WEBUI_IMAGE = os.getenv("OPEN_WEBUI_IMAGE", "ghcr.io/open-webui/open-webui:main")
OPEN_WEBUI_CONTAINER = "open-webui"
OPEN_WEBUI_BACKUP_DIR = os.getenv(
    "OPEN_WEBUI_BACKUP_DIR",
    "/opt/ai-assistant/data/backups/open-webui",
)

# Docker Compose file path — preferred update method
COMPOSE_FILE = os.getenv(
    "OPEN_WEBUI_COMPOSE_FILE",
    "/opt/ai-assistant/docker-compose.open-webui.yml",
)


def _get_image_id(image_ref: str) -> str | None:
    """Get the image ID for a given image reference."""
    result = run_command(
        ["/usr/bin/docker", "inspect", "--format", "{{.Id}}", image_ref],
        timeout=10, mask=False,
    )
    if result["success"] and result["output"].strip():
        return result["output"].strip()
    return None


def _get_container_image_id() -> str | None:
    """Get the image ID currently used by the open-webui container."""
    result = run_command(
        ["/usr/bin/docker", "inspect", "--format", "{{.Image}}", OPEN_WEBUI_CONTAINER],
        timeout=5, mask=False,
    )
    if result["success"] and result["output"].strip():
        return result["output"].strip()
    return None


def get_container_inspect() -> dict | None:
    """Get full container config for recreating without compose."""
    result = run_command(
        ["/usr/bin/docker", "inspect", OPEN_WEBUI_CONTAINER],
        timeout=5, mask=False,
    )
    if not result["success"] or not result["output"].strip():
        return None
    try:
        data = json.loads(result["output"])
        return data[0] if data else None
    except (json.JSONDecodeError, IndexError):
        return None


def get_open_webui_version() -> str | None:
    """Try to get Open WebUI version from container labels or env."""
    inspect = get_container_inspect()
    if not inspect:
        return None

    # Check labels
    labels = inspect.get("Config", {}).get("Labels", {})
    for key in ("org.opencontainers.image.version", "version"):
        if key in labels:
            return labels[key]

    # Check env vars
    env_list = inspect.get("Config", {}).get("Env", [])
    for env in env_list:
        if env.startswith("WEBUI_VERSION="):
            return env.split("=", 1)[1]

    return None


def has_compose_file() -> bool:
    """Check if Docker Compose file exists for Open WebUI."""
    return os.path.isfile(COMPOSE_FILE)


def check_update() -> dict:
    """Check if a new Open WebUI image is available.

    Pulls the latest image and compares image IDs.
    Only call this on user action, not on auto-refresh.
    """
    current_image_id = _get_container_image_id()
    if not current_image_id:
        return {
            "update_available": False,
            "error": "Could not inspect current container",
            "current_image_id": None,
            "latest_image_id": None,
        }

    # Pull latest image
    pull_result = run_command(
        ["/usr/bin/docker", "pull", OPEN_WEBUI_IMAGE],
        timeout=120, mask=False,
    )
    if not pull_result["success"]:
        return {
            "update_available": False,
            "error": f"Failed to pull image: {pull_result.get('message', '')}",
            "current_image_id": current_image_id[:12],
            "latest_image_id": None,
        }

    latest_image_id = _get_image_id(OPEN_WEBUI_IMAGE)
    if not latest_image_id:
        return {
            "update_available": False,
            "error": "Could not get pulled image ID",
            "current_image_id": current_image_id[:12],
            "latest_image_id": None,
        }

    update_available = current_image_id != latest_image_id

    return {
        "update_available": update_available,
        "current_image_id": current_image_id[:12],
        "latest_image_id": latest_image_id[:12],
        "image": OPEN_WEBUI_IMAGE,
        "compose_available": has_compose_file(),
    }


def _find_data_mount() -> dict | None:
    """Find the Open WebUI data volume/bind mount from container inspect."""
    inspect = get_container_inspect()
    if not inspect:
        return None

    mounts = inspect.get("Mounts", [])
    for mount in mounts:
        dest = mount.get("Destination", "")
        # Open WebUI typically mounts data at /app/backend/data
        if "/data" in dest:
            return {
                "type": mount.get("Type", ""),  # "bind" or "volume"
                "source": mount.get("Source", ""),
                "destination": dest,
                "name": mount.get("Name", ""),
            }

    # Fallback: return first mount if any
    if mounts:
        m = mounts[0]
        return {
            "type": m.get("Type", ""),
            "source": m.get("Source", ""),
            "destination": m.get("Destination", ""),
            "name": m.get("Name", ""),
        }

    return None


def backup_data(user: str = "admin") -> dict:
    """Backup Open WebUI data before update."""
    mount = _find_data_mount()
    if not mount:
        return {"success": False, "message": "No data mount found on container"}

    backup_dir = Path(OPEN_WEBUI_BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    backup_name = f"open-webui_{timestamp}.tar.gz"
    backup_path = backup_dir / backup_name

    if mount["type"] == "bind":
        # Bind mount — tar the source directory
        source = mount["source"]
        if not os.path.isdir(source):
            return {"success": False, "message": f"Bind mount source not found: {source}"}

        result = run_command(
            ["/usr/bin/tar", "-czf", str(backup_path), "-C", source, "."],
            timeout=120, mask=False,
        )
    elif mount["type"] == "volume":
        # Named volume — use a temporary container to tar it
        vol_name = mount["name"] or mount["source"]
        result = run_command(
            ["/usr/bin/docker", "run", "--rm",
             "-v", f"{vol_name}:/data:ro",
             "-v", f"{str(backup_dir)}:/backup",
             "alpine", "tar", "-czf", f"/backup/{backup_name}", "-C", "/data", "."],
            timeout=120, mask=False,
        )
    else:
        return {"success": False, "message": f"Unknown mount type: {mount['type']}"}

    if result["success"]:
        size_kb = round(backup_path.stat().st_size / 1024, 1) if backup_path.exists() else 0
        log_admin_action(user, "open-webui backup", f"SUCCESS {backup_path} ({size_kb} KB)")
        return {
            "success": True,
            "path": str(backup_path),
            "size_kb": size_kb,
        }
    else:
        return {"success": False, "message": result.get("message", "Backup failed")}


def update_open_webui(user: str = "admin") -> dict:
    """Update Open WebUI: pull image + recreate container.

    Requires Docker Compose file for safe recreation.
    """
    steps = []

    # Step 1: Verify compose file exists
    if not has_compose_file():
        return {
            "success": False,
            "message": (
                "Open WebUI update requires Docker Compose. "
                f"Create a compose file at {COMPOSE_FILE} before automatic updates can be enabled."
            ),
            "steps": [],
        }

    # Step 2: Pull latest image via compose
    steps.append({"step": "pull_image", "status": "running"})
    pull_result = run_command(
        ["/usr/bin/docker", "compose", "-f", COMPOSE_FILE, "pull", OPEN_WEBUI_CONTAINER],
        timeout=180, mask=False,
    )
    if not pull_result["success"]:
        steps[-1]["status"] = "failed"
        steps[-1]["error"] = pull_result.get("message", "Pull failed")
        log_admin_action(user, "open-webui update", f"FAILED at pull: {pull_result.get('message', '')}")
        return {"success": False, "message": "Failed to pull image", "steps": steps}
    steps[-1]["status"] = "done"

    # Step 3: Recreate container via compose
    steps.append({"step": "recreate_container", "status": "running"})
    up_result = run_command(
        ["/usr/bin/docker", "compose", "-f", COMPOSE_FILE, "up", "-d", OPEN_WEBUI_CONTAINER],
        timeout=60, mask=False,
    )
    if not up_result["success"]:
        steps[-1]["status"] = "failed"
        steps[-1]["error"] = up_result.get("message", "Recreate failed")
        log_admin_action(user, "open-webui update", f"FAILED at recreate: {up_result.get('message', '')}")
        return {"success": False, "message": "Failed to recreate container", "steps": steps}
    steps[-1]["status"] = "done"

    # Step 4: Verify container is running
    steps.append({"step": "verify", "status": "running"})
    verify_result = run_command(
        ["/usr/bin/docker", "ps", "--filter", f"name=^/{OPEN_WEBUI_CONTAINER}$",
         "--format", "{{.Status}}"],
        timeout=10, mask=False,
    )
    running = verify_result["success"] and "Up" in (verify_result["output"] or "")
    steps[-1]["status"] = "done" if running else "warning"

    status = "SUCCESS" if running else "WARNING: container may not be running"
    log_admin_action(user, "open-webui update", status)

    return {
        "success": running,
        "message": "Update complete" if running else "Container may not be running after update",
        "steps": steps,
    }


def list_backups() -> list[dict]:
    """List Open WebUI data backups."""
    backup_dir = Path(OPEN_WEBUI_BACKUP_DIR)
    backups = []
    if not backup_dir.is_dir():
        return backups

    for f in sorted(backup_dir.glob("open-webui_*.tar.gz"), reverse=True):
        try:
            stat = f.stat()
            m = re.match(r"open-webui_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2})\.tar\.gz", f.name)
            created = f"{m.group(1)} {m.group(2).replace('-', ':')}" if m else ""
            backups.append({
                "filename": f.name,
                "size_kb": round(stat.st_size / 1024, 1),
                "created": created,
            })
        except OSError:
            pass

    return backups


def delete_backup(filename: str) -> dict:
    """Delete an Open WebUI backup file."""
    if not re.match(r"^open-webui_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.tar\.gz$", filename):
        return {"success": False, "message": "Invalid backup filename"}

    backup_path = Path(OPEN_WEBUI_BACKUP_DIR) / filename
    if not backup_path.exists():
        return {"success": False, "message": "Backup not found"}

    try:
        backup_path.unlink()
        return {"success": True}
    except OSError as e:
        return {"success": False, "message": str(e)}
