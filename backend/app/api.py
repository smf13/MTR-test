"""HTTP API routes."""

from __future__ import annotations

import asyncio
from collections import deque
import hmac
import json
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from . import __version__, geoip, ipapi
from .config import config
from .db import Database, rows_to_dicts
from .models import BulkAction, NotificationTest, ProbeRequest, SettingsUpdate, TargetCreate, TargetImport, TargetUpdate, sort_tags, validate_options
from .mtr import min_probe_interval, mtr_version, routes_equivalent, run_mtr
from .notify import NotifyError, format_pushover_text, send_pushover, send_webhook, target_url
from .resolver import resolve_host, reverse_lookup_many
from .scheduler import Scheduler

router = APIRouter(prefix="/api")

_RANGE_RE = re.compile(r"^(\d+)([smhdw])$")
_RANGE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
SPARKLINE_POINTS = 60
TIMELINE_BUCKETS = 48
TIMELINE_BUCKET_SEC = 86400 // TIMELINE_BUCKETS


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


# ---------------------------------------------------------------------------
# Authentication and secret handling
# ---------------------------------------------------------------------------


def request_token(request: Request) -> str:
    """The API token a request carries (`Authorization: Bearer` or `X-Api-Token`), empty when absent."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-token", "").strip()


def is_authenticated(request: Request) -> bool:
    """True on an open instance (no token configured) or when the request carries the configured token."""
    if not config.api_token:
        return True
    supplied = request_token(request)
    # Bytes, not str: compare_digest raises on non-ASCII text, and header values may carry any byte.
    return bool(supplied) and hmac.compare_digest(supplied.encode("utf-8"), config.api_token.encode("utf-8"))


SECRET_MASK = "********"
_SECRET_KEYS = ("pushover_api_token", "pushover_user_key", "globalping_token", "maxmind_license_key")


def _mask_token(value: str) -> str:
    return SECRET_MASK + value[-4:] if len(value) > 8 else SECRET_MASK


def _mask_url(value: str) -> str:
    """Keep scheme and host so the operator sees where events go; hide path and query, which usually carry the secret."""
    parts = urlsplit(value)
    if not (parts.path.strip("/") or parts.query):
        return value
    return f"{parts.scheme}://{parts.netloc}/{SECRET_MASK}"


def redact_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Settings as shown to a reader without the API token: credentials and webhook secrets are masked."""
    out = dict(settings)
    for key in _SECRET_KEYS:
        if out.get(key):
            out[key] = _mask_token(str(out[key]))
    if out.get("webhook_url"):
        out["webhook_url"] = _mask_url(str(out["webhook_url"]))
    return out


def strip_masked(patch: dict[str, Any]) -> dict[str, Any]:
    """Drop values that still carry the mask: the client echoed a redacted setting back unchanged."""
    return {k: v for k, v in patch.items() if not (isinstance(v, str) and SECRET_MASK in v)}


# Ad-hoc traces are not bounded by the scheduler; cap them so a burst of quick traces cannot fork mtr without limit.
ADHOC_PROBE_SLOTS = 2
ADHOC_PROBE_WAIT = 30.0
_adhoc_slots = asyncio.Semaphore(ADHOC_PROBE_SLOTS)


