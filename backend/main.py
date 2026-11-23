"""MandiMitra FastAPI application entrypoint."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.dependencies import get_db, init_backend_context
from backend.routers import advisor, analytics, backtest, dashboard, health, pipeline
from config import configure_logging, get_settings
from database.connection import DatabaseManager
from services.dashboard_service import prime_overview_cache

logger = logging.getLogger(__name__)


def _warm_caches(db: DatabaseManager) -> None:
    """Background warm-up against the remote database: pooled connections, the default overview, and the
    default backtest (whose ~22k-row history download is the slowest request in the app)."""
    if db.is_demo:
        return
    prime_overview_cache(db)
    started = time.monotonic()
    try:
        backtest.prime_backtest_cache(db)
        logger.info("Backtest cache primed in %.1fs", time.monotonic() - started)
    except Exception:  # noqa: BLE001 - best effort; the page computes on demand instead
        logger.warning("Backtest warm-up failed; the first Backtesting visit will compute it.", exc_info=True)


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    if get_db not in fastapi_app.dependency_overrides:
        try:
            startup_info = get_settings().validate_startup()
            logger.info("Starting in %s mode (%s)", startup_info["mode"], startup_info["database"])
            for warning in startup_info["warnings"]:
                logger.warning("Config: %s", warning)
        except ValueError:
            logger.exception("Invalid configuration")
        db, _info = init_backend_context()
        threading.Thread(target=_warm_caches, args=(db,), name="cache-warmup", daemon=True).start()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="MandiMitra API",
        description="Agricultural Market Analytics, Deterministic Signal Engine & Grounded Gemini Advisory API",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_origin_regex=settings.cors_origin_regex.strip() or None,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        # Cross-origin browsers can only read these response headers when explicitly exposed.
        expose_headers=["Content-Disposition", "X-Backtest-Source", "X-Backtest-Rows"],
    )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        # Never leak raw stack traces, connection URLs, or API keys in error responses
        return JSONResponse(
            status_code=500,
            content={"detail": f"Internal server error ({type(exc).__name__})."},
        )

    app.include_router(health.router)
    app.include_router(dashboard.router)
    app.include_router(analytics.router)
    app.include_router(advisor.router)
    app.include_router(backtest.router)
    app.include_router(pipeline.router)

    return app


app = create_app()
