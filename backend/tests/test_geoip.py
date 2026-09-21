"""MaxMind GeoLite2 support: record parsing, simulated locations and networks, the download flow (HTTP mocked) and the geo endpoint."""

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


ASN_RECORD = {"autonomous_system_number": 64500, "autonomous_system_organization": "Example Transit GmbH"}


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
    assert geoip.parse_network(ASN_RECORD) == {"asn": "AS64500", "name": "Example Transit GmbH"}
    assert geoip.parse_network({"autonomous_system_number": 7, "autonomous_system_organization": " "}) == {"asn": "AS7", "name": None}
    assert geoip.parse_network({"autonomous_system_organization": "no number"}) is None
    assert geoip.parse_network(None) is None


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
    assert {"hop_no", "ip", "hostname", "asn", "as_name", "avg_ms", "loss_pct", "geo", "note"} <= set(geo["hops"][0])
    # Networks: simulation names the operator behind the number the simulated hop already carries; private hops get none.
    assert geo["asn_available"] is True
    assert geo["hops"][0]["asn"] is None and geo["hops"][0]["as_name"] is None
    named = [h for h in geo["hops"] if h["as_name"]]
    assert named and all(h["asn"].startswith("AS") for h in named)
    assert geo["destination"]["asn"] and geo["destination"]["as_name"]
    assert geo["sources"][0]["asn"] == "AS24940" and geo["sources"][0]["as_name"] == "Hetzner Online GmbH"
    assert (await client.get("/api/geoip/status")).json()["asn_available"] is True

    assert (await client.get("/api/targets/999999/geo")).status_code == 404