def _target_out(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["enabled"] = bool(out.get("enabled"))
    out["notify"] = bool(out.get("notify", 1))
    try:
        # Sorted on the way out as well as on write, so rows saved before tags were sorted read the same.
        out["tags"] = sort_tags([str(t) for t in json.loads(out.get("tags") or "[]")])
    except (json.JSONDecodeError, TypeError):
        out["tags"] = []
    out["type"] = out.get("type") or "mtr"
    try:
        out["options"] = json.loads(out.get("options") or "{}")
    except json.JSONDecodeError:
        out["options"] = {}
    for k in ("created_at", "updated_at", "next_run_at"):
        out[k] = _iso(out.get(k))
    return out


def _run_out(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["reached"] = bool(out.get("reached"))
    out["route_changed"] = bool(out.get("route_changed"))
    out["started_at"] = _iso(out.get("started_at"))
    out["finished_at"] = _iso(out.get("finished_at"))
    raw = out.pop("details", None)
    try:
        out["details"] = json.loads(raw) if raw else None
    except (json.JSONDecodeError, TypeError):
        out["details"] = None
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
        "min_probe_interval": min_probe_interval(),
        "max_concurrent_runs": config.max_concurrent_runs,
        "active_runs": sched.active_run_ids,
        "runs_completed_since_start": sched.runs_completed,
        "db_size_bytes": await db.db_size_bytes(),
        # The database location is only shown to callers that hold the API token; the password is never included.
        "database": db.describe() if is_authenticated(request) else None,
        "targets": {k: int(counts[k] or 0) for k in ("total", "enabled", "up", "degraded", "down", "pending")},
        "runs_24h": {"total": int(runs["total"] or 0), "ok": int(runs["ok"] or 0)},
        "runs_total": int(total_runs["n"] or 0),
        "events_24h": int(events["n"] or 0),
    }


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    """Reads stay open, but when a token is configured only requests carrying it see the secrets unmasked."""
    settings = await _db(request).get_settings()
    return settings if is_authenticated(request) else redact_settings(settings)


@router.put("/settings")
async def put_settings(request: Request, body: SettingsUpdate) -> dict[str, Any]:
    # A masked value echoed back by the UI means "unchanged"; only real values are written.
    patch = strip_masked({k: v for k, v in body.model_dump().items() if v is not None})
    settings = await _db(request).set_settings(patch)
    if "maxmind_license_key" in patch or "maxmind_account_id" in patch:
        # A newly saved key fetches the database in the background, so the map appears without a restart.
        geoip.schedule_refresh(settings)
    if "ip_api_enabled" in patch:
        # A toggled switch ends any pause after a failure, so the next lookup tries the service again.
        ipapi.wake()
    return settings


@router.get("/geoip/status")
async def geoip_status(request: Request) -> dict[str, Any]:
    """State of the MaxMind GeoLite2 City and ASN databases (configured, downloaded, build dates, last error) and, under `ip_api`, of the ip-api.com provider."""
    status = geoip.status(await _db(request).get_settings())
    for key in ("build_epoch", "downloaded_at", "asn_build_epoch", "asn_downloaded_at", "last_attempt", "last_success"):
        status[key] = _iso(status[key])
    for key in ("paused_until", "last_attempt", "last_success"):
        status["ip_api"][key] = _iso(status["ip_api"][key])
    return status


@router.get("/geoip/lookup")
async def geoip_lookup(request: Request, q: str = Query(min_length=1, max_length=253), provider: Literal["auto", "ip-api", "maxmind"] = "auto") -> dict[str, Any]:
    """Locate one address or host name; `q=self` locates this server's public address.

    `provider` picks the backend: `auto` asks exactly as the map does (ip-api.com first when switched on, then the
    GeoLite2 databases), `ip-api` asks the service alone (even with its switch off; its budget and pause apply),
    `maxmind` asks the databases alone.
    """
    result = await geoip.lookup_query(q, await _db(request).get_settings(), provider)
    result["build_epoch"] = _iso(result["build_epoch"])
    return result


@router.post("/geoip/update")
async def geoip_update(request: Request) -> dict[str, Any]:
    """Download the GeoLite2 City and ASN databases now with the saved MaxMind credentials."""
    settings = await _db(request).get_settings()
    if not geoip.maxmind_configured(settings):
        raise HTTPException(400, "save a MaxMind licence key first")
    try:
        await geoip.download(settings)
    except geoip.GeoIpError as exc:
        raise HTTPException(502, str(exc)) from exc
    return await geoip_status(request)


@router.post("/notifications/test")
async def test_notification(request: Request, body: NotificationTest) -> dict[str, Any]:
    """Send a test message through one channel using saved settings merged with any unsaved overrides."""
    settings = await _db(request).get_settings()
    if body.settings is not None:
        settings.update(strip_masked({k: v for k, v in body.settings.model_dump(exclude_unset=True).items() if v is not None}))
    site = settings.get("site_name") or "MTR Tracker"
    payload = {
        "source": site,
        "event": "test",
        "severity": "info",
        "message": f"Test notification from {site}. Notifications are working.",
        "target": {"id": None, "name": "Test", "host": "example.invalid"},
        "run_id": None,
        "details": {},
        "url": target_url(settings, None),
        "timestamp": time.time(),
    }
    try:
        if body.channel == "webhook":
            await send_webhook((settings.get("webhook_url") or "").strip(), payload)
        else:
            title, message = format_pushover_text(payload)
            await send_pushover(settings, title=title, message=message, kind="recovered")
    except NotifyError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, "channel": body.channel}


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

    timeline_rows = await db.fetchall(
        f"SELECT r.target_id, floor((r.started_at - ?) / ?)::int AS b, COUNT(*) AS n, AVG(CASE WHEN r.reached = 1 THEN r.avg_ms END) AS avg_ms, "
        f"MAX(CASE WHEN r.status != 'ok' OR r.reached = 0 THEN 3 "
        f"WHEN (t.alert_loss_pct > 0 AND r.loss_pct >= t.alert_loss_pct) OR (t.alert_latency_ms > 0 AND r.avg_ms >= t.alert_latency_ms) THEN 2 ELSE 1 END) AS worst "
        f"FROM runs r JOIN targets t ON t.id = r.target_id WHERE r.started_at >= ? AND r.target_id IN ({placeholders}) GROUP BY r.target_id, b",
        [since, TIMELINE_BUCKET_SEC, since, *ids],
    )
    timelines: dict[int, list[dict[str, Any] | None]] = {tid: [None] * TIMELINE_BUCKETS for tid in ids}
    worst_label = {1: "up", 2: "degraded", 3: "down"}
    for r in timeline_rows:
        b = int(r["b"])
        if 0 <= b < TIMELINE_BUCKETS:
            timelines[int(r["target_id"])][b] = {"s": worst_label[int(r["worst"])], "n": int(r["n"]), "avg": round(r["avg_ms"], 1) if r["avg_ms"] is not None else None}

    # Last SPARKLINE_POINTS runs per target in one pass. A correlated `id IN (... LIMIT n)` subquery is
    # re-evaluated for every run row and took seconds on large histories.
    spark_rows = await db.fetchall(
        f"SELECT target_id, started_at, avg_ms, loss_pct, reached FROM ("
        f"SELECT id, target_id, started_at, avg_ms, loss_pct, reached, "
        f"ROW_NUMBER() OVER (PARTITION BY target_id ORDER BY started_at DESC, id DESC) AS rn "
        f"FROM runs WHERE target_id IN ({placeholders})) AS ranked WHERE rn <= {SPARKLINE_POINTS} ORDER BY started_at ASC, id ASC",
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
        item["timeline"] = {"bucket_sec": TIMELINE_BUCKET_SEC, "since": _iso(since), "buckets": timelines.get(tid, [])}
        out.append(item)
    return out


@router.get("/targets")
async def list_targets(request: Request) -> list[dict[str, Any]]:
    db = _db(request)
    rows = await db.fetchall("SELECT * FROM targets ORDER BY lower(name), name")
    targets = [_target_out(dict(r)) for r in rows]
    return await _attach_summaries(db, targets)


@router.get("/tags")
async def list_tags(request: Request) -> list[dict[str, Any]]:
    """Every tag in use, alphabetically, with how many targets carry it and its configured colour (null = automatic)."""
    db = _db(request)
    counts: dict[str, int] = {}
    for row in await db.fetchall("SELECT tags FROM targets"):
        try:
            tags = json.loads(row["tags"] or "[]")
        except json.JSONDecodeError:
            continue
        for tag in {str(t) for t in tags}:
            counts[tag] = counts.get(tag, 0) + 1
    colors = (await db.get_settings()).get("tag_colors") or {}
    return [{"name": tag, "count": counts[tag], "color": colors.get(tag)} for tag in sort_tags(list(counts))]


async def _insert_target(db: Database, data: dict[str, Any]) -> int:
    now = time.time()
    return int(await db.fetchval(
        "INSERT INTO targets(name, host, type, options, description, tags, interval_sec, count, probe_interval, protocol, port, packet_size, "
        "ip_version, max_hops, enabled, notify, alert_loss_pct, alert_latency_ms, created_at, updated_at, next_run_at, last_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending') RETURNING id",
        (
            data["name"], data["host"], data["type"], json.dumps(data["options"]), data["description"], json.dumps(data["tags"]), data["interval_sec"],
            data["count"], data["probe_interval"], data["protocol"], data["port"], data["packet_size"], data["ip_version"], data["max_hops"],
            int(data["enabled"]), int(data.get("notify", True)), data["alert_loss_pct"], data["alert_latency_ms"], now, now, now,
        ),
    ))


EXPORT_FIELDS = (
    "name", "host", "type", "options", "description", "tags", "interval_sec", "count", "probe_interval", "protocol", "port", "packet_size",
    "ip_version", "max_hops", "enabled", "notify", "alert_loss_pct", "alert_latency_ms",
)
_BOOL_FIELDS = ("enabled", "notify")


@router.post("/targets", status_code=201)
async def create_target(request: Request, body: TargetCreate) -> dict[str, Any]:
    tid = await _insert_target(_db(request), body.model_dump())
    _sched(request).wake()
    return await _load_target(request, tid)


@router.get("/targets/export")
async def export_targets(request: Request) -> list[dict[str, Any]]:
    """Portable definitions of every target (no runs), suitable for POST /api/targets/import."""
    rows = await _db(request).fetchall("SELECT * FROM targets ORDER BY lower(name), name")
    return [{k: v for k, v in _target_out(dict(r)).items() if k in EXPORT_FIELDS} for r in rows]


@router.post("/targets/import")
async def import_targets(request: Request, body: TargetImport) -> dict[str, Any]:
    """Create or update targets in bulk. upsert matches on name (case-insensitive); replace deletes everything first."""
    db = _db(request)
    created = updated = 0
    if body.mode == "replace":
        await _sched(request).cancel(_sched(request).active_run_ids)
        await db.execute("DELETE FROM targets")
    existing = {r["name"].lower(): int(r["id"]) for r in await db.fetchall("SELECT id, name FROM targets")} if body.mode == "upsert" else {}
    for item in body.targets:
        data = item.model_dump()
        tid = existing.get(data["name"].lower())
        if tid is not None:
            cols = [k for k in EXPORT_FIELDS if k != "name"]
            values = [json.dumps(data[k]) if k in ("options", "tags") else (int(data[k]) if k in _BOOL_FIELDS else data[k]) for k in cols]
            await db.execute(f"UPDATE targets SET {', '.join(f'{c} = ?' for c in cols)}, updated_at = ?, next_run_at = ? WHERE id = ?", [*values, time.time(), time.time(), tid])
            updated += 1
        else:
            existing[data["name"].lower()] = await _insert_target(db, data)
            created += 1
    _sched(request).wake()
    return {"created": created, "updated": updated, "total": int((await db.fetchone("SELECT COUNT(*) AS n FROM targets"))["n"])}


@router.post("/targets/bulk")
async def bulk_targets(request: Request, body: BulkAction) -> dict[str, Any]:
    db = _db(request)
    placeholders = ",".join("?" for _ in body.ids)
    rows = await db.fetchall(f"SELECT id FROM targets WHERE id IN ({placeholders})", body.ids)
    ids = [int(r["id"]) for r in rows]
    if not ids:
        raise HTTPException(404, "no matching targets")
    ph = ",".join("?" for _ in ids)
    if body.action == "delete":
        await _sched(request).cancel(ids)
        await db.execute(f"DELETE FROM targets WHERE id IN ({ph})", ids)
    elif body.action == "pause":
        await db.execute(f"UPDATE targets SET enabled = 0, updated_at = ? WHERE id IN ({ph})", [time.time(), *ids])
    elif body.action == "resume":
        await db.execute(f"UPDATE targets SET enabled = 1, last_status = 'pending', next_run_at = ?, updated_at = ? WHERE id IN ({ph})", [time.time(), time.time(), *ids])
    elif body.action in ("mute", "unmute"):
        await db.execute(f"UPDATE targets SET notify = ?, updated_at = ? WHERE id IN ({ph})", [int(body.action == "unmute"), time.time(), *ids])
    elif body.action == "run":
        for tid in ids:
            await _sched(request).run_now(tid)
    _sched(request).wake()
    return {"action": body.action, "affected": ids}


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
    patch = body.model_dump(exclude_unset=True)
    # An explicit null would land in a NOT NULL column and surface as a 500; only the port may be cleared.
    nulls = sorted(k for k, v in patch.items() if v is None and k != "port")
    if nulls:
        raise HTTPException(422, f"{', '.join(nulls)} cannot be null")
    if not patch:
        return await _load_target(request, target_id)
    kind = patch.get("type") or row["type"] or "mtr"
    if "type" in patch or "options" in patch:
        try:
            current = json.loads(row["options"] or "{}")
        except json.JSONDecodeError:
            current = {}
        merged = patch.get("options") if patch.get("options") is not None else (current if kind == (row["type"] or "mtr") else {})
        try:
            patch["options"] = json.dumps(validate_options(kind, merged))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        patch["type"] = kind
    port = patch["port"] if "port" in patch else row["port"]
    if kind == "tcp" and not port:
        raise HTTPException(422, "tcp probes need a port")
    if "tags" in patch:
        patch["tags"] = json.dumps(patch["tags"])
    for key in _BOOL_FIELDS:
        if key in patch:
            patch[key] = int(patch[key])
    patch["updated_at"] = time.time()
    # Re-run promptly when the schedule or probe definition changes.
    if any(k in patch for k in ("interval_sec", "host", "enabled", "protocol", "port", "ip_version", "type", "options")):
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
    # A run still in flight would otherwise try to write against a row that no longer exists.
    await _sched(request).cancel([target_id])
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
        f"SELECT * FROM runs WHERE {where} ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?", [*params, limit, offset]
    )
    return {"total": int(total["n"] or 0), "items": [_run_out(dict(r)) for r in rows]}


