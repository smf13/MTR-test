"""Background scheduler that keeps every enabled target probed on its interval."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from .config import config
from .db import Database
from .mtr import HopResult, MtrResult, route_signature, routes_equivalent, run_mtr
from .notify import dispatch_event, target_url
from .resolver import resolve_host, reverse_lookup_many

log = logging.getLogger("mtr-tracker.scheduler")

TICK_SECONDS = 1.0
CLEANUP_EVERY = 3600.0


class Scheduler:
    def __init__(self, db: Database):
        self.db = db
        self._task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._sem = asyncio.Semaphore(config.max_concurrent_runs)
        self._running: dict[int, asyncio.Task[None]] = {}
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
        for t in (self._task, self._cleanup_task):
            if t:
                t.cancel()
        for t in list(self._running.values()):
            t.cancel()
        await asyncio.gather(*self._running.values(), return_exceptions=True)
        log.info("scheduler stopped")

    def wake(self) -> None:
        self._wake.set()

    @property
    def active_run_ids(self) -> list[int]:
        return sorted(self._running.keys())

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
            except Exception as exc:  # noqa: BLE001
                log.exception("run for target %s failed", target_id)
                now = time.time()
                run_id = await self.db.execute(
                    "INSERT INTO runs(target_id, started_at, finished_at, duration_ms, status, error, reached, hop_count, loss_pct) "
                    "VALUES (?, ?, ?, 0, 'error', ?, 0, 0, 100)",
                    (target_id, now, now, f"internal error: {exc}"),
                )
                await self._apply_status(t, run_id, "down", settings, error=str(exc))
            finally:
                self.runs_completed += 1
                # If the run overran the interval, go again right away (with a short
                # breather); otherwise keep the original cadence.
                next_at = max(started + interval, time.time() + 1.0)
                await self.db.execute("UPDATE targets SET next_run_at = ? WHERE id = ?", (next_at, target_id))

    async def _execute(self, t: dict[str, Any], settings: dict[str, Any]) -> None:
        started = time.time()
        try:
            dst_ip = await resolve_host(t["host"], t["ip_version"])
        except ValueError as exc:
            finished = time.time()
            run_id = await self.db.execute(
                "INSERT INTO runs(target_id, started_at, finished_at, duration_ms, status, error, reached, hop_count, loss_pct) "
                "VALUES (?, ?, ?, ?, 'error', ?, 0, 0, 100)",
                (t["id"], started, finished, (finished - started) * 1000, str(exc)),
            )
            await self._apply_status(t, run_id, "down", settings, error=str(exc))
            return

        result: MtrResult = await run_mtr(
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

        if not result.ok:
            run_id = await self.db.execute(
                "INSERT INTO runs(target_id, started_at, finished_at, duration_ms, status, error, src, dst_ip, reached, hop_count, loss_pct, command) "
                "VALUES (?, ?, ?, ?, 'error', ?, ?, ?, 0, 0, 100, ?)",
                (t["id"], result.started_at, result.finished_at, result.duration_ms, result.error, result.src, dst_ip, result.command),
            )
            await self._apply_status(t, run_id, "down", settings, error=result.error)
            return

        hops = result.hops
        if settings.get("reverse_dns", True):
            names = await reverse_lookup_many([h.ip for h in hops])
            for h in hops:
                h.hostname = names.get(h.ip or "")

        final = hops[-1]
        reached = final.ip == dst_ip and final.received > 0
        sig = route_signature(hops)

        previous = await self.db.fetchone(
            "SELECT id, route_hash, dst_ip FROM runs WHERE target_id = ? AND status = 'ok' ORDER BY started_at DESC LIMIT 1",
            (t["id"],),
        )
        route_changed = False
        dst_changed = False
        prev_ips: list[str | None] = []
        if previous is not None and previous["route_hash"] and previous["route_hash"] != sig:
            prev_rows = await self.db.fetchall("SELECT ip FROM hops WHERE run_id = ? ORDER BY hop_no", (previous["id"],))
            prev_ips = [r["ip"] for r in prev_rows]
            route_changed = not routes_equivalent(prev_ips, [h.ip for h in hops])
            dst_changed = bool(previous["dst_ip"]) and previous["dst_ip"] != dst_ip

        summary = _summarise(final, reached)
        run_id = await self.db.execute(
            "INSERT INTO runs(target_id, started_at, finished_at, duration_ms, status, error, src, dst_ip, reached, hop_count, sent, "
            "loss_pct, last_ms, avg_ms, best_ms, worst_ms, stdev_ms, jitter_avg_ms, jitter_max_ms, route_hash, route_changed, command) "
            "VALUES (?, ?, ?, ?, 'ok', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                t["id"], result.started_at, result.finished_at, result.duration_ms, result.src, dst_ip, int(reached), len(hops),
                final.sent, summary["loss_pct"], summary["last_ms"], summary["avg_ms"], summary["best_ms"], summary["worst_ms"],
                summary["stdev_ms"], summary["jitter_avg_ms"], summary["jitter_max_ms"], sig, int(route_changed), result.command,
            ),
            commit=False,
        )
        await self.db.executemany(
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

        if status == "down":
            reason = error or "destination unreachable"
            await self._event(t, run_id, "down", "critical", f"{t['name']} is DOWN: {reason}", details, settings)
        elif status == "degraded":
            reason = _degraded_reason(t, summary or {})
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
        asyncio.create_task(dispatch_event(settings, payload))


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


def _classify(t: dict[str, Any], reached: bool, summary: dict[str, Any]) -> str:
    if not reached or (summary.get("loss_pct") or 0) >= 100:
        return "down"
    loss_limit = float(t.get("alert_loss_pct") or 0)
    lat_limit = float(t.get("alert_latency_ms") or 0)
    if loss_limit > 0 and (summary.get("loss_pct") or 0) >= loss_limit:
        return "degraded"
    if lat_limit > 0 and summary.get("avg_ms") is not None and summary["avg_ms"] >= lat_limit:
        return "degraded"
    return "up"


def _degraded_reason(t: dict[str, Any], summary: dict[str, Any]) -> str:
    reasons = []
    loss_limit = float(t.get("alert_loss_pct") or 0)
    lat_limit = float(t.get("alert_latency_ms") or 0)
    loss = summary.get("loss_pct")
    avg = summary.get("avg_ms")
    if loss_limit > 0 and loss is not None and loss >= loss_limit:
        reasons.append(f"packet loss {loss:.1f}% (threshold {loss_limit:g}%)")
    if lat_limit > 0 and avg is not None and avg >= lat_limit:
        reasons.append(f"latency {avg:.1f} ms (threshold {lat_limit:g} ms)")
    return "; ".join(reasons) or "thresholds exceeded"
