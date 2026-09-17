"""Globalping probe type: ping, traceroute, mtr, dns and http measurements from remote probes via the globalping.io API.

A measurement is created with POST /v1/measurements and polled until every probe has finished.

- ping, dns and http become one summary row (a ProbeOutcome, like the local probe types) with one entry per
  probe in `details.probes`. Ping aggregates packets; dns and http are checks: the run is down when no probe
  passed and degraded (a warning) when only some did.
- traceroute and mtr are turned into HopResults, so the scheduler stores them like a local mtr run and every
  path visual works for the remote vantage point.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import config
from .mtr import HopResult, MtrResult
from .mtr import _simulate as simulate_path
from .probes import ProbeOutcome, _apply_stats, status_allowed
from .resolver import is_ip

API_BASE = "https://api.globalping.io/v1"
MEASUREMENT_URL = "https://globalping.io?measurement="
USER_AGENT = "MTR-Tracker/1.0 (+https://github.com/smf13/MTR-test)"
MEASUREMENT_TIMEOUT = 90.0  # seconds to wait for every probe to report
POLL_INTERVAL = 1.0
MAX_PACKETS = 16  # API limit for ping/mtr packets

PATH_MEASUREMENTS = ("mtr", "traceroute")  # stored as hops
PACKET_MEASUREMENTS = ("ping", "mtr", "traceroute")  # report packet loss
CHECK_MEASUREMENTS = ("dns", "http")  # pass/fail per probe

# Tests override these: force live mode and inject an httpx.MockTransport.
_FORCE_LIVE = False
_TRANSPORT: httpx.BaseTransport | None = None


class GlobalpingError(Exception):
    """The measurement could not be run (validation, no probes, rate limit, timeout, network)."""


def _sim() -> bool:
    return config.simulate and not _FORCE_LIVE


def probe_label(probe: dict[str, Any]) -> str:
    """'Frankfurt, DE · AS24940 Hetzner Online' for a Globalping probe description."""
    where = ", ".join(str(x) for x in (probe.get("city"), probe.get("country")) if x)
    net = " ".join(x for x in (f"AS{probe['asn']}" if probe.get("asn") else "", str(probe.get("network") or "")) if x)
    return " · ".join(x for x in (where, net) if x) or "unknown probe"


def _probe_fields(probe: dict[str, Any]) -> dict[str, Any]:
    return {"label": probe_label(probe), **{k: probe.get(k) for k in ("continent", "country", "city", "asn", "network")}}


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


def http_target(host: str, path: str) -> tuple[str, str]:
    """A pasted URL is split into its host and path; a plain host keeps the configured path."""
    host = host.strip()
    if "://" in host:
        parts = urlsplit(host)
        url_path = parts.path + (f"?{parts.query}" if parts.query else "")
        return parts.hostname or host, (url_path if path in ("", "/") and url_path else path)
    return host, path or "/"


def build_request(t: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    """The POST /v1/measurements body for a target: type, target, probe selection and measurement options."""
    measurement = str(opts.get("measurement") or "ping")
    location = str(opts.get("location") or "world").strip()
    host = str(t.get("host") or "").strip()
    path = str(opts.get("path") or "/")
    if measurement == "http":
        host, path = http_target(host, path)
    limit = 1 if measurement in PATH_MEASUREMENTS else max(1, min(10, int(opts.get("probes") or 1)))
    body: dict[str, Any] = {"type": measurement, "target": host, "limit": limit, "inProgressUpdates": False}
    if location and location.lower() != "world":
        body["locations"] = [{"magic": location}]
    options: dict[str, Any] = {}
    ip_version = str(t.get("ip_version") or "auto")
    if ip_version in ("4", "6") and not is_ip(host):
        options["ipVersion"] = int(ip_version)
    if measurement in ("ping", "mtr"):
        options["packets"] = max(1, min(MAX_PACKETS, int(t.get("count") or 4)))
    if measurement in PATH_MEASUREMENTS:
        protocol = str(t.get("protocol") or "icmp").upper()
        options["protocol"] = protocol
        if protocol in ("TCP", "UDP") and t.get("port"):
            options["port"] = int(t["port"])
    elif measurement == "dns":
        options["query"] = {"type": str(opts.get("record_type") or "A").upper()}
        resolver = str(opts.get("resolver") or "").strip()
        if resolver:
            options["resolver"] = resolver
    elif measurement == "http":
        options["request"] = {"path": path, "method": str(opts.get("http_method") or "GET").upper()}
        options["protocol"] = str(opts.get("http_protocol") or "HTTPS").upper()
        if t.get("port"):
            options["port"] = int(t["port"])
    body["measurementOptions"] = options
    return body


def describe(body: dict[str, Any], location: str) -> str:
    """One-line command-style description stored with the run."""
    kind = body["type"]
    o = body.get("measurementOptions") or {}
    n = body.get("limit", 1)
    probes = f"{n} probe{'s' if n != 1 else ''}"
    if kind == "ping":
        return f"globalping ping {body['target']} from {location} ({probes} × {o.get('packets')} packets)"
    if kind == "mtr":
        return f"globalping mtr {body['target']} from {location} ({o.get('packets')} packets, {o.get('protocol', 'ICMP')})"
    if kind == "traceroute":
        return f"globalping traceroute {body['target']} from {location} ({o.get('protocol', 'ICMP')})"
    if kind == "dns":
        return f"globalping dns {o.get('query', {}).get('type', 'A')} {body['target']} from {location}" + (f" @{o['resolver']}" if o.get("resolver") else "") + f" ({probes})"
    req = o.get("request") or {}
    port = f":{o['port']}" if o.get("port") else ""
    return f"globalping http {req.get('method', 'GET')} {str(o.get('protocol', 'HTTPS')).lower()}://{body['target']}{port}{req.get('path', '/')} from {location} ({probes})"


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


def _client(settings: dict[str, Any]) -> httpx.AsyncClient:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    token = str(settings.get("globalping_token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(base_url=API_BASE, timeout=15.0, headers=headers, transport=_TRANSPORT)


def _error_message(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error") or {}
        text = err.get("message") or ""
        kind = err.get("type") or ""
        if text:
            return f"Globalping: {text}" + (f" ({kind})" if kind else "")
    except ValueError:
        pass
    return f"Globalping returned HTTP {resp.status_code}: {resp.text[:200]}"


async def measure(body: dict[str, Any], settings: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Create a measurement and poll until it is finished. Returns (measurement, rate-limit headers)."""
    async with _client(settings) as client:
        try:
            resp = await client.post("/measurements", json=body)
        except httpx.HTTPError as exc:
            raise GlobalpingError(f"Globalping request failed: {exc}") from exc
        limits = {k: resp.headers[f"x-ratelimit-{k}"] for k in ("limit", "remaining", "reset") if f"x-ratelimit-{k}" in resp.headers}
        if resp.status_code == 429:
            reset = limits.get("reset")
            raise GlobalpingError("Globalping rate limit reached" + (f"; it resets in {reset} s" if reset else "") + ". Add an API token under Settings or lengthen the interval.")
        if resp.status_code >= 400:
            raise GlobalpingError(_error_message(resp))
        try:
            mid = str(resp.json().get("id") or "")
        except ValueError:
            mid = ""
        if not mid:
            raise GlobalpingError("Globalping returned no measurement id")
        deadline = time.monotonic() + MEASUREMENT_TIMEOUT
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            try:
                poll = await client.get(f"/measurements/{mid}")
            except httpx.HTTPError as exc:
                raise GlobalpingError(f"Globalping poll failed: {exc}") from exc
            if poll.status_code >= 400:
                raise GlobalpingError(_error_message(poll))
            data = poll.json()
            if data.get("status") == "finished":
                return data, limits
            if time.monotonic() > deadline:
                raise GlobalpingError(f"Globalping measurement {mid} did not finish within {MEASUREMENT_TIMEOUT:.0f} s")


