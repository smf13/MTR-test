"""MaxMind GeoLite2 support: record parsing, simulated locations, the download flow (HTTP mocked) and the geo endpoint."""

from __future__ import annotations

import io
import tarfile
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from helpers import wait_for_runs

CITY_RECORD = {
    "city": {"names": {"en": "Frankfurt"}},
    "subdivisions": [{"names": {"en": "Hesse"}}],
    "country": {"iso_code": "DE", "names": {"en": "Germany"}},
    "location": {"latitude": 50.1, "longitude": 8.7, "accuracy_radius": 20},
}


def test_parse_record_and_unroutable() -> None:
    from app import geoip

    geo = geoip.parse_record(CITY_RECORD)
    assert geo == {"lat": 50.1, "lon": 8.7, "city": "Frankfurt", "region": "Hesse", "country": "Germany", "country_code": "DE", "accuracy_km": 20}
    assert geoip.parse_record({"country": {"iso_code": "DE"}}) is None
    assert geoip.parse_record(None) is None
    for ip in ("10.1.2.3", "172.16.0.1", "192.168.1.1", "100.64.0.9", "127.0.0.1", "169.254.1.1", "fd00::1", "not-an-ip"):
        assert geoip.is_unroutable(ip), ip
    for ip in ("8.8.8.8", "203.0.113.5", "2001:db8::1"):
        assert not geoip.is_unroutable(ip), ip


async def test_geo_endpoint_in_simulation(client: AsyncClient) -> None:
    r = await client.post("/api/targets", json={"name": "Geo", "host": "192.0.2.30", "interval_sec": 60, "count": 3})
    t = r.json()
    await wait_for_runs(client, t["id"], 1)

    off = (await client.get(f"/api/targets/{t['id']}/geo")).json()
    assert off["enabled"] is False and off["sources"] == [] and off["hops"] == []
    status = (await client.get("/api/geoip/status")).json()
    assert status["configured"] is False and status["simulated"] is True

    assert (await client.put("/api/settings", json={"maxmind_account_id": "12x"})).status_code == 422
    saved = (await client.put("/api/settings", json={"maxmind_license_key": " key_abc ", "maxmind_account_id": " 123456 "})).json()
    assert saved["maxmind_license_key"] == "key_abc" and saved["maxmind_account_id"] == "123456"
    assert (await client.get("/api/geoip/status")).json()["configured"] is True

    geo = (await client.get(f"/api/targets/{t['id']}/geo")).json()
    assert geo["enabled"] is True and geo["available"] is True and geo["run_id"]
    assert geo["sources"][0]["kind"] == "simulated" and geo["sources"][0]["geo"]["city"] == "Berlin"
    assert geo["hops"][0]["ip"] == "192.168.1.1" and geo["hops"][0]["geo"] is None and geo["hops"][0]["note"] == "private address"
    located = [h for h in geo["hops"] if h["geo"]]
    assert located and all(-90 <= h["geo"]["lat"] <= 90 and -180 <= h["geo"]["lon"] <= 180 for h in located)
    assert geo["destination"]["ip"] == "192.0.2.30" and geo["destination"]["geo"]["country_code"] and geo["destination"]["reached"] is True
    assert {"hop_no", "ip", "hostname", "asn", "avg_ms", "loss_pct", "geo", "note"} <= set(geo["hops"][0])

    assert (await client.get("/api/targets/999999/geo")).status_code == 404


def _archive(member: str = "GeoLite2-City_20260901/GeoLite2-City.mmdb", content: bytes = b"fake-mmdb") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(member)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class FakeReader:
    """Stands in for maxminddb.Reader: every public address except the destination is in Frankfurt."""

    def __init__(self, path: str):
        self.path = path
        assert open(path, "rb").read() == b"fake-mmdb"

    def get(self, ip: str) -> dict[str, Any] | None:
        return None if ip == "192.0.2.31" else CITY_RECORD

    def metadata(self) -> Any:
        return SimpleNamespace(database_type="GeoLite2-City", build_epoch=1_756_684_800)

    def close(self) -> None:
        pass


