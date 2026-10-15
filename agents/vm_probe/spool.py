"""Bounded SQLite outbox: only a complete ACK deletes pending messages."""

from __future__ import annotations

import json
import random
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

PRIORITY_HIGH = 0
PRIORITY_NORMAL = 1
PRIORITY_LOW = 2


class BoundedSpool:
    def __init__(
        self, *, max_bytes: int, max_seconds: float, path: str | Path | None = None
    ) -> None:
        self.max_bytes, self.max_seconds = max_bytes, max_seconds
        self._lock = threading.RLock()
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS outbox(event_id TEXT PRIMARY KEY,payload TEXT NOT NULL,size INTEGER NOT NULL,priority INTEGER NOT NULL,enqueued_at REAL NOT NULL); CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
        )
        self._db.commit()

    def get_meta(self, key: str, default=None):
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    @property
    def dropped_count(self) -> int:
        return int(self.get_meta("dropped_count", 0))

    @dropped_count.setter
    def dropped_count(self, value: int) -> None:
        self.set_meta("dropped_count", value)

    @property
    def gaps(self) -> list[dict]:
        return self.get_meta("gaps", [])

    def record_gap(self, start: float, end: float) -> None:
        self.set_meta(
            "gaps",
            (
                self.gaps
                + [
                    {
                        "start": datetime.fromtimestamp(start, UTC).isoformat(),
                        "end": datetime.fromtimestamp(end, UTC).isoformat(),
                    }
                ]
            )[-100:],
        )

    def _drop(self, rows: list, now: float) -> None:
        if rows:
            self._db.executemany("DELETE FROM outbox WHERE event_id=?", [(r[0],) for r in rows])
            self.dropped_count += len(rows)
            self.record_gap(min(r[1] for r in rows), now)

    def _expire(self, now: float) -> None:
        self._drop(
            self._db.execute(
                "SELECT event_id,enqueued_at FROM outbox WHERE enqueued_at<?",
                (now - self.max_seconds,),
            ).fetchall(),
            now,
        )

    def enqueue(self, payload: dict, priority: int = PRIORITY_NORMAL) -> bool:
        event_id = str(payload["event_id"])
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        size, now = len(serialized.encode()), time.time()
        with self._lock, self._db:
            if self._db.execute("SELECT 1 FROM outbox WHERE event_id=?", (event_id,)).fetchone():
                return True
            self._expire(now)
            if size > self.max_bytes:
                self.dropped_count += 1
                self.record_gap(now, now)
                return False
            used = self._db.execute("SELECT COALESCE(SUM(size),0) FROM outbox").fetchone()[0]
            while used + size > self.max_bytes:
                row = self._db.execute(
                    "SELECT event_id,enqueued_at,size,priority FROM outbox ORDER BY priority DESC,enqueued_at LIMIT 1"
                ).fetchone()
                if row is None or row[3] < priority:
                    self.dropped_count += 1
                    self.record_gap(now, now)
                    return False
                self._drop([row], now)
                used -= row[2]
            self._db.execute(
                "INSERT INTO outbox VALUES (?,?,?,?,?)", (event_id, serialized, size, priority, now)
            )
        return True

    def peek_batch(self, max_items: int = 100, max_bytes: int = 7 * 1024 * 1024) -> list[dict]:
        with self._lock, self._db:
            self._expire(time.time())
            rows = self._db.execute(
                "SELECT payload,size FROM outbox ORDER BY priority,enqueued_at LIMIT ?",
                (max_items,),
            ).fetchall()
            out, used = [], 0
            for payload, size in rows:
                if out and used + size > max_bytes:
                    break
                out.append(json.loads(payload))
                used += size
            return out

    def dequeue_batch(self, max_items: int = 100) -> list[dict]:
        """Compatibility name: messages remain until acknowledge()."""
        return self.peek_batch(max_items)

    def acknowledge(self, event_ids: list[str]) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "DELETE FROM outbox WHERE event_id=?", [(event_id,) for event_id in event_ids]
            )

    @property
    def depth(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    @property
    def oldest_queued_at(self) -> str | None:
        with self._lock:
            oldest = self._db.execute("SELECT MIN(enqueued_at) FROM outbox").fetchone()[0]
            return datetime.fromtimestamp(oldest, UTC).isoformat() if oldest is not None else None

    @property
    def oldest_age_seconds(self) -> float | None:
        with self._lock:
            oldest = self._db.execute("SELECT MIN(enqueued_at) FROM outbox").fetchone()[0]
            return max(0, time.time() - oldest) if oldest is not None else None

    def close(self) -> None:
        self._db.close()


def backoff_delay(attempt: int, *, base: float = 1.5, cap: float = 60.0) -> float:
    return random.uniform(0, min(cap, base ** min(attempt, 30)))
