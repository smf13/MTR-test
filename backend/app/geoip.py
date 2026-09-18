"""MaxMind GeoLite2 support: database download with the operator's licence key, cached lookups and path geolocation.

The GeoLite2 City database is fetched from MaxMind with the account's licence key (never bundled: its licence
forbids redistribution), stored under the data directory and refreshed weekly. Lookups turn the IPs of a run
(the monitoring host, every hop and the destination) into coordinates for the map on the target page.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import tarfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import config
from .resolver import resolve_host

try:
    import maxminddb
except ImportError:  # pragma: no cover - the dependency is listed in requirements.txt
    maxminddb = None  # type: ignore[assignment]

log = logging.getLogger("mtr-tracker.geoip")

EDITION = "GeoLite2-City"
# Account ID + licence key as HTTP basic auth (the documented permalink) ...
DOWNLOAD_URL = "https://download.maxmind.com/geoip/databases/{edition}/download?suffix=tar.gz"
# ... or the older endpoint that only needs the licence key.
LEGACY_DOWNLOAD_URL = "https://download.maxmind.com/app/geoip_download?edition_id={edition}&license_key={key}&suffix=tar.gz"
# MaxMind publishes GeoLite2 twice a week; a weekly refresh keeps well inside its download allowance.
REFRESH_AFTER = 7 * 86400.0
# After a failed download, wait this long before trying again on the scheduler's own initiative.
RETRY_AFTER = 3600.0
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
DOWNLOAD_TIMEOUT = 120.0
LOOKUP_CACHE_TTL = 3600.0
PUBLIC_IP_URLS = ("https://api.ipify.org", "https://checkip.amazonaws.com")
PUBLIC_IP_TTL = 3600.0
PUBLIC_IP_FAILURE_TTL = 600.0

# Tests inject an httpx.MockTransport here and force live behaviour in simulation mode.
_TRANSPORT: httpx.BaseTransport | None = None
_FORCE_LIVE = False


class GeoIpError(Exception):
    """A download or credential problem, worded for the operator."""


def _simulated() -> bool:
    return config.simulate and not _FORCE_LIVE


def db_path() -> Path:
    return config.data_dir / "geoip" / f"{EDITION}.mmdb"


def configured(settings: dict[str, Any]) -> bool:
    """A licence key is saved: the map is offered and the database is kept up to date."""
    return bool(str(settings.get("maxmind_license_key") or "").strip())


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

_reader: Any = None
_reader_mtime: float | None = None
_lookup_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_last_error: str | None = None
_last_attempt: float | None = None
_last_success: float | None = None
_update_lock = asyncio.Lock()
_refresh_task: asyncio.Task[None] | None = None
_public_ip: tuple[float, str | None] | None = None


def _open_reader(path: Path) -> Any:
    if maxminddb is None:
        raise GeoIpError("the maxminddb package is not installed")
    return maxminddb.open_database(str(path))


def reader() -> Any:
    """The open database, re-opened when the file on disk changed; None when there is no database."""
    global _reader, _reader_mtime
    path = db_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        if _reader is not None:
            _reader.close()
            _reader = None
            _reader_mtime = None
        return None
    if _reader is None or mtime != _reader_mtime:
        if _reader is not None:
            _reader.close()
            _reader = None
        try:
            _reader = _open_reader(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("cannot open %s: %s", path, exc)
            return None
        _reader_mtime = mtime
        _lookup_cache.clear()
    return _reader


def available() -> bool:
    """Lookups can be answered: a database is on disk, or simulation fabricates locations."""
    return _simulated() or reader() is not None


def build_time() -> float | None:
    r = reader()
    if r is None:
        return None
    try:
        return float(r.metadata().build_epoch)
    except Exception:  # noqa: BLE001
        return None


def status(settings: dict[str, Any]) -> dict[str, Any]:
    path = db_path()
    try:
        downloaded_at: float | None = path.stat().st_mtime
    except OSError:
        downloaded_at = None
    return {
        "configured": configured(settings),
        "available": available(),
        "simulated": _simulated(),
        "edition": EDITION,
        "path": str(path),
        "build_epoch": build_time(),
        "downloaded_at": downloaded_at,
        "last_attempt": _last_attempt,
        "last_success": _last_success,
        "last_error": _last_error,
        "updating": _update_lock.locked(),
        "refresh_after_sec": int(REFRESH_AFTER),
    }


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def is_unroutable(ip: str) -> bool:
    """RFC 1918, carrier-grade NAT, loopback and link-local addresses: never in a GeoIP database."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return True
    if addr.version == 4:
        return any(addr in net for net in _PRIVATE_V4)
    return addr in ipaddress.ip_network("fc00::/7")


