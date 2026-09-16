"""Pushover / webhook formatting and delivery (HTTP mocked)."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from app import notify
from app.notify import NotifyError, format_pushover_text, pushover_priority, send_pushover, send_webhook


def test_priority_mapping() -> None:
    assert pushover_priority({"pushover_priority": "auto"}, "down") == 1
    assert pushover_priority({"pushover_priority": "auto"}, "route_change") == -1
    assert pushover_priority({"pushover_priority": "2"}, "recovered") == 2
    assert pushover_priority({"pushover_priority": "garbage"}, "down") == 0


def test_format_text() -> None:
    title, message = format_pushover_text(
        {"source": "NOC", "event": "down", "message": "WAN is DOWN", "target": {"name": "WAN", "host": "203.0.113.1"}, "details": {"loss_pct": 100.0}}
    )
    assert title == "NOC: WAN"
    assert message.splitlines() == ["WAN is DOWN", "loss 100.0%", "host: 203.0.113.1"]


async def test_send_pushover_posts_form(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["form"] = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        return httpx.Response(200, json={"status": 1})

    monkeypatch.setattr(notify, "_TRANSPORT", httpx.MockTransport(handler))
    settings = {"pushover_api_token": "tok", "pushover_user_key": "usr", "pushover_device": "phone", "pushover_priority": "auto"}
    await send_pushover(settings, title="T", message="M", kind="down", url="http://x/targets/1", url_title="Open")
    assert seen["url"] == notify.PUSHOVER_URL
    assert seen["form"] == {"token": "tok", "user": "usr", "title": "T", "message": "M", "priority": "1", "device": "phone", "url": "http://x/targets/1", "url_title": "Open"}


async def test_send_pushover_reports_api_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify, "_TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(400, json={"status": 0, "errors": ["application token is invalid"]})))
    with pytest.raises(NotifyError, match="application token is invalid"):
        await send_pushover({"pushover_api_token": "bad", "pushover_user_key": "usr"}, title="T", message="M", kind="down")
    with pytest.raises(NotifyError, match="both"):
        await send_pushover({"pushover_api_token": "", "pushover_user_key": "usr"}, title="T", message="M", kind="down")


async def test_send_webhook_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify, "_TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(NotifyError, match="HTTP 500"):
        await send_webhook("https://hooks.example/x", {"event": "test"})
