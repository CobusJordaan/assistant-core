"""SQLite-backed WhatsApp session store keyed by phone number."""

import sqlite3
import json
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("assistant-core.wa-session")

SESSION_TIMEOUT_MINUTES = 30
MAX_HISTORY_ENTRIES = 10

CREATE_WA_SESSION_TABLE = """
CREATE TABLE IF NOT EXISTS whatsapp_sessions (
    from_number     TEXT PRIMARY KEY,
    client_id       INTEGER,
    client_name     TEXT DEFAULT '',
    greeted_at      TEXT,
    last_message_at TEXT NOT NULL,
    last_reply      TEXT DEFAULT '',
    history         TEXT DEFAULT '[]',
    active_menu_key TEXT,
    menu_created_at TEXT,
    support_category TEXT,
    awaiting_support_description INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)
"""

# Migration: add menu columns to existing tables
_MENU_COLUMNS = [
    ("active_menu_key", "TEXT"),
    ("menu_created_at", "TEXT"),
    ("support_category", "TEXT"),
    ("awaiting_support_description", "INTEGER DEFAULT 0"),
    ("awaiting_account_lookup", "INTEGER DEFAULT 0"),
    ("awaiting_email_verification", "INTEGER DEFAULT 0"),
    ("pending_client_id", "INTEGER"),
    ("pending_client_name", "TEXT DEFAULT ''"),
    ("pending_client_email", "TEXT DEFAULT ''"),
]


class WhatsAppSession:
    """Snapshot of a single sender's session."""

    __slots__ = (
        "from_number", "client_id", "client_name", "greeted_at",
        "last_message_at", "last_reply", "history",
        "active_menu_key", "menu_created_at",
        "support_category", "awaiting_support_description",
        "awaiting_account_lookup", "awaiting_email_verification",
        "pending_client_id", "pending_client_name", "pending_client_email",
        "created_at", "updated_at",
    )

    def __init__(self, from_number: str, client_id: int | None, client_name: str,
                 greeted_at: str | None, last_message_at: str, last_reply: str,
                 history: list[dict], created_at: str, updated_at: str,
                 active_menu_key: str | None = None, menu_created_at: str | None = None,
                 support_category: str | None = None,
                 awaiting_support_description: bool = False,
                 awaiting_account_lookup: bool = False,
                 awaiting_email_verification: bool = False,
                 pending_client_id: int | None = None,
                 pending_client_name: str = "",
                 pending_client_email: str = ""):
        self.from_number = from_number
        self.client_id = client_id
        self.client_name = client_name
        self.greeted_at = greeted_at
        self.last_message_at = last_message_at
        self.last_reply = last_reply
        self.history = history
        self.active_menu_key = active_menu_key
        self.menu_created_at = menu_created_at
        self.support_category = support_category
        self.awaiting_support_description = awaiting_support_description
        self.awaiting_account_lookup = awaiting_account_lookup
        self.awaiting_email_verification = awaiting_email_verification
        self.pending_client_id = pending_client_id
        self.pending_client_name = pending_client_name
        self.pending_client_email = pending_client_email
        self.created_at = created_at
        self.updated_at = updated_at

    @property
    def is_expired(self) -> bool:
        if not self.last_message_at:
            return True
        try:
            last = datetime.fromisoformat(self.last_message_at)
            return (datetime.now(timezone.utc) - last) > timedelta(minutes=SESSION_TIMEOUT_MINUTES)
        except ValueError:
            return True

    @property
    def needs_greeting(self) -> bool:
        return self.greeted_at is None


