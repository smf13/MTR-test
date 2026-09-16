"""Forward and reverse DNS helpers with a small TTL cache."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time

_REVERSE_CACHE: dict[str, tuple[float, str | None]] = {}
_REVERSE_TTL = 3600.0
_REVERSE_TIMEOUT = 2.0


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


async def resolve_host(host: str, ip_version: str = "auto") -> str:
    """Resolve a hostname to a single IP honouring the requested family."""
    if is_ip(host):
        addr = ipaddress.ip_address(host)
        if ip_version == "4" and addr.version != 4:
            raise ValueError(f"{host} is not an IPv4 address")
        if ip_version == "6" and addr.version != 6:
            raise ValueError(f"{host} is not an IPv6 address")
        return host

    family = {"4": socket.AF_INET, "6": socket.AF_INET6}.get(ip_version, socket.AF_UNSPEC)
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, family=family, type=socket.SOCK_RAW),
            timeout=8.0,
        )
    except asyncio.TimeoutError as exc:
        raise ValueError(f"DNS lookup for {host} timed out") from exc
    except socket.gaierror as exc:
        raise ValueError(f"DNS lookup for {host} failed: {exc.strerror or exc}") from exc
    if not infos:
        raise ValueError(f"DNS lookup for {host} returned no addresses")

    # Prefer IPv4 when 'auto' so behaviour matches what most operators expect.
    if ip_version == "auto":
        infos.sort(key=lambda i: 0 if i[0] == socket.AF_INET else 1)
    return infos[0][4][0]


async def reverse_lookup(ip: str | None) -> str | None:
    if not ip or not is_ip(ip):
        return None
    now = time.time()
    cached = _REVERSE_CACHE.get(ip)
    if cached and cached[0] > now:
        return cached[1]

    loop = asyncio.get_running_loop()
    name: str | None = None
    # Shield the lookup: cancelling uvloop's getnameinfo future on timeout makes
    # its completion callback raise InvalidStateError later. Let it finish quietly.
    inner = asyncio.ensure_future(loop.getnameinfo((ip, 0), socket.NI_NAMEREQD))
    inner.add_done_callback(lambda f: f.cancelled() or f.exception())
    try:
        result = await asyncio.wait_for(asyncio.shield(inner), timeout=_REVERSE_TIMEOUT)
        name = result[0] if result and result[0] != ip else None
    except (asyncio.TimeoutError, socket.gaierror, OSError):
        name = None
    _REVERSE_CACHE[ip] = (now + _REVERSE_TTL, name)
    return name


async def reverse_lookup_many(ips: list[str | None]) -> dict[str, str | None]:
    unique = sorted({ip for ip in ips if ip and is_ip(ip)})
    results = await asyncio.gather(*(reverse_lookup(ip) for ip in unique))
    return dict(zip(unique, results))
