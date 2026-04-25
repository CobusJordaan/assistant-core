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


def _build_git_env() -> dict:
    """Build environment for git commands with non-interactive SSH."""
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new",
    }
    # If a custom SSH_AUTH_SOCK is configured, inject it so systemd can reach an agent
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
    "   ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N \"\"\n"
    "   Add the public key as a deploy key in GitHub repo settings.\n"
    "   Then set in the repo:\n"
    "   git config core.sshCommand \"ssh -i ~/.ssh/deploy_key -o BatchMode=yes\"\n"
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

    logger.info("Git command: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env=_build_git_env(), cwd=REPO_DIR,
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
            result["message"] = (
                "Git pull failed — SSH key not accessible from systemd. "
                "See fix options below."
            )
            result["output"] = _SSH_FIX_MESSAGE

    status = "SUCCESS" if result["success"] else "FAILED"
    log_admin_action(user, "git-pull", status)

    return result
