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


KEY = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"


def _openssl_decrypt(field: str, key_hex: str) -> str:
    """Decrypt one field the way Pushover documents it, with the openssl CLI as an independent implementation."""
    import gzip
    import hashlib
    import hmac
    import shutil
    import subprocess
    from base64 import b64decode

    raw = b64decode(field)
    iv, ciphertext, mac = raw[:16], raw[16:-32], raw[-32:]
    assert hmac.compare_digest(mac, hmac.new(bytes.fromhex(key_hex), iv + ciphertext, hashlib.sha256).digest())
    if shutil.which("openssl") is None:
        pytest.skip("openssl is not installed")
    out = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-K", key_hex, "-iv", iv.hex()], input=ciphertext, capture_output=True, check=True)
    return gzip.decompress(out.stdout).decode("utf-8")


def test_pushover_encrypt_matches_the_documented_scheme() -> None:
    from base64 import b64decode

    text = "Office WAN is DOWN: destination unreachable · ünïcödé"
    a, b = notify.pushover_encrypt(text, KEY), notify.pushover_encrypt(text, KEY)
    assert a != b and b64decode(a)[:16] != b64decode(b)[:16]  # a fresh random IV every time
    assert (len(b64decode(a)) - 16 - 32) % 16 == 0           # IV + whole AES blocks + HMAC
    assert _openssl_decrypt(a, KEY) == text
    with pytest.raises(NotifyError, match="64 hexadecimal"):
        notify.pushover_encrypt(text, "abcd")


async def test_send_pushover_encrypts_title_message_and_link(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["form"] = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        return httpx.Response(200, json={"status": 1})

    monkeypatch.setattr(notify, "_TRANSPORT", httpx.MockTransport(handler))
    settings = {"pushover_api_token": "tok", "pushover_user_key": "usr", "pushover_priority": "auto", "pushover_encryption_key": KEY}
    await send_pushover(settings, title="MTR Tracker: WAN", message="WAN is DOWN" + "x" * 2000, kind="down", url="https://mtr.example/targets/1", url_title="Open")
    form = seen["form"]
    assert form["encrypted"] == "1" and form["token"] == "tok" and form["user"] == "usr" and form["priority"] == "1"
    assert "WAN" not in form["title"] and "DOWN" not in form["message"] and "mtr.example" not in form["url"]
    assert _openssl_decrypt(form["title"], KEY) == "MTR Tracker: WAN"
    assert _openssl_decrypt(form["message"], KEY) == ("WAN is DOWN" + "x" * 2000)[:1024]  # the limit applies to the readable text
    assert _openssl_decrypt(form["url"], KEY) == "https://mtr.example/targets/1"
    assert _openssl_decrypt(form["url_title"], KEY) == "Open"

    # Without a key nothing changes; a broken stored key fails loudly instead of sending in clear text.
    await send_pushover({**settings, "pushover_encryption_key": ""}, title="T", message="M", kind="down")
    assert "encrypted" not in seen["form"] and seen["form"]["message"] == "M"
    with pytest.raises(NotifyError, match="64 hexadecimal"):
        await send_pushover({**settings, "pushover_encryption_key": "zz" * 32}, title="T", message="M", kind="down")