def route_change_marks(runs: list[dict[str, Any]], warm: list[str | None], memory: int) -> list[bool]:
    """Which runs (ascending) the latency chart marks as route changes.

    The stored `route_changed` flag says a run differed from the previous reached run (the events, notifications
    and dashboard counts keep that meaning). The chart is quieter: a flagged run whose route hash appeared among
    the previous `memory` reached runs is a known alternate (load balancing), not a marker. `warm` holds the
    hashes of the reached runs just before the range, newest first, so the first points of the range are judged
    like the rest. Memory 1 reproduces the stored flag.
    """
    recent: deque[str] = deque((h for h in reversed(warm) if h), maxlen=max(1, memory))
    marks: list[bool] = []
    for r in runs:
        reached = bool(r.get("status") == "ok" and r.get("reached"))
        sig = r.get("route_hash")
        marks.append(bool(r.get("route_changed")) and not (memory > 1 and sig in recent))
        if reached and sig:
            recent.append(str(sig))
    return marks


async def _route_marks(db: Database, target_id: int, since: float, settings: dict[str, Any]) -> tuple[list[dict[str, Any]], list[bool]]:
    """Runs in the range (ascending, light columns) and their chart markers, per `route_change_marks`."""
    memory = max(1, min(500, int(settings.get("route_memory_runs") or 1)))
    runs = rows_to_dicts(await db.fetchall(
        "SELECT id, started_at, status, reached, route_hash, route_changed FROM runs WHERE target_id = ? AND started_at >= ? ORDER BY started_at ASC, id ASC",
        (target_id, since),
    ))
    warm_rows = await db.fetchall(
        "SELECT route_hash FROM runs WHERE target_id = ? AND started_at < ? AND status = 'ok' AND reached = 1 ORDER BY started_at DESC, id DESC LIMIT ?",
        (target_id, since, memory),
    ) if memory > 1 else []
    return runs, route_change_marks(runs, [r["route_hash"] for r in warm_rows], memory)


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
    # Route-change markers with the chart's memory of recent routes (the stored flag stays previous-run based).
    marked_runs, marks = await _route_marks(db, target_id, since, await db.get_settings())
    marked = {r["id"] for r, m in zip(marked_runs, marks) if m}
    if n <= max_points:
        rows = await db.fetchall(
            "SELECT id, started_at, status, reached, avg_ms, best_ms, worst_ms, loss_pct, jitter_avg_ms, hop_count, route_changed "
            "FROM runs WHERE target_id = ? AND started_at >= ? ORDER BY started_at ASC, id ASC",
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
                "route_changed": r["id"] in marked,
                "n": 1,
            }
            for r in rows
        ]
        return {"range_sec": range_sec, "bucket_sec": None, "points": points}

    bucket = max(10, math.ceil(range_sec / max_points))
    marked_buckets = {int(r["started_at"] / bucket) * bucket for r in marked_runs if r["id"] in marked}
    rows = await db.fetchall(
        "SELECT floor(started_at / ?)::bigint * ? AS bucket, COUNT(*) AS n, "
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
            "route_changed": int(r["bucket"]) in marked_buckets,
            "n": int(r["n"] or 0),
        }
        for r in rows
    ]
    return {"range_sec": range_sec, "bucket_sec": bucket, "points": points}


