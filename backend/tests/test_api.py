"""API tests running the app in simulation mode against a temporary database (fixtures live in conftest.py)."""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from helpers import wait_for_runs as _wait_for_runs


async def test_target_lifecycle_and_run(client: AsyncClient) -> None:
    r = await client.post("/api/targets", json={"name": "Test", "host": "192.0.2.10", "interval_sec": 60, "count": 3})
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["last_status"] == "pending" and t["tags"] == []

    runs = await _wait_for_runs(client, t["id"], 1)
    run = runs[0]
    assert run["status"] == "ok" and run["reached"] is True and run["hop_count"] > 0

    detail = (await client.get(f"/api/runs/{run['id']}")).json()
    assert detail["target_type"] == "mtr" and detail["target_name"] == "Test"
    assert len(detail["hops"]) == run["hop_count"]
    assert detail["hops"][-1]["ip"] == "192.0.2.10"
    assert {"loss_pct", "avg_ms", "best_ms", "worst_ms", "stdev_ms", "jitter_avg_ms"} <= set(detail["hops"][0])

    report = await client.get(f"/api/runs/{run['id']}/report")
    assert report.status_code == 200 and "Loss%" in report.text

    series = (await client.get(f"/api/targets/{t['id']}/series?range=1h")).json()
    assert series["points"] and series["points"][0]["run_id"] == run["id"]
    hist = (await client.get(f"/api/targets/{t['id']}/hops/history?range=1h")).json()
    assert hist["max_hops"] == run["hop_count"]
    summary = (await client.get(f"/api/targets/{t['id']}/hops/summary?range=1h")).json()
    assert summary["total_runs"] >= 1 and summary["hops"][-1]["primary"]["ip"] == "192.0.2.10"

    full = (await client.get(f"/api/targets/{t['id']}?range=1h")).json()
    assert full["stats"]["runs"] >= 1 and full["last_status"] in {"up", "degraded"}

    r = await client.put(f"/api/targets/{t['id']}", json={"enabled": False, "tags": ["a", "a", " b "]})
    assert r.json()["enabled"] is False and r.json()["tags"] == ["a", "b"]

    assert (await client.delete(f"/api/targets/{t['id']}")).status_code == 204
    assert (await client.get(f"/api/targets/{t['id']}")).status_code == 404


async def test_validation_and_settings(client: AsyncClient) -> None:
    r = await client.post("/api/targets", json={"name": "bad", "host": "-evil", "interval_sec": 5})
    assert r.status_code == 422
    r = await client.post("/api/targets", json={"name": "bad", "host": "has space.example"})
    assert r.status_code == 422

    s = (await client.put("/api/settings", json={"retention_days": 7, "webhook_url": "https://example.com/hook"})).json()
    assert s["retention_days"] == 7 and s["webhook_url"] == "https://example.com/hook"
    assert (await client.put("/api/settings", json={"webhook_url": "ftp://nope"})).status_code == 422

    status = (await client.get("/api/status")).json()
    assert status["simulate"] is True and "targets" in status


async def test_unresolvable_host_marks_target_down(client: AsyncClient) -> None:
    r = await client.post("/api/targets", json={"name": "Nope", "host": "definitely-not-a-real-host.invalid", "interval_sec": 60})
    t = r.json()
    runs = await _wait_for_runs(client, t["id"], 1)
    assert runs[0]["status"] == "error" and "DNS" in (runs[0]["error"] or "")
    full = (await client.get(f"/api/targets/{t['id']}")).json()
    assert full["last_status"] == "down"
    events = (await client.get("/api/events", params={"target_id": t["id"]})).json()
    assert events["total"] >= 1 and events["items"][0]["kind"] == "down"


