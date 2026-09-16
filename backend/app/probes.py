"""Non-path probe types: ping, HTTP(S), TCP connect and DNS.

Each probe returns a ProbeOutcome with the same summary shape the scheduler
stores for MTR runs, plus a free-form `details` dict shown in the UI.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import shutil
import socket
import ssl
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import config
from .resolver import is_ip, resolve_host

PROBE_TYPES = ("mtr", "ping", "http", "tcp", "dns")

# Tests override these: force live mode and inject an httpx.MockTransport.
_FORCE_LIVE = False
_HTTP_TRANSPORT: httpx.BaseTransport | None = None


def _sim() -> bool:
    return config.simulate and not _FORCE_LIVE


@dataclass
class ProbeOutcome:
    ok: bool                       # the probe itself executed (no internal error)
    reached: bool                  # the check passed
    started_at: float
    finished_at: float
    error: str | None = None
    dst_ip: str | None = None
    sent: int | None = None
    loss_pct: float = 0.0
    last_ms: float | None = None
    avg_ms: float | None = None
    best_ms: float | None = None
    worst_ms: float | None = None
    stdev_ms: float | None = None
    jitter_avg_ms: float | None = None
    jitter_max_ms: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    command: str | None = None

    @property
    def duration_ms(self) -> float:
        return (self.finished_at - self.started_at) * 1000.0

    def summary(self) -> dict[str, Any]:
        return {
            "loss_pct": self.loss_pct,
            "last_ms": self.last_ms,
            "avg_ms": self.avg_ms,
            "best_ms": self.best_ms,
            "worst_ms": self.worst_ms,
            "stdev_ms": self.stdev_ms,
            "jitter_avg_ms": self.jitter_avg_ms,
            "jitter_max_ms": self.jitter_max_ms,
        }


def _stats(samples: list[float]) -> dict[str, float | None]:
    if not samples:
        return {"last": None, "avg": None, "best": None, "worst": None, "stdev": None, "javg": None, "jmax": None}
    diffs = [abs(b - a) for a, b in zip(samples, samples[1:])]
    return {
        "last": samples[-1],
        "avg": sum(samples) / len(samples),
        "best": min(samples),
        "worst": max(samples),
        "stdev": statistics.pstdev(samples) if len(samples) > 1 else 0.0,
        "javg": sum(diffs) / len(diffs) if diffs else 0.0,
        "jmax": max(diffs) if diffs else 0.0,
    }


def _apply_stats(o: ProbeOutcome, samples: list[float]) -> None:
    s = _stats(samples)
    o.last_ms, o.avg_ms, o.best_ms, o.worst_ms, o.stdev_ms = s["last"], s["avg"], s["best"], s["worst"], s["stdev"]
    o.jitter_avg_ms, o.jitter_max_ms = s["javg"], s["jmax"]


# ---------------------------------------------------------------------------
# Ping
# ---------------------------------------------------------------------------

_PING_TIME_RE = re.compile(r"time[=<]([\d.]+)\s*ms")
_PING_SUMMARY_RE = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received")


async def run_ping(t: dict[str, Any], opts: dict[str, Any]) -> ProbeOutcome:
    started = time.time()
    count = int(t.get("count") or 5)
    interval = float(t.get("probe_interval") or 1.0)
    size = int(t.get("packet_size") or 56)
    timeout = float(opts.get("timeout_sec") or 2.0)
    try:
        dst_ip = await resolve_host(t["host"], t.get("ip_version") or "auto")
    except ValueError as exc:
        return ProbeOutcome(False, False, started, time.time(), error=str(exc), loss_pct=100.0)

    if _sim() or shutil.which("ping") is None:
        return _simulate_ping(started, dst_ip, count)

    cmd = ["ping", "-6" if ":" in dst_ip else "-4", "-n", "-c", str(count), "-i", f"{max(0.2, interval):g}", "-W", f"{timeout:g}", "-s", str(max(0, size - 8)), dst_ip]
    budget = count * (max(0.2, interval) + timeout) + 5
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=budget)
    except asyncio.TimeoutError:
        return ProbeOutcome(False, False, started, time.time(), error=f"ping timed out after {budget:.0f}s", dst_ip=dst_ip, loss_pct=100.0, command=" ".join(cmd))
    except FileNotFoundError:
        return ProbeOutcome(False, False, started, time.time(), error="ping binary not found", dst_ip=dst_ip, loss_pct=100.0)
    finished = time.time()
    text = stdout.decode("utf-8", "replace")
    samples = [float(m) for m in _PING_TIME_RE.findall(text)]
    m = _PING_SUMMARY_RE.search(text)
    sent = int(m.group(1)) if m else count
    received = int(m.group(2)) if m else len(samples)
    if not m and not samples:
        err = stderr.decode("utf-8", "replace").strip() or text.strip()[-200:] or f"ping exited with {proc.returncode}"
        return ProbeOutcome(False, False, started, finished, error=err, dst_ip=dst_ip, sent=sent, loss_pct=100.0, command=" ".join(cmd))
    o = ProbeOutcome(True, received > 0, started, finished, dst_ip=dst_ip, sent=sent, command=" ".join(cmd))
    o.loss_pct = round(100.0 * (sent - received) / sent, 1) if sent else 100.0
    _apply_stats(o, samples)
    o.details = {"samples_ms": [round(s, 3) for s in samples], "sent": sent, "received": received, "packet_size": size}
    return o


def _simulate_ping(started: float, dst_ip: str, count: int) -> ProbeOutcome:
    rng = random.Random()
    base = 5 + (sum(ord(c) for c in dst_ip) % 60)
    lost = 1 if rng.random() < 0.08 else 0
    samples = [max(0.1, rng.gauss(base, base * 0.08)) for _ in range(count - lost)]
    o = ProbeOutcome(True, bool(samples), started, time.time() + 0.01, dst_ip=dst_ip, sent=count, command=f"[simulated] ping -c {count} {dst_ip}")
    o.loss_pct = round(100.0 * lost / count, 1)
    _apply_stats(o, samples)
    o.details = {"samples_ms": [round(s, 3) for s in samples], "sent": count, "received": len(samples), "packet_size": 56}
    return o


# ---------------------------------------------------------------------------
# HTTP(S)
# ---------------------------------------------------------------------------

_STATUS_TOKEN_RE = re.compile(r"^\s*(\d{3})(?:\s*-\s*(\d{3}))?\s*$")
_NUM_CMP_RE = re.compile(r"^(==|!=|>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)$")


def status_allowed(code: int, spec: str | None) -> bool:
    spec = (spec or "").strip() or "200-299"
    for token in spec.split(","):
        m = _STATUS_TOKEN_RE.match(token)
        if not m:
            continue
        lo = int(m.group(1))
        hi = int(m.group(2) or lo)
        if lo <= code <= hi:
            return True
    return False


def json_path(data: Any, path: str) -> tuple[bool, Any]:
    """Resolve a dotted path with [index] segments, e.g. data.items[0].status. Returns (found, value)."""
    cur = data
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path.strip()):
        if part.startswith("["):
            idx = int(part[1:-1])
            if not isinstance(cur, list) or idx >= len(cur):
                return False, None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or part not in cur:
                return False, None
            cur = cur[part]
    return True, cur


def json_matches(value: Any, expected: str) -> bool:
    expected = expected.strip()
    m = _NUM_CMP_RE.match(expected)
    if m:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return False
        want = float(m.group(2))
        return {"==": num == want, "!=": num != want, ">=": num >= want, "<=": num <= want, ">": num > want, "<": num < want}[m.group(1)]
    if expected.startswith("~"):
        return expected[1:].strip().lower() in str(value).lower()
    if isinstance(value, bool):
        return expected.lower() == str(value).lower()
    if value is None:
        return expected.lower() in {"null", "none"}
    return str(value) == expected


async def tls_expiry_days(host: str, port: int, timeout: float = 5.0) -> tuple[int | None, str | None]:
    """Days until the server certificate expires, or (None, reason)."""
    ctx = ssl.create_default_context()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=ctx, server_hostname=host), timeout=timeout)
    except (OSError, ssl.SSLError, asyncio.TimeoutError) as exc:
        return None, str(exc) or exc.__class__.__name__
    try:
        ssl_obj = writer.get_extra_info("ssl_object")
        cert = ssl_obj.getpeercert() if ssl_obj else None
        if not cert or "notAfter" not in cert:
            return None, "no certificate"
        not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return int((not_after - datetime.now(timezone.utc)).total_seconds() // 86400), None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


async def run_http(t: dict[str, Any], opts: dict[str, Any]) -> ProbeOutcome:
    started = time.time()
    url = t["host"].strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    method = str(opts.get("method") or "GET").upper()
    timeout = float(opts.get("timeout_sec") or 10.0)
    verify = bool(opts.get("verify_tls", True))
    follow = bool(opts.get("follow_redirects", True))
    headers: dict[str, str] = {"User-Agent": "MTR-Tracker/1.0"}
    raw_headers = opts.get("headers") or {}
    if isinstance(raw_headers, dict):
        headers.update({str(k): str(v) for k, v in raw_headers.items()})
    body = opts.get("body") or None
    keyword = (opts.get("keyword") or "").strip()
    keyword_absent = bool(opts.get("keyword_absent", False))
    jpath = (opts.get("json_path") or "").strip()
    jexpected = opts.get("json_expected")
    tls_warn_days = int(opts.get("tls_warn_days") or 0)

    if _sim():
        return _simulate_http(started, url, keyword, jpath)

    parts = urlsplit(url)
    details: dict[str, Any] = {"url": url, "method": method}
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=verify, follow_redirects=follow, headers=headers, transport=_HTTP_TRANSPORT) as client:
            t0 = time.perf_counter()
            resp = await client.request(method, url, content=body.encode() if isinstance(body, str) and body else None)
            elapsed = (time.perf_counter() - t0) * 1000.0
    except httpx.HTTPError as exc:
        return ProbeOutcome(True, False, started, time.time(), error=f"{exc.__class__.__name__}: {exc}", loss_pct=100.0, details=details)
    finished = time.time()

    details.update(
        {
            "status": resp.status_code,
            "reason": resp.reason_phrase,
            "bytes": len(resp.content),
            "content_type": resp.headers.get("content-type"),
            "server": resp.headers.get("server"),
            "final_url": str(resp.url),
            "redirects": len(resp.history),
            "http_version": resp.http_version,
        }
    )
    failures: list[str] = []
    ok_status = status_allowed(resp.status_code, opts.get("expected_status"))
    details["status_ok"] = ok_status
    if not ok_status:
        failures.append(f"HTTP {resp.status_code} not in expected {opts.get('expected_status') or '200-299'}")

    text = resp.text if len(resp.content) <= 5_000_000 else ""
    if keyword:
        found = keyword.lower() in text.lower()
        details["keyword"] = keyword
        details["keyword_found"] = found
        if keyword_absent and found:
            failures.append(f"keyword '{keyword}' present but must be absent")
        if not keyword_absent and not found:
            failures.append(f"keyword '{keyword}' not found")
    if jpath:
        try:
            data = resp.json()
            found, value = json_path(data, jpath)
            details["json_path"] = jpath
            details["json_value"] = value if isinstance(value, (str, int, float, bool)) or value is None else json.dumps(value)[:200]
            if not found:
                failures.append(f"JSON path '{jpath}' not found")
            elif jexpected not in (None, ""):
                matched = json_matches(value, str(jexpected))
                details["json_ok"] = matched
                if not matched:
                    failures.append(f"JSON '{jpath}' = {details['json_value']!r}, expected {jexpected}")
        except ValueError:
            failures.append("response is not valid JSON")

    warnings: list[str] = []
    if parts.scheme == "https" and verify:
        days, why = await tls_expiry_days(parts.hostname or "", parts.port or 443)
        details["tls_expires_in_days"] = days
        if days is None and why:
            details["tls_error"] = why
        elif days is not None and tls_warn_days > 0 and days <= tls_warn_days:
            warnings.append(f"TLS certificate expires in {days} day{'s' if days != 1 else ''}")

    o = ProbeOutcome(True, not failures, started, finished, dst_ip=None, sent=1, details=details, warnings=warnings, command=f"{method} {url}")
    o.loss_pct = 0.0 if not failures else 100.0
    _apply_stats(o, [elapsed])
    if failures:
        o.error = "; ".join(failures)
    return o


def _simulate_http(started: float, url: str, keyword: str, jpath: str) -> ProbeOutcome:
    rng = random.Random()
    elapsed = max(20.0, rng.gauss(180, 40))
    fail = rng.random() < 0.03
    details = {"url": url, "method": "GET", "status": 503 if fail else 200, "reason": "Service Unavailable" if fail else "OK", "bytes": 15234, "content_type": "text/html; charset=utf-8",
               "server": "simulator", "final_url": url, "redirects": 0, "http_version": "HTTP/1.1", "status_ok": not fail, "tls_expires_in_days": 61}
    if keyword:
        details.update({"keyword": keyword, "keyword_found": not fail})
    if jpath:
        details.update({"json_path": jpath, "json_value": "ok", "json_ok": not fail})
    o = ProbeOutcome(True, not fail, started, time.time() + 0.01, sent=1, details=details, command=f"[simulated] GET {url}")
    o.loss_pct = 100.0 if fail else 0.0
    _apply_stats(o, [elapsed])
    if fail:
        o.error = "HTTP 503 not in expected 200-299"
    return o


# ---------------------------------------------------------------------------
# TCP connect
# ---------------------------------------------------------------------------


async def run_tcp(t: dict[str, Any], opts: dict[str, Any]) -> ProbeOutcome:
    started = time.time()
    port = int(t.get("port") or opts.get("port") or 0)
    timeout = float(opts.get("timeout_sec") or 5.0)
    if not port:
        return ProbeOutcome(False, False, started, time.time(), error="TCP probe needs a port", loss_pct=100.0)
    try:
        dst_ip = await resolve_host(t["host"], t.get("ip_version") or "auto")
    except ValueError as exc:
        return ProbeOutcome(False, False, started, time.time(), error=str(exc), loss_pct=100.0)
    if _sim():
        rng = random.Random()
        o = ProbeOutcome(True, True, started, time.time() + 0.01, dst_ip=dst_ip, sent=1, details={"port": port, "connected": True}, command=f"[simulated] tcp {dst_ip}:{port}")
        _apply_stats(o, [max(1.0, rng.gauss(25, 5))])
        return o
    t0 = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(dst_ip, port), timeout=timeout)
    except asyncio.TimeoutError:
        return ProbeOutcome(True, False, started, time.time(), error=f"connect to {dst_ip}:{port} timed out after {timeout:g}s", dst_ip=dst_ip, sent=1, loss_pct=100.0, details={"port": port, "connected": False})
    except OSError as exc:
        return ProbeOutcome(True, False, started, time.time(), error=f"connect to {dst_ip}:{port} failed: {exc.strerror or exc}", dst_ip=dst_ip, sent=1, loss_pct=100.0, details={"port": port, "connected": False})
    elapsed = (time.perf_counter() - t0) * 1000.0
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001
        pass
    o = ProbeOutcome(True, True, started, time.time(), dst_ip=dst_ip, sent=1, details={"port": port, "connected": True}, command=f"tcp connect {dst_ip}:{port}")
    _apply_stats(o, [elapsed])
    return o


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------

DNS_RECORD_TYPES = ("A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "PTR", "SRV")


async def run_dns(t: dict[str, Any], opts: dict[str, Any]) -> ProbeOutcome:
    import dns.asyncresolver
    import dns.exception
    import dns.rdatatype

    started = time.time()
    name = t["host"].strip().rstrip(".")
    rtype = str(opts.get("record_type") or "A").upper()
    server = (opts.get("resolver") or "").strip()
    expected = (opts.get("expected") or "").strip()
    timeout = float(opts.get("timeout_sec") or 5.0)

    if _sim():
        rng = random.Random()
        answers = ["192.0.2.10"] if rtype == "A" else [f"{name}."]
        o = ProbeOutcome(True, True, started, time.time() + 0.01, sent=1, details={"record_type": rtype, "resolver": server or "system", "answers": answers, "ttl": 300}, command=f"[simulated] dns {rtype} {name}")
        _apply_stats(o, [max(1.0, rng.gauss(18, 4))])
        if expected and not any(expected.lower() in a.lower() for a in answers):
            o.reached, o.loss_pct, o.error = False, 100.0, f"expected '{expected}' not in answers"
        return o

    resolver = dns.asyncresolver.Resolver(configure=not server)
    if server:
        try:
            resolver.nameservers = [await resolve_host(server, "auto")] if not is_ip(server) else [server]
        except ValueError as exc:
            return ProbeOutcome(False, False, started, time.time(), error=f"resolver: {exc}", loss_pct=100.0)
    resolver.lifetime = timeout
    resolver.timeout = timeout
    details: dict[str, Any] = {"record_type": rtype, "resolver": server or "system"}
    t0 = time.perf_counter()
    try:
        answer = await resolver.resolve(name, rtype)
    except dns.exception.DNSException as exc:
        return ProbeOutcome(True, False, started, time.time(), error=f"{exc.__class__.__name__}: {exc}", sent=1, loss_pct=100.0, details=details, command=f"dns {rtype} {name}")
    elapsed = (time.perf_counter() - t0) * 1000.0
    answers = sorted(r.to_text() for r in answer)
    details.update({"answers": answers, "ttl": answer.rrset.ttl if answer.rrset is not None else None, "nameserver": getattr(answer, "nameserver", None)})
    o = ProbeOutcome(True, True, started, time.time(), sent=1, details=details, command=f"dns {rtype} {name}" + (f" @{server}" if server else ""))
    _apply_stats(o, [elapsed])
    if expected and not any(expected.lower() in a.lower() for a in answers):
        o.reached, o.loss_pct, o.error = False, 100.0, f"expected '{expected}' not in answers {answers}"
    return o


RUNNERS = {"ping": run_ping, "http": run_http, "tcp": run_tcp, "dns": run_dns}


async def run_probe(t: dict[str, Any]) -> ProbeOutcome:
    kind = t.get("type") or "mtr"
    runner = RUNNERS.get(kind)
    if runner is None:
        now = time.time()
        return ProbeOutcome(False, False, now, now, error=f"unknown probe type {kind}", loss_pct=100.0)
    opts = t.get("options") or {}
    if isinstance(opts, str):
        try:
            opts = json.loads(opts)
        except json.JSONDecodeError:
            opts = {}
    try:
        return await runner(t, opts)
    except Exception as exc:  # noqa: BLE001
        now = time.time()
        return ProbeOutcome(False, False, now, now, error=f"{exc.__class__.__name__}: {exc}", loss_pct=100.0)


def socket_family_hint(ip: str) -> int:
    return socket.AF_INET6 if ":" in ip else socket.AF_INET