async def test_lookup_endpoint(client: AsyncClient) -> None:
    r = await client.get("/api/geoip/lookup", params={"q": "203.0.113.5"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "address" and body["ip"] == "203.0.113.5" and body["configured"] is False and body["simulated"] is True
    # Simulation fabricates a location and a network whether or not a key is saved; the page uses `configured` to explain.
    assert body["geo"]["country_code"] and body["note"] is None
    assert body["asn"].startswith("AS") and body["as_name"] and body["asn_available"] is True

    private = (await client.get("/api/geoip/lookup", params={"q": "10.1.2.3"})).json()
    assert private["note"] == "private address" and private["asn"] is None and private["as_name"] is None
    host = (await client.get("/api/geoip/lookup", params={"q": "www.example.test"})).json()
    assert host["kind"] == "host" and host["host"] == "www.example.test" and host["ip"].startswith("198.51.100.") and host["geo"]
    me = (await client.get("/api/geoip/lookup", params={"q": "self"})).json()
    assert me["kind"] == "simulated" and me["geo"]["city"] == "Berlin" and me["host"] == "This server (simulated)" and me["as_name"] == "Hetzner Online GmbH"
    assert (await client.get("/api/geoip/lookup", params={"q": ""})).status_code == 422
    assert (await client.get("/api/geoip/lookup")).status_code == 422


async def test_sources_carry_their_evidence(client: AsyncClient) -> None:
    await client.put("/api/settings", json={"maxmind_license_key": "key_abc"})
    r = await client.post("/api/targets", json={"name": "Evidence", "host": "192.0.2.32", "type": "ping", "interval_sec": 60, "count": 3})
    await wait_for_runs(client, r.json()["id"], 1)
    geo = (await client.get(f"/api/targets/{r.json()['id']}/geo")).json()
    assert geo["sources"][0]["evidence"] == "simulated location" and geo["sources"][0]["geo"]["accuracy_km"] == 50


async def test_probe_targets_get_a_destination_marker(client: AsyncClient) -> None:
    """Ping and TCP runs store the address they probed; HTTP and DNS runs do not, so the far end is derived."""
    await client.put("/api/settings", json={"maxmind_license_key": "key_abc"})
    specs = {
        "tcp": {"name": "Geo TCP", "host": "192.0.2.41", "type": "tcp", "port": 443, "interval_sec": 60},
        "http": {"name": "Geo HTTP", "host": "https://status.example.test/health", "type": "http", "interval_sec": 60},
        "dns-resolver": {"name": "Geo DNS @", "host": "example.test", "type": "dns", "interval_sec": 60, "options": {"resolver": "9.9.9.9"}},
        "dns-answer": {"name": "Geo DNS", "host": "example.test", "type": "dns", "interval_sec": 60},
        "gp-ping": {"name": "Geo GP", "host": "example.test", "type": "globalping", "interval_sec": 60, "options": {"measurement": "ping", "location": "DE"}},
    }
    ids = {k: (await client.post("/api/targets", json=spec)).json()["id"] for k, spec in specs.items()}
    for tid in ids.values():
        await wait_for_runs(client, tid, 1)

    geo = {k: (await client.get(f"/api/targets/{tid}/geo")).json() for k, tid in ids.items()}
    for k, g in geo.items():
        assert g["hops"] == [] and g["destination"] and g["destination"]["geo"], k
    assert geo["tcp"]["destination"]["ip"] == "192.0.2.41" and geo["tcp"]["destination"]["role"] == "target"
    # The URL's host is resolved (a stable made-up address in simulation) and reported without the scheme or path.
    assert geo["http"]["destination"]["host"] == "status.example.test" and geo["http"]["destination"]["ip"].startswith("198.51.100.") and geo["http"]["destination"]["role"] == "target"
    assert geo["dns-resolver"]["destination"] == {**geo["dns-resolver"]["destination"], "ip": "9.9.9.9", "host": "9.9.9.9", "role": "resolver"}
    assert geo["dns-answer"]["destination"]["role"] == "answer" and geo["dns-answer"]["destination"]["ip"] == "192.0.2.10"
    assert geo["gp-ping"]["sources"][0]["kind"] == "probe" and geo["gp-ping"]["sources"][0]["geo"]["lat"] and geo["gp-ping"]["destination"]["ip"] == "192.0.2.10"
    # A Globalping probe reports its own network; it is shown as the probe's ASN and name.
    probe = geo["gp-ping"]["sources"][0]
    assert probe["asn"] and probe["asn"].startswith("AS") and probe["as_name"]


def _archive(member: str = "GeoLite2-City_20260901/GeoLite2-City.mmdb", content: bytes = b"fake-mmdb") -> bytes:
    """A MaxMind-style tarball; `content` doubles as the edition marker the fake reader checks."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(member)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class FakeReader:
    """Stands in for maxminddb.Reader of either edition (told apart by the file's content marker).

    City: every public address except the destination is in Frankfurt. ASN: every public address except the
    destination is AS64500; the destination is AS64510 without a name.
    """

    def __init__(self, path: str):
        self.path = path
        content = open(path, "rb").read()
        if content not in (b"fake-mmdb", b"fake-asn-mmdb"):
            raise ValueError("not a database")
        self.asn = content == b"fake-asn-mmdb"

    def get(self, ip: str) -> dict[str, Any] | None:
        if self.asn:
            return {"autonomous_system_number": 64510} if ip == "192.0.2.31" else ASN_RECORD
        return None if ip == "192.0.2.31" else CITY_RECORD

    def metadata(self) -> Any:
        return SimpleNamespace(database_type="GeoLite2-ASN" if self.asn else "GeoLite2-City", build_epoch=1_756_771_200 if self.asn else 1_756_684_800)

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
    asn_archive = _archive("GeoLite2-ASN_20260902/GeoLite2-ASN.mmdb", b"fake-asn-mmdb")
    responses: dict[str, httpx.Response | None] = {"download": httpx.Response(200, content=_archive()), "asn": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "download.maxmind.com":
            if "ASN" in str(request.url):
                return responses["asn"] or httpx.Response(200, content=asn_archive)
            return responses["download"]  # type: ignore[return-value]
        return httpx.Response(200, text="198.51.100.7\n")

    monkeypatch.setattr(geoip, "_FORCE_LIVE", True)
    monkeypatch.setattr(geoip, "_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(geoip, "_open_reader", lambda path: FakeReader(str(path)))
    geoip.close()

    before = (await client.get("/api/geoip/status")).json()
    assert before["configured"] is True and before["available"] is False and before["asn_available"] is False and before["simulated"] is False
    no_db = (await client.get(f"/api/targets/{t['id']}/geo")).json()
    assert no_db["enabled"] is True and no_db["available"] is False and no_db["asn_available"] is False and no_db["destination"]["note"] == "no database"
    # Without the ASN database the hops keep the number mtr reported and get no name.
    assert all(h["as_name"] is None for h in no_db["hops"]) and any(h["asn"] for h in no_db["hops"])

    responses["download"] = httpx.Response(401, text="Invalid license key")
    r = await client.post("/api/geoip/update")
    assert r.status_code == 502 and "credentials" in r.json()["detail"]
    assert (await client.get("/api/geoip/status")).json()["last_error"].startswith("GeoLite2-City: MaxMind rejected")
    # The City download failed first, so the ASN edition was not even requested.
    assert not any("ASN" in str(q.url) for q in seen)

    responses["download"] = httpx.Response(200, content=_archive())
    r = await client.post("/api/geoip/update")
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["available"] is True and st["last_error"] is None and st["build_epoch"] == "2025-09-01T00:00:00Z" and st["downloaded_at"]
    assert st["asn_available"] is True and st["asn_edition"] == "GeoLite2-ASN" and st["asn_build_epoch"] == "2025-09-02T00:00:00Z" and st["asn_downloaded_at"]
    assert geoip.db_path().read_bytes() == b"fake-mmdb" and geoip.db_path(geoip.ASN_EDITION).read_bytes() == b"fake-asn-mmdb"
    downloads = [q for q in seen if q.url.host == "download.maxmind.com"][-2:]
    assert [q.url.path for q in downloads] == ["/geoip/databases/GeoLite2-City/download", "/geoip/databases/GeoLite2-ASN/download"]
    assert all(q.headers.get("authorization", "").startswith("Basic ") for q in downloads)

    geo = (await client.get(f"/api/targets/{t['id']}/geo")).json()
    assert geo["asn_available"] is True
    src = geo["sources"][0]
    assert src["kind"] == "public_ip" and src["ip"] == "198.51.100.7" and src["geo"]["city"] == "Frankfurt"
    assert src["asn"] == "AS64500" and src["as_name"] == "Example Transit GmbH"
    public_hops = [h for h in geo["hops"] if h["ip"] and not geoip.is_unroutable(h["ip"]) and h["ip"] != "192.0.2.31"]
    assert all(h["geo"]["country_code"] == "DE" for h in public_hops)
    # A hop whose mtr-reported ASN agrees with the database (or that reported none) gets the database's name;
    # one whose number disagrees keeps its own number and no name, so the two are never shown mismatched.
    for h in public_hops:
        stored = next(x["asn"] for x in no_db["hops"] if x["hop_no"] == h["hop_no"])
        if stored in (None, "AS64500"):
            assert h["asn"] == "AS64500" and h["as_name"] == "Example Transit GmbH", h
        else:
            assert h["asn"] == stored and h["as_name"] is None, h
    assert geo["hops"][0]["note"] == "private address" and geo["hops"][0]["as_name"] is None
    assert geo["destination"]["geo"] is None and geo["destination"]["note"] == "not in database"
    # The destination's network comes from the database too, even when its location is unknown.
    dst_stored = next((x["asn"] for x in no_db["hops"] if x["ip"] == "192.0.2.31"), None)
    assert geo["destination"]["asn"] == (dst_stored or "AS64510") and geo["destination"]["as_name"] is None
    looked = (await client.get("/api/geoip/lookup", params={"q": "203.0.113.5"})).json()
    assert looked["asn"] == "AS64500" and looked["as_name"] == "Example Transit GmbH" and looked["asn_available"] is True

    # Without an account ID the legacy endpoint carries the key in the query string instead of basic auth.
    await client.put("/api/settings", json={"maxmind_account_id": ""})
    assert (await client.post("/api/geoip/update")).status_code == 200
    downloads = [q for q in seen if q.url.host == "download.maxmind.com"][-2:]
    assert [q.url.params["edition_id"] for q in downloads] == ["GeoLite2-City", "GeoLite2-ASN"]
    assert all(q.url.path == "/app/geoip_download" and q.url.params["license_key"] == "lic_key" and "authorization" not in q.headers for q in downloads)

    # A broken archive never replaces the working database.
    responses["download"] = httpx.Response(200, content=b"not a tarball")
    r = await client.post("/api/geoip/update")
    assert r.status_code == 502 and "unreadable" in r.json()["detail"]
    assert geoip.db_path().read_bytes() == b"fake-mmdb" and (await client.get("/api/geoip/status")).json()["available"] is True

    # A failed ASN download leaves the fresh City database in place and names the edition in the error.
    responses["download"] = httpx.Response(200, content=_archive())
    responses["asn"] = httpx.Response(200, content=_archive("GeoLite2-ASN_20260902/GeoLite2-ASN.mmdb", b"fake-mmdb"))
    r = await client.post("/api/geoip/update")
    assert r.status_code == 502 and r.json()["detail"].startswith("GeoLite2-ASN:") and "unexpected database type" in r.json()["detail"]
    st = (await client.get("/api/geoip/status")).json()
    assert st["available"] is True and st["asn_available"] is True and st["last_error"].startswith("GeoLite2-ASN:")
    assert geoip.db_path(geoip.ASN_EDITION).read_bytes() == b"fake-asn-mmdb"


async def test_license_key_is_masked_without_the_token(protected_client: AsyncClient) -> None:
    auth = {"Authorization": "Bearer s3cret"}
    saved = (await protected_client.put("/api/settings", json={"maxmind_license_key": "abcdefghijkl", "maxmind_account_id": "77"}, headers=auth)).json()
    assert saved["maxmind_license_key"] == "abcdefghijkl"
    masked = (await protected_client.get("/api/settings")).json()
    assert masked["maxmind_license_key"] == "********ijkl" and masked["maxmind_account_id"] == "77"
    echoed = (await protected_client.put("/api/settings", json=masked, headers=auth)).json()
    assert echoed["maxmind_license_key"] == "abcdefghijkl"
