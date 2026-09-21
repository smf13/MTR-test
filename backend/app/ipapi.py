"""ip-api.com as a GeoIP provider: batched lookups, a local request budget well below the service's limit, and a cache.

The free tier answers up to 100 addresses per POST to http://ip-api.com/batch (TLS is reserved for the paid
service) and allows 15 such requests per minute per client address; beyond that it answers HTTP 429 and bans
repeat offenders for an hour. This module spends at most `MAX_REQUESTS_PER_MINUTE` batch requests, honours the
service's own X-Rl / X-Ttl allowance headers, and after any failure (network error, 429, unreadable answer)
pauses itself for `FAILURE_BACKOFF` seconds. While it is paused, or while an address has not been answered,
`geoip` falls back to the MaxMind databases when those are on disk.

`geoip.path_geo` and `geoip.lookup_query` call `prefetch()` with every public address of a run first, so one
request answers a whole path; the synchronous `get()` then serves the cached records.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
from collections import deque
from typing import Any

import httpx

log = logging.getLogger("mtr-tracker.ipapi")

PROVIDER = "ip-api.com"
# The free endpoint has no HTTPS; the paid one (pro.ip-api.com) would.
BATCH_URL = "http://ip-api.com/batch"
# The service's own maximum per batch request.
BATCH_SIZE = 100
# Only the fields the map needs, which keeps the answers small.
FIELDS = "status,message,country,countryCode,regionName,city,lat,lon,as,asname,query"
# The service allows 15 batch requests per minute; this budget keeps a wide margin for bursts of polls.
MAX_REQUESTS_PER_MINUTE = 6
# Answers change rarely; a long cache is what keeps the request count tiny in steady state.
CACHE_TTL = 6 * 3600.0
# An address the service could not place (private range, reserved range) is not asked again for as long.
NEGATIVE_TTL = CACHE_TTL
# An address the service did not answer at all is retried after this long.
UNANSWERED_TTL = 300.0
# After a failed request the provider sleeps; MaxMind answers in the meantime.
FAILURE_BACKOFF = 300.0
TIMEOUT = 10.0

# Tests inject an httpx.MockTransport here.
_TRANSPORT: httpx.BaseTransport | None = None

_AS_RE = re.compile(r"^AS(\d+)\s*(.*)$")

# ip -> (expiry, record); a record with geo None and network None is an answered address the service could not place.
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
# Send times of the batch requests of the last minute (the local budget).
_requests: deque[float] = deque()
_lock = asyncio.Lock()
_paused_until = 0.0
_pause_reason: str | None = None
_last_attempt: float | None = None
_last_success: float | None = None
_last_error: str | None = None
_requests_total = 0
_addresses_total = 0


def enabled(settings: dict[str, Any]) -> bool:
    """The operator switched the provider on under Settings."""
    return bool(settings.get("ip_api_enabled"))


def ready() -> bool:
    """Not sleeping after a failure or an exhausted allowance: new addresses can be asked."""
    return time.time() >= _paused_until


def reset() -> None:
    """Forget everything (tests, and a fresh start after the provider was switched on again)."""
    global _paused_until, _pause_reason, _last_attempt, _last_success, _last_error, _requests_total, _addresses_total
    _cache.clear()
    _requests.clear()
    _paused_until = 0.0
    _pause_reason = None
    _last_attempt = _last_success = _last_error = None
    _requests_total = _addresses_total = 0


def wake() -> None:
    """End a pause early (the operator toggled the setting): the next lookup tries the service again."""
    global _paused_until, _pause_reason
    _paused_until = 0.0
    _pause_reason = None


def get(ip: str) -> dict[str, Any] | None:
    """The cached record of an address: {"geo", "network", "message"}; None when it has not been answered (yet)."""
    hit = _cache.get(ip)
    if hit is None:
        return None
    if hit[0] <= time.time():
        _cache.pop(ip, None)
        return None
    return hit[1]


def parse_result(item: dict[str, Any]) -> dict[str, Any]:
    """One entry of a batch answer reduced to the map's shape: geo (as `geoip.parse_record` builds it) and network."""
    if str(item.get("status") or "") != "success":
        return {"geo": None, "network": None, "message": str(item.get("message") or "no data")}
    lat, lon = item.get("lat"), item.get("lon")
    geo: dict[str, Any] | None = None
    if lat is not None and lon is not None:
        geo = {
            "lat": float(lat),
            "lon": float(lon),
            "city": str(item.get("city") or "").strip() or None,
            "region": str(item.get("regionName") or "").strip() or None,
            "country": str(item.get("country") or "").strip() or None,
            "country_code": str(item.get("countryCode") or "").strip() or None,
            # The service states no accuracy radius.
            "accuracy_km": None,
            "provider": PROVIDER,
        }
    network: dict[str, Any] | None = None
    m = _AS_RE.match(str(item.get("as") or "").strip())
    if m:
        name = m.group(2).strip() or str(item.get("asname") or "").strip()
        network = {"asn": f"AS{int(m.group(1))}", "name": name or None}
    return {"geo": geo, "network": network, "message": None}


