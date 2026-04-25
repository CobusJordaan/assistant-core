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
            self._seed_missing_settings()
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

            CREATE TABLE IF NOT EXISTS portal_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'family',
                is_active INTEGER NOT NULL DEFAULT 1,
                chat_allowed INTEGER NOT NULL DEFAULT 1,
                image_gen_allowed INTEGER NOT NULL DEFAULT 1,
                coding_allowed INTEGER NOT NULL DEFAULT 1,
                vision_allowed INTEGER NOT NULL DEFAULT 1,
                voice_allowed INTEGER NOT NULL DEFAULT 1,
                daily_message_limit INTEGER NOT NULL DEFAULT 0,
                daily_image_limit INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS portal_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES portal_users(id),
                title TEXT NOT NULL DEFAULT 'New Chat',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS portal_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES portal_conversations(id),
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                intent TEXT DEFAULT '',
                model_used TEXT DEFAULT '',
                image_url TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS portal_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES portal_users(id),
                date TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                image_count INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id, date)
            );

            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_username ON audit_log(username);
            CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip_address, created_at);
            CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(prefix);
            CREATE INDEX IF NOT EXISTS idx_portal_conv_user ON portal_conversations(user_id);
            CREATE INDEX IF NOT EXISTS idx_portal_msg_conv ON portal_messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_portal_usage_user_date ON portal_usage(user_id, date);
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

    def _seed_missing_settings(self):
        """Insert any new default settings that don't already exist."""
        now = _now()

        # Add voice_allowed column to portal_users if missing
        try:
            self._conn.execute("SELECT voice_allowed FROM portal_users LIMIT 1")
        except sqlite3.OperationalError:
            self._conn.execute("ALTER TABLE portal_users ADD COLUMN voice_allowed INTEGER NOT NULL DEFAULT 1")
            self._conn.commit()
            logger.info("Added voice_allowed column to portal_users")

        # Migrate default_sampler → default_sampler_name
        old_sampler = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = 'default_sampler'"
        ).fetchone()
        new_sampler = self._conn.execute(
            "SELECT 1 FROM app_settings WHERE key = 'default_sampler_name'"
        ).fetchone()
        if old_sampler and not new_sampler:
            self._conn.execute(
                """INSERT INTO app_settings (key, value, value_type, is_secret, updated_at, updated_by)
                   VALUES ('default_sampler_name', ?, 'string', 0, ?, 'system')""",
                (old_sampler[0], now),
            )
            self._conn.execute("DELETE FROM app_settings WHERE key = 'default_sampler'")
            self._conn.commit()

        # Update old defaults if they still have the original values
        self._conn.execute(
            "UPDATE app_settings SET value = '640', updated_at = ? WHERE key = 'default_height' AND value = '512'",
            (now,),
        )
        self._conn.execute(
            "UPDATE app_settings SET value = '35', updated_at = ? WHERE key = 'default_steps' AND value = '20'",
            (now,),
        )
        self._conn.commit()

        extras = [
            ("image_bridge_enabled", "false", "bool", 0),
            ("image_bridge_host", "0.0.0.0", "string", 0),
            ("image_bridge_port", "5000", "string", 0),
            ("forge_base_url", "http://127.0.0.1:7860", "string", 0),
            ("forge_txt2img_endpoint", "/sdapi/v1/txt2img", "string", 0),
            ("forge_img2img_endpoint", "/sdapi/v1/img2img", "string", 0),
            ("default_width", "512", "int", 0),
            ("default_height", "640", "int", 0),
            ("default_steps", "35", "int", 0),
            ("default_cfg_scale", "7", "int", 0),
            ("default_sampler_name", "DPM++ 2M SDE", "string", 0),
            ("default_model", "", "string", 0),
            ("output_dir", "/opt/ai-assistant/data/image-bridge/output", "string", 0),
            ("public_base_url", "http://172.18.2.195:5000", "string", 0),
            ("default_negative_prompt", "cartoon, anime, illustration, painting, drawing, 3d render, cgi, plastic skin, doll, oversaturated, low quality, blurry, deformed, bad anatomy, extra fingers, distorted face, unrealistic eyes", "string", 0),
            ("default_scheduler", "Karras", "string", 0),
            ("default_checkpoint", "juggernautXL_v9.safetensors", "string", 0),
            ("enable_adetailer", "true", "bool", 0),
            ("adetailer_model", "face_yolov8n.pt", "string", 0),
            ("adetailer_prompt", "", "string", 0),
            ("adetailer_negative_prompt", "", "string", 0),
            # AI Router settings
            ("ai_router_enabled", "true", "bool", 0),
            ("ai_router_port", "5100", "string", 0),
            ("ai_router_ollama_url", "http://localhost:11434", "string", 0),
            ("ai_router_image_bridge_url", "http://127.0.0.1:5000", "string", 0),
            ("ai_router_image_bridge_api_key", "", "string", 1),
            ("ai_router_model_general", "qwen2.5:14b", "string", 0),
            ("ai_router_model_code", "qwen2.5-coder:14b", "string", 0),
            ("ai_router_model_vision", "qwen2.5vl:7b", "string", 0),
            ("ai_router_display_name", "Draadloze AI", "string", 0),
            ("ai_router_display_id", "draadloze-ai", "string", 0),
            ("ai_router_api_key_hash", "", "string", 1),
            ("ai_router_api_key_salt", "", "string", 1),
            # Voice settings
            ("voice_enabled", "false", "bool", 0),
            ("stt_provider", "both", "string", 0),
            ("stt_whisper_url", "http://127.0.0.1:5300", "string", 0),
            ("allow_browser_stt", "true", "bool", 0),
            ("allow_whisper_fallback", "true", "bool", 0),
            ("tts_piper_url", "http://127.0.0.1:5400", "string", 0),
            ("tts_voice", "en_US-lessac-medium", "string", 0),
            ("tts_afrikaans_voice", "af-ZA-AdriNeural", "string", 0),
            ("voice_max_seconds", "60", "int", 0),
            ("voice_audio_dir", "/opt/ai-assistant/data/portal/audio", "string", 0),
        ]
        self._executemany_write(
            """INSERT OR IGNORE INTO app_settings (key, value, value_type, is_secret, updated_at, updated_by)
               VALUES (?, ?, ?, ?, ?, 'system')""",
            [(k, v, vt, s, now) for k, v, vt, s in extras],
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

    def get_distinct_actions(self) -> list[str]:
        """Return all distinct action values from audit_log, sorted."""
        rows = self._conn.execute(
            "SELECT DISTINCT action FROM audit_log ORDER BY action"
        ).fetchall()
        return [r[0] for r in rows]

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
                   "login_attempts", "managed_users", "portal_users",
                   "portal_conversations", "portal_messages", "portal_usage"]
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

    # ------------------------------------------------------------------
    # Portal users
    # ------------------------------------------------------------------

    def create_portal_user(self, username: str, display_name: str,
                           password_hash: str, role: str = "family") -> int:
        now = _now()
        return self._execute_write_returning(
            """INSERT INTO portal_users (username, display_name, password_hash, role,
               created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)""",
            (username, display_name, password_hash, role, now, now),
        )

    def get_portal_user(self, username: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM portal_users WHERE username = ?", (username,)
        ).fetchone()
        return self._row_to_dict(row)

    def get_portal_user_by_id(self, user_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM portal_users WHERE id = ?", (user_id,)
        ).fetchone()
        return self._row_to_dict(row)

    def list_portal_users(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM portal_users ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update_portal_user(self, user_id: int, **fields) -> bool:
        allowed = {"display_name", "password_hash", "role", "is_active",
                    "chat_allowed", "image_gen_allowed", "coding_allowed",
                    "vision_allowed", "voice_allowed",
                    "daily_message_limit", "daily_image_limit",
                    "last_login_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [user_id]
        self._execute_write(f"UPDATE portal_users SET {cols} WHERE id = ?", vals)
        return True

    def delete_portal_user(self, user_id: int):
        with self._write_lock:
            self._conn.execute("DELETE FROM portal_messages WHERE conversation_id IN "
                               "(SELECT id FROM portal_conversations WHERE user_id = ?)", (user_id,))
            self._conn.execute("DELETE FROM portal_conversations WHERE user_id = ?", (user_id,))
            self._conn.execute("DELETE FROM portal_usage WHERE user_id = ?", (user_id,))
            self._conn.execute("DELETE FROM portal_users WHERE id = ?", (user_id,))
            self._conn.commit()

    # ------------------------------------------------------------------
    # Portal conversations & messages
    # ------------------------------------------------------------------

    def create_conversation(self, user_id: int, title: str = "New Chat") -> int:
        now = _now()
        return self._execute_write_returning(
            "INSERT INTO portal_conversations (user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, title, now, now),
        )

    def list_conversations(self, user_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM portal_conversations WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_conversation(self, conv_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM portal_conversations WHERE id = ?", (conv_id,)
        ).fetchone()
        return self._row_to_dict(row)

    def update_conversation_title(self, conv_id: int, title: str):
        self._execute_write(
            "UPDATE portal_conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), conv_id),
        )

    def touch_conversation(self, conv_id: int):
        self._execute_write(
            "UPDATE portal_conversations SET updated_at = ? WHERE id = ?",
            (_now(), conv_id),
        )

    def delete_conversation(self, conv_id: int):
        with self._write_lock:
            self._conn.execute("DELETE FROM portal_messages WHERE conversation_id = ?", (conv_id,))
            self._conn.execute("DELETE FROM portal_conversations WHERE id = ?", (conv_id,))
            self._conn.commit()

    def add_message(self, conversation_id: int, role: str, content: str,
                    intent: str = "", model_used: str = "", image_url: str = "") -> int:
        now = _now()
        msg_id = self._execute_write_returning(
            """INSERT INTO portal_messages (conversation_id, role, content, intent,
               model_used, image_url, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (conversation_id, role, content, intent, model_used, image_url, now),
        )
        self.touch_conversation(conversation_id)
        return msg_id

    def get_messages(self, conversation_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM portal_messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Portal usage tracking
    # ------------------------------------------------------------------

    def get_daily_usage(self, user_id: int, date: str) -> dict:
        row = self._conn.execute(
            "SELECT * FROM portal_usage WHERE user_id = ? AND date = ?",
            (user_id, date),
        ).fetchone()
        if row:
            return self._row_to_dict(row)
        return {"user_id": user_id, "date": date, "message_count": 0, "image_count": 0}

    def increment_usage(self, user_id: int, date: str, messages: int = 0, images: int = 0):
        self._execute_write(
            """INSERT INTO portal_usage (user_id, date, message_count, image_count)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, date) DO UPDATE SET
               message_count = message_count + ?, image_count = image_count + ?""",
            (user_id, date, messages, images, messages, images),
        )
