"""One-time import of a SQLite database (the storage of releases before the PostgreSQL backend) into PostgreSQL.

`auto_import` runs at start when the old file is still in the data directory and PostgreSQL holds no data yet;
`python -m app.migrate` does the same by hand, also into a database that already holds data (`--replace`).

The file is read through the stdlib driver, read-only, from one snapshot; every value is coerced to the type of its
PostgreSQL column (SQLite's dynamic typing lets a file hold numbers in text columns and fractions in integer
columns); ids are preserved and the identity sequences advanced past SQLite's own high-water marks; rows that
reference a missing parent are skipped (runs, hops) or keep the row with the reference cleared (events); runs and
events older than the retention window are left behind, because the scheduler's first cleanup tick would purge them
right after the import. Everything lands in one PostgreSQL transaction, so a failure leaves the data tables as they
were, and a failure marker in `meta` stops the next start from repeating a long import that cannot succeed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterator

import asyncpg

from .db import DEFAULT_SETTINGS, ID_TABLES, INDEXES, Database, Transaction, translate

log = logging.getLogger("mtr-tracker.migrate")

TABLES = ("meta", "settings", "targets", "runs", "hops", "events")
RUN_BATCH = 5000
PROGRESS_EVERY = 100_000
# meta keys that describe imports; never copied from the source, always written by the importer.
IMPORT_KEYS = ("migrated_from", "migrated_at", "migrated_counts", "migration_failed")
JSON_COLUMNS = {"targets": ("options", "tags"), "runs": ("details",), "events": ("details",), "settings": ("value",)}
UPSERT_META = "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
UPSERT_SETTING = "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value"


class MigrationError(RuntimeError):
    """The import was refused or failed; the message is written for the operator."""


# ---------------------------------------------------------------------------
# Reading the SQLite file
# ---------------------------------------------------------------------------


def _open_source(path: Path, stats: dict[str, int]) -> sqlite3.Connection:
    """The file, read-only (never `immutable`: a crash-stopped WAL database keeps its last transactions in -wal).

    Reading a WAL database needs write access to the directory or to an existing -shm file; the error says so.
    """
    if not path.is_file():
        raise MigrationError(f"{path} is not a file")

    def text(raw: bytes) -> str:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            stats["invalid_utf8"] = stats.get("invalid_utf8", 0) + 1
            return raw.decode("utf-8", "replace")

    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1.0)
        conn.text_factory = text
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.DatabaseError as exc:
        text_exc = str(exc)
        if "unable to open" in text_exc or "readonly" in text_exc.lower():
            raise MigrationError(
                f"cannot open {path}: {exc}. Reading it needs write access to its directory (or to the -shm file next to it)"
            ) from exc
        if "database is locked" in text_exc:
            raise MigrationError(f"{path} is locked by another process; stop the old MTR Tracker instance first") from exc
        raise MigrationError(f"{path} is not a readable SQLite database: {exc}") from exc
    if "targets" not in tables:
        conn.close()
        raise MigrationError(f"{path} is not an MTR Tracker database (no targets table)")
    return conn


def _sqlite_columns(src: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in src.execute(f"PRAGMA table_info({table})")]


def _sqlite_high_water(src: sqlite3.Connection, table: str) -> int:
    """SQLite's AUTOINCREMENT counter survives deletes; it is the id that must never be handed out again."""
    try:
        row = src.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    return int(row[0]) if row and row[0] is not None else 0


def _max_id(src: sqlite3.Connection, table: str) -> int:
    row = src.execute(f"SELECT MAX(id) FROM {table}").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _read_setting(rows: list[tuple[str, Any]], key: str, default: Any) -> Any:
    for k, v in rows:
        if k == key:
            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return default
    return default


# ---------------------------------------------------------------------------
# Value coercion (SQLite affinity -> PostgreSQL column type)
# ---------------------------------------------------------------------------

Coercer = Callable[[Any], tuple[Any, str | None]]


def _to_int(value: Any) -> tuple[Any, str | None]:
    if value is None or isinstance(value, int):
        return value, None
    if isinstance(value, float):
        return int(value), ("fractional" if value != int(value) else None)
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            return int(value), "text"
        except ValueError:
            return int(float(value)), "text"
    raise TypeError(f"cannot store {type(value).__name__} in an integer column")


