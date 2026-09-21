"""The one-time import of a SQLite database: automatic at start, by hand through the CLI, and every way it must refuse."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from helpers import app_client, app_of, fresh_test_database

from app import migrate
from app.db import Database

# The schema of the last SQLite release, verbatim (schema version 3), and the first release without the later columns.
LEGACY_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, host TEXT NOT NULL, type TEXT NOT NULL DEFAULT 'mtr',
    options TEXT NOT NULL DEFAULT '{}', description TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',
    interval_sec INTEGER NOT NULL DEFAULT 300, count INTEGER NOT NULL DEFAULT 10, probe_interval REAL NOT NULL DEFAULT 1.0,
    protocol TEXT NOT NULL DEFAULT 'icmp', port INTEGER, packet_size INTEGER NOT NULL DEFAULT 64, ip_version TEXT NOT NULL DEFAULT 'auto',
    max_hops INTEGER NOT NULL DEFAULT 30, enabled INTEGER NOT NULL DEFAULT 1, notify INTEGER NOT NULL DEFAULT 1,
    alert_loss_pct REAL NOT NULL DEFAULT 5.0, alert_latency_ms REAL NOT NULL DEFAULT 200.0, created_at REAL NOT NULL,
    updated_at REAL NOT NULL, next_run_at REAL, last_status TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    started_at REAL NOT NULL, finished_at REAL, duration_ms REAL, status TEXT NOT NULL, error TEXT, src TEXT, dst_ip TEXT,
    reached INTEGER NOT NULL DEFAULT 0, hop_count INTEGER NOT NULL DEFAULT 0, sent INTEGER, loss_pct REAL, last_ms REAL,
    avg_ms REAL, best_ms REAL, worst_ms REAL, stdev_ms REAL, jitter_avg_ms REAL, jitter_max_ms REAL, route_hash TEXT,
    route_changed INTEGER NOT NULL DEFAULT 0, command TEXT, details TEXT);
CREATE INDEX IF NOT EXISTS idx_runs_target_started ON runs(target_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);
CREATE TABLE IF NOT EXISTS hops (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE, hop_no INTEGER NOT NULL,
    ip TEXT, hostname TEXT, asn TEXT, loss_pct REAL NOT NULL DEFAULT 0, sent INTEGER NOT NULL DEFAULT 0,
    received INTEGER NOT NULL DEFAULT 0, last_ms REAL, avg_ms REAL, best_ms REAL, worst_ms REAL, stdev_ms REAL, gmean_ms REAL,
    jitter_ms REAL, jitter_avg_ms REAL, jitter_max_ms REAL, jitter_int_ms REAL);
CREATE INDEX IF NOT EXISTS idx_hops_run ON hops(run_id, hop_no);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, target_id INTEGER REFERENCES targets(id) ON DELETE CASCADE,
    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL, kind TEXT NOT NULL, severity TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL, details TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_target ON events(target_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
"""
LEGACY_SCHEMA_V1 = re.sub(r", command TEXT, details TEXT\)", ", command TEXT)", LEGACY_SCHEMA_V3)
for _column in (r"\s*type TEXT NOT NULL DEFAULT 'mtr',", r"\s*options TEXT NOT NULL DEFAULT '\{\}',", r"\s*notify INTEGER NOT NULL DEFAULT 1,"):
    LEGACY_SCHEMA_V1 = re.sub(_column, "", LEGACY_SCHEMA_V1)
# events keep their own details column; only the four columns of the later releases must be gone.
assert "notify" not in LEGACY_SCHEMA_V1 and "options" not in LEGACY_SCHEMA_V1 and "type TEXT" not in LEGACY_SCHEMA_V1
assert "command TEXT, details TEXT" not in LEGACY_SCHEMA_V1 and "command TEXT)" in LEGACY_SCHEMA_V1

DAY = 86400


