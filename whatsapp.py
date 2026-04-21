"""WhatsApp message dedup backed by SQLite."""

import sqlite3
import logging
from datetime import datetime, timezone

logger = logging.getLogger("assistant-core.whatsapp")


# ---------------------------------------------------------------------------
# Message dedup (SQLite)
# ---------------------------------------------------------------------------

CREATE_DEDUP_TABLE = """
CREATE TABLE IF NOT EXISTS whatsapp_dedup (
    message_id TEXT PRIMARY KEY,
    sender TEXT NOT NULL,
    reply TEXT,
    processed_at TEXT NOT NULL
)
"""


class WhatsAppDedup:
    """Lightweight message_id dedup backed by SQLite."""

    def __init__(self, db_path: str = "memory.db"):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def initialize(self):
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute(CREATE_DEDUP_TABLE)
        self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()

    def is_duplicate(self, message_id: str) -> bool:
        """Check if this message_id has already been processed."""
        cursor = self._conn.execute(
            "SELECT 1 FROM whatsapp_dedup WHERE message_id = ?",
            (message_id,),
        )
        return cursor.fetchone() is not None

    def get_reply(self, message_id: str) -> str | None:
        """Get the stored reply for a previously processed message_id."""
        cursor = self._conn.execute(
            "SELECT reply FROM whatsapp_dedup WHERE message_id = ?",
            (message_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def mark_processed(self, message_id: str, sender: str, reply: str | None = None):
        """Record that this message_id has been processed, with its reply."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO whatsapp_dedup (message_id, sender, reply, processed_at) VALUES (?, ?, ?, ?)",
            (message_id, sender, reply, now),
        )
        self._conn.commit()