def _common_details(measurement: str, location: str, data: dict[str, Any] | None, limits: dict[str, str]) -> dict[str, Any]:
    details: dict[str, Any] = {"measurement": measurement, "location": location}
    if data and data.get("id"):
        details["measurement_id"] = data["id"]
        details["url"] = MEASUREMENT_URL + str(data["id"])
    if limits:
        details["rate_limit"] = limits
    return details


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _rtts(result: dict[str, Any]) -> list[float]:
    return [float(x["rtt"]) for x in result.get("timings") or [] if isinstance(x, dict) and x.get("rtt") is not None]


def parse_ping_probes(data: dict[str, Any]) -> list[dict[str, Any]]:
    """One entry per probe with its location and ping statistics."""
    out: list[dict[str, Any]] = []
    for item in data.get("results") or []:
        probe = item.get("probe") or {}
        res = item.get("result") or {}
        stats = res.get("stats") or {}
        out.append(
            {
                **_probe_fields(probe),
                "status": res.get("status"),
                "resolved": res.get("resolvedAddress"),
                "sent": stats.get("total"),
                "received": stats.get("rcv"),
                "loss": stats.get("loss"),
                "min": stats.get("min"),
                "avg": stats.get("avg"),
                "max": stats.get("max"),
                "rtts": _rtts(res),
            }
        )
    return out


