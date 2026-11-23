"""Health & system mode endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.dependencies import get_app_settings, get_db
from backend.schemas import HealthResponse
from config import Settings
from database.connection import DatabaseManager
from services.data_service import get_active_source_names

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check(
    db: DatabaseManager = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> HealthResponse:
    try:
        startup_info = settings.validate_startup()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    db_healthy, db_msg = db.health_check()
    active_sources: list[str] = []
    if db_healthy:
        try:
            active_sources = get_active_source_names(db)
        except Exception:
            active_sources = []

    # This endpoint is public once deployed: never reveal the database host or which credentials are configured.
    # Configuration warnings are written to the server log at startup instead (see backend.main.lifespan).
    return HealthResponse(
        status="ok" if db_healthy else "degraded",
        mode=startup_info["mode"],
        is_demo=db.is_demo,
        backend=db.backend,
        database="Demo SQLite warehouse" if db.is_demo else "PostgreSQL warehouse",
        db_healthy=db_healthy,
        db_message=f"Connected ({db.backend})" if db_healthy else f"Connection error ({db.backend})",
        active_sources=active_sources,
        warnings=[],
    )
