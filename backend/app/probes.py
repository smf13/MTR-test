"""Non-path probe types: ping, HTTP(S), TCP connect and DNS.

Each probe returns a ProbeOutcome with the same summary shape the scheduler
stores for MTR runs, plus a free-form `details` dict shown in the UI.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
import shutil
import ssl
import statistics
import time
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import config
from .resolver import is_ip, resolve_host

# Bytes of an HTTP response body kept in memory for the keyword/JSON checks; the rest is discarded.
MAX_HTTP_BODY = 5_000_000

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


def ping_payload_bytes(packet_size: int, ipv6: bool) -> int:
    """ping's -s counts only the ICMP payload while mtr's -s (and `targets.packet_size`) is the whole IP packet,
    so subtract the IP header (20 bytes for IPv4, 40 for IPv6) and the 8-byte ICMP header to send equal sizes."""
    return max(0, int(packet_size) - (48 if ipv6 else 28))


async def run_ping(t: dict[str, Any], opts: dict[str, Any]) -> ProbeOutcome:
    started = time.time()
    count = int(t.get("count") or 5)
    interval = float(t.get("probe_interval") or 1.0)
    size = int(t.get("packet_size") or 64)
    timeout = float(opts.get("timeout_sec") or 2.0)
    try:
        dst_ip = await resolve_host(t["host"], t.get("ip_version") or "auto")
    except ValueError as exc:
        return ProbeOutcome(False, False, started, time.time(), error=str(exc), loss_pct=100.0)

    if _sim() or shutil.which("ping") is None:
        return _simulate_ping(started, dst_ip, count)

    ipv6 = ":" in dst_ip
    cmd = ["ping", "-6" if ipv6 else "-4", "-n", "-c", str(count), "-i", f"{max(0.2, interval):g}", "-W", f"{timeout:g}", "-s", str(ping_payload_bytes(size, ipv6)), dst_ip]
    budget = count * (max(0.2, interval) + timeout) + 5
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        return ProbeOutcome(False, False, started, time.time(), error="ping binary not found", dst_ip=dst_ip, loss_pct=100.0)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=budget)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ProbeOutcome(False, False, started, time.time(), error=f"ping timed out after {budget:.0f}s", dst_ip=dst_ip, loss_pct=100.0, command=" ".join(cmd))
    except asyncio.CancelledError:
        proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        raise
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


_CERT_DATE = "%b %d %H:%M:%S %Y %Z"


def _cert_datetime(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value), _CERT_DATE).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def cert_days_left(cert: dict[str, Any] | None) -> tuple[int | None, str | None]:
    """Days until a peer certificate (as returned by getpeercert()) expires, or (None, reason)."""
    if not cert or "notAfter" not in cert:
        return None, "no certificate"
    not_after = _cert_datetime(cert["notAfter"])
    if not_after is None:
        return None, f"unreadable expiry date {cert['notAfter']!r}"
    return int((not_after - datetime.now(timezone.utc)).total_seconds() // 86400), None


def _rdn(sequence: Any) -> dict[str, str]:
    """getpeercert() encodes subject/issuer as a tuple of RDNs, each a tuple of (type, value) pairs."""
    out: dict[str, str] = {}
    for rdn in sequence or ():
        for pair in rdn:
            if len(pair) == 2 and pair[0] not in out:
                out[str(pair[0])] = str(pair[1])
    return out


def certificate_details(ssl_obj: Any) -> dict[str, Any] | None:
    """Subject, issuer, validity, alternative names and the negotiated protocol of a verified peer certificate.

    Returns None when the connection was not verified (getpeercert() is empty then) or exposes no certificate.
    """
    try:
        cert = ssl_obj.getpeercert() or {}
    except Exception:  # noqa: BLE001
        return None
    if not cert:
        return None
    subject = _rdn(cert.get("subject"))
    issuer = _rdn(cert.get("issuer"))
    not_before = _cert_datetime(cert.get("notBefore"))
    not_after = _cert_datetime(cert.get("notAfter"))
    days, _ = cert_days_left(cert)
    try:
        cipher = ssl_obj.cipher()
        protocol = ssl_obj.version()
    except Exception:  # noqa: BLE001
        cipher, protocol = None, None
    return {
        "subject": subject.get("commonName"),
        "subject_org": subject.get("organizationName"),
        "issuer": issuer.get("organizationName") or issuer.get("commonName"),
        "issuer_cn": issuer.get("commonName"),
        "not_before": not_before.isoformat().replace("+00:00", "Z") if not_before else None,
        "not_after": not_after.isoformat().replace("+00:00", "Z") if not_after else None,
        "days_left": days,
        "san": [str(v) for kind, v in cert.get("subjectAltName", ()) if kind == "DNS"][:25],
        "serial": cert.get("serialNumber"),
        "protocol": protocol,
        "cipher": cipher[0] if cipher else None,
    }


def tls_from_response(resp: httpx.Response) -> dict[str, Any] | None:
    """Certificate details read off the TLS connection that served this response, or None when unavailable
    (plain HTTP, mocked transports, or a stream that exposes no ssl object)."""
    try:
        stream = resp.extensions.get("network_stream")
        ssl_obj = stream.get_extra_info("ssl_object") if stream is not None else None
    except Exception:  # noqa: BLE001
        return None
    return certificate_details(ssl_obj) if ssl_obj is not None else None


async def tls_details(host: str, port: int, timeout: float = 5.0) -> tuple[dict[str, Any] | None, str | None]:
    """Certificate details via a dedicated connection, or (None, reason)."""
    ctx = ssl.create_default_context()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=ctx, server_hostname=host), timeout=timeout)
    except (OSError, ssl.SSLError, asyncio.TimeoutError) as exc:
        return None, str(exc) or exc.__class__.__name__
    try:
        info = certificate_details(writer.get_extra_info("ssl_object"))
        return (info, None) if info else (None, "no certificate")
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
    tls_info = bool(opts.get("tls_info", True))

    if _sim():
        return _simulate_http(started, url, keyword, jpath, tls_warn_days, tls_info)

    parts = urlsplit(url)
    want_tls = parts.scheme == "https" and verify and (tls_warn_days > 0 or tls_info)
    details: dict[str, Any] = {"url": url, "method": method}
    tls: dict[str, Any] | None = None
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=verify, follow_redirects=follow, headers=headers, transport=_HTTP_TRANSPORT) as client:
            t0 = time.perf_counter()
            # Stream the body: a monitored URL may serve something huge, and only the first MAX_HTTP_BODY
            # bytes are needed for the keyword and JSON checks.
            async with client.stream(method, url, content=body.encode() if isinstance(body, str) and body else None) as resp:
                if want_tls:
                    tls = tls_from_response(resp)
                chunks: list[bytes] = []
                received = 0
                truncated = False
                async for chunk in resp.aiter_bytes():
                    room = MAX_HTTP_BODY - received
                    received += len(chunk)
                    if len(chunk) > room:
                        chunks.append(chunk[:room])
                        truncated = True
                        break
                    chunks.append(chunk)
                elapsed = (time.perf_counter() - t0) * 1000.0
                content = b"".join(chunks)
    except httpx.HTTPError as exc:
        return ProbeOutcome(True, False, started, time.time(), error=f"{exc.__class__.__name__}: {exc}", loss_pct=100.0, details=details)
    finished = time.time()

    details.update(
        {
            "status": resp.status_code,
            "reason": resp.reason_phrase,
            "bytes": received,
            "content_type": resp.headers.get("content-type"),
            "server": resp.headers.get("server"),
            "final_url": str(resp.url),
            "redirects": len(resp.history),
            "http_version": resp.http_version,
        }
    )
    if truncated:
        details["truncated"] = True
    failures: list[str] = []
    ok_status = status_allowed(resp.status_code, opts.get("expected_status"))
    details["status_ok"] = ok_status
    if not ok_status:
        failures.append(f"HTTP {resp.status_code} not in expected {opts.get('expected_status') or '200-299'}")

    try:
        text = content.decode(resp.charset_encoding or "utf-8", "replace")
    except LookupError:
        text = content.decode("utf-8", "replace")
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
            data = json.loads(text)
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
    if want_tls:
        # Prefer the certificate of the connection just used; open a second one only when the stream did not expose it.
        info, why = (tls, None) if tls is not None else await tls_details(parts.hostname or "", parts.port or 443)
        days = info.get("days_left") if info else None
        details["tls_expires_in_days"] = days
        details["tls_warn_days"] = tls_warn_days
        if info is None and why:
            details["tls_error"] = why
        if info and tls_info:
            details["tls"] = info
        if days is not None and tls_warn_days > 0 and days <= tls_warn_days:
            warnings.append(f"TLS certificate expires in {days} day{'s' if days != 1 else ''}")

    o = ProbeOutcome(True, not failures, started, finished, dst_ip=None, sent=1, details=details, warnings=warnings, command=f"{method} {url}")
    o.loss_pct = 0.0 if not failures else 100.0
    _apply_stats(o, [elapsed])
    if failures:
        o.error = "; ".join(failures)
    return o


def _simulate_http(started: float, url: str, keyword: str, jpath: str, tls_warn_days: int = 14, tls_info: bool = True) -> ProbeOutcome:
    rng = random.Random()
    elapsed = max(20.0, rng.gauss(180, 40))
    fail = rng.random() < 0.03
    details: dict[str, Any] = {"url": url, "method": "GET", "status": 503 if fail else 200, "reason": "Service Unavailable" if fail else "OK", "bytes": 15234, "content_type": "text/html; charset=utf-8",
                               "server": "simulator", "final_url": url, "redirects": 0, "http_version": "HTTP/1.1", "status_ok": not fail, "tls_expires_in_days": 61, "tls_warn_days": tls_warn_days}
    if tls_info and url.lower().startswith("https://"):
        host = urlsplit(url).hostname or "example.test"
        now = datetime.now(timezone.utc)
        details["tls"] = {
            "subject": host, "subject_org": None, "issuer": "Simulated CA", "issuer_cn": "Simulated CA R1",
            "not_before": (now - timedelta(days=29)).isoformat().replace("+00:00", "Z"), "not_after": (now + timedelta(days=61)).isoformat().replace("+00:00", "Z"),
            "days_left": 61, "san": [host, f"www.{host}"], "serial": "0A1B2C3D4E5F", "protocol": "TLSv1.3", "cipher": "TLS_AES_256_GCM_SHA384",
        }
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


def random_dns_label() -> str:
    """A label no resolver can have cached, so the query has to travel to the authoritative servers."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


