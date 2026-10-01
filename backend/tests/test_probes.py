"""Probe helpers and live HTTP/TCP paths with mocked transports."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from app import probes
from app.probes import certificate_details, evaluate_json_query, json_condition, ping_payload_bytes, run_dns, run_http, run_tcp, status_allowed


def test_certificate_details_from_peer_cert() -> None:
    class FakeSSL:
        def getpeercert(self) -> dict:
            return {
                "subject": ((("commonName", "portal.example.com"),), (("organizationName", "Example Corp"),)),
                "issuer": ((("countryName", "US"),), (("organizationName", "Let's Encrypt"),), (("commonName", "R11"),)),
                "notBefore": "Jun  1 00:00:00 2026 GMT",
                "notAfter": "Aug 30 23:59:59 2026 GMT",
                "serialNumber": "04AB",
                "subjectAltName": (("DNS", "portal.example.com"), ("DNS", "www.example.com"), ("IP Address", "192.0.2.1")),
            }

        def cipher(self) -> tuple:
            return ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)

        def version(self) -> str:
            return "TLSv1.3"

    info = certificate_details(FakeSSL())
    assert info is not None
    assert info["subject"] == "portal.example.com" and info["subject_org"] == "Example Corp"
    assert info["issuer"] == "Let's Encrypt" and info["issuer_cn"] == "R11"
    assert info["not_before"] == "2026-06-01T00:00:00Z" and info["not_after"] == "2026-08-30T23:59:59Z" and isinstance(info["days_left"], int)
    assert info["san"] == ["portal.example.com", "www.example.com"] and info["serial"] == "04AB"
    assert info["protocol"] == "TLSv1.3" and info["cipher"] == "TLS_AES_256_GCM_SHA384"

    class Unverified:
        def getpeercert(self) -> dict:
            return {}  # what getpeercert() returns when the certificate was not validated

    assert certificate_details(Unverified()) is None


def test_ping_payload_matches_mtr_packet_size() -> None:
    # 64-byte packets like mtr's default: IPv4 20 + ICMP 8 + 36 payload; IPv6 40 + 8 + 16.
    assert ping_payload_bytes(64, ipv6=False) == 36
    assert ping_payload_bytes(64, ipv6=True) == 16
    assert ping_payload_bytes(28, ipv6=False) == 0 and ping_payload_bytes(20, ipv6=True) == 0


def test_status_allowed() -> None:
    assert status_allowed(204, None) and status_allowed(299, "200-299") and not status_allowed(301, "200-299")
    assert status_allowed(301, "200,301,302") and status_allowed(404, "404")


STATUS_PAGE = {
    "status": {"indicator": "none"},
    "components": [
        {"id": "rwppv331jlwc", "name": "claude.ai", "status": "operational"},
        {"id": "yyzkbfz2thpt", "name": "Claude Code", "status": "major_outage"},
    ],
    "queue": 12,
}


def test_json_query_evaluates_jsonata() -> None:
    assert evaluate_json_query(STATUS_PAGE, 'components[id = "yyzkbfz2thpt"].status') == "major_outage"
    assert evaluate_json_query(STATUS_PAGE, 'components[id = "missing"].status') is None
    assert evaluate_json_query(STATUS_PAGE, "components[0].name") == "claude.ai"
    assert evaluate_json_query(STATUS_PAGE, "$count(components)") == 2
    assert evaluate_json_query(STATUS_PAGE, 'status.indicator = "none"') is True
    assert evaluate_json_query(STATUS_PAGE, "components.status") == ["operational", "major_outage"]
    with pytest.raises(probes.JsonQueryError):
        evaluate_json_query(STATUS_PAGE, "$nosuchfunction(queue)")
    with pytest.raises(probes.JsonQueryError, match="timeout|deep|Stack|stack"):
        evaluate_json_query({}, "($f := function($n){ $f($n + 1) }; $f(0))")


def test_json_condition() -> None:
    assert json_condition("operational", "==", "operational") and not json_condition("operational", "==", "Operational")
    assert json_condition("degraded", "!=", "operational") and not json_condition("ok", "!=", "ok")
    assert json_condition(5, "==", "5.0") and json_condition(5.0, "==", "5") and json_condition(True, "==", "TRUE") and json_condition(None, "==", "null")
    assert json_condition(12, "<", "20") and json_condition(20, "<=", "20") and not json_condition(12, ">", "20") and json_condition("21", ">=", "20")
    assert not json_condition("abc", "<", "20") and not json_condition(True, ">", "0")
    assert json_condition("healthy-node", "contains", "HEALTHY") and json_condition(["a", "operational"], "contains", "operational")
    # Without an expected value the result only has to exist and not be false.
    assert json_condition("x", "==", "") and json_condition(0, "==", "") and not json_condition(None, "==", "") and not json_condition(False, "==", "")


def test_http_json_options_validation_and_legacy_upgrade() -> None:
    from app.models import upgrade_http_options, validate_options

    opts = validate_options("http", {"json_query": ' components[id = "x"].status ', "json_operator": "==", "json_expected": "operational"})
    assert opts["json_query"] == 'components[id = "x"].status' and opts["json_operator"] == "==" and "json_path" not in opts
    assert validate_options("http", {})["json_operator"] == "=="
    for bad in (
        {"json_query": "components["},
        {"json_query": "queue", "json_operator": ">", "json_expected": "many"},
        {"json_query": "queue", "json_operator": "!=", "json_expected": ""},
        {"json_query": "queue", "json_operator": "~"},
    ):
        with pytest.raises(ValidationError):
            validate_options("http", bad)
    # The former dotted path, with its comparison inside the expected value, becomes the JSONata query and a condition.
    assert upgrade_http_options({"json_path": "data.items[0].status", "json_expected": "ok"}) == {
        "json_query": "data.items[0].status", "json_operator": "==", "json_expected": "ok"}
    assert upgrade_http_options({"json_path": "queue", "json_expected": "< 20"})["json_operator"] == "<"
    assert upgrade_http_options({"json_path": "queue", "json_expected": "< 20"})["json_expected"] == "20"
    assert upgrade_http_options({"json_path": "name", "json_expected": "~Node"}) == {"json_query": "name", "json_operator": "contains", "json_expected": "Node"}
    assert upgrade_http_options({"json_path": "my-service.up"})["json_query"] == "`my-service`.up"
    assert validate_options("http", {"json_path": "status", "json_expected": "ok"})["json_query"] == "status"
    assert upgrade_http_options({"keyword": "x"}) == {"keyword": "x"}


def test_keyword_regex_validation() -> None:
    from app.models import validate_options

    assert validate_options("http", {"keyword": r"ok|healthy", "keyword_regex": True})["keyword_regex"] is True
    assert validate_options("http", {"keyword": "([unbalanced"})["keyword_regex"] is False  # plain text: anything goes
    with pytest.raises(ValidationError, match="invalid keyword regular expression"):
        validate_options("http", {"keyword": "([unbalanced", "keyword_regex": True})


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(probes, "_FORCE_LIVE", True)
    yield


async def test_run_http_checks(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "degraded", "queue": 12}, headers={"server": "unit"})
        if request.url.path == "/status":
            return httpx.Response(200, json=STATUS_PAGE)
        if request.url.path == "/page":
            return httpx.Response(200, text="<html>Welcome to MTR Tracker</html>")
        return httpx.Response(503, text="nope")

    monkeypatch.setattr(probes, "_HTTP_TRANSPORT", httpx.MockTransport(handler))
    base = {"host": "http://svc.test/page", "type": "http"}

    ok = await run_http(base, {"keyword": "mtr tracker"})
    assert ok.ok and ok.reached and ok.details["status"] == 200 and ok.details["keyword_found"] is True and ok.avg_ms is not None

    missing = await run_http(base, {"keyword": "absent text"})
    assert missing.ok and not missing.reached and "not found" in (missing.error or "")

    absent = await run_http(base, {"keyword": "Welcome", "keyword_absent": True})
    assert not absent.reached and "must be absent" in (absent.error or "")

    rx = await run_http(base, {"keyword": r"welcome\s+to\s+(mtr|ping)", "keyword_regex": True})
    assert rx.reached and rx.details["keyword_regex"] is True and rx.details["keyword_match"] == "Welcome to MTR"

    rx_missing = await run_http(base, {"keyword": r"version \d+", "keyword_regex": True})
    assert not rx_missing.reached and rx_missing.error == "regex 'version \\d+' not found"

    rx_absent = await run_http(base, {"keyword": r"tracker</html>$", "keyword_regex": True, "keyword_absent": True})
    assert not rx_absent.reached and "present but must be absent" in (rx_absent.error or "")

    literal = await run_http(base, {"keyword": "mtr (tracker)"})  # without the switch, regex characters are plain text
    assert not literal.reached and "keyword 'mtr (tracker)' not found" in (literal.error or "")

    monkeypatch.setattr(probes, "KEYWORD_REGEX_TIMEOUT_SEC", 0.2)
    monkeypatch.setattr(probes, "_HTTP_TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(200, text="a" * 5000 + "!")))
    slow = await run_http(base, {"keyword": "(a|aa)+$", "keyword_regex": True})
    assert not slow.reached and "no answer within 0.2 s" in (slow.error or "") and slow.details["keyword_error"]
    monkeypatch.setattr(probes, "_HTTP_TRANSPORT", httpx.MockTransport(handler))

    # Options saved before the JSONata query still run.
    js = await run_http({"host": "http://svc.test/health", "type": "http"}, {"json_path": "status", "json_expected": "ok"})
    assert not js.reached and "expected == ok" in (js.error or "") and js.details["json_value"] == "degraded"

    js2 = await run_http({"host": "http://svc.test/health", "type": "http"}, {"json_path": "queue", "json_expected": "< 20"})
    assert js2.reached and js2.details["json_ok"] is True and js2.details["server"] == "unit" and js2.details["json_query"] == "queue"

    status = {"host": "http://svc.test/status", "type": "http"}
    up = await run_http(status, {"json_query": 'components[id = "rwppv331jlwc"].status', "json_operator": "==", "json_expected": "operational"})
    assert up.reached and up.details["json_value"] == "operational" and up.details["json_ok"] is True

    down = await run_http(status, {"json_query": 'components[id = "yyzkbfz2thpt"].status', "json_operator": "==", "json_expected": "operational"})
    assert not down.reached and down.error == "JSON query returned 'major_outage', expected == operational"

    gone = await run_http(status, {"json_query": 'components[id = "nope"].status', "json_expected": "operational"})
    assert not gone.reached and "returned nothing" in (gone.error or "") and gone.details["json_value"] is None

    boolean = await run_http(status, {"json_query": '$count(components[status != "operational"]) = 0'})
    assert not boolean.reached and boolean.details["json_value"] is False

    listed = await run_http(status, {"json_query": "components.status", "json_operator": "contains", "json_expected": "outage"})
    assert listed.reached and listed.details["json_value"] == '["operational","major_outage"]'

    broken = await run_http(status, {"json_query": "$nosuchfunction(queue)"})
    assert not broken.reached and (broken.error or "").startswith("JSON query failed") and broken.details["json_error"]

    html = await run_http(base, {"json_query": "status"})
    assert not html.reached and "not valid JSON" in (html.error or "") and html.details["json_ok"] is False

    bad = await run_http({"host": "http://svc.test/down", "type": "http"}, {})
    assert not bad.reached and bad.loss_pct == 100.0 and "503" in (bad.error or "")

    allowed = await run_http({"host": "http://svc.test/down", "type": "http"}, {"expected_status": "503"})
    assert allowed.reached


async def test_run_http_caps_the_body_it_keeps(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    big = b"needle" + b"x" * (probes.MAX_HTTP_BODY + 100_000)
    monkeypatch.setattr(probes, "_HTTP_TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(200, content=big)))
    o = await run_http({"host": "http://svc.test/big", "type": "http"}, {"keyword": "needle"})
    assert o.reached and o.details["keyword_found"] is True
    assert o.details["truncated"] is True and o.details["bytes"] == len(big)


async def test_run_dns_random_prefix_measures_uncached_lookups(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import dns.asyncresolver
    import dns.resolver

    queried: list[str] = []

    class FakeResolver:
        def __init__(self, configure: bool = True) -> None:
            self.nameservers: list[str] = []
            self.lifetime = self.timeout = 0.0

        async def resolve(self, name: str, rtype: str, tcp: bool = False) -> None:
            queried.append(name)
            raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(dns.asyncresolver, "Resolver", FakeResolver)
    target = {"host": "example.test", "type": "dns"}

    o = await run_dns(target, {"random_prefix": True})
    assert o.ok and o.reached and o.loss_pct == 0.0 and o.avg_ms is not None
    assert o.details["rcode"] == "NXDOMAIN" and o.details["random_prefix"] is True and o.details["answers"] == []
    assert queried[0].endswith(".example.test") and len(queried[0]) > len("example.test") + 1 and o.details["queried_name"] == queried[0]
    again = await run_dns(target, {"random_prefix": True})
    assert again.details["queried_name"] != o.details["queried_name"]  # a fresh label every run

    strict = await run_dns(target, {"random_prefix": True, "expected": "192.0.2"})
    assert strict.ok and not strict.reached  # an expected answer still has to be present
    plain = await run_dns(target, {})
    assert plain.ok and not plain.reached and "NXDOMAIN" in (plain.error or "") and queried[-1] == "example.test"


def _dns_reply(wire: bytes, rcode: int = 0) -> bytes:
    import dns.message
    import dns.rrset

    query = dns.message.from_wire(wire)
    reply = dns.message.make_response(query)
    reply.set_rcode(rcode)
    if rcode == 0:
        reply.answer.append(dns.rrset.from_text(query.question[0].name, 60, "IN", "A", "192.0.2.53"))
    return reply.to_wire()


class _DnsUdp(asyncio.DatagramProtocol):
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self.transport.sendto(_dns_reply(data), addr)  # type: ignore[attr-defined]


async def _dns_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    # RFC 1035 4.2.2 (and RFC 7858 for TLS): every message is preceded by its two-byte length.
    size = int.from_bytes(await reader.readexactly(2), "big")
    reply = _dns_reply(await reader.readexactly(size))
    writer.write(len(reply).to_bytes(2, "big") + reply)
    await writer.drain()
    writer.close()


async def test_run_dns_over_udp_tcp_and_tls(live: None, tmp_path) -> None:
    import shutil
    import ssl
    import subprocess

    loop = asyncio.get_running_loop()
    udp, _ = await loop.create_datagram_endpoint(_DnsUdp, local_addr=("127.0.0.1", 0))
    udp_port = udp.get_extra_info("sockname")[1]
    tcp_server = await asyncio.start_server(_dns_stream, "127.0.0.1", 0)
    tcp_port = tcp_server.sockets[0].getsockname()[1]
    target = {"host": "example.test", "type": "dns"}
    try:
        o = await run_dns(target, {"transport": "udp", "resolver": "127.0.0.1", "resolver_port": udp_port})
        assert o.reached and o.details["answers"] == ["192.0.2.53"] and o.details["transport"] == "udp" and o.details["port"] == udp_port
        assert o.details["nameserver"] == "127.0.0.1" and o.command == f"dns A example.test @127.0.0.1 -p {udp_port}"

        o = await run_dns(target, {"transport": "tcp", "resolver": "127.0.0.1", "resolver_port": tcp_port})
        assert o.reached and o.details["answers"] == ["192.0.2.53"] and o.details["transport"] == "tcp" and o.command.endswith("+tcp")

        # Nothing listens for UDP on the TCP server's port pair: a UDP query there times out instead of silently using TCP.
        o = await run_dns(target, {"transport": "udp", "resolver": "127.0.0.1", "resolver_port": tcp_port, "timeout_sec": 0.5})
        assert o.ok and not o.reached and o.loss_pct == 100.0

        if shutil.which("openssl") is None:
            pytest.skip("openssl is needed to make a certificate for the DNS over TLS server")
        cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1",
             "-subj", "/CN=dot.test", "-addext", "subjectAltName=IP:127.0.0.1"],
            check=True, capture_output=True,
        )
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(cert, key)
        tls_server = await asyncio.start_server(_dns_stream, "127.0.0.1", 0, ssl=ctx)
        tls_port = tls_server.sockets[0].getsockname()[1]
        try:
            # A self-signed certificate fails verification, which is exactly what the check is there to catch...
            o = await run_dns(target, {"transport": "dot", "resolver": "127.0.0.1", "resolver_port": tls_port, "timeout_sec": 2})
            assert o.ok and not o.reached and o.error
            # ...and passes with verification switched off.
            o = await run_dns(target, {"transport": "dot", "resolver": "127.0.0.1", "resolver_port": tls_port, "verify_tls": False})
            assert o.reached and o.details["answers"] == ["192.0.2.53"] and o.details["transport"] == "dot" and o.command.endswith("+tls")
        finally:
            tls_server.close()
    finally:
        udp.close()
        tcp_server.close()


async def test_run_dns_over_https_and_error_codes(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import dns.asyncquery
    import dns.message

    calls: list[dict] = []
    rcode = {"value": 0}

    async def fake_https(q: dns.message.Message, where: str, **kw) -> dns.message.Message:
        calls.append({"url": where, **kw})
        return dns.message.from_wire(_dns_reply(q.to_wire(), rcode["value"]))

    async def fake_resolve(host: str, ip_version: str = "auto") -> str:
        return {"doh.test": "192.0.2.80"}[host]

    monkeypatch.setattr(dns.asyncquery, "https", fake_https)
    monkeypatch.setattr(probes, "resolve_host", fake_resolve)
    target = {"host": "example.test", "type": "dns"}

    o = await run_dns(target, {"transport": "doh", "resolver": "doh.test"})
    assert o.reached and o.details["answers"] == ["192.0.2.53"] and o.details["resolver"] == "https://doh.test/dns-query"
    assert calls[-1]["url"] == "https://doh.test/dns-query" and calls[-1]["bootstrap_address"] == "192.0.2.80" and calls[-1]["verify"] is True
    assert o.details["nameserver"] == "192.0.2.80" and o.details["port"] == 443 and o.command.endswith("+https")

    o = await run_dns(target, {"transport": "doh", "resolver": "https://doh.test:8443/q", "verify_tls": False})
    assert o.reached and calls[-1]["url"] == "https://doh.test:8443/q" and calls[-1]["verify"] is False and o.details["port"] == 8443

    rcode["value"] = 3  # NXDOMAIN
    o = await run_dns(target, {"transport": "doh", "resolver": "doh.test"})
    assert o.ok and not o.reached and o.details["rcode"] == "NXDOMAIN"
    o = await run_dns(target, {"transport": "doh", "resolver": "doh.test", "random_prefix": True})
    assert o.reached and o.details["rcode"] == "NXDOMAIN"

    rcode["value"] = 2  # SERVFAIL
    o = await run_dns(target, {"transport": "doh", "resolver": "doh.test"})
    assert o.ok and not o.reached and o.details["rcode"] == "SERVFAIL" and "SERVFAIL" in (o.error or "")


def test_dns_options_validation() -> None:
    from app.models import validate_options

    assert validate_options("dns", {})["transport"] == "udp"
    assert validate_options("dns", {"transport": "doh", "resolver": " https://dns.example/dns-query "})["resolver"] == "https://dns.example/dns-query"
    for bad in (
        {"transport": "dot"},  # the system resolver has no TLS endpoint
        {"transport": "doh"},
        {"transport": "tcp", "resolver": "https://dns.example/dns-query"},
        {"transport": "doh", "resolver": "http://dns.example/dns-query"},
        {"transport": "doh", "resolver": "https://dns.example:99999/dns-query"},
        {"transport": "doh", "resolver": "https:///dns-query"},
        {"resolver_port": 5353},
        {"transport": "smtp", "resolver": "192.0.2.1"},
    ):
        with pytest.raises(ValueError):
            validate_options("dns", bad)


async def test_run_tcp_local(live: None) -> None:
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        o = await run_tcp({"host": "127.0.0.1", "port": port, "type": "tcp"}, {"timeout_sec": 2})
        assert o.ok and o.reached and o.details["connected"] and o.avg_ms is not None
    finally:
        server.close()
        await server.wait_closed()
    closed = await run_tcp({"host": "127.0.0.1", "port": port, "type": "tcp"}, {"timeout_sec": 2})
    assert closed.ok and not closed.reached and closed.loss_pct == 100.0
    assert not (await run_tcp({"host": "127.0.0.1", "port": None, "type": "tcp"}, {})).ok
