"""FastAPI dependencies for database access and application configuration."""

from __future__ import annotations

from typing import Any

from config import Settings, get_settings
from database.connection import DatabaseManager, get_db_manager
from services.dashboard_service import init_app_context

_DEMO_BOOTSTRAPPED_URLS: set[str] = set()


def get_app_settings() -> Settings:
    """Return current validated settings without exposing secrets."""
    return get_settings()


def init_backend_context() -> tuple[DatabaseManager, dict[str, Any]]:
    """Initialize DB context once on FastAPI startup (auto-hydrating Demo SQLite only in Demo Mode)."""
    db, info = init_app_context()
    if db.is_demo:
        _DEMO_BOOTSTRAPPED_URLS.add(db.database_url)
    return db, info


def get_db() -> DatabaseManager:
    """Provide the active DatabaseManager (overridable in tests)."""
    db = get_db_manager()
    if db.is_demo and db.database_url not in _DEMO_BOOTSTRAPPED_URLS:
        from services.data_service import ensure_demo_warehouse_ready

        ensure_demo_warehouse_ready(db)
        _DEMO_BOOTSTRAPPED_URLS.add(db.database_url)
    return db