_PRIVATE_V4 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
)


def parse_record(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    """A GeoLite2 City record reduced to what the map needs; None when it carries no coordinates."""
    if not rec:
        return None
    loc = rec.get("location") or {}
    lat, lon = loc.get("latitude"), loc.get("longitude")
    if lat is None or lon is None:
        return None
    subdivisions = rec.get("subdivisions") or []
    country = rec.get("country") or rec.get("registered_country") or {}
    return {
        "lat": float(lat),
        "lon": float(lon),
        "city": ((rec.get("city") or {}).get("names") or {}).get("en"),
        "region": ((subdivisions[0].get("names") or {}).get("en")) if subdivisions else None,
        "country": (country.get("names") or {}).get("en"),
        "country_code": country.get("iso_code"),
        "accuracy_km": loc.get("accuracy_radius"),
    }


def _simulated_geo(ip: str) -> dict[str, Any]:
    """A deterministic location per address so simulated paths draw a stable, plausible map."""
    seed = int(hashlib.md5(ip.encode()).hexdigest(), 16)
    city = _SIM_CITIES[seed % len(_SIM_CITIES)]
    # A small offset keeps two hops in the same city from landing on the same pixel.
    return {
        "lat": city["lat"] + ((seed >> 8) % 100 - 50) / 200.0,
        "lon": city["lon"] + ((seed >> 16) % 100 - 50) / 200.0,
        "city": city["city"],
        "region": None,
        "country": city["country"],
        "country_code": city["cc"],
        "accuracy_km": 50,
    }


_SIM_CITIES = [
    {"city": "Frankfurt", "country": "Germany", "cc": "DE", "lat": 50.1109, "lon": 8.6821},
    {"city": "Amsterdam", "country": "Netherlands", "cc": "NL", "lat": 52.3676, "lon": 4.9041},
    {"city": "London", "country": "United Kingdom", "cc": "GB", "lat": 51.5074, "lon": -0.1278},
    {"city": "Paris", "country": "France", "cc": "FR", "lat": 48.8566, "lon": 2.3522},
    {"city": "Ashburn", "country": "United States", "cc": "US", "lat": 39.0438, "lon": -77.4874},
    {"city": "New York", "country": "United States", "cc": "US", "lat": 40.7128, "lon": -74.0060},
    {"city": "San Jose", "country": "United States", "cc": "US", "lat": 37.3382, "lon": -121.8863},
    {"city": "Singapore", "country": "Singapore", "cc": "SG", "lat": 1.3521, "lon": 103.8198},
    {"city": "Tokyo", "country": "Japan", "cc": "JP", "lat": 35.6762, "lon": 139.6503},
    {"city": "Sydney", "country": "Australia", "cc": "AU", "lat": -33.8688, "lon": 151.2093},
    {"city": "São Paulo", "country": "Brazil", "cc": "BR", "lat": -23.5505, "lon": -46.6333},
    {"city": "Johannesburg", "country": "South Africa", "cc": "ZA", "lat": -26.2041, "lon": 28.0473},
]


def lookup(ip: str | None) -> dict[str, Any] | None:
    """Location of a public address, or None (private address, unknown address, no database)."""
    if not ip or is_unroutable(ip):
        return None
    now = time.time()
    cached = _lookup_cache.get(ip)
    if cached and cached[0] > now:
        return cached[1]
    geo: dict[str, Any] | None
    if _simulated():
        geo = _simulated_geo(ip)
    else:
        r = reader()
        if r is None:
            return None
        try:
            geo = parse_record(r.get(ip))
        except Exception as exc:  # noqa: BLE001
            log.debug("lookup of %s failed: %s", ip, exc)
            geo = None
    _lookup_cache[ip] = (now + LOOKUP_CACHE_TTL, geo)
    return geo


def locate(ip: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """(geo, note): the note explains a missing location in the operator's terms."""
    if not ip:
        return None, "no response"
    if is_unroutable(ip):
        return None, "private address"
    if not available():
        return None, "no database"
    geo = lookup(ip)
    return geo, None if geo else "not in database"


def _client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_TRANSPORT, follow_redirects=True, **kwargs)


async def public_ip() -> str | None:
    """The address this host is seen from, for locating a monitor that sits behind NAT (cached for an hour)."""
    global _public_ip
    now = time.time()
    if _public_ip and _public_ip[0] > now:
        return _public_ip[1]
    result: str | None = None
    async with _client(timeout=5.0) as client:
        for url in PUBLIC_IP_URLS:
            try:
                resp = await client.get(url)
            except httpx.HTTPError:
                continue
            text = resp.text.strip()
            if resp.status_code == 200 and text and not is_unroutable(text):
                try:
                    ipaddress.ip_address(text)
                except ValueError:
                    continue
                result = text
                break
    _public_ip = (now + (PUBLIC_IP_TTL if result else PUBLIC_IP_FAILURE_TTL), result)
    return result


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def _extract_mmdb(archive: Path, destination: Path) -> None:
    """Pull the .mmdb member out of MaxMind's tar.gz into `destination` (a temp path next to the real one)."""
    with tarfile.open(archive, "r:gz") as tar:
        member = next((m for m in tar.getmembers() if m.isfile() and m.name.endswith(".mmdb")), None)
        if member is None:
            raise GeoIpError("the MaxMind archive contains no .mmdb file")
        src = tar.extractfile(member)
        if src is None:
            raise GeoIpError("the MaxMind archive could not be read")
        with src, open(destination, "wb") as out:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)


