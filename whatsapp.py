"""WhatsApp webhook helpers: phone normalization, dedup, TwiML responses."""

import re
import sqlite3
import logging
from datetime import datetime, timezone

logger = logging.getLogger("assistant-core.whatsapp")


# ---------------------------------------------------------------------------
# Phone normalization
# ---------------------------------------------------------------------------

def normalize_phone(raw: str) -> str:
    """Normalize a phone number for billing API lookup.

    Strips 'whatsapp:' prefix, handles +27/27/0 formats.
    Returns digits-only string in local 0-prefixed format (e.g. '0821234567').
    """
    phone = raw.strip()

    # Strip whatsapp: prefix
    if phone.lower().startswith("whatsapp:"):
        phone = phone[9:]

    # Strip everything except digits and leading +
    phone = phone.strip()
    digits = re.sub(r"[^\d]", "", phone)

    # Convert +27 / 27 prefix to 0
    if digits.startswith("27") and len(digits) >= 11:
        digits = "0" + digits[2:]

    return digits


# ---------------------------------------------------------------------------
# MessageSid dedup (SQLite)
# ---------------------------------------------------------------------------

CREATE_DEDUP_TABLE = """
CREATE TABLE IF NOT EXISTS whatsapp_dedup (
    message_sid TEXT PRIMARY KEY,
    sender TEXT NOT NULL,
    processed_at TEXT NOT NULL
)
"""


class WhatsAppDedup:
    """Lightweight MessageSid dedup backed by SQLite."""

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

    def is_duplicate(self, message_sid: str) -> bool:
        """Check if this MessageSid has already been processed."""
        cursor = self._conn.execute(
            "SELECT 1 FROM whatsapp_dedup WHERE message_sid = ?",
            (message_sid,),
        )
        return cursor.fetchone() is not None

    def mark_processed(self, message_sid: str, sender: str):
        """Record that this MessageSid has been processed."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO whatsapp_dedup (message_sid, sender, processed_at) VALUES (?, ?, ?)",
            (message_sid, sender, now),
        )
        self._conn.commit()


# ---------------------------------------------------------------------------
# TwiML response helpers
# ---------------------------------------------------------------------------

def twiml_reply(message: str) -> str:
    """Build a minimal TwiML MessagingResponse with a single reply."""
    # Escape XML special characters
    safe = (
        message
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Message>{safe}</Message>"
        "</Response>"
    )


def twiml_empty() -> str:
    """Build an empty TwiML response (no reply)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response></Response>"
    )