def _to_float(value: Any) -> tuple[Any, str | None]:
    if value is None or isinstance(value, float):
        return value, None
    if isinstance(value, int):
        return float(value), None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        return float(value), "text"
    raise TypeError(f"cannot store {type(value).__name__} in a double precision column")


def _to_text(value: Any) -> tuple[Any, str | None]:
    if value is None:
        return None, None
    kind = None
    if isinstance(value, bytes):
        value, kind = value.decode("utf-8", "replace"), "blob"
    elif not isinstance(value, str):
        value, kind = str(value), "number"
    if "\x00" in value:
        value, kind = value.replace("\x00", "�"), "nul"
    return value, kind


def _coercer(data_type: str) -> Coercer:
    if data_type in ("bigint", "integer", "smallint"):
        return _to_int
    if data_type in ("double precision", "real", "numeric"):
        return _to_float
    if data_type in ("text", "character varying", "character"):
        return _to_text
    return lambda v: (v, None)


# ---------------------------------------------------------------------------
# The import
# ---------------------------------------------------------------------------


class _Import:
    """State of one import: the source, the PostgreSQL transaction, the counters that become the result."""

    def __init__(self, tx: Transaction, src: sqlite3.Connection, path: Path, *, batch: int, cutoff: float | None):
        self.tx = tx
        self.conn = tx.connection
        self.src = src
        self.path = path
        self.batch = max(100, int(batch))
        self.cutoff = cutoff
        self.copied: dict[str, int] = {}
        self.skipped: dict[str, int] = {}
        self.coerced: dict[str, int] = {}
        self.dropped_columns: dict[str, list[str]] = {}
        self.first_last: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self.pg_columns: dict[str, dict[str, str]] = {}

    async def pg_column_types(self, table: str) -> dict[str, str]:
        if table not in self.pg_columns:
            rows = await self.conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = $1 "
                "ORDER BY ordinal_position",
                table,
            )
            self.pg_columns[table] = {r["column_name"]: r["data_type"] for r in rows}
        return self.pg_columns[table]

    async def columns(self, table: str) -> tuple[list[str], list[Coercer]]:
        """The columns both sides know, in PostgreSQL order; a column only SQLite has is logged, never copied silently."""
        types = await self.pg_column_types(table)
        present = set(_sqlite_columns(self.src, table))
        cols = [c for c in types if c in present]
        extra = sorted(present - set(types))
        if extra:
            self.dropped_columns[table] = extra
            log.warning("%s: column(s) %s exist only in the SQLite file and are not imported", table, ", ".join(extra))
        return cols, [_coercer(types[c]) for c in cols]

    def coerce(self, table: str, cols: list[str], coercers: list[Coercer], row: sqlite3.Row) -> tuple[Any, ...]:
        out = []
        for name, fn in zip(cols, coercers):
            try:
                value, kind = fn(row[name])
            except (TypeError, ValueError) as exc:
                raise MigrationError(f"{table} id {row['id'] if 'id' in row.keys() else '?'}: column {name}: {exc}") from exc
            if kind:
                key = f"{table}.{kind}"
                self.coerced[key] = self.coerced.get(key, 0) + 1
            out.append(value)
        return tuple(out)

    async def copy(self, table: str, cols: list[str], records: list[tuple[Any, ...]]) -> None:
        """One COPY per batch under a savepoint; a failing batch is replayed row by row to name the offending row."""
        try:
            async with self.conn.transaction():
                await self.conn.copy_records_to_table(table, records=records, columns=cols)
        except Exception as exc:  # noqa: BLE001 - a server error, or a value the driver cannot encode (DataError, OverflowError)
            insert = f"INSERT INTO {table}({', '.join(cols)}) VALUES ({', '.join(f'${i + 1}' for i in range(len(cols)))})"
            for record in records:
                try:
                    async with self.conn.transaction():
                        await self.conn.execute(insert, *record)
                except Exception as row_exc:  # noqa: BLE001
                    row_id = record[cols.index("id")] if "id" in cols else "?"
                    raise MigrationError(f"{table} id {row_id}: {row_exc}") from row_exc
            raise MigrationError(f"{table}: {exc}") from exc

    async def copy_table(self, table: str, select: str, params: tuple[Any, ...], *, batch: int | None = None, fix: Callable[[dict[str, Any]], None] | None = None) -> int:
        """Stream `select` (which must end in `id > ? ORDER BY id LIMIT ?`) into the PostgreSQL table."""
        cols, coercers = await self.columns(table)
        if "id" not in cols:
            raise MigrationError(f"{table}: the SQLite table has no id column")
        size = batch or self.batch
        last_id = 0
        total = 0
        first: dict[str, Any] | None = None
        last: dict[str, Any] | None = None
        column_list = ", ".join(f"t.{c}" for c in cols)
        while True:
            rows = self.src.execute(select.format(cols=column_list), (*params, last_id, size)).fetchall()
            if not rows:
                break
            records = []
            for row in rows:
                record = self.coerce(table, cols, coercers, row)
                if fix is not None:
                    as_dict = dict(zip(cols, record))
                    fix_row = dict(row)
                    fix_row.update(as_dict)
                    fix(fix_row)
                    record = tuple(fix_row[c] for c in cols)
                    as_dict = dict(zip(cols, record))
                else:
                    as_dict = dict(zip(cols, record))
                records.append(record)
                if first is None:
                    first = as_dict
                last = as_dict
            await self.copy(table, cols, records)
            total += len(records)
            last_id = int(rows[-1]["id"])
            if total % PROGRESS_EVERY < len(records) and total >= PROGRESS_EVERY:
                log.info("%s: %d rows imported so far", table, total)
        self.copied[table] = total
        if first is not None and last is not None:
            self.first_last[table] = (first, last)
        return total

    async def verify(self, table: str) -> None:
        """Counts, id bounds and a byte-level JSON round trip: the import is either faithful or it does not commit."""
        copied = self.copied.get(table, 0)
        count = await self.conn.fetchval(f"SELECT COUNT(*) FROM {table}")
        if count != copied:
            raise MigrationError(f"{table}: {copied} rows were sent but {count} arrived")
        if table not in self.first_last:
            return
        first, last = self.first_last[table]
        bounds = await self.conn.fetchrow(f"SELECT MIN(id) AS lo, MAX(id) AS hi FROM {table}")
        if bounds["lo"] != first["id"] or bounds["hi"] != last["id"]:
            raise MigrationError(f"{table}: id range {bounds['lo']}..{bounds['hi']} differs from the source {first['id']}..{last['id']}")
        for column in JSON_COLUMNS.get(table, ()):
            if column not in first:
                continue
            for sample in (first, last):
                stored = await self.conn.fetchval(f"SELECT {column} FROM {table} WHERE id = $1", sample["id"])
                if stored != sample[column]:
                    raise MigrationError(f"{table} id {sample['id']}: {column} did not survive the copy byte for byte")


