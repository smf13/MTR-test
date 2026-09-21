"""GeoIP: MaxMind GeoLite2 databases (downloaded with the operator's licence key), ip-api.com, cached lookups and path geolocation.

The GeoLite2 City and GeoLite2 ASN databases are fetched from MaxMind with the account's licence key (never
bundled: their licence forbids redistribution), stored under the data directory and refreshed weekly. With the
ip-api.com switch on, that service is asked first (in batches, see `ipapi.py`) and the databases answer whatever
it could not. Lookups turn the IPs of a run (the monitoring host, every hop and the destination) into coordinates
and network names (autonomous system number and organisation) for the map on the target page.
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

from . import ipapi
from .config import config
from .resolver import resolve_host

try:
    import maxminddb
except ImportError:  # pragma: no cover - the dependency is listed in requirements.txt
    maxminddb = None  # type: ignore[assignment]

log = logging.getLogger("mtr-tracker.geoip")

EDITION = "GeoLite2-City"
# The ASN edition names the network (autonomous system) that announces an address; the map prints it per hop.
ASN_EDITION = "GeoLite2-ASN"
EDITIONS = (EDITION, ASN_EDITION)
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


def db_path(edition: str = EDITION) -> Path:
    return config.data_dir / "geoip" / f"{edition}.mmdb"


def maxmind_configured(settings: dict[str, Any]) -> bool:
    """A licence key is saved: the GeoLite2 databases are downloaded and kept up to date."""
    return bool(str(settings.get("maxmind_license_key") or "").strip())


def configured(settings: dict[str, Any]) -> bool:
    """Some provider is set up (ip-api.com switched on, or a MaxMind licence key saved): the map is offered."""
    return maxmind_configured(settings) or ipapi.enabled(settings)


def _ip_api_on(settings: dict[str, Any] | None) -> bool:
    """ip-api.com is asked first; never in simulation mode, which fabricates locations without any network call."""
    return bool(settings) and ipapi.enabled(settings) and not _simulated()


async def _prefetch(ips: list[str | None], settings: dict[str, Any] | None) -> None:
    """One batched ip-api.com request for every public address of a run that is not cached yet."""
    if _ip_api_on(settings):
        await ipapi.prefetch([ip for ip in ips if ip and not is_unroutable(ip)])


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

# One open reader per edition, with the mtime of the file it was opened from.
_readers: dict[str, tuple[Any, float]] = {}
_lookup_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_network_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
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


def _close_edition(edition: str) -> None:
    opened = _readers.pop(edition, None)
    if opened is not None:
        opened[0].close()
    (_lookup_cache if edition == EDITION else _network_cache).clear()


def reader(edition: str = EDITION) -> Any:
    """The open database of one edition, re-opened when the file on disk changed; None when there is none."""
    path = db_path(edition)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _close_edition(edition)
        return None
    opened = _readers.get(edition)
    if opened is None or mtime != opened[1]:
        _close_edition(edition)
        try:
            r = _open_reader(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("cannot open %s: %s", path, exc)
            return None
        _readers[edition] = (r, mtime)
        return r
    return opened[0]


def _maxmind_available(edition: str = EDITION) -> bool:
    """The edition is on disk, or simulation fabricates its answers."""
    return _simulated() or reader(edition) is not None


def available(settings: dict[str, Any] | None = None) -> bool:
    """Location lookups can be answered: ip-api.com is on and awake, the City database is on disk, or simulation."""
    return _maxmind_available() or (_ip_api_on(settings) and ipapi.ready())


def asn_available(settings: dict[str, Any] | None = None) -> bool:
    """Network names can be answered: ip-api.com is on and awake, the ASN database is on disk, or simulation."""
    return _maxmind_available(ASN_EDITION) or (_ip_api_on(settings) and ipapi.ready())


def build_time(edition: str = EDITION) -> float | None:
    r = reader(edition)
    if r is None:
        return None
    try:
        return float(r.metadata().build_epoch)
    except Exception:  # noqa: BLE001
        return None


def _downloaded_at(edition: str) -> float | None:
    try:
        return db_path(edition).stat().st_mtime
    except OSError:
        return None


def status(settings: dict[str, Any]) -> dict[str, Any]:
    """The MaxMind side (`configured` = key saved, `available` = City on disk) plus the ip-api.com provider under `ip_api`."""
    path = db_path()
    return {
        "configured": maxmind_configured(settings),
        "available": _maxmind_available(),
        "simulated": _simulated(),
        "edition": EDITION,
        "path": str(path),
        "build_epoch": build_time(),
        "downloaded_at": _downloaded_at(EDITION),
        "asn_available": _maxmind_available(ASN_EDITION),
        "asn_edition": ASN_EDITION,
        "asn_build_epoch": build_time(ASN_EDITION),
        "asn_downloaded_at": _downloaded_at(ASN_EDITION),
        "last_attempt": _last_attempt,
        "last_success": _last_success,
        "last_error": _last_error,
        "updating": _update_lock.locked(),
        "refresh_after_sec": int(REFRESH_AFTER),
        "ip_api": {**ipapi.status(settings), "simulated": _simulated()},
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
        "provider": "GeoLite2",
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
        "provider": "simulated",
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


def lookup(ip: str | None, settings: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Location of a public address, or None (private address, unknown address, no provider).

    With ip-api.com switched on its cached answer wins (see `_prefetch`); the GeoLite2 City database answers
    when the service could not place the address, has not answered it, or is paused after a failure.
    """
    if not ip or is_unroutable(ip):
        return None
    if _ip_api_on(settings):
        rec = ipapi.get(ip)
        if rec and rec["geo"]:
            return rec["geo"]
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