def seed_legacy(path: Path, *, version: str = "v3", journal: str = "wal", bad_hop_sent: int | None = None) -> dict[str, Any]:
    """A legacy file with two targets, a mix of runs, hops, events and settings, plus the values that need care.

    Returns what an import should produce so the tests can compare. Runs: 1 and 3 reached, 2 failed with a NUL in its
    error, 4 older than the retention window, 5 belongs to a missing target, 6 was deleted (its id stays in
    sqlite_sequence). Hops: run 1 has a silent hop; run 3 carries a BLOB, a fractional value in an integer column and
    an invalid UTF-8 text (an integer written to the asn column becomes text on the way in: SQLite's affinity); two
    hops belong to runs that do not survive. Events: one with its run gone, one of a missing target, one older than
    retention.
    """
    for p in path.parent.glob(path.name + "*"):
        p.unlink()
    c = sqlite3.connect(path)
    c.executescript(LEGACY_SCHEMA_V3 if version == "v3" else LEGACY_SCHEMA_V1)
    c.execute(f"PRAGMA journal_mode={'WAL' if journal == 'wal' else 'DELETE'}")
    now = time.time()
    c.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", ("3" if version == "v3" else "1",))
    c.executemany(
        "INSERT INTO settings(key, value) VALUES (?, ?)",
        [("retention_days", "30"), ("pushover_api_token", json.dumps("s3cret")), ("tag_colors", '{"z": "#ff0000", "a": "#00ff00"}'), ("weird", "not json")],
    )
    if version == "v3":
        c.execute(
            "INSERT INTO targets(id, name, host, type, options, tags, enabled, notify, port, created_at, updated_at, next_run_at, last_status) "
            "VALUES (1, 'Alpha', '192.0.2.1', 'mtr', '{}', '[\"ü\", \"b\"]', 1, 0, NULL, ?, ?, ?, 'up')",
            (now, now, now + 3600),
        )
    else:
        c.execute("INSERT INTO targets(id, name, host, tags, enabled, created_at, updated_at, next_run_at, last_status) VALUES (1, 'Alpha', '192.0.2.1', '[\"ü\", \"b\"]', 1, ?, ?, ?, 'up')", (now, now, now + 3600))
    c.execute("INSERT INTO targets(id, name, host, enabled, created_at, updated_at) VALUES (7, 'Beta', 'https://b.test', 0, ?, ?)", (now, now))
    runs = [
        (1, 1, now - 300, now - 299, "ok", "192.0.2.1", 1, 3, 10, 0.0, 12.5, "abc", 0, None),
        (2, 1, now - 200, now - 199, "error", None, 0, 0, None, 100.0, None, None, 0, "mtr said\x00nothing"),
        (3, 1, now - 100, now - 99, "ok", "192.0.2.1", 1, 5, 10, 0.0, 13.0, "abc", 0, None),
        (4, 1, now - 40 * DAY, now - 40 * DAY + 1, "ok", "192.0.2.1", 1, 2, 10, 0.0, 9.0, "old", 0, None),
        (5, 99, now - 50, now - 49, "ok", "10.9.9.9", 1, 1, 10, 0.0, 1.0, "orphan", 0, None),
        (6, 7, now - 10, now - 9, "ok", None, 1, 0, None, 0.0, 200.5, None, 0, None),
    ]
    c.executemany(
        "INSERT INTO runs(id, target_id, started_at, finished_at, status, dst_ip, reached, hop_count, sent, loss_pct, avg_ms, route_hash, route_changed, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        runs,
    )
    if version == "v3":
        c.execute("UPDATE runs SET details = ? WHERE id = 3", ('{"probe": {"label": "x"}}',))
    c.execute("DELETE FROM runs WHERE id = 6")
    hops = [
        (1, 1, "10.0.0.1", "AS1", 3, 3, 1.0), (1, 2, None, None, 3, 0, None), (1, 3, "192.0.2.1", "AS2", 3, 3, 12.5),
        (3, 1, "10.0.0.1", "AS1", 3, 3, 1.0), (3, 2, "10.0.0.2", None, 3, 3, 5.0), (3, 3, "192.0.2.1", "AS2", 3, 3, 13.0),
        (4, 1, "10.0.0.1", None, 3, 3, 1.0), (4, 2, "192.0.2.1", None, 3, 3, 9.0),
        (5, 1, "10.9.9.9", None, 3, 3, 1.0),
        (42, 1, "1.1.1.1", None, 3, 3, 1.0),
    ]
    c.executemany("INSERT INTO hops(run_id, hop_no, ip, asn, sent, received, avg_ms) VALUES (?, ?, ?, ?, ?, ?, ?)", hops)
    c.execute("INSERT INTO hops(run_id, hop_no, ip, asn, sent, received, avg_ms) VALUES (3, 4, X'41ff', 15169, 2.5, 3, 1.0)")
    c.execute("INSERT INTO hops(run_id, hop_no, ip, sent, received) VALUES (3, 5, CAST(x'ff41' AS TEXT), 3, 3)")
    if bad_hop_sent is not None:
        c.execute("INSERT INTO hops(run_id, hop_no, ip, sent, received) VALUES (3, 6, '10.0.0.6', ?, 3)", (bad_hop_sent,))
    events = [
        (1, 1, 1, "down", "critical", "Alpha is DOWN", '{"a": 1}', now - 300),
        (2, 1, None, "recovered", "info", "Alpha recovered", None, now - 100),
        (3, 99, 5, "down", "critical", "orphan target", None, now - 50),
        (4, 1, 4, "route_change", "info", "refers to an old run", None, now - 20),
        (5, 1, 2, "degraded", "warning", "x", None, now - 60 * DAY),
    ]
    c.executemany("INSERT INTO events(id, target_id, run_id, kind, severity, message, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", events)
    c.commit()
    c.close()
    return {"targets": 2, "runs": 3, "hops": 6 + (1 if bad_hop_sent is not None else 0) + 2, "events": 4, "settings": 4, "now": now}


