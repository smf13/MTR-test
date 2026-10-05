"""API tests running the app in simulation mode against a fresh PostgreSQL database (fixtures live in conftest.py)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from httpx import AsyncClient

from helpers import app_client, app_of, wait_for_runs as _wait_for_runs, wait_until


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
    assert {"loss_pct", "avg_ms", "best_ms", "worst_ms", "stdev_ms", "jitter_avg_ms", "asn", "as_name"} <= set(detail["hops"][0])
    # The hop tables name the network like the map does (simulation fabricates the names; private hops get none).
    assert detail["hops"][-1]["asn"].startswith("AS") and detail["hops"][-1]["as_name"]
    assert detail["hops"][0]["ip"] == "192.168.1.1" and detail["hops"][0]["as_name"] is None

    report = await client.get(f"/api/runs/{run['id']}/report")
    assert report.status_code == 200 and "Loss%" in report.text

    series = (await client.get(f"/api/targets/{t['id']}/series?range=1h")).json()
    assert series["points"] and series["points"][0]["run_id"] == run["id"]
    hist = (await client.get(f"/api/targets/{t['id']}/hops/history?range=1h")).json()
    assert hist["max_hops"] == run["hop_count"]
    summary = (await client.get(f"/api/targets/{t['id']}/hops/summary?range=1h")).json()
    assert summary["total_runs"] >= 1 and summary["hops"][-1]["primary"]["ip"] == "192.0.2.10"
    assert summary["hops"][-1]["primary"]["as_name"] and summary["hops"][0]["primary"]["as_name"] is None

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
    # DNS over TLS/HTTPS cannot use the system resolver, on create and on update alike.
    r = await client.post("/api/targets", json={"name": "dot", "host": "example.com", "type": "dns", "options": {"transport": "dot"}})
    assert r.status_code == 422
    r = await client.post("/api/targets", json={"name": "dot", "host": "example.com", "type": "dns", "options": {"transport": "dot", "resolver": "192.0.2.53"}})
    assert r.status_code == 201 and r.json()["options"]["transport"] == "dot" and r.json()["options"]["verify_tls"] is True
    r = await client.put(f"/api/targets/{r.json()['id']}", json={"options": {"transport": "doh"}})
    assert r.status_code == 422

    s = (await client.put("/api/settings", json={"retention_days": 7, "webhook_url": "https://example.com/hook"})).json()
    assert s["retention_days"] == 7 and s["webhook_url"] == "https://example.com/hook"
    assert (await client.put("/api/settings", json={"webhook_url": "ftp://nope"})).status_code == 422

    status = (await client.get("/api/status")).json()
    assert status["simulate"] is True and "targets" in status
    # The Settings page lists the configuration the server actually applied, not a static example.
    env = {e["name"]: e["value"] for e in status["environment"]}
    assert env["MTR_TRACKER_SIMULATE"] == "1" and env["MTR_TRACKER_MAX_CONCURRENT_RUNS"] == str(status["max_concurrent_runs"])
    assert env["MTR_TRACKER_API_TOKEN"] == "" and env["MTR_TRACKER_DATABASE_URL"] == status["database"]


async def test_unresolvable_host_marks_target_down(client: AsyncClient) -> None:
    r = await client.post("/api/targets", json={"name": "Nope", "host": "definitely-not-a-real-host.invalid", "interval_sec": 60})
    t = r.json()
    runs = await _wait_for_runs(client, t["id"], 1)
    assert runs[0]["status"] == "error" and "DNS" in (runs[0]["error"] or "")

    async def recorded() -> bool:
        return (await client.get("/api/events", params={"target_id": t["id"]})).json()["total"] >= 1

    assert await wait_until(recorded)
    full = (await client.get(f"/api/targets/{t['id']}")).json()
    assert full["last_status"] == "down"
    events = (await client.get("/api/events", params={"target_id": t["id"]})).json()
    assert events["items"][0]["kind"] == "down"


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
    key = "0123456789abcdef" * 4
    assert (await client.put("/api/settings", json={"pushover_encryption_key": f" {key.upper()} "})).json()["pushover_encryption_key"] == key
    for bad in ("abc", "g" * 64, key + "00"):
        assert (await client.put("/api/settings", json={"pushover_encryption_key": bad})).status_code == 422
    assert (await client.put("/api/settings", json={"pushover_encryption_key": ""})).json()["pushover_encryption_key"] == ""
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


async def test_http_json_query_options(client: AsyncClient) -> None:
    spec = {"name": "Status page", "host": "https://status.example.test/api/v2/summary.json", "type": "http", "interval_sec": 60,
            "options": {"json_query": 'components[id = "yyzkbfz2thpt"].status', "json_operator": "==", "json_expected": "operational"}}
    r = await client.post("/api/targets", json=spec)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert r.json()["options"]["json_query"] == 'components[id = "yyzkbfz2thpt"].status'

    bad = await client.put(f"/api/targets/{tid}", json={"options": {**spec["options"], "json_query": "components[id ="}})
    assert bad.status_code == 422 and "invalid JSON query" in bad.text
    bad = await client.put(f"/api/targets/{tid}", json={"options": {**spec["options"], "json_operator": ">"}})
    assert bad.status_code == 422 and "needs a number" in bad.text

    # A target saved with the former dotted path reads back as the JSON query that replaced it.
    db = client._transport.app.state.db  # type: ignore[attr-defined]
    await db.execute("UPDATE targets SET options = ? WHERE id = ?", ('{"json_path": "data.queue", "json_expected": ">= 5"}', tid))
    opts = (await client.get(f"/api/targets/{tid}")).json()["options"]
    assert opts["json_query"] == "data.queue" and opts["json_operator"] == ">=" and opts["json_expected"] == "5" and "json_path" not in opts


async def test_probe_types_end_to_end(client: AsyncClient) -> None:
    specs = [
        {"name": "Ping demo", "host": "192.0.2.40", "type": "ping", "interval_sec": 60, "count": 4},
        {"name": "HTTP demo", "host": "https://status.example.test/health", "type": "http", "interval_sec": 60, "options": {"keyword": "ok", "json_query": "status", "json_operator": "==", "json_expected": "ok"}},
        {"name": "TCP demo", "host": "192.0.2.41", "type": "tcp", "port": 443, "interval_sec": 60},
        {"name": "DNS demo", "host": "example.test", "type": "dns", "interval_sec": 60, "options": {"record_type": "A", "expected": "192.0.2"}},
        {"name": "DNS uncached", "host": "example.test", "type": "dns", "interval_sec": 60, "options": {"record_type": "A", "random_prefix": True}},
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
            assert run["details"]["json_query"] == "status" and run["details"]["json_operator"] == "=="
            assert run["details"]["tls"]["issuer"] == "Simulated CA" and run["details"]["tls"]["subject"] == "status.example.test" and run["details"]["tls"]["days_left"] == 61
        if spec["type"] == "tcp":
            assert run["details"]["port"] == 443
        if spec["type"] == "dns" and spec["options"].get("random_prefix"):
            assert run["reached"] and run["details"]["rcode"] == "NXDOMAIN" and run["details"]["queried_name"].endswith(".example.test")
        elif spec["type"] == "dns":
            assert run["details"]["record_type"] == "A" and run["details"]["answers"] and run["details"]["queried_name"] == "example.test"

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
        async with db.transaction() as tx:
            run_id = await tx.fetchval(
                "INSERT INTO runs(target_id, started_at, finished_at, duration_ms, status, reached, hop_count, loss_pct, avg_ms) "
                "VALUES (?, ?, ?, 100, 'ok', 1, 2, 0, 10) RETURNING id",
                (t["id"], started, started + 0.1),
            )
            await tx.executemany(
                "INSERT INTO hops(run_id, hop_no, ip, loss_pct, sent, received, avg_ms) VALUES (?, ?, ?, 0, 3, 3, ?)",
                [(run_id, 1, "10.0.0.1", 1.0), (run_id, 2, "192.0.2.20", 10.0)],
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


async def test_overview_series_survives_a_huge_range(client: AsyncClient) -> None:
    """Regression: bucket arithmetic stays in 64-bit integers (a range of decades overflowed a 32-bit multiplier)."""
    r = await client.get("/api/overview/series?range=99999999w&max_points=24")
    assert r.status_code == 200, r.text
    assert r.json()["bucket_sec"] > 10 and r.json()["targets"] == []


async def test_clear_history_and_delete_runs(client: AsyncClient) -> None:
    import time

    db = client._transport.app.state.db  # type: ignore[attr-defined]
    made = (await client.post("/api/targets", json={"name": "Setup", "host": "https://svc.example.test/health", "type": "http", "interval_sec": 60, "enabled": False})).json()
    other = (await client.post("/api/targets", json={"name": "Other", "host": "192.0.2.90", "interval_sec": 60, "enabled": False})).json()
    tid, now = made["id"], time.time()

    async def add_run(target_id: int, age: float, status: str, reached: int) -> int:
        rid = await db.fetchval("INSERT INTO runs(target_id, started_at, status, reached) VALUES (?, ?, ?, ?) RETURNING id", (target_id, now - age, status, reached))
        await db.execute("INSERT INTO events(target_id, run_id, kind, severity, message, details, created_at) VALUES (?, ?, 'down', 'critical', 'x', '{}', ?)", (target_id, rid, now - age))
        return rid

    # Two failed setup runs (a check that missed, an error), then good runs; the latest is good.
    bad_check, bad_error = await add_run(tid, 300, "ok", 0), await add_run(tid, 240, "error", 0)
    good_old, good_new = await add_run(tid, 180, "ok", 1), await add_run(tid, 60, "ok", 1)
    foreign = await add_run(other["id"], 30, "ok", 0)
    await db.execute("UPDATE targets SET last_status = 'up' WHERE id = ?", (tid,))

    r = await client.delete(f"/api/targets/{tid}/runs?status=failed")
    assert r.status_code == 200 and r.json() == {"runs": 2, "events": 2, "status_reset": False}
    left = (await client.get(f"/api/targets/{tid}/runs")).json()
    assert [run["id"] for run in left["items"]] == [good_new, good_old]
    assert (await client.get(f"/api/targets/{tid}")).json()["last_status"] == "up"
    assert await db.fetchval("SELECT COUNT(*) FROM runs WHERE id = ANY(?::bigint[])", ([bad_check, bad_error],)) == 0

    # Chosen runs only, and never another target's: deleting the latest run hands the status back to the next run.
    r = await client.post(f"/api/targets/{tid}/runs/delete", json={"ids": [good_new, foreign, good_new]})
    assert r.json() == {"runs": 1, "events": 1, "status_reset": True}
    assert (await client.get(f"/api/targets/{tid}")).json()["last_status"] == "pending"
    assert await db.fetchval("SELECT COUNT(*) FROM runs WHERE id = ?", (foreign,)) == 1
    assert (await client.post(f"/api/targets/{tid}/runs/delete", json={"ids": []})).status_code == 422

    # Clearing everything also removes events that never had a run.
    await db.execute("INSERT INTO events(target_id, kind, severity, message, details, created_at) VALUES (?, 'route_change', 'info', 'y', '{}', ?)", (tid, now))
    r = await client.delete(f"/api/targets/{tid}/runs")
    assert r.json() == {"runs": 1, "events": 2, "status_reset": True}
    assert (await client.get(f"/api/targets/{tid}/runs")).json()["total"] == 0
    assert (await client.get(f"/api/targets/{tid}/events")).json() == []
    assert len((await client.get(f"/api/targets/{other['id']}/events")).json()) == 1

    assert (await client.delete(f"/api/targets/{tid}/runs?status=bogus")).status_code == 422
    assert (await client.delete("/api/targets/999999/runs")).status_code == 404


async def test_target_groups(client: AsyncClient) -> None:
    r = await client.post("/api/targets", json={"name": "Grouped", "host": "192.0.2.90", "interval_sec": 60, "enabled": False, "group_name": "  Head   office "})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert r.json()["group_name"] == "Head office"
    plain = (await client.post("/api/targets", json={"name": "Plain", "host": "192.0.2.91", "interval_sec": 60, "enabled": False})).json()
    assert plain["group_name"] == ""
    assert (await client.put(f"/api/targets/{tid}", json={"group_name": "Branches"})).json()["group_name"] == "Branches"
    assert (await client.put(f"/api/targets/{tid}", json={"group_name": None})).status_code == 422
    assert (await client.put(f"/api/targets/{tid}", json={"group_name": ""})).json()["group_name"] == ""
    await client.put(f"/api/targets/{tid}", json={"group_name": "Branches"})

    exported = {e["name"]: e for e in (await client.get("/api/targets/export")).json()}
    assert exported["Grouped"]["group_name"] == "Branches"
    # A file exported before groups existed leaves the group alone; one that names a group sets it.
    older = {k: v for k, v in exported["Grouped"].items() if k != "group_name"}
    await client.post("/api/targets/import", json={"targets": [older], "mode": "upsert"})
    assert (await client.get(f"/api/targets/{tid}")).json()["group_name"] == "Branches"
    await client.post("/api/targets/import", json={"targets": [{**exported["Plain"], "group_name": "Branches"}], "mode": "upsert"})
    assert (await client.get(f"/api/targets/{plain['id']}")).json()["group_name"] == "Branches"


async def test_group_list_rename_and_bulk_move(client: AsyncClient) -> None:
    async def make(name: str, group: str) -> int:
        r = await client.post("/api/targets", json={"name": name, "host": "192.0.2.93", "interval_sec": 60, "enabled": False, "group_name": group})
        return int(r.json()["id"])

    a, b, c, d = await make("A", "branches"), await make("B", "Branches"), await make("C", "Datacentre"), await make("D", "")
    assert (await client.get("/api/groups")).json() == [{"name": "Branches", "count": 1}, {"name": "branches", "count": 1}, {"name": "Datacentre", "count": 1}]

    # Bulk move: into a new group, whitespace cleaned; an empty name ungroups.
    r = await client.post("/api/targets/bulk", json={"action": "group", "ids": [a, d], "group_name": "  Branch   offices "})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["affected"]) == sorted([a, d])
    groups = {t["id"]: t["group_name"] for t in (await client.get("/api/targets")).json()}
    assert groups[a] == groups[d] == "Branch offices" and groups[b] == "Branches"
    await client.post("/api/targets/bulk", json={"action": "group", "ids": [d], "group_name": ""})
    assert (await client.get(f"/api/targets/{d}")).json()["group_name"] == ""
    assert (await client.post("/api/targets/bulk", json={"action": "group", "ids": [a]})).status_code == 422
    assert (await client.post("/api/targets/bulk", json={"action": "group", "ids": [999999], "group_name": "X"})).status_code == 404

    # Rename: every member moves, the exact (case-sensitive) name is matched, and a clash merges.
    r = (await client.post("/api/groups/rename", json={"name": "Branch offices", "new_name": " Offices "})).json()
    assert r == {"name": "Branch offices", "new_name": "Offices", "renamed": 1, "merged": False}
    assert (await client.get(f"/api/targets/{a}")).json()["group_name"] == "Offices"
    r = (await client.post("/api/groups/rename", json={"name": "Datacentre", "new_name": "Offices"})).json()
    assert r["merged"] is True and r["renamed"] == 1
    assert (await client.get("/api/groups")).json() == [{"name": "Branches", "count": 1}, {"name": "Offices", "count": 2}]
    assert (await client.get(f"/api/targets/{b}")).json()["group_name"] == "Branches"
    same = (await client.post("/api/groups/rename", json={"name": "Offices", "new_name": "Offices"})).json()
    assert same["renamed"] == 2 and same["merged"] is False
    assert (await client.post("/api/groups/rename", json={"name": "Nowhere", "new_name": "X"})).status_code == 404
    assert (await client.post("/api/groups/rename", json={"name": "Offices", "new_name": "   "})).status_code == 422
    assert (await client.get(f"/api/targets/{c}")).json()["group_name"] == "Offices"


async def test_bulk_tag_and_untag(client: AsyncClient) -> None:
    async def make(name: str, tags: list[str]) -> int:
        r = await client.post("/api/targets", json={"name": name, "host": "192.0.2.94", "interval_sec": 60, "enabled": False, "tags": tags})
        return int(r.json()["id"])

    async def tags_of(tid: int) -> list[str]:
        return (await client.get(f"/api/targets/{tid}")).json()["tags"]

    a, b, c = await make("A", ["wan"]), await make("B", ["core", "wan"]), await make("C", [])

    # Adding keeps each target's other tags, cleans and sorts the names, and reports only the targets that changed.
    r = await client.post("/api/targets/bulk", json={"action": "tag", "ids": [a, b, c], "tags": ["  wan ", "Critical", "wan"]})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["affected"]) == sorted([a, b, c])
    assert sorted(r.json()["changed"]) == sorted([a, b, c])
    assert await tags_of(a) == ["Critical", "wan"]
    assert await tags_of(b) == ["core", "Critical", "wan"]
    assert await tags_of(c) == ["Critical", "wan"]
    again = (await client.post("/api/targets/bulk", json={"action": "tag", "ids": [a, b], "tags": ["wan"]})).json()
    assert again["changed"] == []
    assert (await client.get("/api/tags")).json() == [
        {"name": "core", "count": 1, "color": None},
        {"name": "Critical", "count": 3, "color": None},
        {"name": "wan", "count": 3, "color": None},
    ]

    # Removing takes only the named tags; a target without the tag is left alone.
    r = await client.post("/api/targets/bulk", json={"action": "untag", "ids": [b, c], "tags": ["core", "Critical"]})
    assert sorted(r.json()["changed"]) == sorted([b, c])
    assert await tags_of(b) == ["wan"]
    assert await tags_of(c) == ["wan"]
    assert await tags_of(a) == ["Critical", "wan"]
    assert (await client.post("/api/targets/bulk", json={"action": "untag", "ids": [c], "tags": ["core"]})).json()["changed"] == []

    # Both actions need a tag (an all-blank list counts as none), and unknown targets are a 404.
    assert (await client.post("/api/targets/bulk", json={"action": "tag", "ids": [a]})).status_code == 422
    assert (await client.post("/api/targets/bulk", json={"action": "untag", "ids": [a], "tags": ["  "]})).status_code == 422
    assert (await client.post("/api/targets/bulk", json={"action": "tag", "ids": [999999], "tags": ["x"]})).status_code == 404


async def test_bulk_tag_limit_is_all_or_nothing(client: AsyncClient) -> None:
    full = [f"t{i:02d}" for i in range(20)]
    r = await client.post("/api/targets", json={"name": "Full", "host": "192.0.2.95", "interval_sec": 60, "enabled": False, "tags": full})
    full_id = int(r.json()["id"])
    r = await client.post("/api/targets", json={"name": "Roomy", "host": "192.0.2.96", "interval_sec": 60, "enabled": False})
    roomy_id = int(r.json()["id"])

    r = await client.post("/api/targets/bulk", json={"action": "tag", "ids": [roomy_id, full_id], "tags": ["extra"]})
    assert r.status_code == 422
    assert "Full" in r.json()["detail"] and "20" in r.json()["detail"]
    # Neither target changed, including the one that had room.
    assert (await client.get(f"/api/targets/{roomy_id}")).json()["tags"] == []
    assert (await client.get(f"/api/targets/{full_id}")).json()["tags"] == full
    # A tag the full target already carries is no addition at all.
    assert (await client.post("/api/targets/bulk", json={"action": "tag", "ids": [roomy_id, full_id], "tags": ["t00"]})).status_code == 200
    assert (await client.get(f"/api/targets/{roomy_id}")).json()["tags"] == ["t00"]


async def test_group_column_is_added_to_an_existing_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with app_client(tmp_path, monkeypatch) as c:
        tid = (await c.post("/api/targets", json={"name": "Old", "host": "192.0.2.92", "interval_sec": 60, "enabled": False})).json()["id"]
        db = c._transport.app.state.db  # type: ignore[attr-defined]
        await db.execute("ALTER TABLE targets DROP COLUMN group_name")
    async with app_client(tmp_path, monkeypatch, fresh_database=False) as c:
        assert (await c.get(f"/api/targets/{tid}")).json()["group_name"] == ""


async def test_status_history_rebuilds_periods_from_status_events(client: AsyncClient) -> None:
    import json as _json
    import time as _time

    tid = (await client.post("/api/targets", json={"name": "Hist", "host": "192.0.2.93", "interval_sec": 60, "enabled": False})).json()["id"]
    db = client._transport.app.state.db  # type: ignore[attr-defined]
    now = _time.time()
    empty = (await client.get(f"/api/targets/{tid}/status-history?range=24h")).json()
    assert empty["start"] is None and empty["periods"] == [] and empty["paused"] is True

    async def run_at(at: float) -> None:
        await db.execute("INSERT INTO runs(target_id, started_at, status, reached) VALUES (?, ?, 'ok', 1)", (tid, at))

    async def event(kind: str, at: float, previous: str, current: str) -> None:
        await db.execute(
            "INSERT INTO events(target_id, kind, severity, message, details, created_at) VALUES (?, ?, 'info', ?, ?, ?)",
            (tid, kind, f"{kind} at {at}", _json.dumps({"previous": previous, "current": current}), at),
        )

    # Two days ago it went down and came back before this range; inside the range: degraded, down, up again.
    await run_at(now - 2 * 86400)
    await event("down", now - 2 * 86400 + 60, "up", "down")
    await event("recovered", now - 2 * 86400 + 600, "down", "up")
    await event("degraded", now - 3600 * 10, "up", "degraded")
    await event("down", now - 3600 * 9, "degraded", "down")
    await event("recovered", now - 3600 * 8, "down", "up")
    hist = (await client.get(f"/api/targets/{tid}/status-history?range=24h")).json()
    assert [p["status"] for p in hist["periods"]] == ["up", "down", "degraded", "up"]
    assert hist["periods"][0]["ongoing"] is True and hist["periods"][0]["end"] is None
    assert abs(hist["periods"][1]["duration_sec"] - 3600) <= 2 and hist["periods"][1]["message"].startswith("down at")
    assert abs(hist["totals"]["down"] - 3600) <= 2 and abs(hist["totals"]["degraded"] - 3600) <= 2
    assert hist["changes"] == 3 and 91 < hist["uptime_pct"] < 92

    # A target whose first verdict came in the range: no pending stretch, the first status starts at its first run.
    fresh = (await client.post("/api/targets", json={"name": "Fresh", "host": "192.0.2.94", "interval_sec": 60, "enabled": False})).json()["id"]
    await db.execute("INSERT INTO runs(target_id, started_at, status, reached) VALUES (?, ?, 'error', 0)", (fresh, now - 600))
    await db.execute(
        "INSERT INTO events(target_id, kind, severity, message, details, created_at) VALUES (?, 'down', 'critical', 'gone', ?, ?)",
        (fresh, _json.dumps({"previous": "pending", "current": "down"}), now - 590),
    )
    hist = (await client.get(f"/api/targets/{fresh}/status-history?range=1h")).json()
    assert [p["status"] for p in hist["periods"]] == ["down"] and abs(hist["periods"][0]["duration_sec"] - 600) <= 2

    # The target's status strip follows the requested range.
    t = (await client.get(f"/api/targets/{tid}?range=7d")).json()
    assert t["timeline"]["bucket_sec"] == 7 * 86400 // 48 and len(t["timeline"]["buckets"]) == 48
    assert (await client.get(f"/api/targets/{tid}")).json()["timeline"]["bucket_sec"] == 1800


async def test_dashboard_reports_24h_average_and_median_latency(client: AsyncClient) -> None:
    t = (await client.post("/api/targets", json={"name": "Median", "host": "192.0.2.91", "interval_sec": 60, "enabled": False})).json()
    db = app_of(client).state.db
    now = time.time()
    # Three reached runs and an unreached one whose latency must not count; one run older than 24 hours.
    for started, avg, reached in [(now - 300, 10.0, 1), (now - 200, 20.0, 1), (now - 100, 90.0, 1), (now - 50, 500.0, 0), (now - 90000, 1000.0, 1)]:
        await db.execute(
            "INSERT INTO runs(target_id, started_at, finished_at, duration_ms, status, reached, hop_count, sent, loss_pct, avg_ms) "
            "VALUES (?, ?, ?, 1000, 'ok', ?, 0, 1, 0, ?)",
            (t["id"], started, started + 1, reached, avg),
        )
    listed = next(x for x in (await client.get("/api/targets")).json() if x["id"] == t["id"])
    assert listed["stats_24h"]["avg_ms"] == 40.0 and listed["stats_24h"]["median_ms"] == 20.0
    detail = (await client.get(f"/api/targets/{t['id']}?range=24h")).json()
    assert detail["stats"]["avg_ms"] == 40.0 and detail["stats"]["p50_ms"] == 20.0
