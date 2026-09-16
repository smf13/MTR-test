"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import router
from .config import config
from .db import Database
from .scheduler import Scheduler

logging.basicConfig(
    level=getattr(logging, config.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("hopwatch")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db = Database(config.db_path)
    await db.connect()
    scheduler = Scheduler(db)
    app.state.db = db
    app.state.scheduler = scheduler
    await scheduler.start()
    log.info("HopWatch %s listening on %s:%s", __version__, config.host, config.port)
    try:
        yield
    finally:
        await scheduler.stop()
        await db.close()


def create_app() -> FastAPI:
    app = FastAPI(title="HopWatch", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.include_router(router)

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
        async def spa(path: str) -> FileResponse:
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

    uvicorn.run("app.main:app", host=config.host, port=config.port, log_level=config.log_level.lower())


if __name__ == "__main__":
    main()
