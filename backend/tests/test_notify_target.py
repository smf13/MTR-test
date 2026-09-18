"""Per-target notification switch: a muted target still records events, but no channel hears about them."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from httpx import AsyncClient

from helpers import app_of, wait_for_runs

from app import notify


async def _drain_notifications(client: AsyncClient) -> None:
    sched = app_of(client).state.scheduler
    for _ in range(50):
        if not sched.pending_notifications:
            return
        await asyncio.sleep(0.05)


async def test_muted_target_records_events_without_delivering_them(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    delivered: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        delivered.append(json.loads(request.content))
        return httpx.Response(200)

    monkeypatch.setattr(notify, "_TRANSPORT", httpx.MockTransport(handler))
    await client.put("/api/settings", json={"webhook_url": "https://hooks.example/mtr", "webhook_events": ["down", "recovered", "degraded", "route_change"]})

    # An unresolvable host goes down on its first run, which is the cheapest event to provoke.
    r = await client.post("/api/targets", json={"name": "Quiet", "host": "quiet.invalid", "interval_sec": 60, "notify": False})
    assert r.status_code == 201 and r.json()["notify"] is False
    quiet = r.json()["id"]
    r = await client.post("/api/targets", json={"name": "Loud", "host": "loud.invalid", "interval_sec": 60})
    assert r.json()["notify"] is True
    loud = r.json()["id"]

    await wait_for_runs(client, quiet, 1)
    await wait_for_runs(client, loud, 1)
    await _drain_notifications(client)

    events = {e["target_id"] for e in (await client.get("/api/events", params={"kind": "down"})).json()["items"]}
    assert {quiet, loud} <= events, "the event itself is recorded for both targets"
    assert [p["target"]["id"] for p in delivered] == [loud], "only the unmuted target reaches the webhook"

    # The switch is part of the editable definition: update, export/import and bulk actions carry it.
    assert (await client.put(f"/api/targets/{quiet}", json={"notify": True})).json()["notify"] is True
    exported = next(t for t in (await client.get("/api/targets/export")).json() if t["name"] == "Quiet")
    assert exported["notify"] is True
    r = (await client.post("/api/targets/bulk", json={"action": "mute", "ids": [quiet, loud]})).json()
    assert sorted(r["affected"]) == sorted([quiet, loud])
    listing = {t["id"]: t for t in (await client.get("/api/targets")).json()}
    assert listing[quiet]["notify"] is False and listing[loud]["notify"] is False
    await client.post("/api/targets/bulk", json={"action": "unmute", "ids": [loud]})
    assert (await client.get(f"/api/targets/{loud}")).json()["notify"] is True
    exported["notify"] = False
    res = (await client.post("/api/targets/import", json={"targets": [exported], "mode": "upsert"})).json()
    assert res["updated"] == 1 and (await client.get(f"/api/targets/{quiet}")).json()["notify"] is False
