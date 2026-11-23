"""Smoke tests verifying all FastAPI dashboard routes & Next.js frontend pages exist and execute cleanly."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.dependencies import get_app_settings, get_db
from backend.main import create_app
from backend.routers.advisor import clear_advisor_cache
from config import ROOT_DIR, Settings
from database.connection import DatabaseManager


@pytest.mark.parametrize(
    "endpoint,params",
    [
        ("/api/health", None),
        ("/api/dashboard/overview", None),
        ("/api/commodities", None),
        ("/api/markets", None),
        ("/api/market-prices", {"commodity": "Tomato"}),
        ("/api/analytics/price-volatility", None),
        ("/api/analytics/state-price-gaps", {"commodity": "Tomato"}),
        ("/api/analytics/monthly-trends", {"commodity": "Tomato"}),
        ("/api/analytics/arrivals-vs-price", {"commodity": "Tomato"}),
        ("/api/analytics/seasonality", {"commodity": "Tomato"}),
        ("/api/analytics/best-markets", {"commodity": "Tomato", "min_reports": 1}),
        ("/api/analytics/production-vs-price", {"commodity": "Tomato"}),
        ("/api/backtest/results", {"eval_days": 180, "step_days": 14}),
        ("/api/data-sources/status", None),
        ("/api/pipeline/history", None),
    ],
)
def test_api_smoke_endpoints_return_200(
    populated_db: DatabaseManager,
    endpoint: str,
    params: dict[str, object] | None,
) -> None:
    clear_advisor_cache()
    test_settings = Settings(DATABASE_URL="", DEMO_MODE="true", DATA_GOV_IN_API_KEY=" ", GEMINI_API_KEY=" ")
    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db
    app.dependency_overrides[get_app_settings] = lambda: test_settings

    with TestClient(app) as client:
        resp = client.get(endpoint, params=params)
        assert resp.status_code == 200, f"Smoke failure on {endpoint}: {resp.text}"

    app.dependency_overrides.clear()
    clear_advisor_cache()


@pytest.mark.parametrize(
    "route_rel",
    [
        "frontend/src/app/page.tsx",
        "frontend/src/app/market-explorer/page.tsx",
        "frontend/src/app/price-analytics/page.tsx",
        "frontend/src/app/ai-advisor/page.tsx",
        "frontend/src/app/backtesting/page.tsx",
        "frontend/src/app/data-sources/page.tsx",
        "frontend/src/app/about/page.tsx",
    ],
)
def test_nextjs_pages_present(route_rel: str) -> None:
    assert (ROOT_DIR / route_rel).is_file(), f"Missing migrated Next.js page: {route_rel}"
