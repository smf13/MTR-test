"""Route timeline and path summary: a hop that answered nothing is a wildcard, not a different route or address."""

from __future__ import annotations

import time

from httpx import AsyncClient

from helpers import app_of

from app.mtr import HopResult, route_signature


def _hop(no: int, ip: str | None, count: int = 10) -> HopResult:
    silent = ip is None
    return HopResult(no, ip, None, 100.0 if silent else 0.0, count, 0 if silent else count, *(None if silent else 5.0 for _ in range(10)))


async def _insert_run(db, target_id: int, started: float, ips: list[str | None]) -> int:
    hops = [_hop(i + 1, ip) for i, ip in enumerate(ips)]
    async with db.transaction() as tx:
        run_id = await tx.execute(
            "INSERT INTO runs(target_id, started_at, finished_at, duration_ms, status, dst_ip, reached, hop_count, sent, loss_pct, avg_ms, route_hash, route_changed) "
            "VALUES (?, ?, ?, 1000, 'ok', ?, 1, ?, 10, 0, 5, ?, 0)",
            (target_id, started, started + 1, ips[-1], len(ips), route_signature(hops)),
        )
        await tx.executemany(
            "INSERT INTO hops(run_id, hop_no, ip, loss_pct, sent, received, avg_ms, best_ms, worst_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(run_id, h.hop_no, h.ip, h.loss_pct, h.sent, h.received, h.avg_ms, h.best_ms, h.worst_ms) for h in hops],
        )
    return run_id


async def test_silent_hop_is_neither_a_new_route_nor_an_alternate_address(client: AsyncClient) -> None:
    r = await client.post("/api/targets", json={"name": "Routes", "host": "192.0.2.90", "interval_sec": 60, "enabled": False})
    tid = r.json()["id"]
    db = app_of(client).state.db
    now = time.time()
    a, b, c, d, dst = "198.51.100.1", "198.51.100.2", "198.51.100.3", "198.51.100.4", "192.0.2.90"
    full = [a, b, c, dst]
    ids = [
        await _insert_run(db, tid, now - 500, full),
        await _insert_run(db, tid, now - 400, [a, None, c, dst]),  # hop 2 rate-limited this run
        await _insert_run(db, tid, now - 300, full),
        await _insert_run(db, tid, now - 200, [a, d, c, dst]),  # a real reroute at hop 2
        await _insert_run(db, tid, now - 100, [a, None, None, dst]),  # ambiguous: attaches to the most frequent route
    ]

    routes = (await client.get(f"/api/targets/{tid}/routes?range=1h")).json()
    assert routes["total_runs"] == 5
    assert [(rt["runs"], rt["variants"], rt["hops"]) for rt in routes["routes"]] == [(4, 3, 4), (1, 1, 4)]
    assert routes["routes"][0]["example_run_id"] == ids[0]
    # One segment for the first three runs (the silent hop does not split it), one for the reroute, one after it.
    assert [(s["runs"], s["index"], s["first_run_id"]) for s in routes["segments"]] == [(3, 0, ids[0]), (1, 1, ids[3]), (1, 0, ids[4])]

    summary = (await client.get(f"/api/targets/{tid}/hops/summary?range=1h")).json()
    hop2 = next(h for h in summary["hops"] if h["hop"] == 2)
    assert hop2["primary"]["ip"] == b and hop2["primary"]["runs"] == 2 and hop2["silent_runs"] == 2
    # Two answered runs of ten packets plus two silent ones: 20 of 40 packets lost at this position.
    assert hop2["primary"]["loss_pct"] == 50.0 and hop2["primary"]["max_loss_pct"] == 100.0
    assert [alt["ip"] for alt in hop2["alternates"]] == [d]
    hop3 = next(h for h in summary["hops"] if h["hop"] == 3)
    assert hop3["primary"]["ip"] == c and hop3["silent_runs"] == 1 and hop3["alternates"] == [] and hop3["primary"]["loss_pct"] == 20.0
    hop1 = next(h for h in summary["hops"] if h["hop"] == 1)
    assert hop1["silent_runs"] == 0 and hop1["primary"]["loss_pct"] == 0.0

    # A position that never answered keeps a "no response" primary rather than disappearing.
    await _insert_run(db, tid, now - 50, [a, b, c, None, None, dst])
    summary = (await client.get(f"/api/targets/{tid}/hops/summary?range=1h")).json()
    hop5 = next(h for h in summary["hops"] if h["hop"] == 5)
    assert hop5["primary"]["ip"] is None and hop5["primary"]["loss_pct"] == 100.0 and hop5["silent_runs"] == 1


async def test_recently_seen_route_is_not_a_change(client: AsyncClient) -> None:
    """ECMP flapping between known routes stays silent; a route outside the memory window is still a change."""
    r = await client.post("/api/targets", json={"name": "Flap", "host": "192.0.2.91", "interval_sec": 60, "enabled": False})
    tid = r.json()["id"]
    app = app_of(client)
    db, sched = app.state.db, app.state.scheduler
    now = time.time()
    a, b, c, d, dst = "198.51.100.1", "198.51.100.2", "198.51.100.3", "198.51.100.4", "192.0.2.91"
    route_a, route_b, route_c = [a, b, dst], [a, c, dst], [a, d, dst]
    for i, ips in enumerate([route_a, route_b, route_a, route_b]):
        await _insert_run(db, tid, now - 400 + i * 100, ips)
    sig_a = route_signature([_hop(i + 1, ip) for i, ip in enumerate(route_a)])
    sig_c = route_signature([_hop(i + 1, ip) for i, ip in enumerate(route_c)])

    # The previous run used route B. With memory 1 that alone counts, so route A looks like a change ...
    changed, previous, prev_ips = await sched._route_changed(tid, sig_a, route_a, {"route_memory_runs": 1})
    assert changed is True and prev_ips == route_b and previous["dst_ip"] == dst
    # ... while a memory of a few runs remembers route A as a known alternate.
    changed, _, prev_ips = await sched._route_changed(tid, sig_a, route_a, {"route_memory_runs": 4})
    assert changed is False and prev_ips == route_b
    # A silent hop on a known route is a wildcard match, not a new route.
    changed, _, _ = await sched._route_changed(tid, "wild", [a, None, dst], {"route_memory_runs": 4})
    assert changed is False
    # A route never seen in the window is a change whatever the memory.
    for memory in (1, 4, 500):
        changed, _, _ = await sched._route_changed(tid, sig_c, route_c, {"route_memory_runs": memory})
        assert changed is True, memory
    # Two runs on route C push A out of a short window (C, C, B), so A becomes a change again until the window
    # is long enough to reach it (C, C, B, A).
    await _insert_run(db, tid, now - 30, route_c)
    await _insert_run(db, tid, now - 20, route_c)
    changed, _, prev_ips = await sched._route_changed(tid, sig_a, route_a, {"route_memory_runs": 3})
    assert changed is True and prev_ips == route_c
    changed, _, _ = await sched._route_changed(tid, sig_a, route_a, {"route_memory_runs": 4})
    assert changed is False

    # The setting itself is validated and saved.
    assert (await client.put("/api/settings", json={"route_memory_runs": 0})).status_code == 422
    assert (await client.put("/api/settings", json={"route_memory_runs": 50})).json()["route_memory_runs"] == 50
    assert (await client.get("/api/settings")).json()["route_memory_runs"] == 50
