"""Outbound webhook notifications."""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("hopwatch.notify")


async def send_webhook(url: str, payload: dict[str, Any]) -> bool:
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code >= 400:
                log.warning("webhook %s returned %s", url, resp.status_code)
                return False
            return True
    except httpx.HTTPError as exc:
        log.warning("webhook delivery failed: %s", exc)
        return False
