"""Command whitelist, subprocess runner, output masking, audit log."""

import os
import re
import subprocess
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("admin.security")

# ---------------------------------------------------------------------------
# Output masking
# ---------------------------------------------------------------------------

_MASK_PATTERNS = [
    (re.compile(r"(token|password|secret|key|api_key|apikey|auth)\s*[=:]\s*\S+", re.IGNORECASE), r"\1=***MASKED***"),
    (re.compile(r"(Bearer\s+)\S+", re.IGNORECASE), r"\1***MASKED***"),
    (re.compile(r"sk-ant-[a-zA-Z0-9_-]+"), "***MASKED_KEY***"),
    (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "***MASKED_KEY***"),
]


def mask_sensitive(text: str) -> str:
    """Replace tokens, keys, passwords in output text."""
    for pattern, replacement in _MASK_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Command whitelist (absolute paths only)
# ---------------------------------------------------------------------------

ALLOWED_COMMANDS: dict[str, list[str]] = {
    "/bin/systemctl": ["is-active", "status", "restart"],
    "/usr/bin/systemctl": ["is-active", "status", "restart"],
    "/usr/bin/docker": ["ps", "logs", "restart", "inspect", "pull", "run", "compose"],
    "/usr/bin/journalctl": ["-u", "--no-pager", "-n", "--since"],
    "/usr/bin/nvidia-smi": [],
    "/usr/bin/sensors": [],
    "/usr/bin/git": ["status", "pull", "log", "rev-parse", "diff", "--porcelain",
                     "fetch", "describe", "rev-list"],
    "/usr/bin/sudo": ["/bin/systemctl", "/usr/bin/systemctl"],
    "/usr/bin/tar": ["-czf"],
}


def validate_command(cmd: list[str]) -> bool:
    """Check if a command is in the whitelist."""
    if not cmd:
        return False
    binary = cmd[0]
    if binary not in ALLOWED_COMMANDS:
        return False
    allowed_args = ALLOWED_COMMANDS[binary]
    if not allowed_args:
        return True
    # For sudo, check the sub-command
    if binary == "/usr/bin/sudo":
        return len(cmd) >= 2 and cmd[1] in allowed_args
    # Check that at least one arg is in allowed list
    return any(arg in allowed_args for arg in cmd[1:])


def run_command(cmd: list[str], timeout: int = 15, mask: bool = True,
                env: dict | None = None, cwd: str | None = None) -> dict:
    """Run a whitelisted command safely.

    Returns {"success": bool, "message": str, "output": str, "code": int}.
    """
    if not validate_command(cmd):
        logger.warning("Blocked command: %s", cmd)
        return {"success": False, "message": "Command not allowed", "output": "", "code": -1}

    logger.info("Admin command: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env=env, cwd=cwd,
        )
        stdout = mask_sensitive(result.stdout) if mask else result.stdout
        stderr = mask_sensitive(result.stderr) if mask else result.stderr
        output = (stdout + "\n" + stderr).strip()
        return {
            "success": result.returncode == 0,
            "message": "OK" if result.returncode == 0 else f"Exit code {result.returncode}",
            "output": output,
            "code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "message": f"Command timed out ({timeout}s)", "output": "", "code": -1}
    except FileNotFoundError:
        return {"success": False, "message": f"Command not found: {cmd[0]}", "output": "", "code": -1}
    except Exception as e:
        return {"success": False, "message": str(e), "output": "", "code": -1}


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

AUDIT_LOG_PATH = os.getenv(
    "ADMIN_AUDIT_LOG",
    "/opt/ai-assistant/logs/admin-actions.log",
)


def log_admin_action(user: str, action: str, result: str,
                     ip_address: str = "", admin_db=None,
                     user_id: int | None = None, target: str = "",
                     user_agent: str = "", details: str = ""):
    """Log an admin action to DB (if available) and audit log file."""
    # Write to DB first if available
    if admin_db and getattr(admin_db, "available", False):
        try:
            admin_db.log_action(
                username=user, action=action, target=target,
                result=result, ip_address=ip_address,
                user_agent=user_agent, details=details,
                user_id=user_id,
            )
        except Exception as e:
            logger.error("Failed to write audit to DB: %s", e)

    # File logging as fallback / always
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{now}] {user} {action} {result}\n"
    logger.info("Admin audit: %s %s %s", user, action, result)
    try:
        path = Path(AUDIT_LOG_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(entry)
    except Exception as e:
        logger.error("Failed to write audit log: %s", e)