def _verify(path: Path) -> float | None:
    """Open the freshly extracted database once; a corrupt file must never replace a working one."""
    r = _open_reader(path)
    try:
        meta = r.metadata()
        kind = str(getattr(meta, "database_type", EDITION))
        if "City" not in kind:
            raise GeoIpError(f"unexpected database type {kind}; {EDITION} is required for coordinates")
        return float(getattr(meta, "build_epoch", 0)) or None
    finally:
        r.close()


async def download(settings: dict[str, Any]) -> dict[str, Any]:
    """Fetch the current GeoLite2 City database with the saved credentials and swap it in atomically."""
    global _last_error, _last_attempt, _last_success
    key = str(settings.get("maxmind_license_key") or "").strip()
    account = str(settings.get("maxmind_account_id") or "").strip()
    if not key:
        raise GeoIpError("no MaxMind licence key is configured")
    if _update_lock.locked():
        raise GeoIpError("a database download is already in progress")
    async with _update_lock:
        _last_attempt = time.time()
        try:
            build = await _download_locked(key, account)
        except GeoIpError as exc:
            _last_error = str(exc)
            raise
        except Exception as exc:  # noqa: BLE001
            _last_error = f"download failed: {exc}"
            log.exception("GeoLite2 download failed")
            raise GeoIpError(_last_error) from exc
        _last_error = None
        _last_success = time.time()
        _lookup_cache.clear()
        log.info("GeoLite2 City database updated (build %s)", time.strftime("%Y-%m-%d", time.gmtime(build)) if build else "unknown")
        return status(settings)


async def _download_locked(key: str, account: str) -> float | None:
    target = db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = target.parent / f"{target.name}.tar.gz.part"
    extracted = target.parent / f"{target.name}.part"
    if account:
        url = DOWNLOAD_URL.format(edition=EDITION)
        auth: tuple[str, str] | None = (account, key)
    else:
        url = LEGACY_DOWNLOAD_URL.format(edition=EDITION, key=key)
        auth = None
    try:
        async with _client(timeout=DOWNLOAD_TIMEOUT, auth=auth) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code in (401, 403):
                    raise GeoIpError(f"MaxMind rejected the credentials (HTTP {resp.status_code}): check the account ID and licence key")
                if resp.status_code >= 400:
                    raise GeoIpError(f"MaxMind returned HTTP {resp.status_code}")
                size = 0
                with open(archive, "wb") as out:
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_DOWNLOAD_BYTES:
                            raise GeoIpError("the MaxMind download exceeded the size limit")
                        out.write(chunk)
    except httpx.HTTPError as exc:
        raise GeoIpError(f"MaxMind download failed: {exc}") from exc
    try:
        await asyncio.to_thread(_extract_mmdb, archive, extracted)
        build = await asyncio.to_thread(_verify, extracted)
        # Close the reader before the swap: the new file gets a new mtime and reader() re-opens it lazily.
        close()
        os.replace(extracted, target)
    except tarfile.TarError as exc:
        raise GeoIpError(f"the MaxMind archive is unreadable: {exc}") from exc
    finally:
        for p in (archive, extracted):
            try:
                p.unlink()
            except OSError:
                pass
    return build


def needs_refresh() -> bool:
    try:
        age = time.time() - db_path().stat().st_mtime
    except OSError:
        return True
    return age > REFRESH_AFTER


async def maybe_refresh(settings: dict[str, Any]) -> None:
    """Download when a key is configured and the database is missing or older than a week; errors are logged."""
    if not configured(settings) or _simulated() or not needs_refresh() or _update_lock.locked():
        return
    if _last_attempt and _last_error and time.time() - _last_attempt < RETRY_AFTER:
        return
    try:
        await download(settings)
    except GeoIpError as exc:
        log.warning("GeoLite2 refresh skipped: %s", exc)


