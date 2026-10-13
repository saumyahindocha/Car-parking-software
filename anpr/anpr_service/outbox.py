"""Durable, strictly ordered local outbox for events (SQLite, WAL).

Every event is written here *before* any delivery attempt, so a crash, a
power cut or a backend outage never loses an event.  The emitter always
delivers the lowest sequence number first and does not skip ahead while it
is failing, which gives the backend events in crossing order.  Events the
backend rejects permanently (4xx other than 408/409/429) move to a
dead-letter table instead of blocking the queue forever.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    created_ms INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_ms INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS dead_letter (
    seq INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_ms INTEGER NOT NULL,
    attempts INTEGER NOT NULL,
    error TEXT,
    failed_ms INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class OutboxItem:
    seq: int
    event_id: str
    payload: dict[str, Any]
    created_ms: int
    attempts: int
    next_attempt_ms: int


class Outbox:
    """Thread-safe (per instance) and multi-process-safe (SQLite locking) queue."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, timeout=10.0, isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def put(self, payload: dict[str, Any]) -> int | None:
        """Append an event; idempotent on ``event_id``.  Returns its sequence number."""
        event_id = str(payload["event_id"])
        data = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO outbox (event_id, payload, created_ms) VALUES (?, ?, ?)",
                (event_id, data, int(time.time() * 1000)),
            )
            if cur.rowcount == 0:
                row = self._conn.execute("SELECT seq FROM outbox WHERE event_id=?", (event_id,)).fetchone()
                return int(row[0]) if row else None
            return int(cur.lastrowid or 0)

    def head(self) -> OutboxItem | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT seq, event_id, payload, created_ms, attempts, next_attempt_ms "
                "FROM outbox ORDER BY seq ASC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return OutboxItem(int(row[0]), row[1], json.loads(row[2]), int(row[3]), int(row[4]), int(row[5]))

    def ack(self, seq: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM outbox WHERE seq=?", (seq,))

    def fail(self, seq: int, error: str, next_attempt_ms: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE outbox SET attempts=attempts+1, last_error=?, next_attempt_ms=? WHERE seq=?",
                (error[:500], int(next_attempt_ms), seq),
            )

    def dead_letter(self, seq: int, error: str) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO dead_letter (seq, event_id, payload, created_ms, attempts, error, failed_ms) "
                    "SELECT seq, event_id, payload, created_ms, attempts + 1, ?, ? FROM outbox WHERE seq=?",
                    (error[:500], int(time.time() * 1000), seq),
                )
                self._conn.execute("DELETE FROM outbox WHERE seq=?", (seq,))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def depth(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])

    def dead_letters(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT seq, event_id, error FROM dead_letter ORDER BY seq").fetchall()
        return [{"seq": r[0], "event_id": r[1], "error": r[2]} for r in rows]
