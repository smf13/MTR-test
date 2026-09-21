"""Background scheduler that keeps every enabled target probed on its interval."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from typing import Any, Awaitable, Callable, Iterable

from . import geoip
from .config import config
from .db import Database
from .globalping import PATH_MEASUREMENTS, run_globalping_path
from .mtr import HopResult, MtrResult, route_signature, routes_equivalent, run_mtr
from .notify import dispatch_event, target_url
from .probes import run_probe, target_options
from .resolver import resolve_host, reverse_lookup_many

# Produces the path for a target: the local mtr binary, or a remote Globalping probe.
PathRunner = Callable[[dict[str, Any], dict[str, Any]], Awaitable[MtrResult]]

log = logging.getLogger("mtr-tracker.scheduler")

TICK_SECONDS = 1.0
CLEANUP_EVERY = 3600.0
# How long stop() waits for notifications still being delivered before giving up on them.
NOTIFY_DRAIN_TIMEOUT = 10.0


class Scheduler:
    def __init__(self, db: Database):
        self.db = db
        self._task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._sem = asyncio.Semaphore(config.max_concurrent_runs)
        self._running: dict[int, asyncio.Task[None]] = {}
        # Notification deliveries run detached from the run that produced them. asyncio keeps only weak
        # references to tasks, so hold them here (and drain them on stop()) or they can vanish mid-flight.
        self._notify_tasks: set[asyncio.Task[None]] = set()
        self._wake = asyncio.Event()
        self.started_at = time.time()
        self.runs_completed = 0

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="mtr-tracker-scheduler")
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="mtr-tracker-cleanup")
        log.info("scheduler started (max concurrent runs: %d, simulate=%s)", config.max_concurrent_runs, config.simulate)

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        loops = [t for t in (self._task, self._cleanup_task) if t is not None]
        runs = list(self._running.values())
        for t in (*loops, *runs):
            t.cancel()
        await asyncio.gather(*loops, *runs, return_exceptions=True)
        self._running.clear()
        await geoip.cancel_refresh()
        if self._notify_tasks:
            _, pending = await asyncio.wait(self._notify_tasks, timeout=NOTIFY_DRAIN_TIMEOUT)
            for t in pending:
                t.cancel()
            if pending:
                log.warning("gave up on %d notification(s) still in flight", len(pending))
        log.info("scheduler stopped")

    def wake(self) -> None:
        self._wake.set()

    @property
    def active_run_ids(self) -> list[int]:
        return sorted(self._running.keys())

    @property
    def pending_notifications(self) -> int:
        return len(self._notify_tasks)

    async def cancel(self, target_ids: Iterable[int]) -> None:
        """Abort in-flight runs for targets that are about to be deleted, and wait until they are gone."""
        tasks = {tid: self._running[tid] for tid in target_ids if tid in self._running}
        for t in tasks.values():
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            for tid in tasks:
                self._running.pop(tid, None)

    # -- loop --------------------------------------------------------------

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._dispatch_due()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _dispatch_due(self) -> None:
        now = time.time()
        rows = await self.db.fetchall(
            "SELECT id, interval_sec, next_run_at FROM targets WHERE enabled = 1 ORDER BY COALESCE(next_run_at, 0)"
        )
        for row in rows:
            tid = int(row["id"])
            if tid in self._running:
                continue
            due = row["next_run_at"]
            if due is None or float(due) <= now:
                self._launch(tid)

    def _launch(self, target_id: int) -> None:
        if target_id in self._running:
            return
        task = asyncio.create_task(self._run_target(target_id), name=f"mtr-tracker-run-{target_id}")
        self._running[target_id] = task
        task.add_done_callback(lambda _t, tid=target_id: self._running.pop(tid, None))

    async def run_now(self, target_id: int) -> bool:
        """Trigger an immediate run; returns False if one is already in flight."""
        if target_id in self._running:
            return False
        self._launch(target_id)
        return True

    async def _cleanup_loop(self) -> None:
        while not self._stop.is_set():
            try:
                settings = await self.db.get_settings()
                days = int(settings.get("retention_days") or 30)
                removed = await self.db.purge_older_than(days)
                if removed:
                    log.info("retention: purged %d runs older than %d days", removed, days)
                # The GeoLite2 database is refreshed weekly on the same hourly tick (a background task, so a
                # slow download never delays the purge).
                geoip.schedule_refresh(settings)
            except Exception:  # noqa: BLE001
                log.exception("cleanup failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=CLEANUP_EVERY)
            except asyncio.TimeoutError:
                pass

    # -- executing a single run --------------------------------------------

    async def _run_target(self, target_id: int) -> None:
        async with self._sem:
            target = await self.db.fetchone("SELECT * FROM targets WHERE id = ?", (target_id,))
            if target is None:
                return
            t = dict(target)
            interval = max(10, int(t["interval_sec"]))
            # Fixed cadence: the interval is measured from the start of this run, not
            # from its end, so "every 30s" means a run starts every 30s regardless of
            # how long mtr takes. The target stays in self._running meanwhile, which
            # prevents a double launch even if next_run_at passes during a slow run.
            started = time.time()
            await self.db.execute("UPDATE targets SET next_run_at = ? WHERE id = ?", (started + interval, target_id))
            settings = await self.db.get_settings()
            try:
                await self._execute(t, settings)
            except asyncio.CancelledError:
                raise
            except sqlite3.IntegrityError as exc:
                # The foreign key fails when the target row disappeared under a run that was not cancelled
                # (deleted straight from the database, for example): nothing left to record against.
                if await self._target_exists(target_id):
                    log.exception("run for target %s failed", target_id)
                    await self._record_internal_error(t, settings, exc)
                else:
                    log.info("target %s was deleted during its run; result discarded", target_id)
            except Exception as exc:  # noqa: BLE001
                log.exception("run for target %s failed", target_id)
                await self._record_internal_error(t, settings, exc)
            finally:
                self.runs_completed += 1
                # If the run overran the interval, go again right away (with a short
                # breather); otherwise keep the original cadence.
                next_at = max(started + interval, time.time() + 1.0)
                await self.db.execute("UPDATE targets SET next_run_at = ? WHERE id = ?", (next_at, target_id))

    async def _target_exists(self, target_id: int) -> bool:
        return await self.db.fetchone("SELECT 1 FROM targets WHERE id = ?", (target_id,)) is not None

    async def _record_internal_error(self, t: dict[str, Any], settings: dict[str, Any], exc: BaseException) -> None:
        now = time.time()
        try:
            run_id = await self.db.execute(
                "INSERT INTO runs(target_id, started_at, finished_at, duration_ms, status, error, reached, hop_count, loss_pct) "
                "VALUES (?, ?, ?, 0, 'error', ?, 0, 0, 100)",
                (t["id"], now, now, f"internal error: {exc}"),
            )
        except sqlite3.IntegrityError:
            log.info("target %s was deleted during its run; error not recorded", t["id"])
            return
        await self._apply_status(t, run_id, "down", settings, error=str(exc))

    async def _execute(self, t: dict[str, Any], settings: dict[str, Any]) -> None:
        kind = t.get("type") or "mtr"
        if kind == "mtr":
            await self._execute_path(t, settings, self._run_local_mtr, local_names=True)
        elif kind == "globalping" and str(target_options(t).get("measurement") or "ping") in PATH_MEASUREMENTS:
            # A remote mtr or traceroute comes back as hops too, so it takes the same path pipeline as the local binary.
            await self._execute_path(t, settings, lambda tt, s: run_globalping_path(tt, target_options(tt), s), local_names=False)
        else:
            await self._execute_probe(t, settings)

    @staticmethod
    async def _run_local_mtr(t: dict[str, Any], settings: dict[str, Any]) -> MtrResult:
        started = time.time()
        try:
            dst_ip = await resolve_host(t["host"], t["ip_version"])
        except ValueError as exc:
            return MtrResult(False, started, time.time(), "", error=str(exc))
        result = await run_mtr(
            dst_ip=dst_ip,
            count=int(t["count"]),
            probe_interval=float(t["probe_interval"]),
            protocol=t["protocol"],
            port=t["port"],
            packet_size=int(t["packet_size"]),
            max_hops=int(t["max_hops"]),
            ip_version=t["ip_version"],
            asn_lookup=bool(settings.get("asn_lookup", True)),
        )
        if result.dst_ip is None:
            result.dst_ip = dst_ip
        return result

    async def _execute_path(self, t: dict[str, Any], settings: dict[str, Any], runner: PathRunner, *, local_names: bool) -> None:
        """Run a path measurement, store its hops, judge reachability and route changes, apply the status."""
        result = await runner(t, settings)
        details_json = json.dumps(result.details) if result.details else None

        if not result.ok:
            run_id = await self.db.execute(
                "INSERT INTO runs(target_id, started_at, finished_at, duration_ms, status, error, src, dst_ip, reached, hop_count, loss_pct, command, details) "
                "VALUES (?, ?, ?, ?, 'error', ?, ?, ?, 0, 0, 100, ?, ?)",
                (t["id"], result.started_at, result.finished_at, result.duration_ms, result.error, result.src, result.dst_ip, result.command or None, details_json),
            )
            await self._apply_status(t, run_id, "down", settings, error=result.error)
            return

        hops = result.hops
        dst_ip = result.dst_ip
        # A remote probe reports the names it saw; only local runs get local reverse lookups.
        if local_names and settings.get("reverse_dns", True):
            names = await reverse_lookup_many([h.ip for h in hops])
            for h in hops:
                h.hostname = names.get(h.ip or "")

        final = hops[-1]
        reached = final.ip == dst_ip and final.received > 0
        sig = route_signature(hops)

        route_changed, previous, prev_ips = await self._route_changed(t["id"], sig, [h.ip for h in hops], settings) if reached else (False, None, [])
        dst_changed = route_changed and previous is not None and bool(previous["dst_ip"]) and previous["dst_ip"] != dst_ip

        summary = _summarise(final, reached)
        # The run and its hops land together or not at all.
        async with self.db.transaction() as tx:
            run_id = await tx.execute(
                "INSERT INTO runs(target_id, started_at, finished_at, duration_ms, status, error, src, dst_ip, reached, hop_count, sent, "
                "loss_pct, last_ms, avg_ms, best_ms, worst_ms, stdev_ms, jitter_avg_ms, jitter_max_ms, route_hash, route_changed, command, details) "
                "VALUES (?, ?, ?, ?, 'ok', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    t["id"], result.started_at, result.finished_at, result.duration_ms, result.src, dst_ip, int(reached), len(hops),
                    final.sent, summary["loss_pct"], summary["last_ms"], summary["avg_ms"], summary["best_ms"], summary["worst_ms"],
                    summary["stdev_ms"], summary["jitter_avg_ms"], summary["jitter_max_ms"], sig, int(route_changed), result.command, details_json,
                ),
            )
            await tx.executemany(
                "INSERT INTO hops(run_id, hop_no, ip, hostname, asn, loss_pct, sent, received, last_ms, avg_ms, best_ms, worst_ms, "
                "stdev_ms, gmean_ms, jitter_ms, jitter_avg_ms, jitter_max_ms, jitter_int_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id, h.hop_no, h.ip, h.hostname, h.asn, h.loss_pct, h.sent, h.received, h.last_ms, h.avg_ms, h.best_ms,
                        h.worst_ms, h.stdev_ms, h.gmean_ms, h.jitter_ms, h.jitter_avg_ms, h.jitter_max_ms, h.jitter_int_ms,
                    )
                    for h in hops
                ],
            )

        if route_changed:
            if dst_changed:
                msg = f"{t['host']} now resolves to {dst_ip} (was {previous['dst_ip']}); route has {len(hops)} hops"
            else:
                msg = f"Route to {t['name']} changed ({len(prev_ips)} -> {len(hops)} hops)"
            await self._event(
                t, run_id, "route_change", "info", msg,
                {"previous": prev_ips, "current": [h.ip for h in hops], "previous_dst_ip": previous["dst_ip"] if previous else None, "dst_ip": dst_ip},
                settings,
            )

        status = _classify(t, reached, summary)
        await self._apply_status(t, run_id, status, settings, summary=summary)

    async def _route_changed(self, target_id: int, sig: str, current: list[str | None], settings: dict[str, Any]) -> tuple[bool, Any, list[str | None]]:
        """(changed, previous reached run, its hop ips): is this hop sequence new, or a route seen recently?

        Route changes are only judged between runs that reached the destination. An unreachable run is padded
        with unknown hops up to max_hops, so comparing across an outage would announce a bogus reroute on top
        of the down and recovered events. A run differing from the previous one is still not a change when its
        sequence (hash, or wildcard-equivalent) appeared among the last `route_memory_runs` reached runs: a
        load-balanced path flaps between the same few routes every run, and that is not a reroute. Memory 1
        compares with the previous run alone.
        """
        memory = max(1, min(500, int(settings.get("route_memory_runs") or 1)))
        recent = await self.db.fetchall(
            "SELECT id, route_hash, dst_ip FROM runs WHERE target_id = ? AND status = 'ok' AND reached = 1 ORDER BY started_at DESC LIMIT ?",
            (target_id, memory),
        )
        if not recent or not recent[0]["route_hash"]:
            return False, recent[0] if recent else None, []
        previous = recent[0]
        prev_rows = await self.db.fetchall("SELECT ip FROM hops WHERE run_id = ? ORDER BY hop_no", (previous["id"],))
        prev_ips = [r["ip"] for r in prev_rows]
        if previous["route_hash"] == sig or routes_equivalent(prev_ips, current):
            return False, previous, prev_ips
        seen: set[str] = {str(previous["route_hash"])}
        for r in recent[1:]:
            if not r["route_hash"] or r["route_hash"] in seen:
                continue
            seen.add(str(r["route_hash"]))
            if r["route_hash"] == sig:
                return False, previous, prev_ips
            rows = await self.db.fetchall("SELECT ip FROM hops WHERE run_id = ? ORDER BY hop_no", (r["id"],))
            if routes_equivalent([x["ip"] for x in rows], current):
                return False, previous, prev_ips
        return True, previous, prev_ips

    async def _execute_probe(self, t: dict[str, Any], settings: dict[str, Any]) -> None:
        """Ping / HTTP / TCP / DNS / Globalping ping: one summary row per run, no hops."""
        o = await run_probe(t, settings)
        if not o.ok:
            run_id = await self.db.execute(
                "INSERT INTO runs(target_id, started_at, finished_at, duration_ms, status, error, dst_ip, reached, hop_count, sent, loss_pct, command, details) "
                "VALUES (?, ?, ?, ?, 'error', ?, ?, 0, 0, ?, 100, ?, ?)",
                (t["id"], o.started_at, o.finished_at, o.duration_ms, o.error, o.dst_ip, o.sent, o.command, json.dumps(o.details) if o.details else None),
            )
            await self._apply_status(t, run_id, "down", settings, error=o.error)
            return
        summary = o.summary()
        run_id = await self.db.execute(
            "INSERT INTO runs(target_id, started_at, finished_at, duration_ms, status, error, dst_ip, reached, hop_count, sent, "
            "loss_pct, last_ms, avg_ms, best_ms, worst_ms, stdev_ms, jitter_avg_ms, jitter_max_ms, command, details) "
            "VALUES (?, ?, ?, ?, 'ok', ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                t["id"], o.started_at, o.finished_at, o.duration_ms, o.error if not o.reached else None, o.dst_ip, int(o.reached), o.sent,
                summary["loss_pct"], summary["last_ms"], summary["avg_ms"], summary["best_ms"], summary["worst_ms"], summary["stdev_ms"],
                summary["jitter_avg_ms"], summary["jitter_max_ms"], o.command, json.dumps(o.details) if o.details else None,
            ),
        )
        status = _classify(t, o.reached, summary, o.warnings)
        await self._apply_status(t, run_id, status, settings, summary=summary, error=o.error if not o.reached else None, warnings=o.warnings)

    # -- status transitions and alerts ------------------------------------

    async def _apply_status(
        self,
        t: dict[str, Any],
        run_id: int,
        status: str,
        settings: dict[str, Any],
        *,
        summary: dict[str, Any] | None = None,
        error: str | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        prev = t.get("last_status") or "pending"
        await self.db.execute("UPDATE targets SET last_status = ? WHERE id = ?", (status, t["id"]))
        if prev == status:
            return
        details: dict[str, Any] = {"previous": prev, "current": status, "run_id": run_id}
        if summary:
            details.update({k: v for k, v in summary.items() if v is not None})
        if error:
            details["error"] = error
        if warnings:
            details["warnings"] = warnings

        if status == "down":
            reason = error or "destination unreachable"
            await self._event(t, run_id, "down", "critical", f"{t['name']} is DOWN: {reason}", details, settings)
        elif status == "degraded":
            reason = _degraded_reason(t, summary or {}, warnings)
            await self._event(t, run_id, "degraded", "warning", f"{t['name']} is degraded: {reason}", details, settings)
        elif status == "up" and prev in {"down", "degraded"}:
            await self._event(t, run_id, "recovered", "info", f"{t['name']} recovered ({prev} -> up)", details, settings)

    async def _event(
        self,
        t: dict[str, Any],
        run_id: int | None,
        kind: str,
        severity: str,
        message: str,
        details: dict[str, Any],
        settings: dict[str, Any],
    ) -> None:
        now = time.time()
        await self.db.execute(
            "INSERT INTO events(target_id, run_id, kind, severity, message, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (t["id"], run_id, kind, severity, message, json.dumps(details), now),
        )
        log.info("event [%s] %s", kind, message)
        if not t.get("notify", 1):
            # The event is on record (events page, target page, API); only the channels stay quiet for this target.
            log.debug("notifications are muted for target %s; %s event not delivered", t["id"], kind)
            return
        payload = {
            "source": settings.get("site_name") or "MTR Tracker",
            "event": kind,
            "severity": severity,
            "message": message,
            "target": {"id": t["id"], "name": t["name"], "host": t["host"]},
            "run_id": run_id,
            "details": details,
            "url": target_url(settings, t["id"]),
            "timestamp": now,
        }
        task = asyncio.create_task(dispatch_event(settings, payload), name=f"mtr-tracker-notify-{kind}")
        self._notify_tasks.add(task)
        task.add_done_callback(self._notify_tasks.discard)


def _summarise(final: HopResult, reached: bool) -> dict[str, Any]:
    if not reached:
        return {
            "loss_pct": 100.0, "last_ms": None, "avg_ms": None, "best_ms": None, "worst_ms": None,
            "stdev_ms": None, "jitter_avg_ms": None, "jitter_max_ms": None,
        }
    return {
        "loss_pct": final.loss_pct,
        "last_ms": final.last_ms,
        "avg_ms": final.avg_ms,
        "best_ms": final.best_ms,
        "worst_ms": final.worst_ms,
        "stdev_ms": final.stdev_ms,
        "jitter_avg_ms": final.jitter_avg_ms,
        "jitter_max_ms": final.jitter_max_ms,
    }


def _classify(t: dict[str, Any], reached: bool, summary: dict[str, Any], warnings: list[str] | None = None) -> str:
    if not reached or (summary.get("loss_pct") or 0) >= 100:
        return "down"
    if warnings:
        return "degraded"
    loss_limit = float(t.get("alert_loss_pct") or 0)
    lat_limit = float(t.get("alert_latency_ms") or 0)
    if loss_limit > 0 and (summary.get("loss_pct") or 0) >= loss_limit:
        return "degraded"
    if lat_limit > 0 and summary.get("avg_ms") is not None and summary["avg_ms"] >= lat_limit:
        return "degraded"
    return "up"


def _degraded_reason(t: dict[str, Any], summary: dict[str, Any], warnings: list[str] | None = None) -> str:
    reasons = list(warnings or [])
    loss_limit = float(t.get("alert_loss_pct") or 0)
    lat_limit = float(t.get("alert_latency_ms") or 0)
    loss = summary.get("loss_pct")
    avg = summary.get("avg_ms")
    if loss_limit > 0 and loss is not None and loss >= loss_limit:
        reasons.append(f"packet loss {loss:.1f}% (threshold {loss_limit:g}%)")
    if lat_limit > 0 and avg is not None and avg >= lat_limit:
        reasons.append(f"latency {avg:.1f} ms (threshold {lat_limit:g} ms)")
    return "; ".join(reasons) or "thresholds exceeded"
