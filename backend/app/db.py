"""SQLite persistence layer (aiosqlite) with schema management."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

import aiosqlite

log = logging.getLogger("mtr-tracker.db")

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    interval_sec INTEGER NOT NULL DEFAULT 300,
    count INTEGER NOT NULL DEFAULT 10,
    probe_interval REAL NOT NULL DEFAULT 1.0,
    protocol TEXT NOT NULL DEFAULT 'icmp',
    port INTEGER,
    packet_size INTEGER NOT NULL DEFAULT 64,
    ip_version TEXT NOT NULL DEFAULT 'auto',
    max_hops INTEGER NOT NULL DEFAULT 30,
    enabled INTEGER NOT NULL DEFAULT 1,
    alert_loss_pct REAL NOT NULL DEFAULT 5.0,
    alert_latency_ms REAL NOT NULL DEFAULT 200.0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    next_run_at REAL,
    last_status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    started_at REAL NOT NULL,
    finished_at REAL,
    duration_ms REAL,
    status TEXT NOT NULL,
    error TEXT,
    src TEXT,
    dst_ip TEXT,
    reached INTEGER NOT NULL DEFAULT 0,
    hop_count INTEGER NOT NULL DEFAULT 0,
    sent INTEGER,
    loss_pct REAL,
    last_ms REAL,
    avg_ms REAL,
    best_ms REAL,
    worst_ms REAL,
    stdev_ms REAL,
    jitter_avg_ms REAL,
    jitter_max_ms REAL,
    route_hash TEXT,
    route_changed INTEGER NOT NULL DEFAULT 0,
    command TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_target_started ON runs(target_id, started_at DESC);

CREATE TABLE IF NOT EXISTS hops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    hop_no INTEGER NOT NULL,
    ip TEXT,
    hostname TEXT,
    asn TEXT,
    loss_pct REAL NOT NULL DEFAULT 0,
    sent INTEGER NOT NULL DEFAULT 0,
    received INTEGER NOT NULL DEFAULT 0,
    last_ms REAL,
    avg_ms REAL,
    best_ms REAL,
    worst_ms REAL,
    stdev_ms REAL,
    gmean_ms REAL,
    jitter_ms REAL,
    jitter_avg_ms REAL,
    jitter_max_ms REAL,
    jitter_int_ms REAL
);
CREATE INDEX IF NOT EXISTS idx_hops_run ON hops(run_id, hop_no);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id INTEGER REFERENCES targets(id) ON DELETE CASCADE,
    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    details TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_target ON events(target_id, created_at DESC);
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    "retention_days": 30,
    "asn_lookup": True,
    "reverse_dns": True,
    "webhook_url": "",
    "webhook_events": ["down", "recovered", "degraded", "route_change"],
    "site_name": "MTR Tracker",
}


class Database:
    """Thin async wrapper around a single aiosqlite connection."""

    def __init__(self, path: Path):
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        await self._conn.commit()
        log.info("database ready at %s", self.path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # -- generic helpers -------------------------------------------------

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, tuple(params)) as cur:
            return list(await cur.fetchall())

    async def execute(self, sql: str, params: Iterable[Any] = (), commit: bool = True) -> int:
        async with self._lock:
            cur = await self.conn.execute(sql, tuple(params))
            last_id = cur.lastrowid or 0
            await cur.close()
            if commit:
                await self.conn.commit()
            return last_id

    async def executemany(self, sql: str, rows: Iterable[Iterable[Any]], commit: bool = True) -> None:
        async with self._lock:
            await self.conn.executemany(sql, [tuple(r) for r in rows])
            if commit:
                await self.conn.commit()

    async def commit(self) -> None:
        await self.conn.commit()

    # -- settings --------------------------------------------------------

    async def get_settings(self) -> dict[str, Any]:
        rows = await self.fetchall("SELECT key, value FROM settings")
        result = dict(DEFAULT_SETTINGS)
        for row in rows:
            try:
                result[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                result[row["key"]] = row["value"]
        return result

    async def set_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        await self.executemany(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [(k, json.dumps(v)) for k, v in values.items()],
        )
        return await self.get_settings()

    async def db_size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    async def purge_older_than(self, days: int) -> int:
        cutoff = time.time() - days * 86400
        async with self._lock:
            cur = await self.conn.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,))
            removed = cur.rowcount or 0
            await cur.close()
            cur = await self.conn.execute("DELETE FROM events WHERE created_at < ?", (cutoff,))
            await cur.close()
            await self.conn.commit()
        return removed


def rows_to_dicts(rows: Iterable[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


async def iter_rows(rows: list[aiosqlite.Row]) -> AsyncIterator[dict[str, Any]]:
    for r in rows:
        yield dict(r)