class WhatsAppSessionStore:
    """SQLite-backed session store keyed by from_number."""

    def __init__(self, db_path: str = "memory.db"):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def initialize(self):
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(CREATE_WA_SESSION_TABLE)
        # Migrate: add menu columns if missing
        for col_name, col_type in _MENU_COLUMNS:
            try:
                self._conn.execute(
                    f"ALTER TABLE whatsapp_sessions ADD COLUMN {col_name} {col_type}"
                )
            except sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()
        logger.info("WhatsApp session store initialized")

    def close(self):
        if self._conn:
            self._conn.close()

    def get_or_create(self, from_number: str, client_id: int | None = None,
                      client_name: str = "") -> WhatsAppSession:
        """Load existing session or create a fresh one.

        If the existing session is expired (>30 min inactivity), resets it.
        """
        row = self._conn.execute(
            "SELECT * FROM whatsapp_sessions WHERE from_number = ?",
            (from_number,),
        ).fetchone()

        now = datetime.now(timezone.utc).isoformat()

        if row:
            session = self._row_to_session(row)
            if session.is_expired:
                logger.info("WA session expired for %s, resetting", from_number)
                return self._reset_session(from_number, client_id, client_name, now)
            # Update client info if changed (including clearing when number removed)
            if session.client_id != client_id:
                self._conn.execute(
                    "UPDATE whatsapp_sessions SET client_id = ?, client_name = ?, updated_at = ? WHERE from_number = ?",
                    (client_id, client_name or '', now, from_number),
                )
                self._conn.commit()
                session.client_id = client_id
                session.client_name = client_name or ''
            return session

        return self._create_session(from_number, client_id, client_name, now)

    def mark_greeted(self, from_number: str):
        """Record that a greeting was sent in this session."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE whatsapp_sessions SET greeted_at = ?, updated_at = ? WHERE from_number = ?",
            (now, now, from_number),
        )
        self._conn.commit()

    def set_menu(self, from_number: str, menu_key: str):
        """Set the active menu for this session."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE whatsapp_sessions SET active_menu_key = ?, menu_created_at = ?, updated_at = ? WHERE from_number = ?",
            (menu_key, now, now, from_number),
        )
        self._conn.commit()

    def clear_menu(self, from_number: str):
        """Clear the active menu for this session."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE whatsapp_sessions SET active_menu_key = NULL, menu_created_at = NULL, updated_at = ? WHERE from_number = ?",
            (now, from_number),
        )
        self._conn.commit()

    def set_support_category(self, from_number: str, category: str):
        """Set support category and flag awaiting description."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE whatsapp_sessions
               SET support_category = ?, awaiting_support_description = 1, updated_at = ?
               WHERE from_number = ?""",
            (category, now, from_number),
        )
        self._conn.commit()

    def clear_support_state(self, from_number: str):
        """Clear support category and awaiting flag."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE whatsapp_sessions
               SET support_category = NULL, awaiting_support_description = 0, updated_at = ?
               WHERE from_number = ?""",
            (now, from_number),
        )
        self._conn.commit()

    # --- Account lookup / email verification ---

    def set_awaiting_account_lookup(self, from_number: str):
        """Flag session as waiting for account number/name input."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE whatsapp_sessions
               SET awaiting_account_lookup = 1, updated_at = ?
               WHERE from_number = ?""",
            (now, from_number),
        )
        self._conn.commit()

    def set_awaiting_email_verification(self, from_number: str, client_id: int,
                                        client_name: str, email: str):
        """Store pending client and flag session as awaiting email verification."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE whatsapp_sessions
               SET awaiting_account_lookup = 0, awaiting_email_verification = 1,
                   pending_client_id = ?, pending_client_name = ?, pending_client_email = ?,
                   updated_at = ?
               WHERE from_number = ?""",
            (client_id, client_name, email, now, from_number),
        )
        self._conn.commit()

    def confirm_client(self, from_number: str):
        """Promote pending client to actual client and clear lookup state."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE whatsapp_sessions
               SET client_id = pending_client_id, client_name = pending_client_name,
                   awaiting_account_lookup = 0, awaiting_email_verification = 0,
                   pending_client_id = NULL, pending_client_name = '',
                   pending_client_email = '', updated_at = ?
               WHERE from_number = ?""",
            (now, from_number),
        )
        self._conn.commit()

    def clear_account_lookup_state(self, from_number: str):
        """Clear all account lookup / email verification state."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE whatsapp_sessions
               SET awaiting_account_lookup = 0, awaiting_email_verification = 0,
                   pending_client_id = NULL, pending_client_name = '',
                   pending_client_email = '', updated_at = ?
               WHERE from_number = ?""",
            (now, from_number),
        )
        self._conn.commit()

    def update_after_reply(self, from_number: str, user_message: str, reply: str):
        """Update session after processing: bump timestamps, store reply, append history."""
        now = datetime.now(timezone.utc).isoformat()
        row = self._conn.execute(
            "SELECT history FROM whatsapp_sessions WHERE from_number = ?",
            (from_number,),
        ).fetchone()
        history = json.loads(row["history"]) if row else []
        history.append({"role": "user", "text": user_message, "ts": now})
        history.append({"role": "assistant", "text": reply, "ts": now})
        # Keep last N pairs
        history = history[-(MAX_HISTORY_ENTRIES * 2):]

        self._conn.execute(
            """UPDATE whatsapp_sessions
               SET last_message_at = ?, last_reply = ?, history = ?, updated_at = ?
               WHERE from_number = ?""",
            (now, reply, json.dumps(history), now, from_number),
        )
        self._conn.commit()

    # --- internal helpers ---

    def _create_session(self, from_number: str, client_id: int | None,
                        client_name: str, now: str) -> WhatsAppSession:
        self._conn.execute(
            """INSERT INTO whatsapp_sessions
               (from_number, client_id, client_name, greeted_at, last_message_at,
                last_reply, history, created_at, updated_at)
               VALUES (?, ?, ?, NULL, ?, '', '[]', ?, ?)""",
            (from_number, client_id, client_name, now, now, now),
        )
        self._conn.commit()
        logger.info("WA session created for %s", from_number)
        return WhatsAppSession(from_number, client_id, client_name, None, now, "", [], now, now)

    def _reset_session(self, from_number: str, client_id: int | None,
                       client_name: str, now: str) -> WhatsAppSession:
        self._conn.execute(
            """UPDATE whatsapp_sessions
               SET client_id = ?, client_name = ?, greeted_at = NULL,
                   last_message_at = ?, last_reply = '', history = '[]',
                   active_menu_key = NULL, menu_created_at = NULL,
                   support_category = NULL, awaiting_support_description = 0,
                   awaiting_account_lookup = 0, awaiting_email_verification = 0,
                   pending_client_id = NULL, pending_client_name = '',
                   pending_client_email = '',
                   updated_at = ?
               WHERE from_number = ?""",
            (client_id, client_name, now, now, from_number),
        )
        self._conn.commit()
        return WhatsAppSession(from_number, client_id, client_name, None, now, "", [], now, now)

    def _row_to_session(self, row: sqlite3.Row) -> WhatsAppSession:
        return WhatsAppSession(
            from_number=row["from_number"],
            client_id=row["client_id"],
            client_name=row["client_name"] or "",
            greeted_at=row["greeted_at"],
            last_message_at=row["last_message_at"],
            last_reply=row["last_reply"] or "",
            history=json.loads(row["history"] or "[]"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            active_menu_key=row["active_menu_key"],
            menu_created_at=row["menu_created_at"],
            support_category=row["support_category"],
            awaiting_support_description=bool(row["awaiting_support_description"]),
            awaiting_account_lookup=bool(row["awaiting_account_lookup"]),
            awaiting_email_verification=bool(row["awaiting_email_verification"]),
            pending_client_id=row["pending_client_id"],
            pending_client_name=row["pending_client_name"] or "",
            pending_client_email=row["pending_client_email"] or "",
        )
