"""SQLite key/value memory store with namespace support."""

import sqlite3
import json
from datetime import datetime, timezone


CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS memory (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT DEFAULT '',
    confidence REAL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
)
"""


class MemoryStore:
    """Structured key/value memory backed by SQLite."""

    def __init__(self, db_path: str = "memory.db"):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def initialize(self):
        """Open DB and create table if needed."""
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(CREATE_TABLE)
        self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()

    def set(self, namespace: str, key: str, value, source: str = "", confidence: float = 1.0):
        """Set a key/value pair in a namespace. Upserts."""
        now = datetime.now(timezone.utc).isoformat()
        val_str = json.dumps(value) if not isinstance(value, str) else value
        self._conn.execute(
            """
            INSERT INTO memory (namespace, key, value, source, confidence, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(namespace, key) DO UPDATE SET
                value = excluded.value,
                source = excluded.source,
                confidence = excluded.confidence,
                updated_at = excluded.updated_at
            """,
            (namespace, key, val_str, source, confidence, now, now),
        )
        self._conn.commit()

    def get(self, namespace: str, key: str) -> dict | None:
        """Get a single entry. Returns dict or None."""
        cursor = self._conn.execute(
            "SELECT * FROM memory WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_dict(row)

    def list(self, namespace: str) -> list[dict]:
        """List all entries in a namespace."""
        cursor = self._conn.execute(
            "SELECT * FROM memory WHERE namespace = ? ORDER BY key",
            (namespace,),
        )
        return [_row_to_dict(row) for row in cursor.fetchall()]

    def delete(self, namespace: str, key: str) -> bool:
        """Delete a single entry. Returns True if deleted."""
        cursor = self._conn.execute(
            "DELETE FROM memory WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        self._conn.commit()
        return cursor.rowcount > 0


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict, parsing JSON values."""
    d = dict(row)
    try:
        d["value"] = json.loads(d["value"])
    except (json.JSONDecodeError, TypeError):
        pass  # keep as string
    return d
