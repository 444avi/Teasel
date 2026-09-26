"""Atomic, per-account request limits shared by local Gunicorn workers.

Each request opens its own SQLite connection. BEGIN IMMEDIATE serializes the
read/increment across processes, so two workers cannot both accept the last
available request. The database must be on one shared, writable local volume.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Callable, Mapping


class RateLimiter:
    def __init__(
        self,
        path: str | Path,
        limits: Mapping[str, int],
        *,
        window_seconds: int = 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not limits or any(value < 1 for value in limits.values()) or window_seconds < 1:
            raise ValueError("rate limits and window must be positive")
        self.path = Path(path).resolve()
        self.limits = dict(limits)
        self.window_seconds = window_seconds
        self.clock = clock

        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # The file stores opaque account IDs. Restrict permissions at creation.
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        db = self._connect()
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS requests ("
                "account_id TEXT NOT NULL, endpoint TEXT NOT NULL, "
                "window_start INTEGER NOT NULL, hits INTEGER NOT NULL, "
                "PRIMARY KEY (account_id, endpoint, window_start))"
            )
            db.commit()
        finally:
            db.close()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def consume(self, account_id: str, endpoint: str) -> int | None:
        """Return Retry-After seconds if limited, otherwise record one request."""
        if not account_id or endpoint not in self.limits:
            raise ValueError("verified account ID and known endpoint required")
        now = int(self.clock())
        start = now - now % self.window_seconds
        retry_after = max(1, start + self.window_seconds - now)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT hits FROM requests WHERE account_id=? AND endpoint=? AND window_start=?",
                (account_id, endpoint, start),
            ).fetchone()
            if row and row[0] >= self.limits[endpoint]:
                db.rollback()
                return retry_after
            db.execute(
                "INSERT INTO requests (account_id, endpoint, window_start, hits) "
                "VALUES (?, ?, ?, 1) ON CONFLICT(account_id, endpoint, window_start) "
                "DO UPDATE SET hits=hits+1",
                (account_id, endpoint, start),
            )
            db.execute("DELETE FROM requests WHERE window_start < ?", (start - self.window_seconds,))
            db.commit()
            return None
        finally:
            db.close()