DNS_TRANSPORT_PORT = {"udp": 53, "tcp": 53, "dot": 853, "doh": 443}
DNS_TRANSPORT_FLAG = {"udp": "", "tcp": " +tcp", "dot": " +tls", "doh": " +https"}
# Typical extra cost of a fresh connection per transport, for the simulator only (TCP handshake, TLS on top).
_SIM_DNS_EXTRA_MS = {"udp": 0.0, "tcp": 12.0, "dot": 30.0, "doh": 35.0}


def doh_url(resolver: str) -> str:
    """A DoH resolver as a URL: a pasted URL stays as is, a bare host (or IP) gets https:// and the RFC 8484 /dns-query path."""
    if "://" in resolver:
        return resolver
    host = f"[{resolver}]" if ":" in resolver and is_ip(resolver) else resolver
    return f"https://{host}/dns-query"


async def run_dns(t: dict[str, Any], opts: dict[str, Any]) -> ProbeOutcome:
    import dns.asyncquery
    import dns.asyncresolver
    import dns.exception
    import dns.message
    import dns.rcode
    import dns.rdatatype
    import dns.resolver

    started = time.time()
    name = t["host"].strip().rstrip(".")
    rtype = str(opts.get("record_type") or "A").upper()
    transport = str(opts.get("transport") or "udp")
    server = (opts.get("resolver") or "").strip()
    expected = (opts.get("expected") or "").strip()
    timeout = float(opts.get("timeout_sec") or 5.0)
    random_prefix = bool(opts.get("random_prefix", False))
    verify = bool(opts.get("verify_tls", True))
    # With the random prefix the answer is normally NXDOMAIN (unless the zone has a wildcard); the point is the
    # time the resolver needs to go and ask, not the answer itself.
    qname = f"{random_dns_label()}.{name}" if random_prefix else name
    url = doh_url(server) if transport == "doh" and server else None
    port = int(opts.get("resolver_port") or 0) or DNS_TRANSPORT_PORT.get(transport, 53)
    at = url or (server if port == DNS_TRANSPORT_PORT.get(transport, 53) else f"{server} -p {port}")
    command = f"dns {rtype} {qname}" + (f" @{at}" if server else "") + DNS_TRANSPORT_FLAG.get(transport, "")
    details: dict[str, Any] = {
        "record_type": rtype, "resolver": url or server or "system", "queried_name": qname, "random_prefix": random_prefix,
        "transport": transport, "port": (urlsplit(url).port or 443) if url else port,
    }

    if _sim():
        rng = random.Random()
        if random_prefix:
            answers: list[str] = []
            details.update({"answers": answers, "rcode": "NXDOMAIN"})
        else:
            answers = ["192.0.2.10"] if rtype == "A" else [f"{name}."]
            details.update({"answers": answers, "ttl": 300, "rcode": "NOERROR"})
        o = ProbeOutcome(True, True, started, time.time() + 0.01, sent=1, details=details, command=f"[simulated] {command}")
        _apply_stats(o, [max(1.0, rng.gauss((45 if random_prefix else 18) + _SIM_DNS_EXTRA_MS.get(transport, 0.0), 8))])
        if expected and not any(expected.lower() in a.lower() for a in answers):
            o.reached, o.loss_pct, o.error = False, 100.0, f"expected '{expected}' not in answers"
        return o

    def failed(error: str, *, executed: bool = True) -> ProbeOutcome:
        return ProbeOutcome(executed, False, started, time.time(), error=error, sent=1, loss_pct=100.0, details=details, command=command)

    def no_answer(rcode: str, error: str, elapsed: float) -> ProbeOutcome:
        details.update({"answers": [], "rcode": rcode})
        if random_prefix and not expected and rcode in ("NXDOMAIN", "NOANSWER"):
            # An authoritative "no such name" is exactly what an uncached query for a random label yields: the lookup worked.
            o = ProbeOutcome(True, True, started, time.time(), sent=1, details=details, command=command)
        else:
            o = failed(error)
        _apply_stats(o, [elapsed])
        return o

    if not server:
        # The system resolver (resolv.conf) over plain DNS; tcp=True makes it ask over TCP instead of UDP.
        resolver = dns.asyncresolver.Resolver(configure=True)
        resolver.lifetime = timeout
        resolver.timeout = timeout
        t0 = time.perf_counter()
        try:
            answer = await resolver.resolve(qname, rtype, tcp=transport == "tcp")
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer) as exc:
            nx = isinstance(exc, dns.resolver.NXDOMAIN)
            return no_answer("NXDOMAIN" if nx else "NOANSWER", f"{exc.__class__.__name__}: {exc}", (time.perf_counter() - t0) * 1000.0)
        except dns.exception.DNSException as exc:
            return failed(f"{exc.__class__.__name__}: {exc}")
        elapsed = (time.perf_counter() - t0) * 1000.0
        rrset = answer.rrset
        details["nameserver"] = getattr(answer, "nameserver", None)
    else:
        # A named server is queried directly, one query over the chosen transport, so the timing is that transport's.
        # The address is resolved here (and recorded) so the map and the details show which server answered.
        tls_name: str | None = None
        if transport == "doh":
            host = urlsplit(url).hostname or ""
        else:
            host = server
        try:
            address = host if is_ip(host) else await resolve_host(host, "auto")
        except ValueError as exc:
            return failed(f"resolver: {exc}", executed=False)
        if not is_ip(host):
            tls_name = host
        details["nameserver"] = address
        query = dns.message.make_query(qname, rtype)
        t0 = time.perf_counter()
        try:
            if transport == "udp":
                response, used_tcp = await dns.asyncquery.udp_with_fallback(query, address, timeout=timeout, port=port)
                if used_tcp:
                    # A truncated UDP answer is retried over TCP, as every resolver does; say so rather than hide it.
                    details["tcp_fallback"] = True
            elif transport == "tcp":
                response = await dns.asyncquery.tcp(query, address, timeout=timeout, port=port)
            elif transport == "dot":
                context = None
                if not verify:
                    # dnspython refuses verify=False together with a server name; this context still sends the name (SNI).
                    context = ssl.create_default_context()
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                    context.set_alpn_protocols(["dot"])
                response = await dns.asyncquery.tls(
                    query, address, timeout=timeout, port=port, ssl_context=context, server_hostname=tls_name or address, verify=verify
                )
            else:
                response = await asyncio.wait_for(
                    dns.asyncquery.https(query, url, timeout=timeout, verify=verify, bootstrap_address=None if is_ip(host) else address),
                    timeout + 1.0,
                )
        except asyncio.TimeoutError:
            return failed(f"Timeout: no answer from {url or address} within {timeout:g} s")
        except dns.exception.DNSException as exc:
            return failed(f"{exc.__class__.__name__}: {exc}")
        except ssl.SSLError as exc:
            return failed(f"TLS error from {address}: {exc.reason or exc}")
        except (OSError, httpx.HTTPError, ValueError) as exc:
            return failed(f"{exc.__class__.__name__}: {exc}")
        elapsed = (time.perf_counter() - t0) * 1000.0
        rcode = dns.rcode.to_text(response.rcode())
        if rcode == "NXDOMAIN":
            return no_answer(rcode, f"NXDOMAIN: {qname} does not exist", elapsed)
        if rcode != "NOERROR":
            details.update({"answers": [], "rcode": rcode})
            o = failed(f"{rcode} from {url or address}")
            _apply_stats(o, [elapsed])
            return o
        try:
            rrset = response.resolve_chaining().answer
        except dns.exception.DNSException:
            rrset = None
        if rrset is None:
            return no_answer("NOANSWER", f"NoAnswer: no {rtype} record for {qname}", elapsed)
    answers = sorted(r.to_text() for r in rrset)
    details.update({"answers": answers, "ttl": rrset.ttl if rrset is not None else None, "rcode": "NOERROR"})
    o = ProbeOutcome(True, True, started, time.time(), sent=1, details=details, command=command)
    _apply_stats(o, [elapsed])
    if expected and not any(expected.lower() in a.lower() for a in answers):
        o.reached, o.loss_pct, o.error = False, 100.0, f"expected '{expected}' not in answers {answers}"
    return o


