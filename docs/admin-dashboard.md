# Admin Dashboard Setup

## 1. Install Dependencies

```bash
pip install psutil jinja2 itsdangerous "passlib[bcrypt]"
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

Git pull uses non-interactive SSH. The deploy key must not require a passphrase:

```bash
# Test SSH access
ssh -o BatchMode=yes -T git@github.com

# If it fails, set up a deploy key without passphrase
ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N ""
```

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
