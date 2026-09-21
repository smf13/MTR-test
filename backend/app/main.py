"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, geoip, migrate
from .api import is_authenticated, router
from .config import config
from .db import Database
from .scheduler import Scheduler

logging.basicConfig(
    level=getattr(logging, config.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("mtr-tracker")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db = Database(config.database_url, pool_size=config.db_pool_size, connect_timeout=config.db_connect_timeout)
    try:
        await db.connect()
        if config.auto_migrate:
            # An installation that still has its SQLite file gets its data carried over before the first probe runs.
            await migrate.auto_import(db, config.sqlite_path)
    except BaseException:
        await db.close()
        raise
    scheduler = Scheduler(db)
    app.state.db = db
    app.state.scheduler = scheduler
    await scheduler.start()
    log.info("MTR Tracker %s listening on %s:%s", __version__, config.host, config.port)
    try:
        yield
    finally:
        await scheduler.stop()
        await db.close()
        geoip.close()


def create_app() -> FastAPI:
    app = FastAPI(title="MTR Tracker", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.include_router(router)

    if config.api_token:

        @app.middleware("http")
        async def require_token_for_writes(request: Request, call_next):  # type: ignore[no-untyped-def]
            """When MTR_TRACKER_API_TOKEN is set, every mutating /api call needs `Authorization: Bearer <token>`."""
            if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"} and not is_authenticated(request):
                return JSONResponse({"detail": "API token required for write operations"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
            return await call_next(request)

        log.info("API write protection enabled (MTR_TRACKER_API_TOKEN is set)")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    static_dir: Path | None = config.static_dir
    if static_dir and static_dir.exists():
        assets = static_dir / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")
        index = static_dir / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> Response:
            if path == "api" or path.startswith("api/"):
                # An unknown API path must answer as the API would, not with the HTML shell and a 200.
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            candidate = static_dir / path
            if path and candidate.is_file() and candidate.resolve().is_relative_to(static_dir.resolve()):
                return FileResponse(candidate)
            return FileResponse(index)

        log.info("serving UI from %s", static_dir)
    else:
        log.warning("no frontend build found; only the API is served")
    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=config.host, port=config.port, log_level=config.log_level.lower(), access_log=False)


if __name__ == "__main__":
    main()