@router.get("/targets/{target_id}/hops/history")
async def get_hop_history(
    request: Request,
    target_id: int,
    # Named range_ (not range) because the body below needs the builtin range().
    range_: str = Query(default="24h", alias="range"),
    max_runs: int = Query(default=120, ge=10, le=500),
) -> dict[str, Any]:
    db = _db(request)
    since = time.time() - parse_range(range_)
    runs = await db.fetchall(
        "SELECT id, started_at, hop_count, reached FROM runs WHERE target_id = ? AND started_at >= ? AND status = 'ok' "
        "ORDER BY started_at ASC, id ASC",
        (target_id, since),
    )
    runs = list(runs)
    if len(runs) > max_runs:
        # Evenly sample max_runs columns across the window so the heatmap stays bounded.
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
        "SELECT h.hop_no, h.ip, MAX(h.hostname COLLATE \"C\") AS hostname, MAX(h.asn COLLATE \"C\") AS asn, COUNT(*) AS n, "
        "SUM(h.sent) AS sent, SUM(h.received) AS received, AVG(h.loss_pct) AS loss_pct, MAX(h.loss_pct) AS max_loss, "
        "AVG(h.avg_ms) AS avg_ms, MIN(h.best_ms) AS best_ms, MAX(h.worst_ms) AS worst_ms, AVG(h.stdev_ms) AS stdev_ms, "
        "AVG(h.jitter_avg_ms) AS jitter_ms, MAX(h.jitter_max_ms) AS jitter_max_ms "
        "FROM hops h JOIN runs r ON r.id = h.run_id WHERE r.target_id = ? AND r.started_at >= ? AND r.status = 'ok' "
        "GROUP BY h.hop_no, h.ip ORDER BY h.hop_no ASC, n DESC, h.ip",
        (target_id, since),
    )
    total_runs_row = await db.fetchone(
        "SELECT COUNT(*) AS n FROM runs WHERE target_id = ? AND started_at >= ? AND status = 'ok'", (target_id, since)
    )
    total_runs = int(total_runs_row["n"] or 0)
    hops: dict[int, dict[str, Any]] = {}
    silent: dict[int, dict[str, int]] = {}
    for r in rows:
        hop_no = int(r["hop_no"])
        sent = int(r["sent"] or 0)
        received = int(r["received"] or 0)
        if r["ip"] is None:
            # Runs in which this hop answered nothing (rate-limited or deprioritised ICMP, usually) are loss at
            # this position, not a second address; they are folded into the primary entry below.
            silent[hop_no] = {"runs": int(r["n"]), "sent": sent, "received": received}
            continue
        entry = {
            "ip": r["ip"],
            "hostname": r["hostname"],
            "asn": r["asn"],
            "runs": int(r["n"]),
            "share_pct": round(100.0 * int(r["n"]) / total_runs, 1) if total_runs else None,
            "sent": sent,
            "received": received,
            "loss_pct": round(100.0 * (sent - received) / sent, 2) if sent else 100.0,
            "max_loss_pct": r["max_loss"],
            "avg_ms": round(r["avg_ms"], 3) if r["avg_ms"] is not None else None,
            "best_ms": r["best_ms"],
            "worst_ms": r["worst_ms"],
            "stdev_ms": round(r["stdev_ms"], 3) if r["stdev_ms"] is not None else None,
            "jitter_ms": round(r["jitter_ms"], 3) if r["jitter_ms"] is not None else None,
            "jitter_max_ms": r["jitter_max_ms"],
        }
        slot = hops.setdefault(hop_no, {"hop": hop_no, "primary": None, "alternates": [], "silent_runs": 0})
        if slot["primary"] is None:
            slot["primary"] = entry
        else:
            slot["alternates"].append(entry)
    for hop_no, s in silent.items():
        slot = hops.get(hop_no)
        if slot is None:
            # Never answered from any address in this range: the only thing to show is the silence itself.
            hops[hop_no] = {
                "hop": hop_no,
                "primary": {
                    "ip": None, "hostname": None, "asn": None, "runs": s["runs"], "share_pct": round(100.0 * s["runs"] / total_runs, 1) if total_runs else None,
                    "sent": s["sent"], "received": s["received"], "loss_pct": 100.0, "max_loss_pct": 100.0, "avg_ms": None, "best_ms": None, "worst_ms": None,
                    "stdev_ms": None, "jitter_ms": None, "jitter_max_ms": None,
                },
                "alternates": [],
                "silent_runs": s["runs"],
            }
            continue
        primary = slot["primary"]
        sent = primary["sent"] + s["sent"]
        received = primary["received"] + s["received"]
        primary["sent"], primary["received"] = sent, received
        primary["loss_pct"] = round(100.0 * (sent - received) / sent, 2) if sent else 100.0
        primary["max_loss_pct"] = 100.0
        slot["silent_runs"] = s["runs"]
    # The organisation behind each address, as the map shows it (ip-api.com or the GeoLite2 ASN database).
    await geoip.name_networks([e for slot in hops.values() for e in (slot["primary"], *slot["alternates"])], await db.get_settings())
    return {"total_runs": total_runs, "hops": [hops[k] for k in sorted(hops)]}