RUNNERS = {"ping": run_ping, "http": run_http, "tcp": run_tcp, "dns": run_dns}


def target_options(t: dict[str, Any]) -> dict[str, Any]:
    """The target's per-type options, whether they arrive parsed or as the JSON text stored in the database."""
    opts = t.get("options") or {}
    if isinstance(opts, str):
        try:
            opts = json.loads(opts)
        except json.JSONDecodeError:
            opts = {}
    return opts if isinstance(opts, dict) else {}


async def run_probe(t: dict[str, Any], settings: dict[str, Any] | None = None) -> ProbeOutcome:
    kind = t.get("type") or "mtr"
    opts = target_options(t)
    try:
        if kind == "globalping":
            # Imported here: globalping builds on ProbeOutcome, so a top-level import would be circular.
            from .globalping import run_globalping

            return await run_globalping(t, opts, settings or {})
        runner = RUNNERS.get(kind)
        if runner is None:
            now = time.time()
            return ProbeOutcome(False, False, now, now, error=f"unknown probe type {kind}", loss_pct=100.0)
        return await runner(t, opts)
    except Exception as exc:  # noqa: BLE001
        now = time.time()
        return ProbeOutcome(False, False, now, now, error=f"{exc.__class__.__name__}: {exc}", loss_pct=100.0)
