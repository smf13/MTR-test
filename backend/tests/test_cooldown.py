"""Notification cooldown (app.cooldown.NotifyGate) and its use by the scheduler."""

from __future__ import annotations

import asyncio
from typing import Any

from app.cooldown import NotifyGate, cooldown_seconds


class FakeTime:
    """A clock the test moves; the gate's sleep jumps the clock to the wake-up time."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.release = asyncio.Event()

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        await self.release.wait()
        self.now += seconds


def alert(kind: str, target_id: int = 1, **extra: Any) -> dict[str, Any]:
    severity = {"down": "critical", "degraded": "warning"}.get(kind, "info")
    return {"event": kind, "severity": severity, "message": f"{kind} {extra.get('n', '')}".strip(), "target": {"id": target_id, "name": "T", "host": "h"}}


ON = {"notify_cooldown_min": 10}


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def test_cooldown_seconds() -> None:
    assert cooldown_seconds({}) == 0 and cooldown_seconds({"notify_cooldown_min": 0}) == 0
    assert cooldown_seconds(ON) == 600 and cooldown_seconds({"notify_cooldown_min": 99999}) == 1440 * 60
    assert cooldown_seconds({"notify_cooldown_min": "junk"}) == 0


async def test_off_sends_everything() -> None:
    gate = NotifyGate(lambda p: asyncio.sleep(0))
    assert all(gate.submit(alert(k), {"notify_cooldown_min": 0}) for k in ("down", "recovered", "down", "recovered"))


async def test_flapping_sends_one_alert_and_a_catch_up_only_when_the_status_moved() -> None:
    ft, delivered = FakeTime(), []

    async def deliver(p: dict[str, Any]) -> None:
        delivered.append(p)

    gate = NotifyGate(deliver, clock=ft.clock, sleep=ft.sleep)
    assert gate.submit(alert("down"), ON) is True
    assert gate.submit(alert("recovered"), ON) is False
    assert gate.submit(alert("down", n=2), ON) is False
    assert gate.submit(alert("recovered", n=2), ON) is False
    assert gate.held(1) == 3

    ft.release.set()
    await settle()
    # The target ended up (after the DOWN alert that went out), so one catch-up says so and counts what was held.
    assert [p["event"] for p in delivered] == ["recovered"]
    assert delivered[0]["message"] == "recovered 2 (3 alerts held back by the cooldown)" and delivered[0]["cooldown"] == {"held_back": 3}
    # The catch-up opened a new window: the next flap is held again.
    ft.release.clear()
    assert gate.submit(alert("down", n=3), ON) is False
    await gate.close()


async def test_settling_where_the_last_alert_left_it_sends_nothing() -> None:
    ft, delivered = FakeTime(), []

    async def deliver(p: dict[str, Any]) -> None:
        delivered.append(p)

    gate = NotifyGate(deliver, clock=ft.clock, sleep=ft.sleep)
    gate.submit(alert("down"), ON)
    gate.submit(alert("recovered"), ON)
    gate.submit(alert("down", n=2), ON)
    ft.release.set()
    await settle()
    assert delivered == [] and gate.held(1) == 0
    assert gate.submit(alert("recovered"), ON) is True  # window closed: the next alert goes straight out


async def test_escalation_route_changes_and_other_targets_are_not_held() -> None:
    gate = NotifyGate(lambda p: asyncio.sleep(0), clock=FakeTime().clock, sleep=FakeTime().sleep)
    assert gate.submit(alert("degraded"), ON) is True
    assert gate.submit(alert("down"), ON) is True          # worse than the last alert: never held back
    assert gate.submit(alert("degraded"), ON) is False
    assert gate.submit(alert("route_change"), ON) is True  # its own window
    assert gate.submit(alert("route_change"), ON) is False
    assert gate.submit(alert("down", target_id=2), ON) is True
    gate.forget([1])
    assert gate.held(1) == 0 and gate.submit(alert("degraded"), ON) is True
    await gate.close()


async def test_window_expiry_without_held_alerts() -> None:
    ft = FakeTime()
    gate = NotifyGate(lambda p: asyncio.sleep(0), clock=ft.clock, sleep=ft.sleep)
    assert gate.submit(alert("down"), ON) is True
    ft.now += 601
    assert gate.submit(alert("recovered"), ON) is True


async def test_scheduler_holds_back_repeated_alerts(client: Any, monkeypatch: Any) -> None:
    from app import scheduler as sched_mod
    from helpers import app_of

    app = app_of(client)
    sched, db = app.state.scheduler, app.state.db
    delivered: list[str] = []

    async def record(settings: dict[str, Any], payload: dict[str, Any]) -> None:
        delivered.append(payload["event"])

    monkeypatch.setattr(sched_mod, "dispatch_event", record)
    r = await client.put("/api/settings", json={"notify_cooldown_min": 10, "webhook_url": "https://hooks.example.test/mtr"})
    assert r.status_code == 200 and r.json()["notify_cooldown_min"] == 10
    assert (await client.put("/api/settings", json={"notify_cooldown_min": 1441})).status_code == 422
    assert (await client.put("/api/settings", json={"notify_cooldown_min": -1})).status_code == 422

    t = (await client.post("/api/targets", json={"name": "Flappy", "host": "192.0.2.95", "interval_sec": 60, "enabled": False})).json()
    settings = await db.get_settings()
    for kind, severity in (("down", "critical"), ("recovered", "info"), ("down", "critical")):
        await sched._event(t, None, kind, severity, f"{kind}!", {}, settings)
    await settle()
    assert delivered == ["down"] and sched.notify_gate.held(t["id"]) == 2

    # Every event is still recorded; the held ones say so.
    events = (await client.get(f"/api/targets/{t['id']}/events")).json()
    assert [e["kind"] for e in events] == ["down", "recovered", "down"]
    assert [e["details"].get("notification") for e in events] == ["held back", "held back", None]

    # A kind no channel takes neither sends nor holds anything.
    await sched._event(t, None, "route_change", "info", "route", {}, {**settings, "webhook_events": ["down"]})
    assert sched.notify_gate.held(t["id"]) == 2

    # Deleting the target drops its held alerts.
    assert (await client.delete(f"/api/targets/{t['id']}")).status_code == 204
    assert sched.notify_gate.held(t["id"]) == 0
