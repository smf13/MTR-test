"""Outbound notifications: generic JSON webhooks and Pushover."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger("mtr-tracker.notify")

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
EVENT_KINDS = ("down", "recovered", "degraded", "route_change")

# Default Pushover priority per event when the setting is "auto".
_AUTO_PRIORITY = {"down": 1, "degraded": 0, "recovered": 0, "route_change": -1}


class NotifyError(Exception):
    """Raised by the test endpoint so the UI can show why delivery failed."""


# Tests inject an httpx.MockTransport here; production leaves it None.
_TRANSPORT: httpx.BaseTransport | None = None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0, transport=_TRANSPORT)


async def send_webhook(url: str, payload: dict[str, Any]) -> None:
    if not url:
        raise NotifyError("webhook URL is empty")
    try:
        async with _client() as client:
            resp = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        raise NotifyError(f"webhook request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise NotifyError(f"webhook returned HTTP {resp.status_code}: {resp.text[:200]}")


def pushover_priority(settings: dict[str, Any], kind: str) -> int:
    raw = settings.get("pushover_priority", "auto")
    if raw in (None, "", "auto"):
        return _AUTO_PRIORITY.get(kind, 0)
    try:
        return max(-2, min(2, int(raw)))
    except (TypeError, ValueError):
        return 0


async def send_pushover(settings: dict[str, Any], *, title: str, message: str, kind: str, url: str | None = None, url_title: str | None = None) -> None:
    token = (settings.get("pushover_api_token") or "").strip()
    user = (settings.get("pushover_user_key") or "").strip()
    if not token or not user:
        raise NotifyError("Pushover needs both an application API token and a user/group key")
    data: dict[str, Any] = {
        "token": token,
        "user": user,
        "title": title[:250],
        "message": message[:1024],
        "priority": pushover_priority(settings, kind),
    }
    device = (settings.get("pushover_device") or "").strip()
    if device:
        data["device"] = device
    sound = (settings.get("pushover_sound") or "").strip()
    if sound:
        data["sound"] = sound
    if data["priority"] == 2:
        # Emergency priority requires retry/expire; keep them modest.
        data["retry"] = 60
        data["expire"] = 1800
    if url:
        data["url"] = url
        if url_title:
            data["url_title"] = url_title
    try:
        async with _client() as client:
            resp = await client.post(PUSHOVER_URL, data=data)
    except httpx.HTTPError as exc:
        raise NotifyError(f"Pushover request failed: {exc}") from exc
    if resp.status_code >= 400:
        try:
            errors = resp.json().get("errors") or []
        except ValueError:
            errors = []
        raise NotifyError(f"Pushover returned HTTP {resp.status_code}: {'; '.join(errors) or resp.text[:200]}")


def target_url(settings: dict[str, Any], target_id: int | None) -> str | None:
    base = (settings.get("base_url") or "").strip().rstrip("/")
    if not base or target_id is None:
        return None
    return f"{base}/targets/{target_id}"


def format_pushover_text(payload: dict[str, Any]) -> tuple[str, str]:
    """Build (title, message) for a Pushover push from an event payload."""
    target = payload.get("target") or {}
    site = payload.get("source") or "MTR Tracker"
    title = f"{site}: {target.get('name') or 'event'}"
    lines = [str(payload.get("message") or payload.get("event"))]
    details = payload.get("details") or {}
    extras = []
    if details.get("loss_pct") is not None:
        extras.append(f"loss {float(details['loss_pct']):.1f}%")
    if details.get("avg_ms") is not None:
        extras.append(f"avg {float(details['avg_ms']):.1f} ms")
    if details.get("worst_ms") is not None:
        extras.append(f"worst {float(details['worst_ms']):.1f} ms")
    if extras:
        lines.append(" · ".join(extras))
    if target.get("host"):
        lines.append(f"host: {target['host']}")
    return title, "\n".join(lines)


async def dispatch_event(settings: dict[str, Any], payload: dict[str, Any]) -> None:
    """Fan an event payload out to every enabled channel; failures are logged, never raised."""
    kind = str(payload.get("event"))
    tasks = []
    url = (settings.get("webhook_url") or "").strip()
    if url and kind in (settings.get("webhook_events") or []):
        tasks.append(_guard("webhook", send_webhook(url, payload)))
    if settings.get("pushover_enabled") and kind in (settings.get("pushover_events") or []):
        title, message = format_pushover_text(payload)
        link = target_url(settings, (payload.get("target") or {}).get("id"))
        tasks.append(_guard("pushover", send_pushover(settings, title=title, message=message, kind=kind, url=link, url_title="Open in MTR Tracker")))
    if tasks:
        await asyncio.gather(*tasks)


async def _guard(channel: str, coro: Any) -> None:
    try:
        await coro
    except NotifyError as exc:
        log.warning("%s delivery failed: %s", channel, exc)
    except Exception:  # noqa: BLE001
        log.exception("%s delivery crashed", channel)