@router.get("/overview/series")
async def overview_series(
    request: Request, range: str = Query(default="24h"), max_points: int = Query(default=144, ge=24, le=1000)  # noqa: A002
) -> dict[str, Any]:
    """Bucketed destination latency and loss for every enabled target, for side-by-side comparison."""
    db = _db(request)
    range_sec = parse_range(range)
    since = time.time() - range_sec
    bucket = max(10, math.ceil(range_sec / max_points))
    targets = await db.fetchall("SELECT id, name, host, enabled FROM targets ORDER BY lower(name), name")
    rows = await db.fetchall(
        "SELECT target_id, floor(started_at / ?)::bigint * ? AS bucket, COUNT(*) AS n, "
        "SUM(CASE WHEN status='ok' AND reached=1 THEN 1 ELSE 0 END) AS ok_n, "
        "AVG(CASE WHEN reached=1 THEN avg_ms END) AS avg_ms, MAX(loss_pct) AS max_loss "
        "FROM runs WHERE started_at >= ? GROUP BY target_id, bucket ORDER BY bucket ASC",
        (bucket, bucket, since),
    )
    by_target: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by_target.setdefault(int(r["target_id"]), []).append(
            {
                "t": _iso(float(r["bucket"])),
                "avg": round(r["avg_ms"], 2) if r["avg_ms"] is not None else None,
                "loss": r["max_loss"],
                "ok": int(r["ok_n"] or 0) == int(r["n"] or 0),
                "n": int(r["n"] or 0),
            }
        )
    return {
        "range_sec": range_sec,
        "bucket_sec": bucket,
        "targets": [
            {"id": int(t["id"]), "name": t["name"], "host": t["host"], "enabled": bool(t["enabled"]), "points": by_target.get(int(t["id"]), [])}
            for t in targets
            if int(t["id"]) in by_target
        ],
    }


