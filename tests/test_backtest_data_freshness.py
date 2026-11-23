"""Backtest results follow newly ingested prices, and an empty backtest explains how much history is missing."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.routers.backtest as backtest_router
from backend.dependencies import get_app_settings, get_db
from backend.main import create_app
from backtesting.engine import RollingOriginBacktester
from config import Settings
from database.connection import DatabaseManager


def _client(db: DatabaseManager) -> tuple[TestClient, object]:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_app_settings] = lambda: Settings(DATABASE_URL="", DEMO_MODE="true", GEMINI_API_KEY=" ")
    return TestClient(app), app


def test_cached_backtest_recomputes_when_price_history_changes(
    populated_db: DatabaseManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.DATA_SAMPLE_DIR", tmp_path / "sample")
    monkeypatch.setattr("config.DATA_DIR", tmp_path / "live")

    calls = {"count": 0}
    original_run = RollingOriginBacktester.run_backtest

    def counting_run(self, *args, **kwargs):
        calls["count"] += 1
        return original_run(self, *args, **kwargs)

    monkeypatch.setattr(RollingOriginBacktester, "run_backtest", counting_run)

    real_history = backtest_router._price_history
    snapshot = {"extra_rows": 0}

    def history_with_new_rows(db: DatabaseManager) -> dict:
        h = real_history(db)
        return {**h, "total_rows": h["total_rows"] + snapshot["extra_rows"]}

    monkeypatch.setattr(backtest_router, "_price_history", history_with_new_rows)
    params = {"eval_days": 180, "step_days": 14, "page_size": 10}

    client, app = _client(populated_db)
    with client as c:
        c.get("/api/backtest/results", params=params)
        c.get("/api/backtest/results", params={**params, "page": 2})
        assert calls["count"] == 1  # unchanged data -> cached

        snapshot["extra_rows"] = 25  # a new daily ingest landed
        c.get("/api/backtest/results", params=params)
        assert calls["count"] == 2  # recomputed once for the new data

        c.get("/api/backtest/export", params={"eval_days": 180, "step_days": 14})
        assert calls["count"] == 2  # export reuses the refreshed result
    app.dependency_overrides.clear()


def test_backtest_reports_price_history_coverage(
    populated_db: DatabaseManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.DATA_SAMPLE_DIR", tmp_path / "sample")
    monkeypatch.setattr("config.DATA_DIR", tmp_path / "live")
    client, app = _client(populated_db)
    with client as c:
        body = c.get("/api/backtest/results", params={"eval_days": 180, "step_days": 14}).json()
    app.dependency_overrides.clear()

    cov = body["data_coverage"]
    assert cov["history_start"] is not None and cov["history_end"] is not None
    assert cov["history_days"] > 0
    assert cov["history_span_days"] == (
        date.fromisoformat(cov["history_end"]) - date.fromisoformat(cov["history_start"])
    ).days
    assert cov["required_span_days"] == 14 + 180 % 14


def test_history_requirement_for_a_few_days_of_live_data() -> None:
    history = {"start": date(2026, 11, 19), "end": date(2026, 11, 22), "distinct_days": 3, "total_rows": 22149}
    req = backtest_router._history_requirement(history, eval_days=365, step_days=7)
    assert req["history_span_days"] == 3
    assert req["required_span_days"] == 15  # 14-day outcome horizon + 365 % 7
    assert req["results_expected_from"] == date(2026, 12, 4)