async def test_notification_test_endpoint(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app import notify

    calls: list[str] = []
    monkeypatch.setattr(notify, "_TRANSPORT", httpx.MockTransport(lambda r: (calls.append(str(r.url)), httpx.Response(200, json={"status": 1}))[1]))

    r = await client.post("/api/notifications/test", json={"channel": "pushover"})
    assert r.status_code == 502 and "token" in r.json()["detail"]

    r = await client.post("/api/notifications/test", json={"channel": "pushover", "settings": {"pushover_api_token": "t", "pushover_user_key": "u"}})
    assert r.status_code == 200 and calls[-1] == notify.PUSHOVER_URL

    r = await client.post("/api/notifications/test", json={"channel": "webhook", "settings": {"webhook_url": "https://hooks.example/abc"}})
    assert r.status_code == 200 and calls[-1] == "https://hooks.example/abc"

    saved = (await client.put("/api/settings", json={"pushover_enabled": True, "pushover_priority": "1", "pushover_events": ["down", "down"]})).json()
    assert saved["pushover_enabled"] is True and saved["pushover_priority"] == "1" and saved["pushover_events"] == ["down"]
    assert (await client.put("/api/settings", json={"pushover_priority": "5"})).status_code == 422
    assert (await client.put("/api/settings", json={"base_url": "http://mtr.local:8899/"})).json()["base_url"] == "http://mtr.local:8899"


async def test_schedule_is_measured_from_run_start(client: AsyncClient) -> None:
    r = await client.post("/api/targets", json={"name": "Cadence", "host": "192.0.2.20", "interval_sec": 60, "count": 3})
    t = r.json()
    runs = await _wait_for_runs(client, t["id"], 1)
    # allow the finally-block to reschedule
    await asyncio.sleep(0.3)
    full = (await client.get(f"/api/targets/{t['id']}")).json()
    from datetime import datetime

    started = datetime.fromisoformat(runs[0]["started_at"].replace("Z", "+00:00")).timestamp()
    next_at = datetime.fromisoformat(full["next_run_at"].replace("Z", "+00:00")).timestamp()
    assert abs((next_at - started) - 60) < 1.5, "next run must be start + interval, not finish + interval"


async def test_visual_endpoints(client: AsyncClient) -> None:
    r = await client.post("/api/targets", json={"name": "Vis", "host": "192.0.2.30", "interval_sec": 60, "count": 3})
    t = r.json()
    await _wait_for_runs(client, t["id"], 1)

    listing = (await client.get("/api/targets")).json()
    me = next(x for x in listing if x["id"] == t["id"])
    tl = me["timeline"]
    assert tl["bucket_sec"] == 1800 and len(tl["buckets"]) == 48
    assert tl["buckets"][-1] is not None and tl["buckets"][-1]["s"] in {"up", "degraded", "down"}

    ov = (await client.get("/api/overview/series?range=1h")).json()
    mine = next(x for x in ov["targets"] if x["id"] == t["id"])
    assert mine["points"] and mine["points"][-1]["n"] >= 1 and ov["bucket_sec"] >= 10

    hourly = (await client.get(f"/api/targets/{t['id']}/hourly?range=24h")).json()
    assert len(hourly["hours"]) >= 1 and hourly["hours"][-1]["n"] >= 1

    routes = (await client.get(f"/api/targets/{t['id']}/routes?range=1h")).json()
    assert routes["total_runs"] >= 1 and len(routes["segments"]) >= 1
    assert routes["routes"][0]["share_pct"] > 0 and routes["segments"][0]["index"] == routes["routes"][0]["index"]


async def test_probe_types_end_to_end(client: AsyncClient) -> None:
    specs = [
        {"name": "Ping demo", "host": "192.0.2.40", "type": "ping", "interval_sec": 60, "count": 4},
        {"name": "HTTP demo", "host": "https://status.example.test/health", "type": "http", "interval_sec": 60, "options": {"keyword": "ok", "json_path": "status", "json_expected": "ok"}},
        {"name": "TCP demo", "host": "192.0.2.41", "type": "tcp", "port": 443, "interval_sec": 60},
        {"name": "DNS demo", "host": "example.test", "type": "dns", "interval_sec": 60, "options": {"record_type": "A", "expected": "192.0.2"}},
    ]
    ids = []
    for spec in specs:
        r = await client.post("/api/targets", json=spec)
        assert r.status_code == 201, r.text
        assert r.json()["type"] == spec["type"]
        ids.append(r.json()["id"])
    for tid, spec in zip(ids, specs):
        runs = await _wait_for_runs(client, tid, 1)
        run = (await client.get(f"/api/runs/{runs[0]['id']}")).json()
        assert run["status"] == "ok" and run["hop_count"] == 0 and run["hops"] == []
        assert run["avg_ms"] is not None and isinstance(run["details"], dict)
        if spec["type"] == "ping":
            assert "samples_ms" in run["details"]
        if spec["type"] == "http":
            assert run["details"]["status"] in (200, 503) and run["details"]["keyword"] == "ok"
        if spec["type"] == "tcp":
            assert run["details"]["port"] == 443
        if spec["type"] == "dns":
            assert run["details"]["record_type"] == "A" and run["details"]["answers"]

    listing = {t["id"]: t for t in (await client.get("/api/targets")).json()}
    assert listing[ids[1]]["alert_latency_ms"] == 1500 and listing[ids[0]]["alert_latency_ms"] == 200
    explicit = (await client.post("/api/targets", json={"name": "HTTP strict", "host": "https://s.test", "type": "http", "alert_latency_ms": 300})).json()
    assert explicit["alert_latency_ms"] == 300

    # option validation
    assert (await client.post("/api/targets", json={"name": "bad tcp", "host": "192.0.2.1", "type": "tcp"})).status_code == 422
    assert (await client.post("/api/targets", json={"name": "bad http", "host": "https://x.test", "type": "http", "options": {"expected_status": "abc"}})).status_code == 422
    # switching type re-validates options
    r = await client.put(f"/api/targets/{ids[0]}", json={"type": "http", "options": {"method": "HEAD"}})
    assert r.status_code == 200 and r.json()["type"] == "http" and r.json()["options"]["method"] == "HEAD" and r.json()["options"]["expected_status"] == "200-299"


async def test_export_import_bulk(client: AsyncClient) -> None:
    await client.post("/api/targets", json={"name": "Exp A", "host": "192.0.2.50", "interval_sec": 60})
    await client.post("/api/targets", json={"name": "Exp B", "host": "https://b.test", "type": "http", "interval_sec": 60})
    exported = (await client.get("/api/targets/export")).json()
    assert {e["name"] for e in exported} >= {"Exp A", "Exp B"} and "id" not in exported[0] and "options" in exported[0]

    exported[0]["interval_sec"] = 120
    res = (await client.post("/api/targets/import", json={"targets": exported + [{"name": "Exp C", "host": "192.0.2.51"}], "mode": "upsert"})).json()
    assert res["created"] == 1 and res["updated"] == len(exported)
    listing = (await client.get("/api/targets")).json()
    byname = {t["name"]: t for t in listing}
    assert byname[exported[0]["name"]]["interval_sec"] == 120 and "Exp C" in byname

    ids = [byname["Exp A"]["id"], byname["Exp C"]["id"]]
    r = (await client.post("/api/targets/bulk", json={"action": "pause", "ids": ids})).json()
    assert sorted(r["affected"]) == sorted(ids)
    listing = {t["id"]: t for t in (await client.get("/api/targets")).json()}
    assert not listing[ids[0]]["enabled"] and not listing[ids[1]]["enabled"]
    await client.post("/api/targets/bulk", json={"action": "resume", "ids": ids})
    assert (await client.post("/api/targets/bulk", json={"action": "delete", "ids": ids})).status_code == 200
    assert (await client.get(f"/api/targets/{ids[0]}")).status_code == 404
    assert (await client.post("/api/targets/bulk", json={"action": "run", "ids": [999999]})).status_code == 404

    res = (await client.post("/api/targets/import", json={"targets": [{"name": "Only", "host": "192.0.2.60"}], "mode": "replace"})).json()
    assert res["total"] == 1


async def test_tags_are_sorted_and_tag_colours_validated(client: AsyncClient) -> None:
    r = await client.post("/api/targets", json={"name": "Tagged", "host": "192.0.2.80", "interval_sec": 60, "enabled": False, "tags": ["zeta", "Alpha", " mid ", "zeta"]})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert r.json()["tags"] == ["Alpha", "mid", "zeta"]
    r = await client.put(f"/api/targets/{tid}", json={"tags": ["c", "B", "a"]})
    assert r.json()["tags"] == ["a", "B", "c"]

    # Rows written before tags were sorted (or by hand) read back sorted too.
    db = client._transport.app.state.db  # type: ignore[attr-defined]
    await db.execute("UPDATE targets SET tags = ? WHERE id = ?", ('["b", "A", "c"]', tid))
    assert (await client.get(f"/api/targets/{tid}")).json()["tags"] == ["A", "b", "c"]

    s = (await client.put("/api/settings", json={"tag_colors": {"b": "#FF0000", " c ": "#00ff00", "A": ""}})).json()
    assert s["tag_colors"] == {"b": "#ff0000", "c": "#00ff00"}
    assert (await client.put("/api/settings", json={"tag_colors": {"b": "red"}})).status_code == 422
    assert (await client.put("/api/settings", json={"tag_colors": {"b": "#12345"}})).status_code == 422

    tags = (await client.get("/api/tags")).json()
    assert tags == [{"name": "A", "count": 1, "color": None}, {"name": "b", "count": 1, "color": "#ff0000"}, {"name": "c", "count": 1, "color": "#00ff00"}]

    assert (await client.put("/api/settings", json={"tag_colors": {}})).json()["tag_colors"] == {}
    assert all(t["color"] is None for t in (await client.get("/api/tags")).json())


async def test_api_token_protects_writes(protected_client: AsyncClient) -> None:
    c = protected_client
    assert (await c.get("/api/targets")).status_code == 200  # reads stay open
    r = await c.post("/api/targets", json={"name": "T", "host": "192.0.2.70"})
    assert r.status_code == 401 and r.headers.get("www-authenticate") == "Bearer"
    r = await c.post("/api/targets", json={"name": "T", "host": "192.0.2.70"}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    r = await c.post("/api/targets", json={"name": "T", "host": "192.0.2.70"}, headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 201
    r = await c.delete(f"/api/targets/{r.json()['id']}", headers={"X-Api-Token": "s3cret"})
    assert r.status_code == 204


async def test_hop_history_downsamples_when_over_max_runs(client: AsyncClient) -> None:
    """Regression: with more runs than max_runs the endpoint used to raise (query param shadowed builtin range)."""
    import time

    r = await client.post("/api/targets", json={"name": "Busy", "host": "192.0.2.20", "interval_sec": 60, "count": 3, "enabled": False})
    assert r.status_code == 201, r.text
    t = r.json()
    db = client._transport.app.state.db  # type: ignore[attr-defined]
    now = time.time()
    total = 150
    for i in range(total):
        started = now - (total - i) * 10
        run_id = await db.execute(
            "INSERT INTO runs(target_id, started_at, finished_at, duration_ms, status, reached, hop_count, loss_pct, avg_ms) "
            "VALUES (?, ?, ?, 100, 'ok', 1, 2, 0, 10)",
            (t["id"], started, started + 0.1),
            commit=False,
        )
        await db.executemany(
            "INSERT INTO hops(run_id, hop_no, ip, loss_pct, sent, received, avg_ms) VALUES (?, ?, ?, 0, 3, 3, ?)",
            [(run_id, 1, "10.0.0.1", 1.0), (run_id, 2, "192.0.2.20", 10.0)],
            commit=True,
        )

    r = await client.get(f"/api/targets/{t['id']}/hops/history?range=1h&max_runs=50")
    assert r.status_code == 200, r.text
    hist = r.json()
    assert len(hist["runs"]) == 50 and hist["max_hops"] == 2
    ts = [x["t"] for x in hist["runs"]]
    assert ts == sorted(ts)
    assert all(len(x["hops"]) == 2 for x in hist["runs"])

    r = await client.get(f"/api/targets/{t['id']}/hops/history?range=1h")
    assert r.status_code == 200 and len(r.json()["runs"]) == 120
