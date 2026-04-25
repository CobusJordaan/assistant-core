"""Git operations with non-interactive SSH handling.

SSH context:
  CLI git pull works because the interactive shell has ssh-agent loaded.
  The admin dashboard runs under systemd, which does NOT inherit SSH_AUTH_SOCK
  from any interactive session. This means passphrase-protected SSH keys will
  always fail from the web dashboard.

Production-safe options:
  1. (Recommended) Use a GitHub deploy key WITHOUT passphrase for this repo.
  2. Set GIT_SSH_AUTH_SOCK in .env to point to a persistent ssh-agent socket
     (e.g. /run/user/1000/keyring/ssh or a systemd-managed agent).
  3. Switch the remote to HTTPS with a personal access token.
"""

import os
import subprocess
import logging

from admin.security import validate_command, mask_sensitive, log_admin_action

logger = logging.getLogger("admin.git")

REPO_DIR = os.getenv(
    "ASSISTANT_CORE_REPO_DIR",
    "/opt/ai-assistant/services/assistant-core",
)

# Optional: allow injecting SSH_AUTH_SOCK for systemd environments
_SSH_AUTH_SOCK = os.getenv("GIT_SSH_AUTH_SOCK", "").strip()

# Optional: explicit deploy key path
_DEPLOY_KEY = os.getenv(
    "GIT_DEPLOY_KEY",
    os.path.expanduser("~/.ssh/assistant_core_deploy_key"),
)

# Service user HOME (systemd may not set this)
_HOME = os.getenv("HOME", "/home/aicrm")


def _build_git_env() -> dict:
    """Build a minimal, explicit environment for git commands under systemd."""
    ssh_cmd = "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"

    # Use deploy key if it exists on disk
    if os.path.isfile(_DEPLOY_KEY):
        ssh_cmd = f"ssh -i {_DEPLOY_KEY} -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
        logger.debug("Using deploy key: %s", _DEPLOY_KEY)

    env = {
        "HOME": _HOME,
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": ssh_cmd,
        "LANG": "C.UTF-8",
    }

    # If a custom SSH_AUTH_SOCK is configured, inject it
    if _SSH_AUTH_SOCK:
        env["SSH_AUTH_SOCK"] = _SSH_AUTH_SOCK
        logger.debug("Using GIT_SSH_AUTH_SOCK: %s", _SSH_AUTH_SOCK)

    return env


_SSH_ERROR_KEYWORDS = [
    "enter passphrase",
    "permission denied (publickey)",
    "could not read from remote repository",
    "host key verification failed",
]

_SSH_FIX_MESSAGE = (
    "Git pull failed from the web dashboard.\n"
    "\n"
    "WHY: assistant-core runs under systemd, which does not inherit\n"
    "SSH_AUTH_SOCK from your interactive terminal. Even if ssh-agent\n"
    "is loaded in your CLI session, the web service cannot see it.\n"
    "\n"
    "FIX (choose one):\n"
    "\n"
    "1. (Recommended) Use a GitHub deploy key WITHOUT passphrase:\n"
    "   ssh-keygen -t ed25519 -f ~/.ssh/assistant_core_deploy_key -N \"\"\n"
    "   Add the public key as a deploy key in GitHub repo settings.\n"
    "\n"
    "2. Set GIT_SSH_AUTH_SOCK in .env to a persistent agent socket:\n"
    "   GIT_SSH_AUTH_SOCK=/run/user/1000/keyring/ssh\n"
    "\n"
    "3. Switch remote to HTTPS with a personal access token:\n"
    "   git remote set-url origin https://<token>@github.com/user/repo.git"
)


def _run_git(args: list[str], timeout: int = 15) -> dict:
    """Run a git command with non-interactive SSH env."""
    cmd = ["/usr/bin/git"] + args

    if not validate_command(cmd):
        return {"success": False, "message": "Command not allowed", "output": "", "code": -1}

    git_env = _build_git_env()
    logger.info("Git command: %s (cwd=%s)", " ".join(cmd), REPO_DIR)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env=git_env, cwd=REPO_DIR,
        )
        stdout = mask_sensitive(result.stdout)
        stderr = mask_sensitive(result.stderr)

        logger.info(
            "Git result: rc=%d stdout=%r stderr=%r",
            result.returncode,
            stdout[:200] if stdout else "",
            stderr[:200] if stderr else "",
        )

        return {
            "success": result.returncode == 0,
            "message": "OK" if result.returncode == 0 else f"Exit code {result.returncode}",
            "output": (stdout + "\n" + stderr).strip(),
            "code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        logger.error("Git timed out (%ds): %s", timeout, " ".join(cmd))
        return {"success": False, "message": f"Git timed out ({timeout}s)", "output": "", "code": -1}
    except FileNotFoundError:
        logger.error("git binary not found at /usr/bin/git")
        return {"success": False, "message": "git not found", "output": "", "code": -1}
    except Exception as e:
        logger.error("Git exception: %s", e, exc_info=True)
        return {"success": False, "message": str(e), "output": "", "code": -1}


def get_status() -> dict:
    """Get git status summary."""
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    status = _run_git(["status", "--porcelain"])
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
    """Run git pull --ff-only with SSH error detection and detailed logging."""
    logger.info("=== Git pull requested by %s ===", user)
    logger.info("Git pull cwd=%s", REPO_DIR)

    git_env = _build_git_env()
    # Log env keys (not values) for debugging
    logger.info("Git pull env keys: %s", sorted(git_env.keys()))
    if "GIT_SSH_COMMAND" in git_env:
        logger.info("Git pull GIT_SSH_COMMAND=%s", git_env["GIT_SSH_COMMAND"])

    result = _run_git(["pull", "--ff-only"], timeout=30)

    logger.info("Git pull rc=%d", result["code"])
    logger.info("Git pull stdout=%s", result["output"][:500] if result["output"] else "(empty)")

    # Only flag SSH errors if the command actually failed
    if not result["success"] and result["code"] != 0:
        output_lower = result["output"].lower()
        is_ssh_error = any(kw in output_lower for kw in _SSH_ERROR_KEYWORDS)
        if is_ssh_error:
            result["message"] = (
                "Git pull failed — SSH key not accessible from systemd. "
                "See fix options below."
            )
            result["output"] = _SSH_FIX_MESSAGE

    # Build detailed audit entry
    output_preview = result["output"][:100].replace("\n", " ") if result["output"] else ""
    if result["success"]:
        audit_detail = f"SUCCESS {output_preview}"
    else:
        audit_detail = f"FAILED rc={result['code']} {output_preview}"

    log_admin_action(user, "git-pull", audit_detail)

    return result
