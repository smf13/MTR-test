"""Notification cooldown: at most one alert per target and kind of alert within `notify_cooldown_min` minutes.

Events are always recorded; only their delivery to the channels is held back. Each target has two independent
windows, one for status alerts (down, degraded, recovered) and one for route changes, so a route change never
silences a DOWN. Inside a window:

- a problem getting worse than the last alert delivered (degraded -> down) goes out at once and starts a new
  window; a problem coming back after a delivered recovery is held like any other alert;
- any other alert is held back, keeping only the latest;
- when the window ends, the latest held-back alert is delivered as a catch-up, with the number held back, if it
  says something the last delivered alert did not (a different status; any route change). A target that flapped
  and settled where it was produces nothing more. The catch-up starts a new window, so a target that keeps
  flapping sends at most one alert per cooldown period.

The windows live in memory: a restart forgets them (the next alert is delivered) and drops held-back alerts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("mtr-tracker.cooldown")

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
# The status a status alert leaves the target in; held-back alerts are compared by it.
STATUS_AFTER = {"down": "down", "degraded": "degraded", "recovered": "up"}
MAX_COOLDOWN_MIN = 1440

Deliver = Callable[[dict[str, Any]], Awaitable[None]]


def category(kind: str) -> str:
    return "route" if kind == "route_change" else "status"


def _escalates(sent: str, new: str) -> bool:
    """A problem getting worse (degraded -> down) is never held back; a problem coming back after a recovery is
    exactly the flapping the cooldown exists for, so it is."""
    return sent != "info" and SEVERITY_RANK.get(new, 0) > SEVERITY_RANK.get(sent, 0)


def cooldown_seconds(settings: dict[str, Any]) -> float:
    try:
        minutes = float(settings.get("notify_cooldown_min") or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(float(MAX_COOLDOWN_MIN), minutes)) * 60.0


@dataclass
class _Window:
    until: float
    cooldown: float
    sent_kind: str
    sent_severity: str
    held: dict[str, Any] | None = None
    held_count: int = 0
    timer: asyncio.Task[None] | None = field(default=None, repr=False)


class NotifyGate:
    """Decides when an event payload is delivered; `deliver` does the delivery (settings, mute and channels)."""

    def __init__(self, deliver: Deliver, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._deliver = deliver
        self._clock = clock
        self._sleep = sleep
        self._windows: dict[tuple[int, str], _Window] = {}
        # Every timer task, including one still delivering its catch-up after its window was replaced.
        self._tasks: set[asyncio.Task[None]] = set()

    def submit(self, payload: dict[str, Any], settings: dict[str, Any]) -> bool:
        """True when the caller should deliver the payload now; False when it is held back for a later catch-up."""
        cooldown = cooldown_seconds(settings)
        kind = str(payload.get("event"))
        key = (int((payload.get("target") or {}).get("id") or 0), category(kind))
        now = self._clock()
        win = self._windows.get(key)
        if cooldown <= 0:
            self._drop(key)
            return True
        severity = str(payload.get("severity") or "info")
        if win is None or now >= win.until or _escalates(win.sent_severity, severity):
            self._drop(key)
            self._windows[key] = _Window(until=now + cooldown, cooldown=cooldown, sent_kind=kind, sent_severity=severity)
            return True
        win.held = payload
        win.held_count += 1
        if win.timer is None:
            win.timer = asyncio.create_task(self._flush_later(key, win), name=f"mtr-tracker-cooldown-{key[0]}-{key[1]}")
            self._tasks.add(win.timer)
            win.timer.add_done_callback(self._tasks.discard)
        log.info("notification held back by the cooldown: %s (%d held for target %s)", payload.get("message"), win.held_count, key[0])
        return False

    def held(self, target_id: int) -> int:
        """Alerts currently held back for a target (both windows)."""
        return sum(w.held_count for (tid, _), w in self._windows.items() if tid == target_id)

    def forget(self, target_ids: list[int]) -> None:
        """Drop the windows (and held alerts) of deleted targets."""
        for key in [k for k in self._windows if k[0] in target_ids]:
            self._drop(key)

    async def close(self) -> None:
        """Cancel the catch-up timers; held-back alerts are dropped (the events stay recorded)."""
        timers = list(self._tasks)
        self._windows.clear()
        for t in timers:
            t.cancel()
        if timers:
            await asyncio.gather(*timers, return_exceptions=True)

    def _drop(self, key: tuple[int, str]) -> None:
        win = self._windows.pop(key, None)
        if win is not None and win.timer is not None and win.timer is not asyncio.current_task():
            win.timer.cancel()

    async def _flush_later(self, key: tuple[int, str], win: _Window) -> None:
        await self._sleep(max(0.0, win.until - self._clock()))
        if self._windows.get(key) is not win:
            return
        win.timer = None
        held, count = win.held, win.held_count
        if held is None:
            return
        kind = str(held.get("event"))
        news = key[1] == "route" or STATUS_AFTER.get(kind) != STATUS_AFTER.get(win.sent_kind)
        if not news:
            # Flapped and settled where the last alert left it: the events page has the details.
            log.info("cooldown ended for target %s: %d held-back alert(s) dropped, status unchanged", key[0], count)
            self._windows.pop(key, None)
            return
        # The catch-up opens the next window, so a target that keeps flapping sends one alert per period.
        self._windows[key] = _Window(until=self._clock() + win.cooldown, cooldown=win.cooldown, sent_kind=kind, sent_severity=str(held.get("severity") or "info"))
        try:
            await self._deliver(catch_up(held, count))
        except Exception:  # noqa: BLE001
            log.exception("catch-up notification for target %s failed", key[0])


def catch_up(payload: dict[str, Any], held: int) -> dict[str, Any]:
    """The latest held-back alert, marked as a catch-up with the number of alerts the cooldown held back."""
    out = dict(payload)
    out["cooldown"] = {"held_back": held}
    out["message"] = f"{payload.get('message')} ({held} alert{'s' if held != 1 else ''} held back by the cooldown)"
    return out