def schedule_refresh(settings: dict[str, Any]) -> None:
    """Run maybe_refresh in the background (the scheduler's hourly tick and a settings save call this)."""
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return
    if not configured(settings) or _simulated() or not needs_refresh():
        return
    _refresh_task = asyncio.create_task(maybe_refresh(settings), name="mtr-tracker-geoip-refresh")


async def cancel_refresh() -> None:
    global _refresh_task
    task = _refresh_task
    _refresh_task = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def close() -> None:
    global _reader, _reader_mtime
    if _reader is not None:
        _reader.close()
        _reader = None
        _reader_mtime = None


# ---------------------------------------------------------------------------
# Path geolocation
# ---------------------------------------------------------------------------


def _point(geo: dict[str, Any] | None, note: str | None, **fields: Any) -> dict[str, Any]:
    return {**fields, "geo": geo, "note": note}


def _probe_point(probe: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """A Globalping probe carries its own coordinates; no database lookup is needed."""
    lat, lon = probe.get("latitude"), probe.get("longitude")
    if lat is None or lon is None:
        return None
    return _point(
        {"lat": float(lat), "lon": float(lon), "city": probe.get("city"), "region": None, "country": probe.get("country"), "country_code": probe.get("country"), "accuracy_km": None},
        None,
        kind=kind,
        label=str(probe.get("label") or probe.get("city") or "Globalping probe"),
        ip=None,
        evidence="coordinates reported by the Globalping probe itself",
    )


async def path_geo(target: dict[str, Any], run: dict[str, Any] | None, hops: list[dict[str, Any]], settings: dict[str, Any]) -> dict[str, Any]:
    """Sources (monitor or remote probes), hops and destination of a run with their locations."""
    out: dict[str, Any] = {"enabled": configured(settings), "available": available(), "run_id": None, "sources": [], "hops": [], "destination": None}
    if not out["enabled"] or run is None:
        return out
    out["run_id"] = run["id"]
    details = run.get("details") or {}
    if target.get("type") == "globalping":
        probes = [details["probe"]] if isinstance(details.get("probe"), dict) else [p for p in details.get("probes") or [] if isinstance(p, dict)]
        out["sources"] = [p for p in (_probe_point(pr, "probe") for pr in probes) if p]
    else:
        out["sources"] = [await _monitor_point(run.get("src"))]
    dst_ip, role, host, failure = await _destination(target, run)
    simulated_route = _simulated_route(out["sources"], dst_ip, hops) if _simulated() else {}
    for h in hops:
        if h.get("ip") in simulated_route:
            geo, note = simulated_route[h["ip"]], None
        else:
            geo, note = locate(h.get("ip"))
        out["hops"].append(_point(geo, note, hop_no=h["hop_no"], ip=h.get("ip"), hostname=h.get("hostname"), asn=h.get("asn"), avg_ms=h.get("avg_ms"), loss_pct=h.get("loss_pct")))
    if dst_ip:
        geo, note = locate(dst_ip)
        out["destination"] = _point(geo, note, ip=dst_ip, host=host, role=role, reached=bool(run.get("reached")))
    elif failure:
        out["destination"] = _point(None, failure, ip=None, host=host, role=role, reached=bool(run.get("reached")))
    return out


_RESOLVE_TTL = 600.0
_resolve_cache: dict[str, tuple[float, str | None]] = {}


async def _resolve_cached(host: str, ip_version: str) -> str | None:
    """Forward lookup for targets whose runs store no address (http, dns); cached so polling stays cheap."""
    key = f"{ip_version}:{host}"
    now = time.time()
    hit = _resolve_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    ip: str | None
    try:
        ip = await resolve_host(host, ip_version)
    except ValueError:
        # Simulation targets rarely resolve; give them the same stable made-up address the simulated probes use.
        ip = f"198.51.100.{int(hashlib.md5(host.encode()).hexdigest(), 16) % 254 + 1}" if _simulated() else None
    _resolve_cache[key] = (now + _RESOLVE_TTL, ip)
    return ip


def _looks_like_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


async def lookup_query(query: str, settings: dict[str, Any]) -> dict[str, Any]:
    """The GeoIP lookup page: an address, a host name (resolved first) or "self" (this server's public address).

    Always answers, even without a database, so the page can explain what is missing.
    """
    q = query.strip()
    out: dict[str, Any] = {
        "query": q, "host": None, "ip": None, "geo": None, "note": None, "kind": "address",
        "configured": configured(settings), "available": available(), "simulated": _simulated(), "build_epoch": build_time(),
    }
    if q.lower() in ("self", "me", "this server"):
        point = await _monitor_point(None)
        out.update({"kind": point["kind"], "ip": point["ip"], "geo": point["geo"], "note": point["note"], "host": point["label"]})
        return out
    if _looks_like_ip(q):
        ip: str | None = q
    else:
        out["kind"] = "host"
        out["host"] = q
        ip = await _resolve_cached(q, "auto")
        if not ip:
            out["note"] = "host could not be resolved"
            return out
    out["ip"] = ip
    out["geo"], out["note"] = locate(ip)
    return out


async def _destination(target: dict[str, Any], run: dict[str, Any]) -> tuple[str | None, str, str, str | None]:
    """(ip, role, host, failure note) of what the run talked to.

    Path, ping, tcp and Globalping ping/http runs store the address they probed. A local HTTP run stores none, so
    the URL's host is resolved here. A DNS check talks to a resolver: with one configured that is the destination
    ("resolver"); otherwise the first address in the answer stands in for the name being looked up ("answer").
    """
    kind = str(target.get("type") or "mtr")
    options = target.get("options") or {}
    details = run.get("details") or {}
    host = str(target.get("host") or "")
    ip_version = str(target.get("ip_version") or "auto")
    measurement = str(options.get("measurement") or "ping") if kind == "globalping" else kind
    if measurement == "dns":
        resolver = str(options.get("resolver") or "").strip()
        if resolver:
            ip = resolver if _looks_like_ip(resolver) else await _resolve_cached(resolver, "auto")
            return ip, "resolver", resolver, None if ip else "resolver could not be resolved"
        answers = details.get("answers") or []
        ip = next((str(a) for a in answers if isinstance(a, str) and _looks_like_ip(a)), None)
        return ip, "answer", host, None if ip else "no address in the answer"
    dst_ip = run.get("dst_ip")
    if dst_ip:
        return str(dst_ip), "target", host, None
    if measurement == "http":
        parts = urlsplit(host if "://" in host else f"https://{host}")
        host = parts.hostname or host
    ip = await _resolve_cached(host, ip_version)
    return ip, "target", host, None if ip else "host could not be resolved"


def _simulated_route(sources: list[dict[str, Any]], dst_ip: str | None, hops: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Simulation only: public hops are spread along the way from the monitor to the destination's city.

    Per-address hashes alone would scatter one path over three continents; a plausible progression makes the
    demo map readable. The destination itself keeps its per-address location so it stays stable across runs.
    """
    start = next((s["geo"] for s in sources if s.get("geo")), None)
    if start is None or not dst_ip:
        return {}
    end = lookup(dst_ip) or start
    public = [h["ip"] for h in hops if h.get("ip") and h["ip"] != dst_ip and not is_unroutable(h["ip"])]
    route: dict[str, dict[str, Any]] = {}
    for i, ip in enumerate(public):
        f = (i + 1) / (len(public) + 1)
        seed = int(hashlib.md5(ip.encode()).hexdigest(), 16)
        lat = start["lat"] + (end["lat"] - start["lat"]) * f + ((seed >> 8) % 100 - 50) / 40.0
        lon = start["lon"] + (end["lon"] - start["lon"]) * f + ((seed >> 16) % 100 - 50) / 40.0
        nearest = min(_SIM_CITIES, key=lambda c: (c["lat"] - lat) ** 2 + (c["lon"] - lon) ** 2)
        route[ip] = {"lat": round(lat, 4), "lon": round(lon, 4), "city": nearest["city"], "region": None, "country": nearest["country"], "country_code": nearest["cc"], "accuracy_km": 100}
    return route


async def _monitor_point(src: str | None) -> dict[str, Any]:
    """Where this server runs from: its own address when public, otherwise the address it is seen from."""
    if _simulated():
        return _point({"lat": 52.52, "lon": 13.405, "city": "Berlin", "region": None, "country": "Germany", "country_code": "DE", "accuracy_km": 50}, None, kind="simulated", label="This server (simulated)", ip=src, evidence="simulated location")
    if src and not is_unroutable(src):
        geo, note = locate(src)
        return _point(geo, note, kind="monitor", label="This server", ip=src, evidence=f"placed by its own address {src}")
    ip = await public_ip()
    if not ip:
        return _point(None, "public address unknown", kind="monitor", label="This server", ip=src, evidence="the public address could not be determined")
    geo, note = locate(ip)
    return _point(geo, note, kind="public_ip", label="This server (public address)", ip=ip, evidence=f"placed by the public address it is seen from, {ip}, not by its own address {src or 'unknown'}")