@router.get("/targets/{target_id}/hourly")
async def get_hourly(request: Request, target_id: int, range: str = Query(default="7d")) -> dict[str, Any]:  # noqa: A002
    """Hour buckets for the day-by-hour heatmap. The client folds them into its local timezone."""
    db = _db(request)
    range_sec = parse_range(range)
    since = time.time() - range_sec
    rows = await db.fetchall(
        "SELECT floor(started_at / 3600)::bigint * 3600 AS hour, COUNT(*) AS n, "
        "SUM(CASE WHEN status='ok' AND reached=1 THEN 1 ELSE 0 END) AS ok_n, "
        "AVG(CASE WHEN reached=1 THEN avg_ms END) AS avg_ms, MAX(worst_ms) AS worst_ms, AVG(loss_pct) AS loss_pct, MAX(loss_pct) AS max_loss, "
        "AVG(jitter_avg_ms) AS jitter "
        "FROM runs WHERE target_id = ? AND started_at >= ? GROUP BY hour ORDER BY hour ASC",
        (target_id, since),
    )
    return {
        "range_sec": range_sec,
        "hours": [
            {
                "t": _iso(float(r["hour"])),
                "n": int(r["n"] or 0),
                "ok_n": int(r["ok_n"] or 0),
                "avg": round(r["avg_ms"], 2) if r["avg_ms"] is not None else None,
                "worst": r["worst_ms"],
                "loss": round(r["loss_pct"], 2) if r["loss_pct"] is not None else None,
                "max_loss": r["max_loss"],
                "jitter": round(r["jitter"], 2) if r["jitter"] is not None else None,
            }
            for r in rows
        ],
    }