async def test_download_and_live_lookups(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import geoip

    r = await client.post("/api/targets", json={"name": "Live", "host": "192.0.2.31", "interval_sec": 60, "count": 3})
    t = r.json()
    await wait_for_runs(client, t["id"], 1)

    assert (await client.post("/api/geoip/update")).status_code == 400
    # Saved while still simulated, so the save does not start a background download of its own.
    await client.put("/api/settings", json={"maxmind_license_key": "lic_key", "maxmind_account_id": "4242"})

    seen: list[httpx.Request] = []
    responses: dict[str, httpx.Response] = {"download": httpx.Response(200, content=_archive())}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "download.maxmind.com":
            return responses["download"]
        return httpx.Response(200, text="198.51.100.7\n")

    monkeypatch.setattr(geoip, "_FORCE_LIVE", True)
    monkeypatch.setattr(geoip, "_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(geoip, "_open_reader", lambda path: FakeReader(str(path)))
    geoip.close()

    before = (await client.get("/api/geoip/status")).json()
    assert before["configured"] is True and before["available"] is False and before["simulated"] is False
    no_db = (await client.get(f"/api/targets/{t['id']}/geo")).json()
    assert no_db["enabled"] is True and no_db["available"] is False and no_db["destination"]["note"] == "no database"

    responses["download"] = httpx.Response(401, text="Invalid license key")
    r = await client.post("/api/geoip/update")
    assert r.status_code == 502 and "credentials" in r.json()["detail"]
    assert (await client.get("/api/geoip/status")).json()["last_error"].startswith("MaxMind rejected")

    responses["download"] = httpx.Response(200, content=_archive())
    r = await client.post("/api/geoip/update")
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["available"] is True and st["last_error"] is None and st["build_epoch"] == "2025-09-01T00:00:00Z" and st["downloaded_at"]
    assert geoip.db_path().read_bytes() == b"fake-mmdb"
    dl = [q for q in seen if q.url.host == "download.maxmind.com"][-1]
    assert dl.url.path == "/geoip/databases/GeoLite2-City/download" and dl.headers.get("authorization", "").startswith("Basic ")

    geo = (await client.get(f"/api/targets/{t['id']}/geo")).json()
    src = geo["sources"][0]
    assert src["kind"] == "public_ip" and src["ip"] == "198.51.100.7" and src["geo"]["city"] == "Frankfurt"
    public_hops = [h for h in geo["hops"] if h["ip"] and not geoip.is_unroutable(h["ip"]) and h["ip"] != "192.0.2.31"]
    assert all(h["geo"]["country_code"] == "DE" for h in public_hops)
    assert geo["hops"][0]["note"] == "private address"
    assert geo["destination"]["geo"] is None and geo["destination"]["note"] == "not in database"

    # Without an account ID the legacy endpoint carries the key in the query string instead of basic auth.
    await client.put("/api/settings", json={"maxmind_account_id": ""})
    assert (await client.post("/api/geoip/update")).status_code == 200
    dl = [q for q in seen if q.url.host == "download.maxmind.com"][-1]
    assert dl.url.path == "/app/geoip_download" and dl.url.params["license_key"] == "lic_key" and "authorization" not in dl.headers

    # A broken archive never replaces the working database.
    responses["download"] = httpx.Response(200, content=b"not a tarball")
    r = await client.post("/api/geoip/update")
    assert r.status_code == 502 and "unreadable" in r.json()["detail"]
    assert geoip.db_path().read_bytes() == b"fake-mmdb" and (await client.get("/api/geoip/status")).json()["available"] is True


async def test_license_key_is_masked_without_the_token(protected_client: AsyncClient) -> None:
    auth = {"Authorization": "Bearer s3cret"}
    saved = (await protected_client.put("/api/settings", json={"maxmind_license_key": "abcdefghijkl", "maxmind_account_id": "77"}, headers=auth)).json()
    assert saved["maxmind_license_key"] == "abcdefghijkl"
    masked = (await protected_client.get("/api/settings")).json()
    assert masked["maxmind_license_key"] == "********ijkl" and masked["maxmind_account_id"] == "77"
    echoed = (await protected_client.put("/api/settings", json=masked, headers=auth)).json()
    assert echoed["maxmind_license_key"] == "abcdefghijkl"