_PRIVATE = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fc00::/7"),
)


def _is_public(ip: str) -> bool:
    """A syntactically valid address outside the private, loopback and link-local ranges (the same rule as `geoip.is_unroutable`)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return False
    return not any(addr in net for net in _PRIVATE)


def _requests_last_minute(now: float) -> int:
    while _requests and _requests[0] <= now - 60.0:
        _requests.popleft()
    return len(_requests)


def _pause(reason: str, seconds: float, *, error: bool) -> None:
    global _paused_until, _pause_reason, _last_error
    _paused_until = max(_paused_until, time.time() + seconds)
    _pause_reason = reason
    if error:
        _last_error = reason
        log.warning("ip-api.com lookups paused for %ds: %s", int(seconds), reason)
    else:
        log.info("ip-api.com lookups paused for %ds: %s", int(seconds), reason)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_TRANSPORT, timeout=TIMEOUT)


async def prefetch(ips: list[str]) -> None:
    """Ask the service, in batches of up to 100, for every public address in `ips` that is not cached.

    Never waits for the budget: an address that does not fit into this minute's allowance is simply left
    unanswered (the caller falls back to MaxMind, and the next poll asks again).
    """
    global _last_attempt, _last_success, _last_error, _requests_total, _addresses_total
    wanted = list(dict.fromkeys(ip for ip in ips if ip and _is_public(ip) and get(ip) is None))
    if not wanted or not ready():
        return
    async with _lock:
        # Another request may have answered some of them while this one waited for the lock.
        wanted = [ip for ip in wanted if get(ip) is None]
        for start in range(0, len(wanted), BATCH_SIZE):
            now = time.time()
            if not ready():
                return
            if _requests_last_minute(now) >= MAX_REQUESTS_PER_MINUTE:
                log.debug("ip-api.com budget of %d requests per minute spent; %d addresses wait", MAX_REQUESTS_PER_MINUTE, len(wanted) - start)
                return
            chunk = wanted[start : start + BATCH_SIZE]
            _requests.append(now)
            _last_attempt = now
            _requests_total += 1
            try:
                async with _client() as client:
                    resp = await client.post(BATCH_URL, params={"fields": FIELDS}, json=chunk)
            except httpx.HTTPError as exc:
                _pause(f"request failed: {exc.__class__.__name__}: {exc}", FAILURE_BACKOFF, error=True)
                return
            if resp.status_code == 429:
                _pause("rate limited by ip-api.com (HTTP 429)", _ttl_header(resp, FAILURE_BACKOFF), error=True)
                return
            if resp.status_code != 200:
                _pause(f"ip-api.com returned HTTP {resp.status_code}", FAILURE_BACKOFF, error=True)
                return
            try:
                data = resp.json()
            except ValueError:
                _pause("ip-api.com returned an unreadable answer", FAILURE_BACKOFF, error=True)
                return
            if not isinstance(data, list):
                _pause("ip-api.com returned an unexpected answer", FAILURE_BACKOFF, error=True)
                return
            answered = 0
            pending = set(chunk)
            for item in data:
                if not isinstance(item, dict):
                    continue
                query = str(item.get("query") or "")
                if query not in pending:
                    continue
                pending.discard(query)
                record = parse_result(item)
                _cache[query] = (now + (CACHE_TTL if record["geo"] or record["network"] else NEGATIVE_TTL), record)
                answered += 1
            for ip in pending:
                _cache[ip] = (now + UNANSWERED_TTL, {"geo": None, "network": None, "message": "no answer"})
            _addresses_total += answered
            _last_success = now
            _last_error = None
            remaining = resp.headers.get("X-Rl")
            if remaining is not None and remaining.strip().isdigit() and int(remaining) <= 0:
                # The service says the allowance is used up: nothing more until it resets.
                _pause("ip-api.com allowance used up for this minute", _ttl_header(resp, 60.0), error=False)
                return


def _ttl_header(resp: httpx.Response, default: float) -> float:
    value = (resp.headers.get("X-Ttl") or "").strip()
    return float(value) + 1.0 if value.isdigit() else default


def status(settings: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    paused = _paused_until > now
    return {
        "enabled": enabled(settings),
        "endpoint": BATCH_URL,
        "ready": not paused,
        "paused_until": _paused_until if paused else None,
        "pause_reason": _pause_reason if paused else None,
        "last_attempt": _last_attempt,
        "last_success": _last_success,
        "last_error": _last_error,
        "requests_last_minute": _requests_last_minute(now),
        "max_requests_per_minute": MAX_REQUESTS_PER_MINUTE,
        "batch_size": BATCH_SIZE,
        "requests_total": _requests_total,
        "addresses_total": _addresses_total,
        "cached": len(_cache),
    }
