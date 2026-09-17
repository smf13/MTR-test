"""Fixtures shared by every test module."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import app_client


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async with app_client(tmp_path, monkeypatch) as c:
        yield c


@pytest.fixture
async def protected_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """App with MTR_TRACKER_API_TOKEN=s3cret: writes need the token and secrets are masked without it."""
    async with app_client(tmp_path, monkeypatch, token="s3cret") as c:
        yield c


@pytest.fixture
async def static_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """App serving a stub frontend build, so the SPA catch-all route is installed."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>MTR Tracker</title>")
    async with app_client(tmp_path, monkeypatch, static_dir=static) as c:
        yield c
