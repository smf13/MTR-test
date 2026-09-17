"""Unit tests for the mtr parser, route comparison and command builder."""

from __future__ import annotations

import json

import pytest

from app.mtr import build_command, parse_report, route_signature, routes_equivalent, run_mtr

SAMPLE = {
    "report": {
        "mtr": {"src": "probe01", "dst": "1.1.1.1", "tos": 0, "tests": 3, "psize": "64", "bitpattern": "0x00"},
        "hubs": [
            {"count": 1, "host": "192.0.2.1", "ASN": "AS???", "Loss%": 0.0, "Snt": 3, "Drop": 0, "Rcv": 3, "Last": 0.256, "Best": 0.227,
             "Avg": 0.26, "Wrst": 0.3, "StDev": 0.036, "Gmean": 0.258, "Jttr": 0.029, "Javg": 0.034, "Jmax": 0.073, "Jint": 0.097},
            {"count": 2, "host": "???", "ASN": "AS???", "Loss%": 100.0, "Snt": 3, "Drop": 3, "Rcv": 0, "Last": 0.0, "Best": 0.0,
             "Avg": 0.0, "Wrst": 0.0, "StDev": 0.0, "Gmean": 0.0, "Jttr": 0.0, "Javg": 0.0, "Jmax": 0.0, "Jint": 0.0},
            {"count": 3, "host": "1.1.1.1", "ASN": "AS13335", "Loss%": 33.3, "Snt": 3, "Drop": 1, "Rcv": 2, "Last": 9.1, "Best": 8.9,
             "Avg": 9.0, "Wrst": 9.1, "StDev": 0.1, "Gmean": 9.0, "Jttr": 0.2, "Javg": 0.2, "Jmax": 0.2, "Jint": 0.3},
        ],
    }
}


def test_parse_report_extracts_all_fields() -> None:
    src, hops = parse_report(SAMPLE)
    assert src == "probe01"
    assert [h.hop_no for h in hops] == [1, 2, 3]
    first, unknown, last = hops
    assert first.ip == "192.0.2.1" and first.asn is None  # AS??? is normalised to None
    assert first.avg_ms == pytest.approx(0.26) and first.jitter_avg_ms == pytest.approx(0.034)
    assert unknown.ip is None and unknown.loss_pct == 100.0 and unknown.avg_ms is None
    assert last.asn == "AS13335" and last.received == 2 and last.loss_pct == pytest.approx(33.3)


def test_parse_report_without_optional_columns() -> None:
    raw = {"report": {"mtr": {}, "hubs": [{"count": 1, "host": "10.0.0.1", "Loss%": 50.0, "Snt": 4, "Avg": 1.5}]}}
    _, hops = parse_report(raw)
    assert hops[0].received == 2  # derived from Snt and Loss%
    assert hops[0].avg_ms == 1.5 and hops[0].best_ms is None


def test_route_signature_is_stable_and_wildcard_aware() -> None:
    _, hops = parse_report(SAMPLE)
    sig = route_signature(hops)
    assert sig == route_signature(hops)
    assert routes_equivalent(["a", None, "c"], ["a", "b", "c"])
    assert not routes_equivalent(["a", "x", "c"], ["a", "b", "c"])
    assert not routes_equivalent(["a", "b"], ["a", "b", "c"])


def test_build_command_flags() -> None:
    cmd = build_command(binary="mtr", dst_ip="203.0.113.5", count=10, probe_interval=0.5, protocol="tcp", port=443,
                        packet_size=64, max_hops=30, ip_version="4", asn_lookup=True)
    assert cmd[0] == "mtr" and cmd[-1] == "203.0.113.5"
    assert "--json" in cmd and "-n" in cmd and "--tcp" in cmd and "-z" in cmd and "-4" in cmd
    assert cmd[cmd.index("-P") + 1] == "443"
    assert cmd[cmd.index("-c") + 1] == "10"
    assert cmd[cmd.index("-i") + 1] == "0.5"

    icmp = build_command(binary="mtr", dst_ip="2001:db8::1", count=5, probe_interval=1.0, protocol="icmp", port=443,
                         packet_size=64, max_hops=30, ip_version="6", asn_lookup=False)
    assert "-P" not in icmp and "-z" not in icmp and "-6" in icmp


def test_min_probe_interval_depends_on_root(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from app import mtr

    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert mtr.min_probe_interval() == 1.0
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert mtr.min_probe_interval() == 0.1


async def test_simulator_reaches_destination() -> None:
    result = await run_mtr(dst_ip="198.51.100.7", count=3, probe_interval=0.1, protocol="icmp", port=None, packet_size=64,
                           max_hops=30, ip_version="auto", asn_lookup=False, simulate=True)
    assert result.ok and result.hops
    assert result.hops[-1].ip == "198.51.100.7"
    assert json.dumps([h.__dict__ for h in result.hops])  # serialisable
