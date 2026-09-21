"""Route timeline and path summary: a hop that answered nothing is a wildcard, not a different route or address."""

from __future__ import annotations

import time

from httpx import AsyncClient

from helpers import app_of

from app.mtr import HopResult, route_signature


def _hop(no: int, ip: str | None, count: int = 10) -> HopResult:
    silent = ip is None
    return HopResult(no, ip, None, 100.0 if silent else 0.0, count, 0 if silent else count, *(None if silent else 5.0 for _ in range(10)))


async def _insert_run(db, target_id: int, started: float, ips: list[str | None], route_changed: bool = False) -> int:
    hops = [_hop(i + 1, ip) for i, ip in enumerate(ips)]
    async with db.transaction() as tx:
        run_id = await tx.fetchval(
            "INSERT INTO runs(target_id, started_at, finished_at, duration_ms, status, dst_ip, reached, hop_count, sent, loss_pct, avg_ms, route_hash, route_changed) "
            "VALUES (?, ?, ?, 1000, 'ok', ?, 1, ?, 10, 0, 5, ?, ?) RETURNING id",
            (target_id, started, started + 1, ips[-1], len(ips), route_signature(hops), int(route_changed)),
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


async def test_chart_markers_forget_recently_seen_routes(client: AsyncClient) -> None:
    """The latency chart drops markers for routes seen among the last N reached runs; the stored flag and events do not change."""
    from app.api import route_change_marks

    def run(sig: str, changed: bool = True, reached: bool = True) -> dict:
        return {"status": "ok", "reached": reached, "route_hash": sig, "route_changed": changed}

    flapping = [run("A", False), run("B"), run("A"), run("B"), run("A"), run("C"), run("B"), run("A")]
    # Memory 1 is the stored flag as it is.
    assert route_change_marks(flapping, [], 1) == [False, True, True, True, True, True, True, True]
    # Memory 3: A and B are known alternates once both were seen; C is new; A and B stay known afterwards.
    assert route_change_marks(flapping, [], 3) == [False, True, False, False, False, True, False, False]
    # The runs just before the range warm the memory, so the first points of the range are judged like the rest.
    assert route_change_marks(flapping[1:3], ["A"], 3) == [True, False]
    assert route_change_marks(flapping[1:3], ["B", "A"], 3) == [False, False]
    # Runs that did not reach the destination neither mark nor enter the memory.
    assert route_change_marks([run("A", False), run("B", False, reached=False), run("B")], [], 5) == [False, False, True]

    r = await client.post("/api/targets", json={"name": "Flap", "host": "192.0.2.91", "interval_sec": 60, "enabled": False})
    tid = r.json()["id"]
    db = app_of(client).state.db
    now = time.time()
    a, b, c, dst = "198.51.100.1", "198.51.100.2", "198.51.100.3", "192.0.2.91"
    routes = {"A": [a, b, dst], "B": [a, c, dst], "C": [b, c, dst]}
    # 56 runs a minute apart: more than the smallest max_points, so the bucketed branch can be exercised too.
    sequence = "ABABAC" + "AB" * 25
    for i, name in enumerate(sequence):
        await _insert_run(db, tid, now - 3500 + i * 60, routes[name], route_changed=i > 0 and sequence[i - 1] != name)
    changes = sum(1 for i, n in enumerate(sequence) if i > 0 and sequence[i - 1] != n)

    assert (await client.put("/api/settings", json={"route_memory_runs": 0})).status_code == 422
    # Memory 1: every stored change is a marker (raw points, one per run) and nothing is hidden.
    await client.put("/api/settings", json={"route_memory_runs": 1})
    series = (await client.get(f"/api/targets/{tid}/series?range=1h")).json()
    assert series["bucket_sec"] is None and len(series["points"]) == len(sequence)
    assert [p["route_changed"] for p in series["points"]] == [i > 0 and sequence[i - 1] != n for i, n in enumerate(sequence)]
    assert series["route_changes"] == {"stored": changes, "marked": changes, "hidden": 0, "memory": 1}
    # Memory 20: only the first appearances of B and C are markers; the response counts what the memory hid.
    await client.put("/api/settings", json={"route_memory_runs": 20})
    series = (await client.get(f"/api/targets/{tid}/series?range=1h")).json()
    assert [i for i, p in enumerate(series["points"]) if p["route_changed"]] == [1, 5]
    assert series["route_changes"] == {"stored": changes, "marked": 2, "hidden": changes - 2, "memory": 20}
    # Bucketed series apply the same markers per bucket; the counts stay per run.
    series = (await client.get(f"/api/targets/{tid}/series?range=1h&max_points=50")).json()
    assert series["bucket_sec"] and len(series["points"]) < len(sequence)
    flagged = [p["t"] for p in series["points"] if p["route_changed"]]
    assert len(flagged) == 2
    assert series["route_changes"] == {"stored": changes, "marked": 2, "hidden": changes - 2, "memory": 20}
    # The stored flags, and therefore the route-change counts, are untouched.
    detail = (await client.get(f"/api/targets/{tid}?range=1h")).json()
    assert detail["stats"]["route_changes"] == changes
