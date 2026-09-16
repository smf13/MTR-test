"""Runtime configuration sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    db_path: Path
    host: str
    port: int
    mtr_binary: str
    simulate: bool
    max_concurrent_runs: int
    static_dir: Path | None
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        data_dir = Path(os.environ.get("MTR_TRACKER_DATA_DIR", "./data")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = Path(os.environ.get("MTR_TRACKER_DB_PATH", str(data_dir / "mtr-tracker.db"))).resolve()

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
            db_path=db_path,
            host=os.environ.get("MTR_TRACKER_HOST", "0.0.0.0"),
            port=_env_int("MTR_TRACKER_PORT", 8080),
            mtr_binary=os.environ.get("MTR_TRACKER_MTR_BINARY", "mtr"),
            simulate=_env_bool("MTR_TRACKER_SIMULATE", False),
            max_concurrent_runs=max(1, _env_int("MTR_TRACKER_MAX_CONCURRENT_RUNS", 4)),
            static_dir=static_dir,
            log_level=os.environ.get("MTR_TRACKER_LOG_LEVEL", "info"),
        )


config = Config.from_env()
