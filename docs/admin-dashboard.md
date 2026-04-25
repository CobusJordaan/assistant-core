# Admin Dashboard Setup

## 1. Install Dependencies

```bash
pip install psutil jinja2 itsdangerous bcrypt python-multipart
```

Or install everything:

```bash
pip install -r requirements.txt
```

## 2. Generate Admin Password

```bash
python scripts/generate_admin_password.py
```

This will prompt for a password and output a bcrypt hash.

## 3. Configure Environment

Add to `.env`:

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=$2b$12$...   # paste the hash from step 2
ADMIN_SECRET_KEY=<random-string>  # generate with: python -c "import secrets; print(secrets.token_hex(32))"
```

## 4. Sudoers Configuration

The admin dashboard can restart systemd services. This requires passwordless sudo for the service user:

```bash
# /etc/sudoers.d/assistant-core-admin
aicrm ALL=(ALL) NOPASSWD: /bin/systemctl restart assistant-core
aicrm ALL=(ALL) NOPASSWD: /bin/systemctl restart ollama
```

Docker: the service user must be in the `docker` group (no sudo needed).

## 5. SSH Key for Git Pull

### Why git pull fails from the dashboard

`git pull` works fine in your SSH terminal because your interactive shell has
`ssh-agent` loaded with the key unlocked. However, assistant-core runs under
**systemd**, which does **not** inherit `SSH_AUTH_SOCK` from any interactive
session. This means passphrase-protected SSH keys will always fail when
triggered from the web dashboard.

### Fix options (choose one)

**Option A — Deploy key without passphrase (recommended)**

Create a dedicated SSH key with no passphrase and add it as a deploy key in
your GitHub repo settings:

```bash
# Generate key (no passphrase)
ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N ""

# Add the public key to GitHub → repo → Settings → Deploy keys
cat ~/.ssh/deploy_key.pub

# Configure this repo to use the deploy key
cd /opt/ai-assistant/services/assistant-core
git config core.sshCommand "ssh -i ~/.ssh/deploy_key -o BatchMode=yes"

# Test
ssh -i ~/.ssh/deploy_key -o BatchMode=yes -T git@github.com
```

**Option B — Point systemd to a persistent ssh-agent socket**

If you have a persistent agent (e.g. GNOME Keyring or a systemd user agent),
add to `.env`:

```env
GIT_SSH_AUTH_SOCK=/run/user/1000/keyring/ssh
```

The dashboard will inject this as `SSH_AUTH_SOCK` when running git commands.

**Option C — Switch to HTTPS with a personal access token**

```bash
git remote set-url origin https://<token>@github.com/user/repo.git
```

No SSH needed, but the token is stored in the git remote URL.

## 6. Restart assistant-core

```bash
sudo systemctl restart assistant-core
```

## 7. Access

- Dashboard: `http://<server-ip>:8000/admin`
- Health API (no auth): `http://<server-ip>:8000/admin/api/health`

## Security Notes

- Login is rate-limited: 5 failed attempts = 15-minute lockout per IP
- Session cookies are signed (itsdangerous), httponly, samesite=strict, 8h expiry
- All POST actions require CSRF tokens
- System commands use absolute-path whitelist only — no shell execution
- Service restarts are limited to whitelisted names (assistant-core, ollama, open-webui)
- Command output is masked for tokens/keys/passwords
- All admin actions are logged to `/opt/ai-assistant/logs/admin-actions.log`

## Monitoring Cards

| Card | Data Source |
|------|------------|
| Services | systemctl is-active, docker ps |
| GPU | nvidia-smi (CSV output) |
| CPU/RAM | psutil |
| Disk | psutil (/, /opt/ai-data, /opt/ai-assistant) |
| Git | git status, git log |
| Database | SQLite file scan |
| System | psutil boot_time, git rev-parse |

Auto-refresh: every 10 seconds via fetch to `/admin/api/status`.