@router.get("/targets/{target_id}/routes")
async def get_routes(request: Request, target_id: int, range: str = Query(default="24h")) -> dict[str, Any]:  # noqa: A002
    """Contiguous segments of equivalent routes over time, plus a per-route share summary.

    Runs are grouped the way the route change detector judges them: a hop that answered nothing (`???`) is a
    wildcard, so a run with one silent hop belongs to the route it otherwise matches instead of becoming a
    "distinct path" of its own. Only one example run per stored route hash is read for its hop sequence.
    """
    db = _db(request)
    range_sec = parse_range(range)
    since = time.time() - range_sec
    rows = await db.fetchall(
        "SELECT id, started_at, finished_at, route_hash, hop_count, reached FROM runs WHERE target_id = ? AND started_at >= ? AND status = 'ok' "
        "ORDER BY started_at ASC, id ASC",
        (target_id, since),
    )
    canonical = await _canonical_routes(db, rows)
    segments: list[dict[str, Any]] = []
    totals: dict[str, dict[str, Any]] = {}
    for r in rows:
        raw = r["route_hash"] or "unknown"
        h = canonical.get(raw, raw)
        end = float(r["finished_at"] or r["started_at"])
        if segments and segments[-1]["hash"] == h:
            seg = segments[-1]
            seg["end"] = end
            seg["runs"] += 1
        else:
            segments.append({"hash": h, "start": float(r["started_at"]), "end": end, "runs": 1, "first_run_id": int(r["id"]), "hops": int(r["hop_count"])})
        tot = totals.setdefault(h, {"hash": h, "runs": 0, "hops": int(r["hop_count"]), "first_seen": float(r["started_at"]), "last_seen": end, "reached": 0, "example_run_id": int(r["id"]), "variants": set()})
        tot["runs"] += 1
        tot["last_seen"] = end
        tot["reached"] += int(r["reached"])
        tot["variants"].add(raw)
        if raw == h and not tot.get("_full_example"):
            # Prefer a run whose hops all answered as the example to open, not one with a silent hop.
            tot["example_run_id"] = int(r["id"])
            tot["_full_example"] = True
    for tot in totals.values():
        tot["variants"] = len(tot["variants"])
        tot.pop("_full_example", None)
    n = len(rows)
    for seg in segments:
        seg["start"] = _iso(seg["start"])
        seg["end"] = _iso(seg["end"])
    routes = sorted(totals.values(), key=lambda x: -x["runs"])
    for i, rt in enumerate(routes):
        rt["index"] = i
        rt["share_pct"] = round(100.0 * rt["runs"] / n, 1) if n else 0.0
        rt["first_seen"] = _iso(rt["first_seen"])
        rt["last_seen"] = _iso(rt["last_seen"])
    index = {rt["hash"]: rt["index"] for rt in routes}
    for seg in segments:
        seg["index"] = index.get(seg["hash"], 0)
    return {"range_sec": range_sec, "since": _iso(since), "total_runs": n, "segments": segments, "routes": routes}


async def _canonical_routes(db: Database, runs: list[Any]) -> dict[str, str]:
    """Map every stored route hash to the hash of the route it is wildcard-equivalent to.

    Distinct hashes are processed from the most frequent down, so the fullest observation of a path becomes
    the canonical one and a hash with silent hops attaches to it. Known addresses seen in a variant fill the
    wildcards of the canonical sequence, so `A,*,C` and `A,B,*` both end up as `A,B,C`.
    """
    counts: dict[str, int] = {}
    example: dict[str, int] = {}
    order: dict[str, float] = {}
    for r in runs:
        h = r["route_hash"]
        if not h:
            continue
        counts[h] = counts.get(h, 0) + 1
        example.setdefault(h, int(r["id"]))
        order.setdefault(h, float(r["started_at"]))
    if len(counts) < 2:
        return {}
    ids = list(example.values())
    placeholders = ",".join("?" for _ in ids)
    hop_rows = await db.fetchall(f"SELECT run_id, hop_no, ip FROM hops WHERE run_id IN ({placeholders}) ORDER BY run_id, hop_no", ids)
    by_run: dict[int, list[str | None]] = {}
    for h in hop_rows:
        by_run.setdefault(int(h["run_id"]), []).append(h["ip"])
    sequences = {h: by_run.get(rid, []) for h, rid in example.items()}
    canonical: list[tuple[str, list[str | None]]] = []
    mapping: dict[str, str] = {}
    for h in sorted(counts, key=lambda x: (-counts[x], order[x])):
        seq = sequences[h]
        for canon_hash, merged in canonical:
            if seq and routes_equivalent(merged, seq):
                mapping[h] = canon_hash
                for i, ip in enumerate(seq):
                    if merged[i] is None and ip is not None:
                        merged[i] = ip
                break
        else:
            canonical.append((h, list(seq)))
            mapping[h] = h
    return mapping


