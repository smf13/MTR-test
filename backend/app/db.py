"""SQLite persistence layer (aiosqlite) with schema management."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

import aiosqlite

log = logging.getLogger("mtr-tracker.db")

SCHEMA_VERSION = 3

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
    type TEXT NOT NULL DEFAULT 'mtr',
    options TEXT NOT NULL DEFAULT '{}',
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
    notify INTEGER NOT NULL DEFAULT 1,
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
    command TEXT,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_target_started ON runs(target_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);

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
-- Without this, every run deleted by the retention purge scans the whole events table for ON DELETE SET NULL.
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    "retention_days": 30,
    "asn_lookup": True,
    "reverse_dns": True,
    "webhook_url": "",
    "webhook_events": ["down", "recovered", "degraded", "route_change"],
    "pushover_enabled": False,
    "pushover_user_key": "",
    "pushover_api_token": "",
    "pushover_device": "",
    "pushover_sound": "",
    "pushover_priority": "auto",
    "pushover_events": ["down", "recovered", "degraded"],
    "base_url": "",
    "site_name": "MTR Tracker",
    # tag -> "#rrggbb"; tags without an entry get an automatic colour in the UI.
    "tag_colors": {},
    # Optional Globalping API token (globalping.io); raises the rate limits for the globalping probe type.
    "globalping_token": "",
    # MaxMind GeoLite2 credentials: the licence key enables the map on target pages (the database is
    # downloaded with it); the account ID is optional and switches to MaxMind's basic-auth download endpoint.
    "maxmind_account_id": "",
    "maxmind_license_key": "",
    # ip-api.com as the first GeoIP provider (batched lookups, no key); the MaxMind databases answer what it cannot.
    "ip_api_enabled": False,
}


class Transaction:
    """Statements issued inside one `Database.transaction()` block: one lock hold, one commit (or one rollback)."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        cur = await self._conn.execute(sql, tuple(params))
        last_id = cur.lastrowid or 0
        await cur.close()
        return last_id

    async def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        await self._conn.executemany(sql, [tuple(r) for r in rows])


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
        await self._migrate()
        await self._conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        await self._conn.commit()
        log.info("database ready at %s", self.path)

    async def _migrate(self) -> None:
        """Add columns introduced after the first release to databases created earlier."""
        wanted = {
            "targets": [("type", "TEXT NOT NULL DEFAULT 'mtr'"), ("options", "TEXT NOT NULL DEFAULT '{}'"), ("notify", "INTEGER NOT NULL DEFAULT 1")],
            "runs": [("details", "TEXT")],
        }
        for table, cols in wanted.items():
            async with self.conn.execute(f"PRAGMA table_info({table})") as cur:
                existing = {row[1] for row in await cur.fetchall()}
            for name, decl in cols:
                if name not in existing:
                    log.info("migrating: adding %s.%s", table, name)
                    await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

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

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Transaction]:
        """Run several statements atomically: every one is committed on success, none on error.

        The lock is held for the whole block, so no other writer can slip a commit in between
        (which is what `execute(commit=False)` followed by a second call allowed).
        """
        async with self._lock:
            try:
                yield Transaction(self.conn)
            except BaseException:
                await self.conn.rollback()
                raise
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

    async def purge_older_than(self, days: int, batch: int = 5000) -> int:
        """Delete runs (hops follow by cascade) and events older than `days`.

        Runs go in batches with a commit and a yield to the event loop between them: one huge DELETE
        holds the single connection for minutes when the retention window shrinks, and every API
        request waits behind it.
        """
        cutoff = time.time() - days * 86400
        removed = 0
        while True:
            async with self._lock:
                cur = await self.conn.execute(
                    "DELETE FROM runs WHERE id IN (SELECT id FROM runs WHERE started_at < ? LIMIT ?)", (cutoff, batch)
                )
                n = cur.rowcount or 0
                await cur.close()
                await self.conn.commit()
            removed += n
            if n < batch:
                break
            await asyncio.sleep(0.05)
        async with self._lock:
            cur = await self.conn.execute("DELETE FROM events WHERE created_at < ?", (cutoff,))
            await cur.close()
            await self.conn.commit()
        return removed


def rows_to_dicts(rows: Iterable[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
