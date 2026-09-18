"""Shared test machinery: build the app in simulation mode against a temporary database."""

from __future__ import annotations

import asyncio
import importlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient


def reload_app_modules() -> Any:
    """config is evaluated at import time; reload it and every module that imports it, in dependency order."""
    from app import config as config_mod

    importlib.reload(config_mod)
    from app import api as api_mod, geoip as geoip_mod, globalping as gp_mod, main as main_mod, mtr as mtr_mod, probes as probes_mod, scheduler as sched_mod

    for m in (mtr_mod, probes_mod, gp_mod, geoip_mod, sched_mod, api_mod, main_mod):
        importlib.reload(m)
    return main_mod


@asynccontextmanager
async def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, token: str = "", static_dir: Path | None = None) -> AsyncIterator[AsyncClient]:
    """A running app (lifespan started) behind an in-process HTTP client."""
    monkeypatch.setenv("MTR_TRACKER_SIMULATE", "1")
    monkeypatch.setenv("MTR_TRACKER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MTR_TRACKER_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MTR_TRACKER_STATIC_DIR", str(static_dir or tmp_path / "missing"))
    if token:
        monkeypatch.setenv("MTR_TRACKER_API_TOKEN", token)
    else:
        monkeypatch.delenv("MTR_TRACKER_API_TOKEN", raising=False)
    main_mod = reload_app_modules()
    app = main_mod.create_app()
    async with main_mod.lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    if token:
        # Leave the modules configured without a token for tests that import them directly.
        monkeypatch.delenv("MTR_TRACKER_API_TOKEN")
        reload_app_modules()


def app_of(client: AsyncClient) -> Any:
    """The FastAPI app behind a test client; its state holds the database and the scheduler."""
    return client._transport.app  # type: ignore[attr-defined]


async def wait_for_runs(c: AsyncClient, target_id: int, n: int, timeout: float = 15.0) -> list[dict]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        r = await c.get(f"/api/targets/{target_id}/runs")
        items = r.json()["items"]
        if len(items) >= n:
            return items
        await asyncio.sleep(0.2)
    raise AssertionError("runs did not appear in time")