@router.get("/targets/{target_id}/geo")
async def get_target_geo(request: Request, target_id: int) -> dict[str, Any]:
    """Locations and networks of the monitor (or remote probes), every hop and the destination of the latest completed run.

    `enabled` is false until ip-api.com is switched on or a MaxMind licence key is saved; the UI then hides
    the map. Hops without a location carry a `note` (private address, not in database, no database yet,
    ip-api.com unavailable, lookup pending). Every point carries `asn` and `as_name` from ip-api.com or the
    GeoLite2 ASN database (`asn_available` says whether either can answer); every `geo` names its `provider`.
    """
    db = _db(request)
    row = await db.fetchone("SELECT * FROM targets WHERE id = ?", (target_id,))
    if row is None:
        raise HTTPException(404, "target not found")
    target = _target_out(dict(row))
    settings = await db.get_settings()
    run_row = await db.fetchone("SELECT * FROM runs WHERE target_id = ? AND status = 'ok' ORDER BY started_at DESC, id DESC LIMIT 1", (target_id,))
    run = _run_out(dict(run_row)) if run_row else None
    hops = rows_to_dicts(await db.fetchall("SELECT hop_no, ip, hostname, asn, avg_ms, loss_pct FROM hops WHERE run_id = ? ORDER BY hop_no ASC", (run["id"],))) if run else []
    return await geoip.path_geo(target, run, hops, settings)


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
        "SELECT r.*, t.name AS target_name, t.host AS target_host, t.type AS target_type FROM runs r JOIN targets t ON t.id = r.target_id WHERE r.id = ?",
        (run_id,),
    )
    if row is None:
        raise HTTPException(404, "run not found")
    run = _run_out(dict(row))
    hops = await db.fetchall("SELECT * FROM hops WHERE run_id = ? ORDER BY hop_no ASC", (run_id,))
    run["hops"] = rows_to_dicts(hops)
    await geoip.name_networks(run["hops"], await db.get_settings())
    prev = await db.fetchone(
        "SELECT id FROM runs WHERE target_id = ? AND started_at < ? ORDER BY started_at DESC, id DESC LIMIT 1",
        (run["target_id"], row["started_at"]),
    )
    nxt = await db.fetchone(
        "SELECT id FROM runs WHERE target_id = ? AND started_at > ? ORDER BY started_at ASC, id ASC LIMIT 1",
        (run["target_id"], row["started_at"]),
    )
    run["prev_run_id"] = prev["id"] if prev else None
    run["next_run_id"] = nxt["id"] if nxt else None
    return run


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: int) -> dict[str, Any]:
    """A run with its hops; each hop carries `as_name` (the organisation behind `asn`) from the GeoIP provider."""
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
    try:
        await asyncio.wait_for(_adhoc_slots.acquire(), timeout=ADHOC_PROBE_WAIT)
    except asyncio.TimeoutError as exc:
        raise HTTPException(429, f"{ADHOC_PROBE_SLOTS} quick traces are already running; try again in a moment") from exc
    try:
        result = await run_mtr(
            dst_ip=dst_ip,
            count=body.count,
            # Half a second between probes keeps a trace short; non-root mtr only allows whole seconds.
            probe_interval=max(0.5, min_probe_interval()),
            protocol=body.protocol,
            port=body.port,
            packet_size=64,
            max_hops=body.max_hops,
            ip_version=body.ip_version,
            asn_lookup=bool(settings.get("asn_lookup", True)),
        )
    finally:
        _adhoc_slots.release()
    if not result.ok:
        raise HTTPException(502, result.error or "mtr failed")
    if settings.get("reverse_dns", True):
        names = await reverse_lookup_many([h.ip for h in result.hops])
        for h in result.hops:
            h.hostname = names.get(h.ip or "")
    final = result.hops[-1]
    hops = [dict(h.__dict__) for h in result.hops]
    await geoip.name_networks(hops, settings)
    return {
        "host": body.host,
        "dst_ip": dst_ip,
        "src": result.src,
        "reached": final.ip == dst_ip and final.received > 0,
        "duration_ms": round(result.duration_ms, 1),
        "command": result.command,
        "hops": hops,
    }