async def _admin_count(dsn: str, sql: str) -> int:
    conn = await asyncpg.connect(dsn, timeout=5)
    try:
        return int(await conn.fetchval(sql))
    finally:
        await conn.close()


async def test_automatic_import_carries_the_installation_over(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "test.db"
    expected = seed_legacy(path)
    holder = sqlite3.connect(path)  # a second connection keeps the -wal file in place, as a crashed instance would
    holder.execute("SELECT COUNT(*) FROM runs").fetchone()
    caplog.set_level(logging.INFO, logger="mtr-tracker.migrate")

    async with app_client(tmp_path, monkeypatch) as client:
        db = app_of(client).state.db
        counts = json.loads((await db.fetchone("SELECT value FROM meta WHERE key = 'migrated_from'")) and (await db.fetchone("SELECT value FROM meta WHERE key = 'migrated_counts'"))["value"])
        assert {k: counts[k] for k in ("targets", "runs", "hops", "events", "settings")} == {k: expected[k] for k in ("targets", "runs", "hops", "events", "settings")}
        assert counts["skipped"] == {"runs_old": 1, "hops_old": 2, "events_old": 1, "runs_orphaned": 1, "hops_orphaned": 2, "events_target_cleared": 1, "events_run_cleared": 2}
        assert counts["coerced"] == {"runs.nul": 1, "hops.blob": 1, "hops.fractional": 1, "invalid_utf8": 1}
        assert counts["retention_days"] == 30 and counts["settings_replaced"] == []

        # Ids, relations and values survived; the bad values arrived coerced.
        targets = {t["name"]: t for t in (await client.get("/api/targets")).json()}
        assert targets["Alpha"]["id"] == 1 and targets["Beta"]["id"] == 7
        assert targets["Alpha"]["tags"] == ["b", "ü"] and targets["Alpha"]["notify"] is False and targets["Alpha"]["port"] is None
        assert targets["Alpha"]["latest_run"]["id"] == 3 and targets["Alpha"]["last_status"] == "up"
        run = (await client.get("/api/runs/3")).json()
        assert run["details"] == {"probe": {"label": "x"}} and [h["ip"] for h in run["hops"]] == ["10.0.0.1", "10.0.0.2", "192.0.2.1", "A�", "�A"]
        assert run["hops"][3]["asn"] == "15169" and run["hops"][3]["sent"] == 2
        assert run["prev_run_id"] == 2 and run["next_run_id"] is None
        failed = (await client.get("/api/runs/2")).json()
        assert failed["error"] == "mtr said�nothing" and failed["prev_run_id"] == 1
        silent = (await client.get("/api/runs/1")).json()
        assert silent["hops"][1]["ip"] is None and silent["hops"][1]["received"] == 0
        assert (await client.get("/api/runs/4")).status_code == 404  # older than the retention window, left behind
        events = (await client.get("/api/events")).json()["items"]
        assert {(e["id"], e["target_id"], e["run_id"]) for e in events} == {(1, 1, 1), (2, 1, None), (3, None, None), (4, 1, None)}
        settings = (await client.get("/api/settings")).json()
        assert settings["retention_days"] == 30 and settings["pushover_api_token"] == "s3cret" and settings["weird"] == "not json"
        assert settings["tag_colors"] == {"z": "#ff0000", "a": "#00ff00"}
        assert (await db.fetchval("SELECT value FROM settings WHERE key = 'tag_colors'")) == '{"z": "#ff0000", "a": "#00ff00"}'  # verbatim, not re-encoded

        # New rows continue above every id SQLite ever handed out, including the deleted run 6 and the gap before target 7.
        created = (await client.post("/api/targets", json={"name": "New", "host": "192.0.2.9", "enabled": False})).json()
        assert created["id"] == 8
        assert await db.fetchval("SELECT nextval(pg_get_serial_sequence('runs', 'id'))") == 7
        assert await db.fetchval("SELECT nextval(pg_get_serial_sequence('hops', 'id'))") == 13
        assert await db.fetchval("SELECT nextval(pg_get_serial_sequence('events', 'id'))") == 6
        indexes = {r["indexname"] for r in await db.fetchall("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()")}
        assert {"idx_runs_target_started", "idx_runs_started", "idx_hops_run", "idx_events_created", "idx_events_target", "idx_events_run"} <= indexes
        assert (await db.fetchone("SELECT value FROM meta WHERE key = 'migrated_from'"))["value"] == str(path.resolve())
        assert (await db.fetchone("SELECT value FROM meta WHERE key = 'migration_failed'")) is None

    holder.close()
    names = sorted(p.name for p in tmp_path.glob("test.db*"))
    assert "test.db" not in names and "test.db.migrated" in names and "test.db.migrated-wal" in names
    assert "imported 3 runs" in caplog.text and "skipped 1 runs old" in caplog.text and "hops.blob" in caplog.text

    # A second start against the same database imports nothing and stays quiet.
    caplog.clear()
    async with app_client(tmp_path, monkeypatch, fresh_database=False) as client:
        assert len((await client.get("/api/targets")).json()) == 3
        assert (await client.get("/api/runs/3")).status_code == 200
    assert "import" not in caplog.text.lower() and "test.db.migrated" in {p.name for p in tmp_path.glob("test.db*")}


async def test_schema_one_file_gets_the_later_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed_legacy(tmp_path / "test.db", version="v1")
    async with app_client(tmp_path, monkeypatch) as client:
        targets = {t["name"]: t for t in (await client.get("/api/targets")).json()}
        assert targets["Alpha"]["type"] == "mtr" and targets["Alpha"]["options"] == {} and targets["Alpha"]["notify"] is True
        run = (await client.get("/api/runs/3")).json()
        assert run["details"] is None and run["hop_count"] == 5


async def test_cli_refuses_a_used_database_and_replaces_on_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="mtr-tracker.migrate")
    async with app_client(tmp_path, monkeypatch) as client:
        await client.post("/api/targets", json={"name": "Live", "host": "192.0.2.50", "enabled": False})
        await client.put("/api/settings", json={"site_name": "NOC", "retention_days": 14})
        dsn = app_of(client).state.db.dsn
    # The file arrives later, next to a database that already holds data: the automatic import only warns.
    path = tmp_path / "test.db"
    seed_legacy(path)
    async with app_client(tmp_path, monkeypatch, fresh_database=False) as client:
        assert "already holds data" in caplog.text and path.exists()
        assert [t["name"] for t in (await client.get("/api/targets")).json()] == ["Live"]

    args = migrate.build_parser({"sqlite": str(path), "database_url": dsn}).parse_args([])
    assert await migrate._run(args) == 1  # refused without --replace
    assert await _admin_count(dsn, "SELECT COUNT(*) FROM targets") == 1 and path.exists()

    args = migrate.build_parser({"sqlite": str(path), "database_url": dsn}).parse_args(["--replace", "--all", "--rename"])
    assert await migrate._run(args) == 0
    assert not path.exists() and (tmp_path / "test.db.migrated").exists()
    assert "settings: retention_days already had a value" in caplog.text
    assert await _admin_count(dsn, "SELECT COUNT(*) FROM runs WHERE id = 4") == 1  # --all kept the run older than the retention window
    async with app_client(tmp_path, monkeypatch, fresh_database=False) as client:
        names = sorted(t["name"] for t in (await client.get("/api/targets")).json())
        assert names == ["Alpha", "Beta"]  # history replaced
        assert (await client.get("/api/runs/3")).status_code == 200
        await app_of(client).state.db.purge_older_than(30)  # what the cleanup tick does at start, run here so the check cannot race it
        assert (await client.get("/api/runs/4")).status_code == 404
        settings = (await client.get("/api/settings")).json()
        assert settings["site_name"] == "NOC" and settings["retention_days"] == 30 and settings["pushover_api_token"] == "s3cret"


