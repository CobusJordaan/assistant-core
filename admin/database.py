"""Admin SQLite database — users, API keys, settings, audit log."""

import hashlib
import logging
import os
import secrets
import shutil
import sqlite3
import threading
from datetime import datetime, timezone

logger = logging.getLogger("admin.database")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AdminDB:
    """SQLite admin database following the MemoryStore pattern."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._available = False
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self):
        """Open DB, set pragmas, create tables, bootstrap owner."""
        try:
            os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")

            self._create_tables()
            self._bootstrap_owner()
            self._seed_default_settings()
            self._available = True
            logger.info("AdminDB initialized at %s", self._db_path)
        except Exception as e:
            self._available = False
            logger.error("AdminDB init failed: %s", e)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute_write(self, sql: str, params: tuple = ()):
        with self._write_lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def _executemany_write(self, sql: str, seq_of_params):
        with self._write_lock:
            self._conn.executemany(sql, seq_of_params)
            self._conn.commit()

    def _execute_write_returning(self, sql: str, params: tuple = ()):
        """Execute a write and return lastrowid."""
        with self._write_lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid

    def _row_to_dict(self, row) -> dict | None:
        if row is None:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # Table creation
    # ------------------------------------------------------------------

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT,
                password_changed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                prefix TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                expires_at TEXT,
                created_by TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                value_type TEXT NOT NULL DEFAULT 'string',
                is_secret INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL DEFAULT 'system'
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                user_id INTEGER,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT DEFAULT '',
                result TEXT DEFAULT '',
                ip_address TEXT DEFAULT '',
                user_agent TEXT DEFAULT '',
                details TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                success INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS managed_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL,
                email TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                user_type TEXT NOT NULL DEFAULT 'friend',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_username ON audit_log(username);
            CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip_address, created_at);
            CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(prefix);
        """)

    def _bootstrap_owner(self):
        """Import first admin from env vars if admin_users is empty."""
        count = self._conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
        if count > 0:
            return

        username = os.getenv("ADMIN_USERNAME", "").strip()
        pw_hash = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
        if not username or not pw_hash:
            return

        now = _now()
        self._execute_write(
            """INSERT INTO admin_users (username, password_hash, role, is_active,
               created_at, updated_at, password_changed_at)
               VALUES (?, ?, 'owner', 1, ?, ?, ?)""",
            (username, pw_hash, now, now, now),
        )
        logger.info("Bootstrapped owner user '%s' from env", username)

    def _seed_default_settings(self):
        """Seed default app_settings if table is empty."""
        count = self._conn.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0]
        if count > 0:
            return

        now = _now()
        defaults = [
            ("open_webui_host", os.getenv("OPEN_WEBUI_HOST", "172.18.2.195"), "string", 0),
            ("open_webui_port", os.getenv("OPEN_WEBUI_PORT", "3000"), "string", 0),
            ("refresh_interval", "10", "int", 0),
            ("session_timeout_hours", "8", "int", 0),
        ]
        self._executemany_write(
            """INSERT OR IGNORE INTO app_settings (key, value, value_type, is_secret, updated_at, updated_by)
               VALUES (?, ?, ?, ?, ?, 'system')""",
            [(k, v, vt, s, now) for k, v, vt, s in defaults],
        )

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def get_user_by_username(self, username: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM admin_users WHERE username = ?", (username,)
        ).fetchone()
        return self._row_to_dict(row)

    def get_user_by_id(self, user_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM admin_users WHERE id = ?", (user_id,)
        ).fetchone()
        return self._row_to_dict(row)

    def list_users(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, username, role, is_active, created_at, updated_at, "
            "last_login_at, password_changed_at FROM admin_users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def create_user(self, username: str, password_hash: str, role: str = "admin") -> int:
        now = _now()
        return self._execute_write_returning(
            """INSERT INTO admin_users (username, password_hash, role, is_active,
               created_at, updated_at, password_changed_at)
               VALUES (?, ?, ?, 1, ?, ?, ?)""",
            (username, password_hash, role, now, now, now),
        )

    def update_user_password(self, user_id: int, password_hash: str):
        now = _now()
        self._execute_write(
            "UPDATE admin_users SET password_hash = ?, password_changed_at = ?, updated_at = ? WHERE id = ?",
            (password_hash, now, now, user_id),
        )

    def update_user_role(self, user_id: int, role: str):
        self._execute_write(
            "UPDATE admin_users SET role = ?, updated_at = ? WHERE id = ?",
            (role, _now(), user_id),
        )

    def deactivate_user(self, user_id: int):
        self._execute_write(
            "UPDATE admin_users SET is_active = 0, updated_at = ? WHERE id = ?",
            (_now(), user_id),
        )

    def activate_user(self, user_id: int):
        self._execute_write(
            "UPDATE admin_users SET is_active = 1, updated_at = ? WHERE id = ?",
            (_now(), user_id),
        )

    def delete_user(self, user_id: int) -> bool:
        with self._write_lock:
            cur = self._conn.execute("DELETE FROM admin_users WHERE id = ?", (user_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def count_owners(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM admin_users WHERE role = 'owner' AND is_active = 1"
        ).fetchone()
        return row[0]

    def record_login(self, user_id: int):
        self._execute_write(
            "UPDATE admin_users SET last_login_at = ? WHERE id = ?",
            (_now(), user_id),
        )

    # ------------------------------------------------------------------
    # API Keys (salted SHA-256)
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_api_key(raw_key: str, salt: str) -> str:
        return hashlib.sha256((salt + raw_key).encode()).hexdigest()

    def create_api_key(self, name: str, scope: str, created_by: str) -> tuple[str, int]:
        """Create API key. Returns (raw_key, key_id). Raw key shown once only."""
        raw_key = "acore_" + secrets.token_hex(32)
        salt = secrets.token_hex(16)
        key_hash = self._hash_api_key(raw_key, salt)
        prefix = raw_key[:8]
        now = _now()

        key_id = self._execute_write_returning(
            """INSERT INTO api_keys (name, prefix, key_hash, salt, scope, is_active,
               created_at, created_by)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
            (name, prefix, key_hash, salt, scope, now, created_by),
        )
        return raw_key, key_id

    def validate_api_key(self, raw_key: str) -> dict | None:
        """Validate an API key. Returns key info dict or None."""
        if not raw_key or len(raw_key) < 8:
            return None

        prefix = raw_key[:8]
        rows = self._conn.execute(
            "SELECT * FROM api_keys WHERE prefix = ? AND is_active = 1",
            (prefix,),
        ).fetchall()

        for row in rows:
            row_dict = dict(row)
            # Check expiry
            if row_dict.get("expires_at"):
                try:
                    exp = datetime.fromisoformat(row_dict["expires_at"])
                    if datetime.now(timezone.utc) > exp:
                        continue
                except ValueError:
                    pass

            computed = self._hash_api_key(raw_key, row_dict["salt"])
            if secrets.compare_digest(computed, row_dict["key_hash"]):
                # Update last_used_at
                self._execute_write(
                    "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                    (_now(), row_dict["id"]),
                )
                # Don't return hash/salt
                return {
                    "id": row_dict["id"],
                    "name": row_dict["name"],
                    "prefix": row_dict["prefix"],
                    "scope": row_dict["scope"],
                    "created_by": row_dict["created_by"],
                }
        return None

    def list_api_keys(self) -> list[dict]:
        """List all API keys (never returns key_hash or salt)."""
        rows = self._conn.execute(
            "SELECT id, name, prefix, scope, is_active, created_at, last_used_at, "
            "expires_at, created_by FROM api_keys ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def revoke_api_key(self, key_id: int):
        self._execute_write(
            "UPDATE api_keys SET is_active = 0 WHERE id = ?", (key_id,)
        )

    # ------------------------------------------------------------------
    # Settings (typed values)
    # ------------------------------------------------------------------

    def get_setting(self, key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        if d.get("is_secret"):
            d["value"] = "••••••••"
        return d

    def get_setting_raw(self, key: str) -> dict | None:
        """Get setting without masking (for internal use only)."""
        row = self._conn.execute(
            "SELECT * FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return self._row_to_dict(row)

    def get_all_settings(self) -> list[dict]:
        """Get all settings. Secret values are masked."""
        rows = self._conn.execute(
            "SELECT * FROM app_settings ORDER BY key"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("is_secret"):
                d["value"] = "••••••••"
            result.append(d)
        return result

    def set_setting(self, key: str, value: str, value_type: str = "string",
                    is_secret: int = 0, updated_by: str = "system"):
        self._execute_write(
            """INSERT INTO app_settings (key, value, value_type, is_secret, updated_at, updated_by)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   value_type = excluded.value_type,
                   is_secret = excluded.is_secret,
                   updated_at = excluded.updated_at,
                   updated_by = excluded.updated_by""",
            (key, value, value_type, is_secret, _now(), updated_by),
        )

    def delete_setting(self, key: str):
        self._execute_write("DELETE FROM app_settings WHERE key = ?", (key,))

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    def log_action(self, username: str, action: str, target: str = "",
                   result: str = "", ip_address: str = "", user_agent: str = "",
                   details: str = "", user_id: int | None = None):
        self._execute_write(
            """INSERT INTO audit_log (timestamp, user_id, username, action, target,
               result, ip_address, user_agent, details)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_now(), user_id, username, action, target, result,
             ip_address, user_agent, details),
        )

    def get_audit_log(self, limit: int = 50, offset: int = 0,
                      user_filter: str = "", action_filter: str = "") -> list[dict]:
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params: list = []

        if user_filter:
            sql += " AND username = ?"
            params.append(user_filter)
        if action_filter:
            sql += " AND action = ?"
            params.append(action_filter)

        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_audit_log_count(self, user_filter: str = "", action_filter: str = "") -> int:
        sql = "SELECT COUNT(*) FROM audit_log WHERE 1=1"
        params: list = []

        if user_filter:
            sql += " AND username = ?"
            params.append(user_filter)
        if action_filter:
            sql += " AND action = ?"
            params.append(action_filter)

        return self._conn.execute(sql, params).fetchone()[0]

    # ------------------------------------------------------------------
    # Login attempts
    # ------------------------------------------------------------------

    def record_attempt(self, username: str, ip_address: str, success: bool):
        self._execute_write(
            "INSERT INTO login_attempts (username, ip_address, success, created_at) VALUES (?, ?, ?, ?)",
            (username, ip_address, 1 if success else 0, _now()),
        )

    def get_recent_failures(self, ip_address: str, minutes: int = 15) -> int:
        cutoff = datetime.now(timezone.utc).isoformat()
        # Simple approach: get failures in last N minutes
        rows = self._conn.execute(
            """SELECT COUNT(*) FROM login_attempts
               WHERE ip_address = ? AND success = 0
               AND created_at > datetime(?, '-' || ? || ' minutes')""",
            (ip_address, cutoff, minutes),
        ).fetchone()
        return rows[0]

    def cleanup_old_attempts(self, days: int = 30):
        cutoff = datetime.now(timezone.utc).isoformat()
        self._execute_write(
            "DELETE FROM login_attempts WHERE created_at < datetime(?, '-' || ? || ' days')",
            (cutoff, days),
        )

    # ------------------------------------------------------------------
    # Managed users
    # ------------------------------------------------------------------

    def list_managed_users(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM managed_users ORDER BY display_name"
        ).fetchall()
        return [dict(r) for r in rows]

    def create_managed_user(self, display_name: str, email: str = "",
                            notes: str = "", user_type: str = "friend") -> int:
        now = _now()
        return self._execute_write_returning(
            """INSERT INTO managed_users (display_name, email, notes, user_type,
               is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (display_name, email, notes, user_type, now, now),
        )

    def update_managed_user(self, user_id: int, **fields):
        allowed = {"display_name", "email", "notes", "user_type", "is_active"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [user_id]
        self._execute_write(
            f"UPDATE managed_users SET {set_clause} WHERE id = ?",
            tuple(values),
        )

    def delete_managed_user(self, user_id: int):
        self._execute_write("DELETE FROM managed_users WHERE id = ?", (user_id,))

    # ------------------------------------------------------------------
    # Database health
    # ------------------------------------------------------------------

    def get_db_size(self) -> int:
        """Return DB file size in bytes."""
        try:
            return os.path.getsize(self._db_path)
        except OSError:
            return 0

    def get_table_counts(self) -> dict:
        """Return row counts for each table."""
        tables = ["admin_users", "api_keys", "app_settings", "audit_log",
                   "login_attempts", "managed_users"]
        counts = {}
        for t in tables:
            try:
                row = self._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()
                counts[t] = row[0]
            except Exception:
                counts[t] = -1
        return counts

    def get_wal_mode(self) -> str:
        """Return current journal mode."""
        try:
            row = self._conn.execute("PRAGMA journal_mode").fetchone()
            return row[0] if row else "unknown"
        except Exception:
            return "unknown"

    def backup(self, backup_dir: str) -> str:
        """Create a backup of the database. Returns backup file path."""
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M")
        backup_path = os.path.join(backup_dir, f"admin_{timestamp}.db")

        with self._write_lock:
            backup_conn = sqlite3.connect(backup_path)
            self._conn.backup(backup_conn)
            backup_conn.close()

        logger.info("AdminDB backup created: %s", backup_path)
        return backup_path
