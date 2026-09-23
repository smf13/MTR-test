"""Regression tests for the review fixes: validation, deletion during a run, secret masking, scheduler and database behaviour."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from app import scheduler as sched_mod
from helpers import app_of


def _hop(no: int, ip: str | None, sent: int = 3) -> Any:
    from app.mtr import HopResult

    received = sent if ip else 0
    m = 5.0 if received else None
    return HopResult(no, ip, None, 100.0 * (sent - received) / sent, sent, received, m, m, m, m, m, m, m, m, m, m)


def _result(dst: str, ips: list[str | None]) -> Any:
    from app.mtr import MtrResult

    now = time.time()
    return MtrResult(True, now - 1, now, "[fake] mtr", src="test", dst_ip=dst, hops=[_hop(i + 1, ip) for i, ip in enumerate(ips)])


# ---------------------------------------------------------------------------
# API validation and routing
# ---------------------------------------------------------------------------


async def test_update_rejects_nulls_but_allows_clearing_the_port(client: AsyncClient) -> None:
    t = (await client.post("/api/targets", json={"name": "N", "host": "192.0.2.90", "interval_sec": 60, "enabled": False})).json()
    for body in ({"name": None}, {"tags": None}, {"interval_sec": None}, {"enabled": None}, {"description": None}):
        r = await client.put(f"/api/targets/{t['id']}", json=body)
        assert r.status_code == 422, body
        assert "cannot be null" in r.json()["detail"]
    r = await client.put(f"/api/targets/{t['id']}", json={"protocol": "udp", "port": 33434})
    assert r.status_code == 200 and r.json()["port"] == 33434
    r = await client.put(f"/api/targets/{t['id']}", json={"port": None})
    assert r.status_code == 200 and r.json()["port"] is None

    tcp = (await client.post("/api/targets", json={"name": "T", "host": "192.0.2.91", "type": "tcp", "port": 443, "interval_sec": 60, "enabled": False})).json()
    assert (await client.put(f"/api/targets/{tcp['id']}", json={"port": None})).status_code == 422
    assert (await client.put(f"/api/targets/{tcp['id']}", json={"port": 8443})).json()["port"] == 8443


async def test_unknown_api_path_is_404_while_the_spa_is_still_served(static_client: AsyncClient) -> None:
    r = await static_client.get("/api/does-not-exist")
    assert r.status_code == 404 and r.json()["detail"] == "Not Found"
    r = await static_client.get("/targets/1")
    assert r.status_code == 200 and "MTR Tracker" in r.text
    assert (await static_client.get("/api/status")).status_code == 200


async def test_non_ascii_bearer_token_is_rejected_not_crashed(protected_client: AsyncClient) -> None:
    headers = {b"authorization": "Bearer tést".encode("latin-1")}
    r = await protected_client.post("/api/targets", json={"name": "X", "host": "192.0.2.1"}, headers=headers)
    assert r.status_code == 401


async def test_secrets_are_masked_without_the_token(protected_client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    c = protected_client
    auth = {"Authorization": "Bearer s3cret"}
    body = {"pushover_api_token": "abcdefghijkl", "pushover_user_key": "u123456789", "webhook_url": "https://hooks.example/abc?key=1", "site_name": "NOC", "globalping_token": "gp_secret_token_1234"}
    saved = (await c.put("/api/settings", json=body, headers=auth)).json()
    assert saved["pushover_api_token"] == "abcdefghijkl" and saved["webhook_url"] == body["webhook_url"] and saved["globalping_token"] == body["globalping_token"]

    masked = (await c.get("/api/settings")).json()
    assert masked["pushover_api_token"] == "********ijkl"
    assert masked["pushover_user_key"] == "********6789"
    assert masked["globalping_token"] == "********1234"
    assert masked["webhook_url"] == "https://hooks.example/********"
    assert masked["site_name"] == "NOC"
    assert (await c.get("/api/settings", headers=auth)).json()["pushover_api_token"] == "abcdefghijkl"

    # Echoing masked values back (as a UI without the token would) changes nothing.
    r = (await c.put("/api/settings", json={**masked, "site_name": "NOC 2"}, headers=auth)).json()
    assert r["site_name"] == "NOC 2" and r["pushover_api_token"] == "abcdefghijkl" and r["webhook_url"] == body["webhook_url"]

    # The test endpoint merges unsaved form values; a masked webhook URL falls back to the stored one.
    from app import notify

    calls: list[str] = []
    monkeypatch.setattr(notify, "_TRANSPORT", httpx.MockTransport(lambda req: (calls.append(str(req.url)), httpx.Response(200, json={"status": 1}))[1]))
    r2 = await c.post("/api/notifications/test", json={"channel": "webhook", "settings": {"webhook_url": masked["webhook_url"]}}, headers=auth)
    assert r2.status_code == 200 and calls[-1] == body["webhook_url"]

    assert (await c.get("/api/status")).json()["database"] is None
    status = (await c.get("/api/status", headers=auth)).json()
    # Token holders see where the data lives, never the password.
    from urllib.parse import urlsplit

    from helpers import TEST_ADMIN_DSN, TEST_DB_NAME

    assert status["database"].startswith("postgresql://") and status["database"].endswith("/" + TEST_DB_NAME)
    # The URL carries no password part at all. A substring check misfired when the password equals the user
    # name (CI's postgres:postgres), because the user name is rightly still shown.
    shown = urlsplit(status["database"])
    assert shown.password is None and ":" not in (shown.netloc.rpartition("@")[0])
    assert shown.username == urlsplit(TEST_ADMIN_DSN).username
    assert status["db_size_bytes"] > 0


async def test_quick_trace_concurrency_is_bounded(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import api as api_mod

    await client.put("/api/settings", json={"reverse_dns": False})
    running = peak = 0

    async def fake_run_mtr(**kw: Any) -> Any:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.2)
        running -= 1
        return _result(kw["dst_ip"], ["10.0.0.1", kw["dst_ip"]])

    monkeypatch.setattr(api_mod, "run_mtr", fake_run_mtr)
    results = await asyncio.gather(*(client.post("/api/probe", json={"host": "192.0.2.95", "count": 3}) for _ in range(5)))
    assert all(r.status_code == 200 and r.json()["reached"] for r in results)
    assert peak == api_mod.ADHOC_PROBE_SLOTS


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


async def _wait_until_running(client: AsyncClient, target_id: int) -> asyncio.Task[None]:
    sched = app_of(client).state.scheduler
    for _ in range(100):
        if target_id in sched._running:
            return sched._running[target_id]
        await asyncio.sleep(0.05)
    raise AssertionError("run did not start")


async def test_delete_cancels_a_run_in_flight(client: AsyncClient) -> None:
    # count=30 makes the simulated run take about two seconds, long enough to delete the target underneath it.
    t = (await client.post("/api/targets", json={"name": "Busy", "host": "192.0.2.92", "interval_sec": 60, "count": 30})).json()
    task = await _wait_until_running(client, t["id"])
    assert (await client.delete(f"/api/targets/{t['id']}")).status_code == 204
    assert task.done() and task.cancelled()
    assert t["id"] not in app_of(client).state.scheduler.active_run_ids
    db = app_of(client).state.db
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM runs WHERE target_id = ?", (t["id"],)))["n"] == 0

    a = (await client.post("/api/targets", json={"name": "Bulk A", "host": "192.0.2.96", "interval_sec": 60, "count": 30})).json()
    b = (await client.post("/api/targets", json={"name": "Bulk B", "host": "192.0.2.97", "interval_sec": 60, "count": 30})).json()
    tasks = [await _wait_until_running(client, a["id"]), await _wait_until_running(client, b["id"])]
    assert (await client.post("/api/targets/bulk", json={"action": "delete", "ids": [a["id"], b["id"]]})).status_code == 200
    assert all(x.cancelled() for x in tasks) and not app_of(client).state.scheduler.active_run_ids


async def test_route_changes_are_not_reported_across_outages(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    dst = "192.0.2.99"
    tid = (await client.post("/api/targets", json={"name": "Route", "host": dst, "interval_sec": 60, "enabled": False, "alert_loss_pct": 0, "alert_latency_ms": 0})).json()["id"]
    await client.put("/api/settings", json={"reverse_dns": False})
    app = app_of(client)
    sched, db = app.state.scheduler, app.state.db
    settings = await db.get_settings()

    async def probe(ips: list[str | None]) -> None:
        async def fake(**kw: Any) -> Any:
            return _result(dst, ips)

        monkeypatch.setattr(sched_mod, "run_mtr", fake)
        t = dict(await db.fetchone("SELECT * FROM targets WHERE id = ?", (tid,)))
        await sched._execute(t, settings)

    await probe(["10.0.0.1", "10.0.0.2", dst])        # up
    await probe(["10.0.0.1", None, None, None, None])  # outage: mtr pads the path with unknown hops
    await probe(["10.0.0.1", "10.0.0.2", dst])        # recovered on the same route
    await probe(["10.0.0.1", "10.0.0.9", dst])        # a real reroute
    await probe(["10.0.0.1", None, dst])              # one hop silent: wildcard, not a reroute

    runs = (await client.get(f"/api/targets/{tid}/runs")).json()["items"]
    assert [r["route_changed"] for r in reversed(runs)] == [False, False, False, True, False]
    assert [r["reached"] for r in reversed(runs)] == [True, False, True, True, True]
    events = [e["kind"] for e in reversed((await client.get(f"/api/targets/{tid}/events")).json())]
    assert events == ["down", "recovered", "route_change"]
    assert (await client.get(f"/api/targets/{tid}")).json()["last_status"] == "up"


async def test_notifications_are_tracked_and_drained_on_stop(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    app = app_of(client)
    sched = app.state.scheduler
    delivered: list[str] = []

    async def slow_dispatch(settings: dict[str, Any], payload: dict[str, Any]) -> None:
        await asyncio.sleep(0.2)
        delivered.append(payload["event"])

    monkeypatch.setattr(sched_mod, "dispatch_event", slow_dispatch)
    t = (await client.post("/api/targets", json={"name": "Notify", "host": "192.0.2.94", "interval_sec": 60, "enabled": False})).json()
    await sched._event(t, None, "down", "critical", "test", {}, await app.state.db.get_settings())
    assert sched.pending_notifications == 1
    await sched.stop()  # waits for the delivery instead of dropping it
    assert delivered == ["down"] and sched.pending_notifications == 0


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


async def test_transaction_is_atomic(client: AsyncClient) -> None:
    db = app_of(client).state.db
    t = (await client.post("/api/targets", json={"name": "Tx", "host": "192.0.2.98", "interval_sec": 60, "enabled": False})).json()

    async def runs() -> int:
        return int((await db.fetchone("SELECT COUNT(*) AS n FROM runs WHERE target_id = ?", (t["id"],)))["n"])

    with pytest.raises(RuntimeError):
        async with db.transaction() as tx:
            await tx.execute("INSERT INTO runs(target_id, started_at, status) VALUES (?, ?, 'ok')", (t["id"], time.time()))
            raise RuntimeError("boom")
    assert await runs() == 0

    async with db.transaction() as tx:
        rid = await tx.fetchval("INSERT INTO runs(target_id, started_at, status) VALUES (?, ?, 'ok') RETURNING id", (t["id"], time.time()))
        await tx.executemany("INSERT INTO hops(run_id, hop_no, ip, loss_pct, sent, received) VALUES (?, ?, '10.0.0.1', 0, 3, 3)", [(rid, 1), (rid, 2)])
    assert await runs() == 1
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM hops WHERE run_id = ?", (rid,)))["n"] == 2


async def test_purge_runs_in_batches_and_keeps_recent_data(client: AsyncClient) -> None:
    db = app_of(client).state.db
    t = (await client.post("/api/targets", json={"name": "Old", "host": "192.0.2.93", "interval_sec": 60, "enabled": False})).json()
    now = time.time()
    old = now - 40 * 86400
    async with db.transaction() as tx:
        await tx.executemany("INSERT INTO runs(target_id, started_at, status) VALUES (?, ?, 'ok')", [(t["id"], old + i) for i in range(12_000)])
        await tx.executemany("INSERT INTO runs(target_id, started_at, status) VALUES (?, ?, 'ok')", [(t["id"], now - i) for i in range(10)])
    ids = [r["id"] for r in await db.fetchall("SELECT id FROM runs WHERE started_at < ?", (now - 86400,))]
    async with db.transaction() as tx:
        await tx.executemany("INSERT INTO hops(run_id, hop_no, ip, loss_pct, sent, received) VALUES (?, 1, '10.0.0.1', 0, 3, 3)", [(i,) for i in ids[:1000]])
        await tx.executemany(
            "INSERT INTO events(target_id, run_id, kind, severity, message, created_at) VALUES (?, ?, 'down', 'critical', 'x', ?)",
            [(t["id"], ids[0], now), (t["id"], ids[1], old)],
        )

    assert await db.purge_older_than(30, batch=5000) == 12_000
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM runs"))["n"] == 10
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM hops"))["n"] == 0
    events = await db.fetchall("SELECT run_id FROM events")
    assert len(events) == 1 and events[0]["run_id"] is None  # recent event kept with its run reference cleared; old one purged
    indexes = {r["name"] for r in await db.fetchall("SELECT indexname AS name FROM pg_indexes WHERE schemaname = current_schema()")}
    assert {"idx_events_run", "idx_runs_started"} <= indexes


async def test_connect_fails_fast_on_an_unreadable_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """A permission error while asyncpg looks for client certificates is a configuration mistake, not a server still starting."""
    import asyncpg

    from app.db import Database

    async def denied(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError(13, "Permission denied", "/root/.postgresql/postgresql.key")

    monkeypatch.setattr(asyncpg, "create_pool", denied)
    monkeypatch.setenv("HOME", "/root")
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="sslmode=disable") as info:
        await Database("postgresql://mtr:secret@db:5432/mtr_tracker", connect_timeout=30).connect()
    assert "HOME=/root" in str(info.value) and "postgresql.key" in str(info.value) and "secret" not in str(info.value)
    assert time.monotonic() - started < 1, "no retries for a permission error"


async def test_connect_names_the_host_network_case_when_the_name_does_not_resolve(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """A service name that does not resolve is what network_mode: host looks like; the log and the error say so."""
    import socket

    import asyncpg

    from app.db import Database

    async def unresolved(*args: Any, **kwargs: Any) -> Any:
        raise socket.gaierror(-3, "Temporary failure in name resolution")

    monkeypatch.setattr(asyncpg, "create_pool", unresolved)
    with pytest.raises(RuntimeError, match="docker-compose.host.yml") as info:
        await Database("postgresql://mtr@db:5432/mtr_tracker", connect_timeout=1).connect()
    assert "name resolution" in str(info.value) and "docker-compose.host.yml" in caplog.text


async def test_connect_explains_a_password_changed_after_the_first_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """The postgres image keeps the password its volume was created with; the error says how to change it on the server."""
    import asyncpg

    from app.db import Database

    async def refused(*args: Any, **kwargs: Any) -> Any:
        raise asyncpg.InvalidPasswordError('password authentication failed for user "mtr"')

    monkeypatch.setattr(asyncpg, "create_pool", refused)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="ALTER USER") as info:
        await Database("postgresql://mtr:changed@db:5432/mtr_tracker", connect_timeout=30).connect()
    assert "password authentication failed" in str(info.value) and "mtr:changed@" not in str(info.value)
    assert time.monotonic() - started < 1, "a refused password is final, not retried"