async def test_opt_out_leaves_both_sides_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "test.db"
    seed_legacy(path)
    monkeypatch.setenv("MTR_TRACKER_AUTO_MIGRATE", "0")
    async with app_client(tmp_path, monkeypatch) as client:
        assert (await client.get("/api/targets")).json() == []
        assert (await app_of(client).state.db.fetchone("SELECT 1 FROM meta WHERE key = 'migrated_from'")) is None
    assert path.exists()


async def test_failed_import_leaves_the_database_empty_and_is_remembered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "test.db"
    seed_legacy(path, bad_hop_sent=2**40)  # fits SQLite's 64-bit integers, not PostgreSQL's integer column
    with pytest.raises(migrate.MigrationError) as info:
        async with app_client(tmp_path, monkeypatch):
            pass
    assert "hops id 13" in str(info.value) and "int32 range" in str(info.value)
    dsn = __import__("os").environ["MTR_TRACKER_DATABASE_URL"]
    assert path.exists() and not (tmp_path / "test.db.migrated").exists()
    for table in ("targets", "runs", "hops", "events"):
        assert await _admin_count(dsn, f"SELECT COUNT(*) FROM {table}") == 0, table
    assert await _admin_count(dsn, "SELECT COUNT(*) FROM meta WHERE key = 'migration_failed'") == 1
    assert await _admin_count(dsn, "SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend'") == 0

    # The next start fails at once on the marker; nothing is read again.
    monkeypatch.setattr(migrate, "import_sqlite", None)
    with pytest.raises(migrate.MigrationError, match="failed earlier and the file has not changed"):
        async with app_client(tmp_path, monkeypatch, fresh_database=False):
            pass


