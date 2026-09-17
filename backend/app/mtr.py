"""Runs the mtr binary (or a simulator) and parses its JSON report."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import random
import shutil
import time
from dataclasses import dataclass, field
from typing import Any

from .config import config

log = logging.getLogger("mtr-tracker.mtr")


def min_probe_interval() -> float:
    """Smallest `-i` this process may pass to mtr: mtr rejects sub-second intervals unless it runs as root."""
    return 0.1 if getattr(os, "geteuid", lambda: 0)() == 0 else 1.0

# Field order requested from mtr. L=Loss%, S=Snt, D=Drop, R=Rcv, N=Last, B=Best,
# A=Avg, W=Wrst, V=StDev, G=Gmean, J=Jttr, M=Javg, X=Jmax, I=Jint.
MTR_FIELDS = "LSDRNBAWVGJMXI"

UNKNOWN_HOST = "???"


@dataclass
class HopResult:
    hop_no: int
    ip: str | None
    asn: str | None
    loss_pct: float
    sent: int
    received: int
    last_ms: float | None
    avg_ms: float | None
    best_ms: float | None
    worst_ms: float | None
    stdev_ms: float | None
    gmean_ms: float | None
    jitter_ms: float | None
    jitter_avg_ms: float | None
    jitter_max_ms: float | None
    jitter_int_ms: float | None
    hostname: str | None = None


@dataclass
class MtrResult:
    ok: bool
    started_at: float
    finished_at: float
    command: str
    src: str | None = None
    dst_ip: str | None = None
    hops: list[HopResult] = field(default_factory=list)
    error: str | None = None
    raw: dict[str, Any] | None = None

    @property
    def duration_ms(self) -> float:
        return (self.finished_at - self.started_at) * 1000.0


def build_command(
    *,
    binary: str,
    dst_ip: str,
    count: int,
    probe_interval: float,
    protocol: str,
    port: int | None,
    packet_size: int,
    max_hops: int,
    ip_version: str,
    asn_lookup: bool,
) -> list[str]:
    cmd = [binary, "--json", "-n", "-c", str(count), "-i", f"{probe_interval:g}", "-s", str(packet_size),
           "-m", str(max_hops), "-o", MTR_FIELDS]
    if ip_version == "4":
        cmd.append("-4")
    elif ip_version == "6":
        cmd.append("-6")
    if protocol == "udp":
        cmd.append("--udp")
    elif protocol == "tcp":
        cmd.append("--tcp")
    if protocol in {"udp", "tcp"} and port:
        cmd.extend(["-P", str(port)])
    if asn_lookup:
        cmd.append("-z")
    cmd.append(dst_ip)
    return cmd


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_report(raw: dict[str, Any]) -> tuple[str | None, list[HopResult]]:
    report = raw.get("report") or {}
    meta = report.get("mtr") or {}
    hubs = report.get("hubs") or []
    hops: list[HopResult] = []
    for idx, hub in enumerate(hubs):
        host = str(hub.get("host") or UNKNOWN_HOST)
        ip = None if host == UNKNOWN_HOST else host
        asn_raw = hub.get("ASN")
        asn = None
        if isinstance(asn_raw, str) and asn_raw and "???" not in asn_raw:
            asn = asn_raw
        loss = _num(hub.get("Loss%")) or 0.0
        sent = int(_num(hub.get("Snt")) or 0)
        received_raw = _num(hub.get("Rcv"))
        if received_raw is None:
            received = int(round(sent * (1.0 - loss / 100.0)))
        else:
            received = int(received_raw)
        answered = received > 0 or ip is not None

        def metric(key: str) -> float | None:
            v = _num(hub.get(key))
            if v is None or not answered:
                return None
            return v

        hops.append(
            HopResult(
                hop_no=int(hub.get("count") or idx + 1),
                ip=ip,
                asn=asn,
                loss_pct=loss,
                sent=sent,
                received=received,
                last_ms=metric("Last"),
                avg_ms=metric("Avg"),
                best_ms=metric("Best"),
                worst_ms=metric("Wrst"),
                stdev_ms=metric("StDev"),
                gmean_ms=metric("Gmean"),
                jitter_ms=metric("Jttr"),
                jitter_avg_ms=metric("Javg"),
                jitter_max_ms=metric("Jmax"),
                jitter_int_ms=metric("Jint"),
            )
        )
    return meta.get("src"), hops


def route_signature(hops: list[HopResult]) -> str:
    """Stable hash of the hop IP sequence (unknown hops hash as '*')."""
    parts = [h.ip or "*" for h in hops]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def routes_equivalent(a: list[str | None], b: list[str | None]) -> bool:
    """Compare two IP sequences treating unknown hops as wildcards."""
    if len(a) != len(b):
        return False
    return all(x is None or y is None or x == y for x, y in zip(a, b))


async def run_mtr(
    *,
    dst_ip: str,
    count: int,
    probe_interval: float,
    protocol: str,
    port: int | None,
    packet_size: int,
    max_hops: int,
    ip_version: str,
    asn_lookup: bool,
    simulate: bool | None = None,
    timeout: float | None = None,
) -> MtrResult:
    use_sim = config.simulate if simulate is None else simulate
    if not use_sim and shutil.which(config.mtr_binary) is None:
        log.warning("mtr binary '%s' not found; falling back to simulation", config.mtr_binary)
        use_sim = True

    if use_sim:
        return await _simulate(dst_ip=dst_ip, count=count, probe_interval=probe_interval, max_hops=max_hops)

    floor = min_probe_interval()
    if probe_interval < floor:
        log.debug("probe interval %.2fs raised to %.1fs: mtr accepts sub-second intervals only as root", probe_interval, floor)
        probe_interval = floor

    cmd = build_command(
        binary=config.mtr_binary,
        dst_ip=dst_ip,
        count=count,
        probe_interval=probe_interval,
        protocol=protocol,
        port=port,
        packet_size=packet_size,
        max_hops=max_hops,
        ip_version=ip_version,
        asn_lookup=asn_lookup,
    )
    started = time.time()
    # Generous timeout: each probe cycle waits for slow hops, plus DNS/ASN lookups.
    budget = timeout or (count * max(probe_interval, 1.0) * 2.0 + 60.0)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError:
        finished = time.time()
        return MtrResult(False, started, finished, " ".join(cmd), error=f"mtr binary not found: {cmd[0]}")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=budget)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        finished = time.time()
        return MtrResult(False, started, finished, " ".join(cmd), error=f"mtr timed out after {budget:.0f}s")
    except asyncio.CancelledError:
        # Shutdown or target deletion: do not leave an orphaned mtr probing in the background.
        proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        raise
    finished = time.time()

    text = stdout.decode("utf-8", "replace").strip()
    err_text = stderr.decode("utf-8", "replace").strip()
    if not text:
        msg = err_text or f"mtr exited with code {proc.returncode} and no output"
        return MtrResult(False, started, finished, " ".join(cmd), error=msg)
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        # mtr sometimes emits diagnostics before the JSON document.
        brace = text.find("{")
        try:
            raw = json.loads(text[brace:]) if brace >= 0 else None
        except json.JSONDecodeError:
            raw = None
        if raw is None:
            snippet = (err_text or text)[:300]
            return MtrResult(False, started, finished, " ".join(cmd), error=f"could not parse mtr output: {snippet}")

    src, hops = parse_report(raw)
    if not hops:
        msg = err_text or "mtr returned no hops"
        return MtrResult(False, started, finished, " ".join(cmd), src=src, dst_ip=dst_ip, error=msg, raw=raw)
    return MtrResult(True, started, finished, " ".join(cmd), src=src, dst_ip=dst_ip, hops=hops, raw=raw)


# ---------------------------------------------------------------------------
# Simulator: produces plausible paths so the UI can be exercised without
# raw-socket privileges (developer machines, CI, demos).
# ---------------------------------------------------------------------------

_SIM_STATE: dict[str, dict[str, Any]] = {}


def _sim_path(dst_ip: str, max_hops: int) -> list[dict[str, Any]]:
    prefix = dst_ip.rsplit(".", 1)[0] if "." in dst_ip else dst_ip.rsplit(":", 2)[0]
    seed = int(hashlib.md5(prefix.encode()).hexdigest(), 16)
    rng = random.Random(seed)
    hop_total = min(max_hops, rng.randint(6, 13))
    path: list[dict[str, Any]] = []
    base = 0.4
    for i in range(hop_total):
        last = i == hop_total - 1
        if last:
            ip = dst_ip
        elif i == 0:
            ip = "192.168.1.1"
        else:
            ip = f"{rng.choice([10, 100, 172, 203, 62, 80, 141, 185])}.{rng.randint(0, 254)}.{rng.randint(0, 254)}.{rng.randint(1, 254)}"
        base += rng.uniform(0.8, 12.0) if i < 4 else rng.uniform(0.2, 6.0)
        path.append(
            {
                "ip": ip,
                "asn": None if i == 0 else f"AS{rng.choice([13335, 15169, 3356, 174, 6939, 1299, 2914, 7018, 3257])}",
                "base": base,
                "jitter": rng.uniform(0.1, 3.0),
                "no_reply": (not last and i > 2 and rng.random() < 0.12),
                "rate_limited": (not last and rng.random() < 0.15),
            }
        )
    return path


async def _simulate(*, dst_ip: str, count: int, probe_interval: float, max_hops: int) -> MtrResult:
    started = time.time()
    state = _SIM_STATE.setdefault(dst_ip, {"path": _sim_path(dst_ip, max_hops), "incident": 0, "runs": 0})
    state["runs"] += 1
    rng = random.Random()

    # Occasionally flip a mid-path hop to simulate a route change.
    if state["runs"] > 3 and rng.random() < 0.05:
        idx = rng.randint(2, max(2, len(state["path"]) - 3))
        state["path"][idx]["ip"] = f"10.{rng.randint(0, 254)}.{rng.randint(0, 254)}.{rng.randint(1, 254)}"
    # Occasionally begin an incident (elevated loss/latency from a hop onward).
    if state["incident"] <= 0 and rng.random() < 0.04:
        state["incident"] = rng.randint(2, 6)
        state["incident_hop"] = rng.randint(2, len(state["path"]) - 1)
        state["incident_kind"] = rng.choice(["loss", "latency", "outage"])
    incident_active = state["incident"] > 0
    if incident_active:
        state["incident"] -= 1

    await asyncio.sleep(min(2.0, count * probe_interval * 0.15))

    hops: list[HopResult] = []
    for i, hop in enumerate(state["path"]):
        loss_probes = 0
        if hop["no_reply"]:
            loss_probes = count
        elif hop["rate_limited"] and rng.random() < 0.5:
            loss_probes = rng.randint(1, max(1, count // 3))
        elif rng.random() < 0.03:
            loss_probes = 1
        latency_mult = 1.0
        if incident_active and i >= state.get("incident_hop", 99):
            kind = state.get("incident_kind")
            if kind == "loss":
                loss_probes = max(loss_probes, rng.randint(count // 4, max(1, count // 2)))
            elif kind == "latency":
                latency_mult = rng.uniform(4.0, 12.0)
            elif kind == "outage":
                loss_probes = count
        received = max(0, count - loss_probes)
        samples = [max(0.05, rng.gauss(hop["base"] * latency_mult, hop["jitter"])) for _ in range(received)]
        if received == 0:
            hops.append(HopResult(i + 1, None, None, 100.0, count, 0, None, None, None, None, None, None, None, None, None, None))
            continue
        avg = sum(samples) / len(samples)
        best = min(samples)
        worst = max(samples)
        stdev = (sum((s - avg) ** 2 for s in samples) / len(samples)) ** 0.5
        diffs = [abs(samples[k] - samples[k - 1]) for k in range(1, len(samples))]
        javg = sum(diffs) / len(diffs) if diffs else 0.0
        jmax = max(diffs) if diffs else 0.0
        hops.append(
            HopResult(
                hop_no=i + 1,
                ip=hop["ip"],
                asn=hop["asn"],
                loss_pct=round(100.0 * loss_probes / count, 1),
                sent=count,
                received=received,
                last_ms=round(samples[-1], 3),
                avg_ms=round(avg, 3),
                best_ms=round(best, 3),
                worst_ms=round(worst, 3),
                stdev_ms=round(stdev, 3),
                gmean_ms=round(avg, 3),
                jitter_ms=round(diffs[-1], 3) if diffs else 0.0,
                jitter_avg_ms=round(javg, 3),
                jitter_max_ms=round(jmax, 3),
                jitter_int_ms=round(javg * 1.4, 3),
            )
        )
    finished = time.time()
    return MtrResult(True, started, finished, f"[simulated] mtr --json -n -c {count} {dst_ip}", src="simulator", dst_ip=dst_ip, hops=hops)


async def mtr_version() -> str | None:
    if shutil.which(config.mtr_binary) is None:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            config.mtr_binary, "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return out.decode("utf-8", "replace").strip().splitlines()[0] if out else None
    except Exception:  # noqa: BLE001
        return None
