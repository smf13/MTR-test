"""API tests running the app in simulation mode against a temporary database."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MTR_TRACKER_SIMULATE", "1")
    monkeypatch.setenv("MTR_TRACKER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MTR_TRACKER_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MTR_TRACKER_STATIC_DIR", str(tmp_path / "missing"))
    # config is evaluated at import time; reload the modules for this test.
    import importlib

    from app import config as config_mod

    importlib.reload(config_mod)
    from app import api as api_mod, main as main_mod, mtr as mtr_mod, scheduler as sched_mod

    for m in (mtr_mod, sched_mod, api_mod, main_mod):
        importlib.reload(m)
    app = main_mod.create_app()
    async with main_mod.lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def _wait_for_runs(c: AsyncClient, target_id: int, n: int, timeout: float = 15.0) -> list[dict]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        r = await c.get(f"/api/targets/{target_id}/runs")
        items = r.json()["items"]
        if len(items) >= n:
            return items
        await asyncio.sleep(0.2)
    raise AssertionError("runs did not appear in time")


async def test_target_lifecycle_and_run(client: AsyncClient) -> None:
    r = await client.post("/api/targets", json={"name": "Test", "host": "192.0.2.10", "interval_sec": 60, "count": 3})
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["last_status"] == "pending" and t["tags"] == []

    runs = await _wait_for_runs(client, t["id"], 1)
    run = runs[0]
    assert run["status"] == "ok" and run["reached"] is True and run["hop_count"] > 0

    detail = (await client.get(f"/api/runs/{run['id']}")).json()
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
