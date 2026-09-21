"""Runtime configuration sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .db import DEFAULT_DATABASE_URL


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    data_dir: Path
    database_url: str
    # The SQLite file of an installation that predates the PostgreSQL backend; read once by the automatic import.
    sqlite_path: Path
    auto_migrate: bool
    db_pool_size: int
    db_connect_timeout: int
    host: str
    port: int
    mtr_binary: str
    simulate: bool
    max_concurrent_runs: int
    static_dir: Path | None
    log_level: str
    api_token: str

    @classmethod
    def from_env(cls) -> "Config":
        data_dir = Path(os.environ.get("MTR_TRACKER_DATA_DIR", "./data")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        sqlite_path = Path(os.environ.get("MTR_TRACKER_DB_PATH", str(data_dir / "mtr-tracker.db"))).resolve()
        max_concurrent_runs = max(1, _env_int("MTR_TRACKER_MAX_CONCURRENT_RUNS", 8))

        static_env = os.environ.get("MTR_TRACKER_STATIC_DIR")
        static_dir: Path | None = None
        if static_env:
            static_dir = Path(static_env).resolve()
        else:
            candidate = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
            if candidate.exists():
                static_dir = candidate

        return cls(
            data_dir=data_dir,
            database_url=os.environ.get("MTR_TRACKER_DATABASE_URL", "").strip() or DEFAULT_DATABASE_URL,
            sqlite_path=sqlite_path,
            auto_migrate=_env_bool("MTR_TRACKER_AUTO_MIGRATE", True),
            # Every in-flight run and every request may hold a connection; keep a few spare for the API.
            db_pool_size=max(2, _env_int("MTR_TRACKER_DB_POOL_SIZE", max(4, max_concurrent_runs + 4))),
            db_connect_timeout=max(1, _env_int("MTR_TRACKER_DB_CONNECT_TIMEOUT", 120)),
            host=os.environ.get("MTR_TRACKER_HOST", "0.0.0.0"),
            port=_env_int("MTR_TRACKER_PORT", 8899),
            mtr_binary=os.environ.get("MTR_TRACKER_MTR_BINARY", "mtr"),
            simulate=_env_bool("MTR_TRACKER_SIMULATE", False),
            max_concurrent_runs=max_concurrent_runs,
            static_dir=static_dir,
            log_level=os.environ.get("MTR_TRACKER_LOG_LEVEL", "info"),
            api_token=os.environ.get("MTR_TRACKER_API_TOKEN", "").strip(),
        )


config = Config.from_env()