async def test_changed_and_locked_files_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "test.db"
    seed_legacy(path)
    db = Database(await fresh_test_database(), pool_size=2, connect_timeout=5)
    await db.connect()
    try:
        # A writer that is still running: the file gains a run between the snapshot and the final check.
        original = migrate._Import.verify

        async def verify_and_write(self: Any, table: str) -> None:
            if table == "targets":
                extra = sqlite3.connect(path)
                extra.execute("INSERT INTO runs(target_id, started_at, status) VALUES (1, ?, 'ok')", (time.time(),))
                extra.commit()
                extra.close()
            await original(self, table)

        monkeypatch.setattr(migrate._Import, "verify", verify_and_write)
        with pytest.raises(migrate.MigrationError, match="changed while it was being imported"):
            await migrate.import_sqlite(db, path)
        monkeypatch.setattr(migrate._Import, "verify", original)
        assert await db.fetchval("SELECT COUNT(*) FROM targets") == 0

        # A rollback-journal file held exclusively by another process cannot even be read.
        seed_legacy(path, journal="delete")
        locker = sqlite3.connect(path)
        locker.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(migrate.MigrationError, match="stop the old MTR Tracker"):
                await migrate.import_sqlite(db, path)
        finally:
            locker.rollback()
            locker.close()
        assert (await migrate.import_sqlite(db, path))["runs"] == 3
    finally:
        await db.close()


def test_value_coercions() -> None:
    """Every kind the importer counts, plus the values it must refuse rather than mangle."""
    assert migrate._to_text(15169) == ("15169", "number") and migrate._to_text(b"A\xff") == ("A\ufffd", "blob")
    assert migrate._to_text("a\x00b") == ("a\ufffdb", "nul") and migrate._to_text(None) == (None, None)
    assert migrate._to_int("12") == (12, "text") and migrate._to_int(2.0) == (2, None) and migrate._to_int(2.5) == (2, "fractional")
    assert migrate._to_float("2.5") == (2.5, "text") and migrate._to_float(3) == (3.0, None)
    with pytest.raises(ValueError):
        migrate._to_int(float("inf"))
    with pytest.raises(ValueError):
        migrate._to_int("1e400")
    with pytest.raises(TypeError):
        migrate._to_int([1])
