"""Shared test machinery: build the app in simulation mode against a fresh PostgreSQL database."""

from __future__ import annotations

import asyncio
import importlib
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

# A superuser (or a role with CREATEDB) on the test server; every app fixture drops and recreates its own database.
TEST_ADMIN_DSN = os.environ.get("MTR_TRACKER_TEST_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/postgres")
# One fixed name per pytest worker: DROP IF EXISTS ... WITH (FORCE) before CREATE makes a crashed run self-healing and
# leaves the last database behind for a post-mortem. It also means a test holds at most one app fixture at a time.
TEST_DB_NAME = "mtr_tracker_test_" + os.environ.get("PYTEST_XDIST_WORKER", "main").replace("-", "_")
SKIP_HINT = (
    "PostgreSQL is not reachable at MTR_TRACKER_TEST_DATABASE_URL; start one with "
    "`docker run --rm -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16-alpine` or point the variable at a server"
)

_server: dict[str, str | None] = {}


async def _admin_error() -> str | None:
    """Probe the admin DSN once per session; the error text when the server cannot be reached, else None."""
    if "error" not in _server:
        try:
            conn = await asyncpg.connect(TEST_ADMIN_DSN, timeout=5)
            await conn.close()
            _server["error"] = None
        except (OSError, asyncio.TimeoutError, asyncpg.PostgresError) as exc:
            _server["error"] = f"{exc.__class__.__name__}: {exc}"
    return _server["error"]


async def fresh_test_database() -> str:
    """Drop and recreate this worker's test database and return its URL.

    Without a server the app fixtures are skipped with a hint; in CI (the CI variable is set) that is a failure.
    """
    error = await _admin_error()
    if error:
        message = f"{SKIP_HINT} ({error})"
        if os.environ.get("CI"):
            raise RuntimeError(message)
        pytest.skip(message)
    conn = await asyncpg.connect(TEST_ADMIN_DSN, timeout=5)
    try:
        # Two separate statements: neither may run inside a transaction block.
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await conn.close()
    parts = urlsplit(TEST_ADMIN_DSN)
    return urlunsplit((parts.scheme, parts.netloc, f"/{TEST_DB_NAME}", parts.query, ""))


def reload_app_modules() -> Any:
    """config is evaluated at import time; reload it and every module that imports it, in dependency order."""
    from app import config as config_mod

    importlib.reload(config_mod)
    from app import api as api_mod, geoip as geoip_mod, globalping as gp_mod, main as main_mod, mtr as mtr_mod, probes as probes_mod, scheduler as sched_mod

    for m in (mtr_mod, probes_mod, gp_mod, geoip_mod, sched_mod, api_mod, main_mod):
        importlib.reload(m)
    return main_mod


@asynccontextmanager
async def app_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, token: str = "", static_dir: Path | None = None, fresh_database: bool = True
) -> AsyncIterator[AsyncClient]:
    """A running app (lifespan started) behind an in-process HTTP client.

    `fresh_database=False` starts the app against the database of an earlier `app_client` in the same test.
    """
    monkeypatch.setenv("MTR_TRACKER_SIMULATE", "1")
    monkeypatch.setenv("MTR_TRACKER_DATA_DIR", str(tmp_path))
    # The legacy SQLite location: empty unless a migration test puts a file there before the app starts.
    monkeypatch.setenv("MTR_TRACKER_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MTR_TRACKER_DB_CONNECT_TIMEOUT", "5")
    monkeypatch.setenv("MTR_TRACKER_STATIC_DIR", str(static_dir or tmp_path / "missing"))
    if fresh_database:
        monkeypatch.setenv("MTR_TRACKER_DATABASE_URL", await fresh_test_database())
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