def _missing_note(ip: str, settings: dict[str, Any] | None, edition: str) -> str:
    """Why neither provider placed (or named) a public address."""
    if _maxmind_available(edition):
        return "not in database"
    if _ip_api_on(settings):
        rec = ipapi.get(ip)
        if rec is not None:
            return "not in database"
        return "ip-api.com unavailable" if not ipapi.ready() else "lookup pending"
    return "no database"


def locate(ip: str | None, settings: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """(geo, note): the note explains a missing location in the operator's terms."""
    if not ip:
        return None, "no response"
    if is_unroutable(ip):
        return None, "private address"
    geo = lookup(ip, settings)
    if geo:
        return geo, None
    return None, _missing_note(ip, settings, EDITION)


def parse_network(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    """A GeoLite2 ASN record reduced to the number ("AS13335") and the organisation name; None without a number."""
    if not rec:
        return None
    number = rec.get("autonomous_system_number")
    if number is None:
        return None
    name = str(rec.get("autonomous_system_organization") or "").strip()
    return {"asn": f"AS{int(number)}", "name": name or None}


# Real operators behind the numbers the simulator hands out, so the demo map reads like a live one.
_SIM_AS_NAMES = {
    "AS13335": "Cloudflare, Inc.",
    "AS15169": "Google LLC",
    "AS3356": "Level 3 Parent, LLC",
    "AS174": "Cogent Communications",
    "AS6939": "Hurricane Electric LLC",
    "AS1299": "Arelion Sweden AB",
    "AS2914": "NTT America, Inc.",
    "AS7018": "AT&T Services, Inc.",
    "AS3257": "GTT Communications Inc.",
    "AS24940": "Hetzner Online GmbH",
    "AS14618": "Amazon.com, Inc.",
    "AS16509": "Amazon.com, Inc.",
    "AS20473": "The Constant Company, LLC",
    "AS262287": "Latitude.sh LTDA",
}


def _simulated_network(ip: str, asn: str | None) -> dict[str, Any]:
    """A stable network per address; a number the simulated hop already carries keeps its matching name."""
    if asn in _SIM_AS_NAMES:
        return {"asn": asn, "name": _SIM_AS_NAMES[asn]}
    seed = int(hashlib.md5(ip.encode()).hexdigest(), 16)
    number = list(_SIM_AS_NAMES)[(seed >> 24) % len(_SIM_AS_NAMES)]
    return {"asn": number, "name": _SIM_AS_NAMES[number]}


def network(ip: str | None, asn: str | None = None, settings: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """{"asn", "name"} of the autonomous system announcing a public address, or None (private, unknown, no provider).

    `asn` is the number the probe itself reported for the address, if any; simulation keeps the fabricated name
    consistent with it, a real provider ignores it. ip-api.com's cached answer wins when the switch is on; the
    GeoLite2 ASN database answers the rest.
    """
    if not ip or is_unroutable(ip):
        return None
    if _ip_api_on(settings):
        rec = ipapi.get(ip)
        if rec and rec["network"]:
            return rec["network"]
    now = time.time()
    cached = _network_cache.get(ip)
    if cached and cached[0] > now:
        return cached[1]
    net: dict[str, Any] | None
    if _simulated():
        net = _simulated_network(ip, asn)
    else:
        r = reader(ASN_EDITION)
        if r is None:
            return None
        try:
            net = parse_network(r.get(ip))
        except Exception as exc:  # noqa: BLE001
            log.debug("network lookup of %s failed: %s", ip, exc)
            net = None
    _network_cache[ip] = (now + LOOKUP_CACHE_TTL, net)
    return net


def _network_fields(ip: str | None, asn: str | None = None, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """`asn` and `as_name` for a point: the probe's own number when it reported one, the provider's otherwise.

    The name is printed only when it belongs to the number shown, so a hop whose mtr-reported ASN disagrees with
    the provider keeps its number and gets no name rather than a misleading one.
    """
    net = network(ip, asn, settings)
    if net is None:
        return {"asn": asn, "as_name": None}
    if asn and asn != net["asn"]:
        return {"asn": asn, "as_name": None}
    return {"asn": net["asn"], "as_name": net["name"]}


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


def _verify(path: Path, edition: str) -> float | None:
    """Open the freshly extracted database once; a corrupt or wrong-edition file must never replace a working one."""
    r = _open_reader(path)
    try:
        meta = r.metadata()
        kind = str(getattr(meta, "database_type", edition))
        expected = edition.split("-", 1)[1]
        if expected not in kind:
            raise GeoIpError(f"unexpected database type {kind}; {edition} was requested")
        return float(getattr(meta, "build_epoch", 0)) or None
    finally:
        r.close()


async def download(settings: dict[str, Any]) -> dict[str, Any]:
    """Fetch the current GeoLite2 City and ASN databases with the saved credentials and swap each in atomically.

    The City database comes first (it is what draws the map); the ASN database only adds network names, so a
    failure there leaves a fresh City database in place and reports the error.
    """
    global _last_error, _last_attempt, _last_success
    key = str(settings.get("maxmind_license_key") or "").strip()
    account = str(settings.get("maxmind_account_id") or "").strip()
    if not key:
        raise GeoIpError("no MaxMind licence key is configured")
    if _update_lock.locked():
        raise GeoIpError("a database download is already in progress")
    async with _update_lock:
        _last_attempt = time.time()
        for edition in EDITIONS:
            try:
                build = await _download_locked(key, account, edition)
            except GeoIpError as exc:
                _last_error = f"{edition}: {exc}"
                raise GeoIpError(_last_error) from exc
            except Exception as exc:  # noqa: BLE001
                _last_error = f"{edition}: download failed: {exc}"
                log.exception("%s download failed", edition)
                raise GeoIpError(_last_error) from exc
            log.info("%s database updated (build %s)", edition, time.strftime("%Y-%m-%d", time.gmtime(build)) if build else "unknown")
        _last_error = None
        _last_success = time.time()
        _lookup_cache.clear()
        _network_cache.clear()
        return status(settings)


async def _download_locked(key: str, account: str, edition: str) -> float | None:
    target = db_path(edition)
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = target.parent / f"{target.name}.tar.gz.part"
    extracted = target.parent / f"{target.name}.part"
    if account:
        url = DOWNLOAD_URL.format(edition=edition)
        auth: tuple[str, str] | None = (account, key)
    else:
        url = LEGACY_DOWNLOAD_URL.format(edition=edition, key=key)
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
        build = await asyncio.to_thread(_verify, extracted, edition)
        # Close the reader before the swap: the new file gets a new mtime and reader() re-opens it lazily.
        _close_edition(edition)
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
    """Either database is missing or older than a week (an installation from before the ASN edition lacks it)."""
    for edition in EDITIONS:
        try:
            age = time.time() - db_path(edition).stat().st_mtime
        except OSError:
            return True
        if age > REFRESH_AFTER:
            return True
    return False


async def maybe_refresh(settings: dict[str, Any]) -> None:
    """Download when a key is configured and a database is missing or older than a week; errors are logged."""
    if not maxmind_configured(settings) or _simulated() or not needs_refresh() or _update_lock.locked():
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
    if not maxmind_configured(settings) or _simulated() or not needs_refresh():
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
    for edition in list(_readers):
        _close_edition(edition)


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
        {"lat": float(lat), "lon": float(lon), "city": probe.get("city"), "region": None, "country": probe.get("country"), "country_code": probe.get("country"), "accuracy_km": None, "provider": "Globalping"},
        None,
        kind=kind,
        label=str(probe.get("label") or probe.get("city") or "Globalping probe"),
        ip=None,
        evidence="coordinates reported by the Globalping probe itself",
        asn=f"AS{probe['asn']}" if probe.get("asn") else None,
        as_name=str(probe.get("network") or "").strip() or None,
    )


async def path_geo(target: dict[str, Any], run: dict[str, Any] | None, hops: list[dict[str, Any]], settings: dict[str, Any]) -> dict[str, Any]:
    """Sources (monitor or remote probes), hops and destination of a run with their locations and networks."""
    out: dict[str, Any] = {"enabled": configured(settings), "ip_api_enabled": ipapi.enabled(settings), "available": available(settings), "asn_available": asn_available(settings), "run_id": None, "sources": [], "hops": [], "destination": None}
    if not out["enabled"] or run is None:
        return out
    out["run_id"] = run["id"]
    details = run.get("details") or {}
    remote = target.get("type") == "globalping"
    dst_ip, role, host, failure = await _destination(target, run)
    # One ip-api.com request for the whole run: the monitor, every hop and the destination.
    wanted: list[str | None] = [h.get("ip") for h in hops] + [dst_ip]
    if not remote:
        src = run.get("src")
        if src and not is_unroutable(src):
            wanted.append(src)
        elif _ip_api_on(settings):
            # Behind NAT the monitor is placed by its public address (cached, so _monitor_point asks no second time).
            wanted.append(await public_ip())
    await _prefetch(wanted, settings)
    out["available"], out["asn_available"] = available(settings), asn_available(settings)
    if remote:
        probes = [details["probe"]] if isinstance(details.get("probe"), dict) else [p for p in details.get("probes") or [] if isinstance(p, dict)]
        out["sources"] = [p for p in (_probe_point(pr, "probe") for pr in probes) if p]
    else:
        out["sources"] = [await _monitor_point(run.get("src"), settings)]
    simulated_route = _simulated_route(out["sources"], dst_ip, hops) if _simulated() else {}
    for h in hops:
        if h.get("ip") in simulated_route:
            geo, note = simulated_route[h["ip"]], None
        else:
            geo, note = locate(h.get("ip"), settings)
        out["hops"].append(_point(geo, note, hop_no=h["hop_no"], ip=h.get("ip"), hostname=h.get("hostname"), **_network_fields(h.get("ip"), h.get("asn"), settings), avg_ms=h.get("avg_ms"), loss_pct=h.get("loss_pct")))
    if dst_ip:
        geo, note = locate(dst_ip, settings)
        dst_asn = next((h.get("asn") for h in hops if h.get("ip") == dst_ip and h.get("asn")), None)
        out["destination"] = _point(geo, note, ip=dst_ip, host=host, role=role, reached=bool(run.get("reached")), **_network_fields(dst_ip, dst_asn, settings))
    elif failure:
        out["destination"] = _point(None, failure, ip=None, host=host, role=role, reached=bool(run.get("reached")), asn=None, as_name=None)
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
        "query": q, "host": None, "ip": None, "geo": None, "note": None, "kind": "address", "asn": None, "as_name": None,
        "configured": configured(settings), "available": available(settings), "asn_available": asn_available(settings), "simulated": _simulated(), "build_epoch": build_time(),
        "ip_api_enabled": ipapi.enabled(settings), "ip_api_ready": _ip_api_on(settings) and ipapi.ready(), "maxmind_configured": maxmind_configured(settings),
    }
    if q.lower() in ("self", "me", "this server"):
        point = await _monitor_point(None, settings)
        out["available"], out["asn_available"], out["ip_api_ready"] = available(settings), asn_available(settings), _ip_api_on(settings) and ipapi.ready()
        out.update({k: point[k] for k in ("kind", "ip", "geo", "note", "asn", "as_name")})
        out["host"] = point["label"]
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
    await _prefetch([ip], settings)
    out["available"], out["asn_available"], out["ip_api_ready"] = available(settings), asn_available(settings), _ip_api_on(settings) and ipapi.ready()
    out["geo"], out["note"] = locate(ip, settings)
    out.update(_network_fields(ip, None, settings))
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
        route[ip] = {"lat": round(lat, 4), "lon": round(lon, 4), "city": nearest["city"], "region": None, "country": nearest["country"], "country_code": nearest["cc"], "accuracy_km": 100, "provider": "simulated"}
    return route


async def _monitor_point(src: str | None, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Where this server runs from: its own address when public, otherwise the address it is seen from."""
    if _simulated():
        return _point({"lat": 52.52, "lon": 13.405, "city": "Berlin", "region": None, "country": "Germany", "country_code": "DE", "accuracy_km": 50, "provider": "simulated"}, None, kind="simulated", label="This server (simulated)", ip=src, evidence="simulated location", asn="AS24940", as_name=_SIM_AS_NAMES["AS24940"])
    if src and not is_unroutable(src):
        await _prefetch([src], settings)
        geo, note = locate(src, settings)
        return _point(geo, note, kind="monitor", label="This server", ip=src, evidence=f"placed by its own address {src}", **_network_fields(src, None, settings))
    ip = await public_ip()
    if not ip:
        return _point(None, "public address unknown", kind="monitor", label="This server", ip=src, evidence="the public address could not be determined", asn=None, as_name=None)
    await _prefetch([ip], settings)
    geo, note = locate(ip, settings)
    # mtr reports the local host by name, not by address, so say which it is.
    own = "unknown" if not src else f"address {src}" if _looks_like_ip(src) else f"host name {src}"
    return _point(geo, note, kind="public_ip", label="This server (public address)", ip=ip, evidence=f"placed by the public address it is seen from, {ip}, not by its own {own}", **_network_fields(ip, None, settings))