def hop_from_result(index: int, hop: dict[str, Any]) -> HopResult:
    """A Globalping mtr hop (stats + timings + resolved address) as the HopResult the scheduler stores."""
    stats = hop.get("stats") or {}
    ip = hop.get("resolvedAddress") or None
    if ip in ("*", ""):
        ip = None
    hostname = hop.get("resolvedHostname") or None
    if not ip or hostname in ("*", "", ip):
        hostname = None
    asn_list = hop.get("asn") or []
    asn = f"AS{asn_list[0]}" if asn_list else None
    sent = int(stats.get("total") or 0)
    received = int(stats.get("rcv") or 0)
    loss = float(stats["loss"]) if stats.get("loss") is not None else (100.0 * (sent - received) / sent if sent else 100.0)
    rtts = _rtts(hop)
    answered = received > 0 and ip is not None

    def metric(key: str) -> float | None:
        v = stats.get(key)
        return float(v) if (v is not None and answered) else None

    return HopResult(
        hop_no=index + 1,
        ip=ip,
        asn=asn,
        loss_pct=loss,
        sent=sent,
        received=received,
        last_ms=rtts[-1] if rtts and answered else None,
        avg_ms=metric("avg"),
        best_ms=metric("min"),
        worst_ms=metric("max"),
        stdev_ms=metric("stDev"),
        gmean_ms=None,
        jitter_ms=None,
        jitter_avg_ms=metric("jAvg"),
        jitter_max_ms=metric("jMax"),
        jitter_int_ms=None,
        hostname=hostname,
    )


def hop_from_traceroute(index: int, hop: dict[str, Any], sent: int) -> HopResult:
    """A Globalping traceroute hop has only timings (one per reply); `sent` is the packets per hop the probe used."""
    ip = hop.get("resolvedAddress") or None
    if ip in ("*", ""):
        ip = None
    hostname = hop.get("resolvedHostname") or None
    if not ip or hostname in ("*", "", ip):
        hostname = None
    rtts = _rtts(hop) if ip else []
    received = len(rtts)
    sent = max(sent, received, 1)
    avg = sum(rtts) / received if rtts else None
    stdev = (sum((r - avg) ** 2 for r in rtts) / received) ** 0.5 if rtts and avg is not None else None
    return HopResult(
        hop_no=index + 1,
        ip=ip,
        asn=None,
        loss_pct=round(100.0 * (sent - received) / sent, 1),
        sent=sent,
        received=received,
        last_ms=rtts[-1] if rtts else None,
        avg_ms=round(avg, 3) if avg is not None else None,
        best_ms=min(rtts) if rtts else None,
        worst_ms=max(rtts) if rtts else None,
        stdev_ms=round(stdev, 3) if stdev is not None else None,
        gmean_ms=None,
        jitter_ms=None,
        jitter_avg_ms=None,
        jitter_max_ms=None,
        jitter_int_ms=None,
        hostname=hostname,
    )


def parse_dns_probes(data: dict[str, Any], expected: str = "") -> list[dict[str, Any]]:
    """One entry per probe: response code, answers, lookup time and whether the check passed."""
    out: list[dict[str, Any]] = []
    for item in data.get("results") or []:
        probe = item.get("probe") or {}
        res = item.get("result") or {}
        status = res.get("status")
        answers = [
            {"name": a.get("name"), "type": a.get("type"), "ttl": a.get("ttl"), "value": a.get("value")}
            for a in res.get("answers") or []
            if isinstance(a, dict)
        ]
        values = [str(a["value"]) for a in answers if a.get("value") is not None]
        rcode = res.get("statusCodeName") or ("NOERROR" if res.get("statusCode") == 0 else None)
        total = (res.get("timings") or {}).get("total")
        reason: str | None = None
        if status != "finished":
            reason = f"probe reported {status or 'no result'}"
        elif rcode != "NOERROR":
            reason = rcode or "no answer"
        elif expected and not any(expected.lower() in v.lower() for v in values):
            reason = f"expected '{expected}' not in answers"
        out.append(
            {
                **_probe_fields(probe),
                "status": status,
                "rcode": rcode,
                "resolver": res.get("resolver"),
                "total_ms": float(total) if total is not None else None,
                "answers": answers,
                "passed": reason is None,
                "reason": reason,
            }
        )
    return out