async def _counts(tx: Transaction) -> dict[str, int]:
    row = await tx.fetchone(
        "SELECT (SELECT COUNT(*) FROM targets) AS targets, (SELECT COUNT(*) FROM runs) AS runs, (SELECT COUNT(*) FROM settings) AS settings"
    )
    return {k: int(row[k]) for k in ("targets", "runs", "settings")}


async def _fresh(tx: Transaction) -> bool:
    return not await tx.fetchval("SELECT EXISTS (SELECT 1 FROM targets) OR EXISTS (SELECT 1 FROM runs) OR EXISTS (SELECT 1 FROM events)")


def _size(path: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            total += p.stat().st_size
    return total


async def import_sqlite(db: Database, path: Path, *, replace: bool = False, all_history: bool = False, batch: int = RUN_BATCH) -> dict[str, Any]:
    """Copy the SQLite database at `path` into PostgreSQL; returns the counts the log lines also carry.

    `replace` first truncates the four data tables (settings and meta are only ever upserted); without it the
    import is refused unless PostgreSQL holds no target, run or event. `all_history` keeps runs and events older
    than the retention window, which the scheduler would otherwise purge on its first tick.
    """
    path = Path(path)
    stats: dict[str, int] = {}
    src = _open_source(path, stats)
    log.info("importing %s (%.1f MB; the database needs roughly the same space again)", path, _size(path) / 1e6)
    try:
        # One snapshot for every table, so a run and its hops cannot disagree about what exists.
        src.execute("BEGIN")
        meta_rows = [(r["key"], r["value"]) for r in src.execute("SELECT key, value FROM meta")] if "meta" in _tables(src) else []
        settings_rows = [(r["key"], r["value"]) for r in src.execute("SELECT key, value FROM settings")] if "settings" in _tables(src) else []
        retention_days = int(_read_setting(settings_rows, "retention_days", DEFAULT_SETTINGS["retention_days"]) or DEFAULT_SETTINGS["retention_days"])
        cutoff = None if all_history else time.time() - retention_days * 86400
        high_water = {t: max(_sqlite_high_water(src, t), _max_id(src, t)) for t in ID_TABLES}

        async with db.transaction() as tx:
            conn = tx.connection
            await conn.execute("LOCK TABLE targets, runs, hops, events, settings, meta IN SHARE ROW EXCLUSIVE MODE")
            if not await _fresh(tx):
                if not replace:
                    have = await _counts(tx)
                    raise MigrationError(
                        f"PostgreSQL already holds {have['targets']} targets, {have['runs']} runs and {have['settings']} saved settings; "
                        "rerun with --replace after stopping the app, or remove the SQLite file"
                    )
                await conn.execute("TRUNCATE targets, runs, hops, events RESTART IDENTITY CASCADE")
            imp = _Import(tx, src, path, batch=batch, cutoff=cutoff)

            # meta and settings: upserted, never truncated; a value already in PostgreSQL that changes is logged.
            meta_keep = [(k, _to_text(v)[0]) for k, v in meta_rows if k not in ("schema_version", *IMPORT_KEYS)]
            await tx.executemany(UPSERT_META, meta_keep)
            imp.copied["meta"] = len(meta_keep)
            existing = {r["key"]: r["value"] for r in await tx.fetchall("SELECT key, value FROM settings")}
            settings_keep = [(k, _to_text(v)[0]) for k, v in settings_rows]
            replaced = sorted(k for k, v in settings_keep if k in existing and existing[k] != v)
            for key in replaced:
                log.warning("settings: %s already had a value in PostgreSQL; the imported one replaces it", key)
            await tx.executemany(UPSERT_SETTING, settings_keep)
            imp.copied["settings"] = len(settings_keep)

            # Building the indexes once after the copy is several times faster than maintaining them per row.
            for name in INDEXES:
                await conn.execute(f"DROP INDEX IF EXISTS {name}")

            time_filter = "" if cutoff is None else " AND t.started_at >= ?"
            time_params: tuple[Any, ...] = () if cutoff is None else (cutoff,)
            await imp.copy_table("targets", "SELECT {cols} FROM targets t WHERE t.id > ? ORDER BY t.id LIMIT ?", ())
            await imp.copy_table(
                "runs",
                "SELECT {cols} FROM runs t WHERE t.target_id IN (SELECT id FROM targets)" + time_filter + " AND t.id > ? ORDER BY t.id LIMIT ?",
                time_params,
            )
            await imp.copy_table(
                "hops",
                "SELECT {cols} FROM hops t WHERE t.run_id IN (SELECT t.id FROM runs t WHERE t.target_id IN (SELECT id FROM targets)"
                + time_filter + ") AND t.id > ? ORDER BY t.id LIMIT ?",
                time_params,
                batch=imp.batch * 4,
            )

            def fix_event(row: dict[str, Any]) -> None:
                if row.get("target_id") is not None and not row["_has_target"]:
                    row["target_id"] = None
                    imp.skipped["events_target_cleared"] = imp.skipped.get("events_target_cleared", 0) + 1
                if row.get("run_id") is not None and not row["_has_run"]:
                    row["run_id"] = None
                    imp.skipped["events_run_cleared"] = imp.skipped.get("events_run_cleared", 0) + 1

            event_filter = "" if cutoff is None else " AND t.created_at >= ?"
            run_join_filter = "" if cutoff is None else " AND r.started_at >= ?"
            await imp.copy_table(
                "events",
                "SELECT {cols}, (tg.id IS NOT NULL) AS _has_target, (r.id IS NOT NULL) AS _has_run FROM events t "
                "LEFT JOIN targets tg ON tg.id = t.target_id "
                "LEFT JOIN runs r ON r.id = t.run_id AND r.target_id IN (SELECT id FROM targets)" + run_join_filter
                + " WHERE 1 = 1" + event_filter + " AND t.id > ? ORDER BY t.id LIMIT ?",
                (*time_params, *time_params),
                fix=fix_event,
            )

            # What was left behind, for the log and the result.
            totals = {t: int(src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in ID_TABLES}
            if cutoff is not None:
                old_runs = int(src.execute("SELECT COUNT(*) FROM runs WHERE target_id IN (SELECT id FROM targets) AND started_at < ?", (cutoff,)).fetchone()[0])
                old_hops = int(
                    src.execute(
                        "SELECT COUNT(*) FROM hops WHERE run_id IN (SELECT id FROM runs WHERE target_id IN (SELECT id FROM targets) AND started_at < ?)",
                        (cutoff,),
                    ).fetchone()[0]
                )
                old_events = int(src.execute("SELECT COUNT(*) FROM events WHERE created_at < ?", (cutoff,)).fetchone()[0])
            else:
                old_runs = old_hops = old_events = 0
            imp.skipped.update(
                {
                    "runs_old": old_runs,
                    "hops_old": old_hops,
                    "events_old": old_events,
                    "runs_orphaned": totals["runs"] - imp.copied["runs"] - old_runs,
                    "hops_orphaned": totals["hops"] - imp.copied["hops"] - old_hops,
                }
            )

            for statement in INDEXES.values():
                await conn.execute(statement)
            for table in ID_TABLES:
                # COPY with explicit ids does not touch the identity sequence; start it past every id SQLite ever used.
                next_id = max(high_water[table], int(await conn.fetchval(f"SELECT COALESCE(MAX(id), 0) FROM {table}"))) + 1
                await conn.execute("SELECT setval(pg_get_serial_sequence($1, 'id'), $2, false)", table, next_id)
            for table in ID_TABLES:
                await imp.verify(table)

            # End the snapshot and look again: a source that moved meanwhile belongs to a still-running instance.
            src.execute("COMMIT")
            moved = [t for t in ID_TABLES if _max_id(src, t) > high_water[t] or _sqlite_high_water(src, t) > high_water[t]]
            if moved:
                raise MigrationError(f"{path} changed while it was being imported ({', '.join(moved)}); stop the old MTR Tracker instance and retry")

            result: dict[str, Any] = {
                **{t: imp.copied.get(t, 0) for t in TABLES},
                "skipped": imp.skipped,
                "coerced": {**imp.coerced, **({"invalid_utf8": stats["invalid_utf8"]} if stats.get("invalid_utf8") else {})},
                "settings_replaced": replaced,
                "columns_dropped": imp.dropped_columns,
                "retention_days": None if cutoff is None else retention_days,
            }
            await tx.executemany(
                UPSERT_META,
                [("migrated_from", str(path)), ("migrated_at", repr(time.time())), ("migrated_counts", json.dumps(result))],
            )
            await tx.execute("DELETE FROM meta WHERE key = ?", ("migration_failed",))
    finally:
        src.close()
    for table in TABLES:
        log.info("imported %d %s", result[table], table)
    for key, n in sorted(result["skipped"].items()):
        if n:
            log.info("skipped %d %s", n, key.replace("_", " "))
    for key, n in sorted(result["coerced"].items()):
        log.warning("%d value(s) needed coercion: %s", n, key)
    return result


def _tables(src: sqlite3.Connection) -> set[str]:
    return {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


# ---------------------------------------------------------------------------
# Automatic import at start
# ---------------------------------------------------------------------------


def rename_imported(path: Path) -> bool:
    """`<file>` -> `<file>.migrated` (and the -wal/-shm siblings alike); a failure is a warning, the import is done."""
    path = Path(path)
    ok = True
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(path) + suffix)
        if not source.exists():
            continue
        target = Path(f"{path}.migrated{suffix}")
        try:
            os.replace(source, target)
        except OSError as exc:
            ok = False
            log.warning("could not rename %s to %s: %s", source, target, exc)
    return ok


async def _get_meta(db: Database, key: str) -> str | None:
    row = await db.fetchone("SELECT value FROM meta WHERE key = ?", (key,))
    return row["value"] if row else None


def _fingerprint(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {"path": str(path), "size": st.st_size, "mtime": st.st_mtime}


async def _record_failure(db: Database, path: Path, error: BaseException) -> None:
    marker = {**_fingerprint(path), "message": str(error), "at": time.time()}
    await db.execute(UPSERT_META, ("migration_failed", json.dumps(marker)))


async def auto_import(db: Database, path: Path) -> dict[str, Any] | None:
    """Import the legacy file at `path` when it exists and PostgreSQL is still empty; rename it afterwards.

    Raises `MigrationError` when the import fails, so start-up stops instead of probing into an empty database;
    the failure is remembered in `meta.migration_failed` and reported again without re-reading the file until the
    file changes. `MTR_TRACKER_AUTO_MIGRATE=0` skips all of this.
    """
    path = Path(path)
    if not path.exists():
        return None
    if not await db.is_fresh():
        if await _get_meta(db, "migrated_from") == str(path):
            log.info("%s was imported earlier; renaming it now", path)
            rename_imported(path)
            return None
        log.warning(
            "%s exists but PostgreSQL already holds data; import it by hand with `python -m app.migrate --sqlite %s --replace` "
            "(after stopping the app) or remove the file. MTR_TRACKER_AUTO_MIGRATE=0 silences this.",
            path,
            path,
        )
        return None
    raw_marker = await _get_meta(db, "migration_failed")
    if raw_marker:
        try:
            marker = json.loads(raw_marker)
        except ValueError:
            marker = {}
        if {k: marker.get(k) for k in ("path", "size", "mtime")} == _fingerprint(path):
            raise MigrationError(
                f"the import of {path} failed earlier and the file has not changed: {marker.get('message')}. "
                "Fix or remove the file, or set MTR_TRACKER_AUTO_MIGRATE=0"
            )
    try:
        counts = await import_sqlite(db, path)
    except MigrationError as exc:
        await _record_failure(db, path, exc)
        raise
    except Exception as exc:  # noqa: BLE001
        await _record_failure(db, path, exc)
        raise MigrationError(f"importing {path} failed: {exc}") from exc
    rename_imported(path)
    log.info("SQLite data imported from %s; the file is kept as %s.migrated", path, path)
    return counts


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def build_parser(defaults: dict[str, Any]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.migrate", description="Import a SQLite MTR Tracker database into PostgreSQL.")
    parser.add_argument("--sqlite", default=defaults["sqlite"], help="the SQLite file to read (default: MTR_TRACKER_DB_PATH)")
    parser.add_argument("--database-url", default=defaults["database_url"], help="the PostgreSQL URL to write to (default: MTR_TRACKER_DATABASE_URL)")
    parser.add_argument("--replace", action="store_true", help="truncate targets, runs, hops and events first (settings and meta are only upserted)")
    parser.add_argument("--all", dest="all_history", action="store_true", help="also import runs and events older than the retention window")
    parser.add_argument("--batch", type=int, default=RUN_BATCH, help=f"rows per COPY batch for runs (hops use four times as many; default {RUN_BATCH})")
    parser.add_argument("--rename", action="store_true", help="rename the file to <file>.migrated after a successful import")
    return parser


async def _run(args: argparse.Namespace) -> int:
    db = Database(args.database_url, pool_size=2, connect_timeout=30)
    try:
        await db.connect()
        result = await import_sqlite(db, Path(args.sqlite), replace=args.replace, all_history=args.all_history, batch=args.batch)
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        await db.close()
    for table in TABLES:
        print(f"{table:9s} {result[table]:>10d}")
    for key, n in sorted(result["skipped"].items()):
        if n:
            print(f"skipped   {n:>10d}  {key.replace('_', ' ')}")
    if args.rename:
        rename_imported(Path(args.sqlite))
    return 0


def main(argv: list[str] | None = None) -> int:
    from .config import config  # evaluated here, not at import: the module must stay usable without the environment

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    parser = build_parser({"sqlite": str(config.sqlite_path), "database_url": config.database_url})
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
