"""Git operations with non-interactive SSH handling."""

import os
import subprocess
import logging

from admin.security import validate_command, mask_sensitive, log_admin_action

logger = logging.getLogger("admin.git")

REPO_DIR = os.getenv(
    "ASSISTANT_CORE_REPO_DIR",
    "/opt/ai-assistant/services/assistant-core",
)

# Non-interactive SSH: no passphrase prompt, no host key prompt
_GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new",
}

_SSH_ERROR_KEYWORDS = [
    "enter passphrase",
    "permission denied (publickey)",
    "could not read from remote repository",
    "host key verification failed",
]

_SSH_FIX_MESSAGE = (
    "Fix options:\n"
    "1. Run: eval $(ssh-agent) && ssh-add ~/.ssh/id_ed25519\n"
    "2. Use a GitHub deploy key without passphrase\n"
    "3. Switch remote to HTTPS with a personal access token"
)


def _run_git(args: list[str], timeout: int = 15) -> dict:
    """Run a git command with non-interactive SSH env."""
    cmd = ["/usr/bin/git"] + args

    if not validate_command(cmd):
        return {"success": False, "message": "Command not allowed", "output": "", "code": -1}

    logger.info("Git command: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env=_GIT_ENV, cwd=REPO_DIR,
        )
        stdout = mask_sensitive(result.stdout)
        stderr = mask_sensitive(result.stderr)
        return {
            "success": result.returncode == 0,
            "message": "OK" if result.returncode == 0 else f"Exit code {result.returncode}",
            "output": (stdout + "\n" + stderr).strip(),
            "code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "message": f"Git timed out ({timeout}s)", "output": "", "code": -1}
    except FileNotFoundError:
        return {"success": False, "message": "git not found", "output": "", "code": -1}
    except Exception as e:
        return {"success": False, "message": str(e), "output": "", "code": -1}


def get_status() -> dict:
    """Get git status summary."""
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    status = _run_git(["status", "--porcelain"])
    # Separate fields with a delimiter to parse reliably
    log_hash = _run_git(["log", "-1", "--format=%h"])
    log_msg = _run_git(["log", "-1", "--format=%s"])
    log_time = _run_git(["log", "-1", "--format=%cr"])

    dirty_lines = [
        line for line in status["output"].strip().splitlines()
        if line.strip()
    ] if status["success"] else []

    last_commit = None
    if log_hash["success"]:
        last_commit = {
            "hash": log_hash["output"].strip(),
            "message": log_msg["output"].strip() if log_msg["success"] else "",
            "relative_time": log_time["output"].strip() if log_time["success"] else "",
        }

    return {
        "branch": branch["output"].strip() if branch["success"] else "unknown",
        "clean": status["success"] and len(dirty_lines) == 0,
        "dirty_files": len(dirty_lines),
        "last_commit": last_commit,
    }


def pull(user: str = "admin") -> dict:
    """Run git pull --ff-only with SSH error detection."""
    result = _run_git(["pull", "--ff-only"], timeout=30)

    # Detect SSH-specific failures
    if not result["success"]:
        output_lower = result["output"].lower()
        is_ssh_error = any(kw in output_lower for kw in _SSH_ERROR_KEYWORDS)
        if is_ssh_error:
            result["message"] = "Git pull failed — SSH key requires passphrase."
            result["output"] = _SSH_FIX_MESSAGE

    status = "SUCCESS" if result["success"] else "FAILED"
    log_admin_action(user, "git-pull", status)

    return result