def _iso(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def tls_info_from_result(tls: dict[str, Any] | None) -> dict[str, Any] | None:
    """Globalping's tls block in the same shape as the local HTTP probe's certificate details (`details.tls`)."""
    if not tls:
        return None
    issuer = tls.get("issuer") or {}
    subject = tls.get("subject") or {}
    alt = subject.get("alt")
    if isinstance(alt, str):
        san = [a.strip().removeprefix("DNS:") for a in alt.split(",") if a.strip()]
    elif isinstance(alt, list):
        san = [str(a).strip().removeprefix("DNS:") for a in alt]
    else:
        san = []
    created = _iso(tls.get("createdAt"))
    expires = _iso(tls.get("expiresAt"))
    return {
        "subject": subject.get("CN"),
        "subject_org": subject.get("O"),
        "issuer": issuer.get("O") or issuer.get("CN"),
        "issuer_cn": issuer.get("CN"),
        "not_before": created.isoformat().replace("+00:00", "Z") if created else None,
        "not_after": expires.isoformat().replace("+00:00", "Z") if expires else None,
        "days_left": int((expires - datetime.now(timezone.utc)).total_seconds() // 86400) if expires else None,
        "san": san[:25],
        "serial": tls.get("serialNumber"),
        "protocol": tls.get("protocol"),
        "cipher": tls.get("cipherName"),
        "authorized": tls.get("authorized"),
    }


def parse_http_probes(data: dict[str, Any], expected_status: str = "200-299", keyword: str = "") -> list[dict[str, Any]]:
    """One entry per probe: status, timing breakdown, certificate and whether the check passed."""
    out: list[dict[str, Any]] = []
    for item in data.get("results") or []:
        probe = item.get("probe") or {}
        res = item.get("result") or {}
        status = res.get("status")
        timings = res.get("timings") or {}
        headers = {str(k).lower(): v for k, v in (res.get("headers") or {}).items()}
        code = res.get("statusCode")
        body = str(res.get("rawBody") or "")
        reason: str | None = None
        if status != "finished":
            raw = str(res.get("rawOutput") or "").strip().splitlines()
            reason = f"probe reported {status or 'no result'}" + (f": {raw[-1][:120]}" if raw else "")
        elif code is None:
            reason = "no HTTP response"
        elif not status_allowed(int(code), expected_status):
            reason = f"HTTP {code} not in expected {expected_status or '200-299'}"
        elif keyword and keyword.lower() not in body.lower():
            reason = f"keyword '{keyword}' not found"
        out.append(
            {
                **_probe_fields(probe),
                "status": status,
                "status_code": code,
                "status_name": res.get("statusCodeName"),
                "resolved": res.get("resolvedAddress"),
                "total_ms": float(timings["total"]) if timings.get("total") is not None else None,
                "timings": {k: timings.get(k) for k in ("dns", "tcp", "tls", "firstByte", "download")},
                "server": headers.get("server"),
                "content_type": headers.get("content-type"),
                "truncated": bool(res.get("truncated")),
                "tls": tls_info_from_result(res.get("tls")),
                "passed": reason is None,
                "reason": reason,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Summary measurements: ping, dns, http
# ---------------------------------------------------------------------------


def _check_outcome(started: float, finished: float, probes: list[dict[str, Any]], details: dict[str, Any], command: str) -> ProbeOutcome:
    """Fold per-probe pass/fail results into one run: down when nobody passed, degraded when only some did."""
    passed = [p for p in probes if p.get("passed")]
    failed = [p for p in probes if not p.get("passed")]
    n = len(probes)
    reasons = "; ".join(f"{p['label']}: {p.get('reason') or 'failed'}" for p in failed)
    o = ProbeOutcome(True, bool(passed), started, finished, dst_ip=next((p.get("resolved") for p in passed if p.get("resolved")), None), sent=n or None, details=details, command=command)
    o.loss_pct = round(100.0 * len(failed) / n, 1) if n else 100.0
    _apply_stats(o, [float(p["total_ms"]) for p in passed if p.get("total_ms") is not None])
    if not passed:
        o.error = reasons or "no probe reported a result"
    elif failed:
        o.warnings = [f"{len(failed)} of {n} probes failed ({reasons})"]
    return o


def _ping_outcome(started: float, finished: float, probes: list[dict[str, Any]], details: dict[str, Any], command: str) -> ProbeOutcome:
    finished_probes = [p for p in probes if p.get("status") == "finished" and p.get("sent")]
    if not finished_probes:
        why = "; ".join(f"{p['label']}: {p.get('status') or 'no result'}" for p in probes) or "no probes reported"
        return ProbeOutcome(True, False, started, finished, error=f"no probe completed the ping ({why})", sent=0, loss_pct=100.0, details=details, command=command)
    sent = sum(int(p["sent"]) for p in finished_probes)
    received = sum(int(p.get("received") or 0) for p in finished_probes)
    o = ProbeOutcome(True, received > 0, started, finished, dst_ip=next((p["resolved"] for p in finished_probes if p.get("resolved")), None), sent=sent, details=details, command=command)
    o.loss_pct = round(100.0 * (sent - received) / sent, 1) if sent else 100.0
    _apply_stats(o, [r for p in finished_probes for r in p["rtts"]])
    if received == 0:
        o.error = "no probe received a reply"
    return o


async def run_globalping(t: dict[str, Any], opts: dict[str, Any], settings: dict[str, Any]) -> ProbeOutcome:
    """ping, dns or http from remote probes as one summary row (per-probe details kept in `details.probes`)."""
    started = time.time()
    measurement = str(opts.get("measurement") or "ping")
    location = str(opts.get("location") or "world").strip() or "world"
    if measurement in PATH_MEASUREMENTS:
        # Path measurements go through the scheduler's path pipeline (run_globalping_path), not here.
        return ProbeOutcome(False, False, started, time.time(), error=f"globalping measurement '{measurement}' is stored as a path, not a summary", loss_pct=100.0)
    body = build_request(t, opts)
    command = describe(body, location)

    if _sim():
        return _simulate_summary(started, measurement, body, opts, location, command)

    try:
        data, limits = await measure(body, settings)
    except GlobalpingError as exc:
        return ProbeOutcome(False, False, started, time.time(), error=str(exc), loss_pct=100.0, details=_common_details(measurement, location, None, {}), command=command)
    finished = time.time()
    details = _common_details(measurement, location, data, limits)

    if measurement == "ping":
        details["probes"] = parse_ping_probes(data)
        return _ping_outcome(started, finished, details["probes"], details, command)
    if measurement == "dns":
        probes = parse_dns_probes(data, str(opts.get("expected") or "").strip())
        details.update({"probes": probes, "record_type": body["measurementOptions"]["query"]["type"], "resolver": body["measurementOptions"].get("resolver") or "probe default"})
        first = next((p for p in probes if p["passed"]), probes[0] if probes else None)
        details["answers"] = [str(a["value"]) for a in first["answers"] if a.get("value") is not None] if first else []
        details["rcode"] = first["rcode"] if first else None
        return _check_outcome(started, finished, probes, details, command)
    probes = parse_http_probes(data, str(opts.get("expected_status") or "200-299"), str(opts.get("keyword") or "").strip())
    o = body["measurementOptions"]
    details.update({"probes": probes, "request": f"{o['request']['method']} {str(o['protocol']).lower()}://{body['target']}{o['request']['path']}"})
    first = next((p for p in probes if p["passed"]), probes[0] if probes else None)
    if first:
        details.update({"status": first["status_code"], "status_name": first["status_name"], "timings": first["timings"], "tls": first["tls"]})
        if first["tls"]:
            details["tls_expires_in_days"] = first["tls"].get("days_left")
    return _check_outcome(started, finished, probes, details, command)


# ---------------------------------------------------------------------------
# Path measurements: mtr, traceroute
# ---------------------------------------------------------------------------


async def run_globalping_path(t: dict[str, Any], opts: dict[str, Any], settings: dict[str, Any]) -> MtrResult:
    """mtr or traceroute from one remote probe, returned like a local run so the scheduler stores hops and judges the route."""
    started = time.time()
    measurement = str(opts.get("measurement") or "mtr")
    location = str(opts.get("location") or "world").strip() or "world"
    body = build_request(t, opts)
    command = describe(body, location)

    if _sim():
        return await _simulate_path(t, body, location, command)

    try:
        data, limits = await measure(body, settings)
    except GlobalpingError as exc:
        return MtrResult(False, started, time.time(), command, error=str(exc), details=_common_details(measurement, location, None, {}))
    finished = time.time()
    details = _common_details(measurement, location, data, limits)
    items = data.get("results") or []
    if not items:
        return MtrResult(False, started, finished, command, error="Globalping returned no results", details=details)
    probe = items[0].get("probe") or {}
    res = items[0].get("result") or {}
    label = probe_label(probe)
    details["probe"] = _probe_fields(probe)
    if res.get("status") != "finished":
        raw = str(res.get("rawOutput") or "").strip().splitlines()
        return MtrResult(False, started, finished, command, src=label, error=f"probe {label} reported {res.get('status') or 'no result'}" + (f": {raw[-1][:120]}" if raw else ""), details=details)
    raw_hops = res.get("hops") or []
    if measurement == "traceroute":
        per_hop = max((len(h.get("timings") or []) for h in raw_hops), default=0) or 3
        hops = [hop_from_traceroute(i, h, per_hop) for i, h in enumerate(raw_hops)]
    else:
        hops = [hop_from_result(i, h) for i, h in enumerate(raw_hops)]
    dst_ip = res.get("resolvedAddress") or None
    if not hops:
        return MtrResult(False, started, finished, command, src=label, dst_ip=dst_ip, error="Globalping result contains no hops", details=details)
    return MtrResult(True, started, finished, command, src=label, dst_ip=dst_ip, hops=hops, raw=data, details=details)


# ---------------------------------------------------------------------------
# Simulation (MTR_TRACKER_SIMULATE=1): no API calls, plausible remote probes.
# ---------------------------------------------------------------------------

_SIM_PROBES = [
    {"continent": "EU", "country": "DE", "city": "Frankfurt", "asn": 24940, "network": "Hetzner Online GmbH"},
    {"continent": "NA", "country": "US", "city": "Ashburn", "asn": 14618, "network": "Amazon.com, Inc."},
    {"continent": "AS", "country": "SG", "city": "Singapore", "asn": 16509, "network": "Amazon.com, Inc."},
    {"continent": "EU", "country": "GB", "city": "London", "asn": 20473, "network": "The Constant Company"},
    {"continent": "SA", "country": "BR", "city": "São Paulo", "asn": 262287, "network": "Latitude.sh"},
]


def _sim_probe(location: str, index: int) -> dict[str, Any]:
    seed = int(hashlib.md5(f"{location}:{index}".encode()).hexdigest(), 16)
    return dict(_SIM_PROBES[seed % len(_SIM_PROBES)])


def _simulate_summary(started: float, measurement: str, body: dict[str, Any], opts: dict[str, Any], location: str, command: str) -> ProbeOutcome:
    rng = random.Random()
    limit = int(body["limit"])
    target = body["target"]
    details: dict[str, Any] = {"measurement": measurement, "location": location, "measurement_id": "simulated"}
    probes: list[dict[str, Any]] = []
    if measurement == "ping":
        packets = int(body["measurementOptions"]["packets"])
        for i in range(limit):
            probe = _sim_probe(location, i)
            base = 8 + (probe["asn"] % 90)
            lost = 1 if rng.random() < 0.05 else 0
            rtts = [round(max(0.3, rng.gauss(base, base * 0.06)), 3) for _ in range(packets - lost)]
            probes.append({**_probe_fields(probe), "status": "finished", "resolved": "192.0.2.10", "sent": packets, "received": len(rtts), "loss": round(100.0 * lost / packets, 1),
                           "min": min(rtts) if rtts else None, "avg": round(sum(rtts) / len(rtts), 3) if rtts else None, "max": max(rtts) if rtts else None, "rtts": rtts})
        details["probes"] = probes
        return _ping_outcome(started, time.time() + 0.01, probes, details, f"[simulated] {command}")
    if measurement == "dns":
        rtype = body["measurementOptions"]["query"]["type"]
        expected = str(opts.get("expected") or "").strip()
        for i in range(limit):
            probe = _sim_probe(location, i)
            value = "192.0.2.10" if rtype == "A" else "2001:db8::10" if rtype == "AAAA" else f"{target}."
            answers = [{"name": f"{target}.", "type": rtype, "ttl": 300, "value": value}]
            reason = None if not expected or expected.lower() in value.lower() else f"expected '{expected}' not in answers"
            probes.append({**_probe_fields(probe), "status": "finished", "rcode": "NOERROR", "resolver": body["measurementOptions"].get("resolver") or "private", "total_ms": round(max(1.0, rng.gauss(22, 6)), 1),
                           "answers": answers, "passed": reason is None, "reason": reason})
        details.update({"probes": probes, "record_type": rtype, "resolver": body["measurementOptions"].get("resolver") or "probe default", "answers": [a["value"] for a in probes[0]["answers"]], "rcode": "NOERROR"})
        return _check_outcome(started, time.time() + 0.01, probes, details, f"[simulated] {command}")
    o = body["measurementOptions"]
    expected_status = str(opts.get("expected_status") or "200-299")
    now = datetime.now(timezone.utc)
    for i in range(limit):
        probe = _sim_probe(location, i)
        fail = rng.random() < 0.03
        code = 503 if fail else 200
        timings = {"dns": round(rng.uniform(2, 20)), "tcp": round(rng.uniform(5, 40)), "tls": round(rng.uniform(10, 60)), "firstByte": round(rng.uniform(20, 120)), "download": round(rng.uniform(0, 10))}
        total = sum(timings.values())
        tls = None
        if str(o.get("protocol", "HTTPS")).upper() != "HTTP":
            tls = {"subject": target, "subject_org": None, "issuer": "Simulated CA", "issuer_cn": "Simulated CA R1", "not_before": (now - timedelta(days=29)).isoformat().replace("+00:00", "Z"),
                   "not_after": (now + timedelta(days=61)).isoformat().replace("+00:00", "Z"), "days_left": 61, "san": [target, f"www.{target}"], "serial": "0A1B2C3D4E5F",
                   "protocol": "TLSv1.3", "cipher": "TLS_AES_256_GCM_SHA384", "authorized": True}
        reason = None if status_allowed(code, expected_status) else f"HTTP {code} not in expected {expected_status}"
        probes.append({**_probe_fields(probe), "status": "finished", "status_code": code, "status_name": "Service Unavailable" if fail else "OK", "resolved": "192.0.2.10", "total_ms": float(total),
                       "timings": timings, "server": "simulator", "content_type": "text/html; charset=utf-8", "truncated": False, "tls": tls, "passed": reason is None, "reason": reason})
    first = next((p for p in probes if p["passed"]), probes[0])
    details.update({"probes": probes, "request": f"{o['request']['method']} {str(o['protocol']).lower()}://{target}{o['request']['path']}", "status": first["status_code"],
                    "status_name": first["status_name"], "timings": first["timings"], "tls": first["tls"], "tls_expires_in_days": 61 if first["tls"] else None})
    return _check_outcome(started, time.time() + 0.01, probes, details, f"[simulated] {command}")


async def _simulate_path(t: dict[str, Any], body: dict[str, Any], location: str, command: str) -> MtrResult:
    host = body["target"]
    dst_ip = host if is_ip(host) else f"198.51.100.{int(hashlib.md5(host.encode()).hexdigest(), 16) % 254 + 1}"
    packets = int(body["measurementOptions"].get("packets") or 3)
    result = await simulate_path(dst_ip=dst_ip, count=packets, probe_interval=0.2, max_hops=int(t.get("max_hops") or 30))
    if body["type"] == "traceroute":
        for h in result.hops:
            h.asn = None
            h.jitter_ms = h.jitter_avg_ms = h.jitter_max_ms = h.jitter_int_ms = h.gmean_ms = None
    probe = _sim_probe(location, 0)
    result.command = f"[simulated] {command}"
    result.src = probe_label(probe)
    result.details = {"measurement": body["type"], "location": location, "measurement_id": "simulated", "probe": _probe_fields(probe)}
    return result
