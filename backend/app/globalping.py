"""Globalping probe type: ping or mtr from a remote vantage point through the public globalping.io API.

A measurement is created with POST /v1/measurements and polled until every probe has finished. Ping
measurements become one summary row (like the local ping probe, with per-probe details); mtr measurements
are turned into HopResults so the scheduler stores them like a local mtr run and every path visual works.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from typing import Any

import httpx

from .config import config
from .mtr import HopResult, MtrResult
from .mtr import _simulate as simulate_path
from .probes import ProbeOutcome, _apply_stats
from .resolver import is_ip, resolve_host

API_BASE = "https://api.globalping.io/v1"
MEASUREMENT_URL = "https://globalping.io?measurement="
USER_AGENT = "MTR-Tracker/1.0 (+https://github.com/smf13/MTR-test)"
MEASUREMENT_TIMEOUT = 90.0  # seconds to wait for every probe to report
POLL_INTERVAL = 1.0
MAX_PACKETS = 16  # API limit for ping/mtr packets

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


def build_request(t: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    """The POST /v1/measurements body for a target: type, target, probe selection and measurement options."""
    measurement = str(opts.get("measurement") or "ping")
    location = str(opts.get("location") or "world").strip()
    host = str(t.get("host") or "").strip()
    limit = 1 if measurement == "mtr" else max(1, min(10, int(opts.get("probes") or 1)))
    body: dict[str, Any] = {"type": measurement, "target": host, "limit": limit, "inProgressUpdates": False}
    if location and location.lower() != "world":
        body["locations"] = [{"magic": location}]
    options: dict[str, Any] = {"packets": max(1, min(MAX_PACKETS, int(t.get("count") or 4)))}
    ip_version = str(t.get("ip_version") or "auto")
    if ip_version in ("4", "6") and not is_ip(host):
        options["ipVersion"] = int(ip_version)
    if measurement == "mtr":
        protocol = str(t.get("protocol") or "icmp").upper()
        options["protocol"] = protocol
        if protocol in ("TCP", "UDP") and t.get("port"):
            options["port"] = int(t["port"])
    body["measurementOptions"] = options
    return body


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
                "label": probe_label(probe),
                "continent": probe.get("continent"),
                "country": probe.get("country"),
                "city": probe.get("city"),
                "asn": probe.get("asn"),
                "network": probe.get("network"),
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


def _common_details(measurement: str, location: str, data: dict[str, Any] | None, limits: dict[str, str]) -> dict[str, Any]:
    details: dict[str, Any] = {"measurement": measurement, "location": location}
    if data and data.get("id"):
        details["measurement_id"] = data["id"]
        details["url"] = MEASUREMENT_URL + str(data["id"])
    if limits:
        details["rate_limit"] = limits
    return details


async def run_globalping(t: dict[str, Any], opts: dict[str, Any], settings: dict[str, Any]) -> ProbeOutcome:
    """Ping from remote probes as one summary row (per-probe details kept in `details.probes`)."""
    started = time.time()
    measurement = str(opts.get("measurement") or "ping")
    location = str(opts.get("location") or "world").strip() or "world"
    if measurement != "ping":
        # mtr measurements go through the scheduler's path pipeline (run_globalping_mtr), not here.
        return ProbeOutcome(False, False, started, time.time(), error=f"globalping measurement '{measurement}' is not a summary probe", loss_pct=100.0)
    body = build_request(t, opts)
    packets = body["measurementOptions"]["packets"]
    command = f"globalping ping {body['target']} from {location} ({body['limit']} probe{'s' if body['limit'] != 1 else ''} × {packets} packets)"

    if _sim():
        return _simulate_ping(started, body, location, command)

    try:
        data, limits = await measure(body, settings)
    except GlobalpingError as exc:
        return ProbeOutcome(False, False, started, time.time(), error=str(exc), loss_pct=100.0, details=_common_details("ping", location, None, {}), command=command)
    finished = time.time()
    probes = parse_ping_probes(data)
    details = _common_details("ping", location, data, limits)
    details["probes"] = probes
    finished_probes = [p for p in probes if p.get("status") == "finished" and p.get("sent")]
    if not finished_probes:
        why = "; ".join(f"{p['label']}: {p.get('status') or 'no result'}" for p in probes) or "no probes reported"
        return ProbeOutcome(True, False, started, finished, error=f"no probe completed the ping ({why})", sent=0, loss_pct=100.0, details=details, command=command)
    sent = sum(int(p["sent"]) for p in finished_probes)
    received = sum(int(p.get("received") or 0) for p in finished_probes)
    rtts = [r for p in finished_probes for r in p["rtts"]]
    o = ProbeOutcome(True, received > 0, started, finished, dst_ip=next((p["resolved"] for p in finished_probes if p.get("resolved")), None), sent=sent, details=details, command=command)
    o.loss_pct = round(100.0 * (sent - received) / sent, 1) if sent else 100.0
    _apply_stats(o, rtts)
    if received == 0:
        o.error = "no probe received a reply"
    return o


async def run_globalping_mtr(t: dict[str, Any], opts: dict[str, Any], settings: dict[str, Any]) -> MtrResult:
    """mtr from one remote probe, returned like a local run so the scheduler stores hops and judges the route."""
    started = time.time()
    location = str(opts.get("location") or "world").strip() or "world"
    body = build_request(t, opts)
    command = f"globalping mtr {body['target']} from {location} ({body['measurementOptions']['packets']} packets, {body['measurementOptions'].get('protocol', 'ICMP')})"

    if _sim():
        return await _simulate_mtr(t, body, location, command)

    try:
        data, limits = await measure(body, settings)
    except GlobalpingError as exc:
        return MtrResult(False, started, time.time(), command, error=str(exc), details=_common_details("mtr", location, None, {}))
    finished = time.time()
    details = _common_details("mtr", location, data, limits)
    items = data.get("results") or []
    if not items:
        return MtrResult(False, started, finished, command, error="Globalping returned no results", details=details)
    probe = items[0].get("probe") or {}
    res = items[0].get("result") or {}
    label = probe_label(probe)
    details["probe"] = {k: probe.get(k) for k in ("continent", "country", "city", "asn", "network")}
    details["probe"]["label"] = label
    if res.get("status") != "finished":
        raw = str(res.get("rawOutput") or "").strip().splitlines()
        return MtrResult(False, started, finished, command, src=label, error=f"probe {label} reported {res.get('status') or 'no result'}" + (f": {raw[-1][:120]}" if raw else ""), details=details)
    hops = [hop_from_result(i, h) for i, h in enumerate(res.get("hops") or [])]
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


def _simulate_ping(started: float, body: dict[str, Any], location: str, command: str) -> ProbeOutcome:
    rng = random.Random()
    packets = int(body["measurementOptions"]["packets"])
    probes: list[dict[str, Any]] = []
    for i in range(int(body["limit"])):
        probe = _sim_probe(location, i)
        base = 8 + (probe["asn"] % 90)
        lost = 1 if rng.random() < 0.05 else 0
        rtts = [round(max(0.3, rng.gauss(base, base * 0.06)), 3) for _ in range(packets - lost)]
        probes.append(
            {
                **probe, "label": probe_label(probe), "status": "finished", "resolved": "192.0.2.10", "sent": packets, "received": len(rtts),
                "loss": round(100.0 * lost / packets, 1), "min": min(rtts) if rtts else None, "avg": round(sum(rtts) / len(rtts), 3) if rtts else None,
                "max": max(rtts) if rtts else None, "rtts": rtts,
            }
        )
    details = {"measurement": "ping", "location": location, "measurement_id": "simulated", "probes": probes}
    sent = sum(p["sent"] for p in probes)
    received = sum(p["received"] for p in probes)
    o = ProbeOutcome(True, received > 0, started, time.time() + 0.01, dst_ip="192.0.2.10", sent=sent, details=details, command=f"[simulated] {command}")
    o.loss_pct = round(100.0 * (sent - received) / sent, 1) if sent else 100.0
    _apply_stats(o, [r for p in probes for r in p["rtts"]])
    return o


async def _simulate_mtr(t: dict[str, Any], body: dict[str, Any], location: str, command: str) -> MtrResult:
    host = body["target"]
    try:
        dst_ip = await resolve_host(host, str(t.get("ip_version") or "auto")) if is_ip(host) else f"198.51.100.{int(hashlib.md5(host.encode()).hexdigest(), 16) % 254 + 1}"
    except ValueError:
        dst_ip = "198.51.100.7"
    result = await simulate_path(dst_ip=dst_ip, count=int(body["measurementOptions"]["packets"]), probe_interval=0.2, max_hops=int(t.get("max_hops") or 30))
    probe = _sim_probe(location, 0)
    result.command = f"[simulated] {command}"
    result.src = probe_label(probe)
    result.details = {"measurement": "mtr", "location": location, "measurement_id": "simulated", "probe": {**probe, "label": result.src}}
    return result
