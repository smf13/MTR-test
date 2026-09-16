"""HTTP API routes."""

from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from . import __version__
from .config import config
from .db import Database, rows_to_dicts
from .models import ProbeRequest, SettingsUpdate, TargetCreate, TargetUpdate
from .mtr import mtr_version, run_mtr
from .resolver import resolve_host, reverse_lookup_many
from .scheduler import Scheduler

router = APIRouter(prefix="/api")

_RANGE_RE = re.compile(r"^(\d+)([smhdw])$")
_RANGE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
SPARKLINE_POINTS = 60


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_range(value: str | None, default: int = 86400) -> int:
    if not value:
        return default
    m = _RANGE_RE.match(value.strip().lower())
    if m:
        return int(m.group(1)) * _RANGE_UNITS[m.group(2)]
    try:
        return max(60, int(value))
    except ValueError as exc:
        raise HTTPException(400, f"invalid range '{value}'") from exc


def _db(request: Request) -> Database:
    return request.app.state.db


def _sched(request: Request) -> Scheduler:
    return request.app.state.scheduler


def _target_out(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["enabled"] = bool(out.get("enabled"))
    try:
        out["tags"] = json.loads(out.get("tags") or "[]")
    except json.JSONDecodeError:
        out["tags"] = []
    for k in ("created_at", "updated_at", "next_run_at"):
        out[k] = _iso(out.get(k))
    return out


def _run_out(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["reached"] = bool(out.get("reached"))
    out["route_changed"] = bool(out.get("route_changed"))
    out["started_at"] = _iso(out.get("started_at"))
    out["finished_at"] = _iso(out.get("finished_at"))
    return out


def _event_out(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["created_at"] = _iso(out.get("created_at"))
    try:
        out["details"] = json.loads(out["details"]) if out.get("details") else None
    except json.JSONDecodeError:
        out["details"] = None
    return out


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return ordered[int(k)]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    db = _db(request)
    sched = _sched(request)
    counts = await db.fetchone(
        "SELECT COUNT(*) AS total, SUM(enabled) AS enabled, "
        "SUM(CASE WHEN enabled = 1 AND last_status = 'up' THEN 1 ELSE 0 END) AS up, "
        "SUM(CASE WHEN enabled = 1 AND last_status = 'degraded' THEN 1 ELSE 0 END) AS degraded, "
        "SUM(CASE WHEN enabled = 1 AND last_status = 'down' THEN 1 ELSE 0 END) AS down, "
        "SUM(CASE WHEN enabled = 1 AND last_status = 'pending' THEN 1 ELSE 0 END) AS pending "
        "FROM targets"
    )
    runs = await db.fetchone(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok FROM runs WHERE started_at >= ?",
        (time.time() - 86400,),
    )
    events = await db.fetchone("SELECT COUNT(*) AS n FROM events WHERE created_at >= ?", (time.time() - 86400,))
    total_runs = await db.fetchone("SELECT COUNT(*) AS n FROM runs")
    return {
        "app": "MTR Tracker",
        "version": __version__,
        "time": _iso(time.time()),
        "uptime_sec": int(time.time() - sched.started_at),
        "simulate": config.simulate,
        "mtr_version": await mtr_version(),
        "mtr_binary": config.mtr_binary,
        "max_concurrent_runs": config.max_concurrent_runs,
        "active_runs": sched.active_run_ids,
        "runs_completed_since_start": sched.runs_completed,
        "db_size_bytes": await db.db_size_bytes(),
        "db_path": str(config.db_path),
        "targets": {k: int(counts[k] or 0) for k in ("total", "enabled", "up", "degraded", "down", "pending")},
        "runs_24h": {"total": int(runs["total"] or 0), "ok": int(runs["ok"] or 0)},
        "runs_total": int(total_runs["n"] or 0),
        "events_24h": int(events["n"] or 0),
    }


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    return await _db(request).get_settings()


@router.put("/settings")
async def put_settings(request: Request, body: SettingsUpdate) -> dict[str, Any]:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return await _db(request).set_settings(patch)


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


async def _attach_summaries(db: Database, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not targets:
        return []
    ids = [t["id"] for t in targets]
    placeholders = ",".join("?" for _ in ids)
    since = time.time() - 86400

    latest_rows = await db.fetchall(
        f"SELECT r.* FROM runs r JOIN (SELECT target_id, MAX(started_at) AS m FROM runs WHERE target_id IN ({placeholders}) "
        f"GROUP BY target_id) x ON x.target_id = r.target_id AND x.m = r.started_at",
        ids,
    )
    latest = {int(r["target_id"]): _run_out(dict(r)) for r in latest_rows}

    stat_rows = await db.fetchall(
        f"SELECT target_id, COUNT(*) AS n, SUM(CASE WHEN status='ok' AND reached=1 THEN 1 ELSE 0 END) AS ok_n, "
        f"AVG(CASE WHEN reached=1 THEN avg_ms END) AS avg_ms, AVG(loss_pct) AS loss_pct, "
        f"SUM(route_changed) AS route_changes FROM runs WHERE started_at >= ? AND target_id IN ({placeholders}) GROUP BY target_id",
        [since, *ids],
    )
    stats = {int(r["target_id"]): dict(r) for r in stat_rows}

    spark_rows = await db.fetchall(
        f"SELECT target_id, started_at, avg_ms, loss_pct, reached FROM runs WHERE target_id IN ({placeholders}) "
        f"AND id IN (SELECT id FROM runs r2 WHERE r2.target_id = runs.target_id ORDER BY started_at DESC LIMIT {SPARKLINE_POINTS}) "
        f"ORDER BY started_at ASC",
        ids,
    )
    sparks: dict[int, list[dict[str, Any]]] = {}
    for r in spark_rows:
        sparks.setdefault(int(r["target_id"]), []).append(
            {"t": _iso(r["started_at"]), "avg": r["avg_ms"], "loss": r["loss_pct"], "reached": bool(r["reached"])}
        )

    out = []
    for t in targets:
        tid = t["id"]
        st = stats.get(tid) or {}
        n = int(st.get("n") or 0)
        ok_n = int(st.get("ok_n") or 0)
        item = dict(t)
        item["latest_run"] = latest.get(tid)
        item["stats_24h"] = {
            "runs": n,
            "availability_pct": round(100.0 * ok_n / n, 2) if n else None,
            "avg_ms": round(st["avg_ms"], 2) if st.get("avg_ms") is not None else None,
            "loss_pct": round(st["loss_pct"], 2) if st.get("loss_pct") is not None else None,
            "route_changes": int(st.get("route_changes") or 0),
        }
        item["sparkline"] = sparks.get(tid, [])
        out.append(item)
    return out


@router.get("/targets")
async def list_targets(request: Request) -> list[dict[str, Any]]:
    db = _db(request)
    rows = await db.fetchall("SELECT * FROM targets ORDER BY name COLLATE NOCASE")
    targets = [_target_out(dict(r)) for r in rows]
    return await _attach_summaries(db, targets)


@router.post("/targets", status_code=201)
async def create_target(request: Request, body: TargetCreate) -> dict[str, Any]:
    db = _db(request)
    now = time.time()
    data = body.model_dump()
    tid = await db.execute(
        "INSERT INTO targets(name, host, description, tags, interval_sec, count, probe_interval, protocol, port, packet_size, "
        "ip_version, max_hops, enabled, alert_loss_pct, alert_latency_ms, created_at, updated_at, next_run_at, last_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
        (
            data["name"], data["host"], data["description"], json.dumps(data["tags"]), data["interval_sec"], data["count"],
            data["probe_interval"], data["protocol"], data["port"], data["packet_size"], data["ip_version"], data["max_hops"],
            int(data["enabled"]), data["alert_loss_pct"], data["alert_latency_ms"], now, now, now,
        ),
    )
    _sched(request).wake()
    return await _load_target(request, tid)


async def _load_target(request: Request, target_id: int, range_sec: int = 86400) -> dict[str, Any]:
    db = _db(request)
    row = await db.fetchone("SELECT * FROM targets WHERE id = ?", (target_id,))
    if row is None:
        raise HTTPException(404, "target not found")
    target = _target_out(dict(row))
    [target] = await _attach_summaries(db, [target])
    target["stats"] = await _range_stats(db, target_id, range_sec)
    target["running"] = target_id in _sched(request).active_run_ids
    return target


@router.get("/targets/{target_id}")
async def get_target(request: Request, target_id: int, range: str | None = Query(default="24h")) -> dict[str, Any]:  # noqa: A002
    return await _load_target(request, target_id, parse_range(range))


async def _range_stats(db: Database, target_id: int, range_sec: int) -> dict[str, Any]:
    since = time.time() - range_sec
    rows = await db.fetchall(
        "SELECT status, reached, avg_ms, best_ms, worst_ms, loss_pct, jitter_avg_ms, route_changed, hop_count "
        "FROM runs WHERE target_id = ? AND started_at >= ?",
        (target_id, since),
    )
    n = len(rows)
    ok = [r for r in rows if r["status"] == "ok" and r["reached"]]
    avgs = [float(r["avg_ms"]) for r in ok if r["avg_ms"] is not None]
    losses = [float(r["loss_pct"]) for r in rows if r["loss_pct"] is not None]
    bests = [float(r["best_ms"]) for r in ok if r["best_ms"] is not None]
    worsts = [float(r["worst_ms"]) for r in ok if r["worst_ms"] is not None]
    jitters = [float(r["jitter_avg_ms"]) for r in ok if r["jitter_avg_ms"] is not None]
    hops = [int(r["hop_count"]) for r in ok if r["hop_count"]]
    events = await db.fetchone(
        "SELECT COUNT(*) AS n FROM events WHERE target_id = ? AND created_at >= ?", (target_id, since)
    )
    return {
        "range_sec": range_sec,
        "runs": n,
        "ok_runs": len(ok),
        "failed_runs": n - len(ok),
        "availability_pct": round(100.0 * len(ok) / n, 2) if n else None,
        "avg_ms": round(sum(avgs) / len(avgs), 2) if avgs else None,
        "p50_ms": round(_percentile(avgs, 0.5) or 0, 2) if avgs else None,
        "p95_ms": round(_percentile(avgs, 0.95) or 0, 2) if avgs else None,
        "p99_ms": round(_percentile(avgs, 0.99) or 0, 2) if avgs else None,
        "best_ms": round(min(bests), 2) if bests else None,
        "worst_ms": round(max(worsts), 2) if worsts else None,
        "loss_pct": round(sum(losses) / len(losses), 2) if losses else None,
        "max_loss_pct": round(max(losses), 2) if losses else None,
        "jitter_ms": round(sum(jitters) / len(jitters), 2) if jitters else None,
        "route_changes": sum(int(r["route_changed"]) for r in rows),
        "hop_count_min": min(hops) if hops else None,
        "hop_count_max": max(hops) if hops else None,
        "events": int(events["n"] or 0),
    }


@router.put("/targets/{target_id}")
async def update_target(request: Request, target_id: int, body: TargetUpdate) -> dict[str, Any]:
    db = _db(request)
    row = await db.fetchone("SELECT * FROM targets WHERE id = ?", (target_id,))
    if row is None:
        raise HTTPException(404, "target not found")
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if not patch:
        return await _load_target(request, target_id)
    if "tags" in patch and patch["tags"] is not None:
        patch["tags"] = json.dumps(patch["tags"])
    if "enabled" in patch and patch["enabled"] is not None:
        patch["enabled"] = int(patch["enabled"])
    patch["updated_at"] = time.time()
    # Re-run promptly when the schedule or probe definition changes.
    if any(k in patch for k in ("interval_sec", "host", "enabled", "protocol", "port", "ip_version")):
        patch["next_run_at"] = time.time()
        if patch.get("enabled") == 1 and not row["enabled"]:
            patch["last_status"] = "pending"
    cols = ", ".join(f"{k} = ?" for k in patch)
    await db.execute(f"UPDATE targets SET {cols} WHERE id = ?", [*patch.values(), target_id])
    _sched(request).wake()
    return await _load_target(request, target_id)


@router.delete("/targets/{target_id}", status_code=204)
async def delete_target(request: Request, target_id: int) -> None:
    db = _db(request)
    row = await db.fetchone("SELECT id FROM targets WHERE id = ?", (target_id,))
    if row is None:
        raise HTTPException(404, "target not found")
    await db.execute("DELETE FROM targets WHERE id = ?", (target_id,))


@router.post("/targets/{target_id}/run", status_code=202)
async def run_target_now(request: Request, target_id: int) -> dict[str, Any]:
    db = _db(request)
    row = await db.fetchone("SELECT id FROM targets WHERE id = ?", (target_id,))
    if row is None:
        raise HTTPException(404, "target not found")
    started = await _sched(request).run_now(target_id)
    return {"queued": started, "already_running": not started}


@router.get("/targets/{target_id}/runs")
async def list_runs(
    request: Request,
    target_id: int,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    range: str | None = Query(default=None),  # noqa: A002
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    db = _db(request)
    clauses = ["target_id = ?"]
    params: list[Any] = [target_id]
    if range:
        clauses.append("started_at >= ?")
        params.append(time.time() - parse_range(range))
    if status == "ok":
        clauses.append("status = 'ok' AND reached = 1")
    elif status == "failed":
        clauses.append("(status != 'ok' OR reached = 0)")
    elif status == "route_change":
        clauses.append("route_changed = 1")
    where = " AND ".join(clauses)
    total = await db.fetchone(f"SELECT COUNT(*) AS n FROM runs WHERE {where}", params)
    rows = await db.fetchall(
        f"SELECT * FROM runs WHERE {where} ORDER BY started_at DESC LIMIT ? OFFSET ?", [*params, limit, offset]
    )
    return {"total": int(total["n"] or 0), "items": [_run_out(dict(r)) for r in rows]}


@router.get("/targets/{target_id}/series")
async def get_series(
    request: Request,
    target_id: int,
    range: str = Query(default="24h"),  # noqa: A002
    max_points: int = Query(default=600, ge=50, le=5000),
) -> dict[str, Any]:
    db = _db(request)
    range_sec = parse_range(range)
    since = time.time() - range_sec
    count_row = await db.fetchone(
        "SELECT COUNT(*) AS n FROM runs WHERE target_id = ? AND started_at >= ?", (target_id, since)
    )
    n = int(count_row["n"] or 0)
    if n <= max_points:
        rows = await db.fetchall(
            "SELECT id, started_at, status, reached, avg_ms, best_ms, worst_ms, loss_pct, jitter_avg_ms, hop_count, route_changed "
            "FROM runs WHERE target_id = ? AND started_at >= ? ORDER BY started_at ASC",
            (target_id, since),
        )
        points = [
            {
                "t": _iso(r["started_at"]),
                "run_id": r["id"],
                "avg": r["avg_ms"],
                "best": r["best_ms"],
                "worst": r["worst_ms"],
                "loss": r["loss_pct"],
                "jitter": r["jitter_avg_ms"],
                "hops": r["hop_count"],
                "ok": bool(r["status"] == "ok" and r["reached"]),
                "route_changed": bool(r["route_changed"]),
                "n": 1,
            }
            for r in rows
        ]
        return {"range_sec": range_sec, "bucket_sec": None, "points": points}

    bucket = max(10, math.ceil(range_sec / max_points))
    rows = await db.fetchall(
        "SELECT CAST(started_at / ? AS INTEGER) * ? AS bucket, COUNT(*) AS n, "
        "SUM(CASE WHEN status='ok' AND reached=1 THEN 1 ELSE 0 END) AS ok_n, "
        "AVG(CASE WHEN reached=1 THEN avg_ms END) AS avg_ms, MIN(best_ms) AS best_ms, MAX(worst_ms) AS worst_ms, "
        "AVG(loss_pct) AS loss_pct, MAX(loss_pct) AS max_loss, AVG(jitter_avg_ms) AS jitter, MAX(hop_count) AS hops, "
        "SUM(route_changed) AS route_changes "
        "FROM runs WHERE target_id = ? AND started_at >= ? GROUP BY bucket ORDER BY bucket ASC",
        (bucket, bucket, target_id, since),
    )
    points = [
        {
            "t": _iso(float(r["bucket"])),
            "run_id": None,
            "avg": round(r["avg_ms"], 3) if r["avg_ms"] is not None else None,
            "best": r["best_ms"],
            "worst": r["worst_ms"],
            "loss": round(r["loss_pct"], 3) if r["loss_pct"] is not None else None,
            "max_loss": r["max_loss"],
            "jitter": round(r["jitter"], 3) if r["jitter"] is not None else None,
            "hops": r["hops"],
            "ok": int(r["ok_n"] or 0) == int(r["n"] or 0),
            "route_changed": int(r["route_changes"] or 0) > 0,
            "n": int(r["n"] or 0),
        }
        for r in rows
    ]
    return {"range_sec": range_sec, "bucket_sec": bucket, "points": points}


@router.get("/targets/{target_id}/hops/history")
async def get_hop_history(
    request: Request,
    target_id: int,
    range: str = Query(default="24h"),  # noqa: A002
    max_runs: int = Query(default=120, ge=10, le=500),
) -> dict[str, Any]:
    db = _db(request)
    since = time.time() - parse_range(range)
    runs = await db.fetchall(
        "SELECT id, started_at, hop_count, reached FROM runs WHERE target_id = ? AND started_at >= ? AND status = 'ok' "
        "ORDER BY started_at ASC",
        (target_id, since),
    )
    runs = list(runs)
    if len(runs) > max_runs:
        step = len(runs) / max_runs
        runs = [runs[int(i * step)] for i in range(max_runs)]
    if not runs:
        return {"runs": [], "max_hops": 0}
    ids = [int(r["id"]) for r in runs]
    placeholders = ",".join("?" for _ in ids)
    hop_rows = await db.fetchall(
        f"SELECT run_id, hop_no, ip, hostname, loss_pct, avg_ms, best_ms, worst_ms, jitter_avg_ms FROM hops "
        f"WHERE run_id IN ({placeholders}) ORDER BY run_id, hop_no",
        ids,
    )
    by_run: dict[int, list[dict[str, Any]]] = {}
    for h in hop_rows:
        by_run.setdefault(int(h["run_id"]), []).append(
            {
                "hop": h["hop_no"],
                "ip": h["ip"],
                "hostname": h["hostname"],
                "loss": h["loss_pct"],
                "avg": h["avg_ms"],
                "best": h["best_ms"],
                "worst": h["worst_ms"],
                "jitter": h["jitter_avg_ms"],
            }
        )
    out_runs = [
        {"run_id": int(r["id"]), "t": _iso(r["started_at"]), "reached": bool(r["reached"]), "hops": by_run.get(int(r["id"]), [])}
        for r in runs
    ]
    return {"runs": out_runs, "max_hops": max((int(r["hop_count"]) for r in runs), default=0)}


@router.get("/targets/{target_id}/hops/summary")
async def get_hop_summary(
    request: Request, target_id: int, range: str = Query(default="24h")  # noqa: A002
) -> dict[str, Any]:
    db = _db(request)
    since = time.time() - parse_range(range)
    rows = await db.fetchall(
        "SELECT h.hop_no, h.ip, MAX(h.hostname) AS hostname, MAX(h.asn) AS asn, COUNT(*) AS n, "
        "SUM(h.sent) AS sent, SUM(h.received) AS received, AVG(h.loss_pct) AS loss_pct, MAX(h.loss_pct) AS max_loss, "
        "AVG(h.avg_ms) AS avg_ms, MIN(h.best_ms) AS best_ms, MAX(h.worst_ms) AS worst_ms, AVG(h.stdev_ms) AS stdev_ms, "
        "AVG(h.jitter_avg_ms) AS jitter_ms, MAX(h.jitter_max_ms) AS jitter_max_ms "
        "FROM hops h JOIN runs r ON r.id = h.run_id WHERE r.target_id = ? AND r.started_at >= ? AND r.status = 'ok' "
        "GROUP BY h.hop_no, h.ip ORDER BY h.hop_no ASC, n DESC",
        (target_id, since),
    )
    total_runs_row = await db.fetchone(
        "SELECT COUNT(*) AS n FROM runs WHERE target_id = ? AND started_at >= ? AND status = 'ok'", (target_id, since)
    )
    total_runs = int(total_runs_row["n"] or 0)
    hops: dict[int, dict[str, Any]] = {}
    for r in rows:
        hop_no = int(r["hop_no"])
        sent = int(r["sent"] or 0)
        received = int(r["received"] or 0)
        entry = {
            "ip": r["ip"],
            "hostname": r["hostname"],
            "asn": r["asn"],
            "runs": int(r["n"]),
            "share_pct": round(100.0 * int(r["n"]) / total_runs, 1) if total_runs else None,
            "loss_pct": round(100.0 * (sent - received) / sent, 2) if sent else 100.0,
            "max_loss_pct": r["max_loss"],
            "avg_ms": round(r["avg_ms"], 3) if r["avg_ms"] is not None else None,
            "best_ms": r["best_ms"],
            "worst_ms": r["worst_ms"],
            "stdev_ms": round(r["stdev_ms"], 3) if r["stdev_ms"] is not None else None,
            "jitter_ms": round(r["jitter_ms"], 3) if r["jitter_ms"] is not None else None,
            "jitter_max_ms": r["jitter_max_ms"],
        }
        slot = hops.setdefault(hop_no, {"hop": hop_no, "primary": None, "alternates": []})
        if slot["primary"] is None:
            slot["primary"] = entry
        else:
            slot["alternates"].append(entry)
    return {"total_runs": total_runs, "hops": [hops[k] for k in sorted(hops)]}


@router.get("/targets/{target_id}/events")
async def list_target_events(
    request: Request, target_id: int, limit: int = Query(default=100, ge=1, le=1000), range: str | None = None  # noqa: A002
) -> list[dict[str, Any]]:
    db = _db(request)
    params: list[Any] = [target_id]
    where = "target_id = ?"
    if range:
        where += " AND created_at >= ?"
        params.append(time.time() - parse_range(range))
    rows = await db.fetchall(f"SELECT * FROM events WHERE {where} ORDER BY created_at DESC LIMIT ?", [*params, limit])
    return [_event_out(dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


async def _load_run(db: Database, run_id: int) -> dict[str, Any]:
    row = await db.fetchone(
        "SELECT r.*, t.name AS target_name, t.host AS target_host FROM runs r JOIN targets t ON t.id = r.target_id WHERE r.id = ?",
        (run_id,),
    )
    if row is None:
        raise HTTPException(404, "run not found")
    run = _run_out(dict(row))
    hops = await db.fetchall("SELECT * FROM hops WHERE run_id = ? ORDER BY hop_no ASC", (run_id,))
    run["hops"] = rows_to_dicts(hops)
    prev = await db.fetchone(
        "SELECT id FROM runs WHERE target_id = ? AND started_at < ? ORDER BY started_at DESC LIMIT 1",
        (run["target_id"], row["started_at"]),
    )
    nxt = await db.fetchone(
        "SELECT id FROM runs WHERE target_id = ? AND started_at > ? ORDER BY started_at ASC LIMIT 1",
        (run["target_id"], row["started_at"]),
    )
    run["prev_run_id"] = prev["id"] if prev else None
    run["next_run_id"] = nxt["id"] if nxt else None
    return run


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: int) -> dict[str, Any]:
    return await _load_run(_db(request), run_id)


@router.get("/runs/{run_id}/report", response_class=PlainTextResponse)
async def get_run_report(request: Request, run_id: int) -> str:
    run = await _load_run(_db(request), run_id)
    return format_text_report(run)


def format_text_report(run: dict[str, Any]) -> str:
    lines = [
        f"Start: {run['started_at']}",
        f"HOST: {run.get('src') or '-'}  ->  {run.get('target_host')} ({run.get('dst_ip') or '-'})",
    ]
    if run.get("status") != "ok":
        lines.append(f"STATUS: {run.get('status')} - {run.get('error') or ''}")
        return "\n".join(lines) + "\n"
    header = f"{'Hop':>4} {'Host':<44} {'Loss%':>6} {'Snt':>4} {'Last':>8} {'Avg':>8} {'Best':>8} {'Wrst':>8} {'StDev':>7} {'Javg':>7}"
    lines.append(header)
    for h in run["hops"]:
        name = h.get("hostname") or h.get("ip") or "???"
        if h.get("hostname") and h.get("ip"):
            name = f"{h['hostname']} ({h['ip']})"
        if h.get("asn"):
            name = f"[{h['asn']}] {name}"

        def f(v: Any) -> str:
            return f"{v:8.1f}" if isinstance(v, (int, float)) else f"{'-':>8}"

        lines.append(
            f"{h['hop_no']:>4} {name[:44]:<44} {h['loss_pct']:>5.1f}% {h['sent']:>4} {f(h.get('last_ms'))} {f(h.get('avg_ms'))} "
            f"{f(h.get('best_ms'))} {f(h.get('worst_ms'))} {f(h.get('stdev_ms'))[1:]} {f(h.get('jitter_avg_ms'))[1:]}"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@router.get("/events")
async def list_events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    kind: str | None = None,
    severity: str | None = None,
    target_id: int | None = None,
    range: str | None = None,  # noqa: A002
) -> dict[str, Any]:
    db = _db(request)
    clauses: list[str] = ["1=1"]
    params: list[Any] = []
    if kind:
        clauses.append("e.kind = ?")
        params.append(kind)
    if severity:
        clauses.append("e.severity = ?")
        params.append(severity)
    if target_id is not None:
        clauses.append("e.target_id = ?")
        params.append(target_id)
    if range:
        clauses.append("e.created_at >= ?")
        params.append(time.time() - parse_range(range))
    where = " AND ".join(clauses)
    total = await db.fetchone(f"SELECT COUNT(*) AS n FROM events e WHERE {where}", params)
    rows = await db.fetchall(
        f"SELECT e.*, t.name AS target_name, t.host AS target_host FROM events e LEFT JOIN targets t ON t.id = e.target_id "
        f"WHERE {where} ORDER BY e.created_at DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    return {"total": int(total["n"] or 0), "items": [_event_out(dict(r)) for r in rows]}


@router.delete("/events", status_code=204)
async def clear_events(request: Request, target_id: int | None = None) -> None:
    db = _db(request)
    if target_id is None:
        await db.execute("DELETE FROM events")
    else:
        await db.execute("DELETE FROM events WHERE target_id = ?", (target_id,))


# ---------------------------------------------------------------------------
# Ad-hoc probe (not persisted)
# ---------------------------------------------------------------------------


@router.post("/probe")
async def adhoc_probe(request: Request, body: ProbeRequest) -> dict[str, Any]:
    settings = await _db(request).get_settings()
    try:
        dst_ip = await resolve_host(body.host.strip(), body.ip_version)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result = await run_mtr(
        dst_ip=dst_ip,
        count=body.count,
        probe_interval=0.5,
        protocol=body.protocol,
        port=body.port,
        packet_size=64,
        max_hops=body.max_hops,
        ip_version=body.ip_version,
        asn_lookup=bool(settings.get("asn_lookup", True)),
    )
    if not result.ok:
        raise HTTPException(502, result.error or "mtr failed")
    if settings.get("reverse_dns", True):
        names = await reverse_lookup_many([h.ip for h in result.hops])
        for h in result.hops:
            h.hostname = names.get(h.ip or "")
    final = result.hops[-1]
    return {
        "host": body.host,
        "dst_ip": dst_ip,
        "src": result.src,
        "reached": final.ip == dst_ip and final.received > 0,
        "duration_ms": round(result.duration_ms, 1),
        "command": result.command,
        "hops": [h.__dict__ for h in result.hops],
    }
