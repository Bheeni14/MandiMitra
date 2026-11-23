"""Limits that keep a public deployment safe: bounded caches, refresh cooldowns, one backtest at a time,
and a health endpoint that does not reveal configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.dependencies import get_app_settings, get_db
from backend.main import create_app
from backend.routers import advisor as advisor_router
from backend.routers import backtest as backtest_router
from backtesting.engine import RollingOriginBacktester
from config import Settings
from database.connection import DatabaseManager


@pytest.fixture()
def client(populated_db: DatabaseManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # No precomputed backtest CSVs: every evaluation is computed, so the counters below are exact.
    monkeypatch.setattr("config.DATA_SAMPLE_DIR", tmp_path / "sample")
    monkeypatch.setattr("config.DATA_DIR", tmp_path / "live")
    test_settings = Settings(DATABASE_URL="", DEMO_MODE="true", DATA_GOV_IN_API_KEY=" ", GEMINI_API_KEY=" ")
    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db
    app.dependency_overrides[get_app_settings] = lambda: test_settings
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    advisor_router.clear_advisor_cache()


@pytest.fixture()
def backtest_runs(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"count": 0}
    original_run = RollingOriginBacktester.run_backtest

    def counting_run(self, *args, **kwargs):
        calls["count"] += 1
        return original_run(self, *args, **kwargs)

    monkeypatch.setattr(RollingOriginBacktester, "run_backtest", counting_run)
    return calls


PARAMS = {"eval_days": 180, "step_days": 14, "page_size": 10}


def test_backtest_refresh_is_throttled_per_view(
    client: TestClient, backtest_runs: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get("/api/backtest/results", params=PARAMS).status_code == 200
    assert client.get("/api/backtest/results", params={**PARAMS, "refresh": "true"}).status_code == 200
    assert backtest_runs["count"] == 2

    # A second refresh inside the cooldown is served from cache instead of recomputing.
    assert client.get("/api/backtest/results", params={**PARAMS, "refresh": "true"}).status_code == 200
    assert client.get("/api/backtest/export", params={**PARAMS, "refresh": "true"}).status_code == 200
    assert backtest_runs["count"] == 2

    monkeypatch.setattr(backtest_router, "BACKTEST_REFRESH_COOLDOWN_SECONDS", 0.0)
    assert client.get("/api/backtest/results", params={**PARAMS, "refresh": "true"}).status_code == 200
    assert backtest_runs["count"] == 3


def test_backtest_cache_is_bounded(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest_router, "BACKTEST_CACHE_MAX_ENTRIES", 2)
    for step in (7, 14, 21):
        assert client.get("/api/backtest/results", params={**PARAMS, "step_days": step}).status_code == 200
    assert len(backtest_router._BACKTEST_CACHE) == 2
    assert {key[3] for key in backtest_router._BACKTEST_CACHE} == {14, 21}
    assert set(backtest_router._BACKTEST_DATA_VERSION) == set(backtest_router._BACKTEST_CACHE)


def test_backtest_returns_503_while_another_computes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest_router, "BACKTEST_COMPUTE_WAIT_SECONDS", 0.01)
    assert backtest_router._BACKTEST_COMPUTE_LOCK.acquire(timeout=5)
    try:
        busy = client.get("/api/backtest/results", params=PARAMS)
    finally:
        backtest_router._BACKTEST_COMPUTE_LOCK.release()
    assert busy.status_code == 503
    assert busy.headers.get("retry-after") == "60"
    assert client.get("/api/backtest/results", params=PARAMS).status_code == 200


ADVISOR_PAYLOAD = {"commodity": "Tomato", "market": "Lasalgaon", "state": "Maharashtra", "as_of_date": "2025-06-30"}


def test_advisor_force_refresh_is_throttled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    first = client.post("/api/advisor/recommendation", json=ADVISOR_PAYLOAD)
    assert first.status_code == 200 and first.json()["cached"] is False

    forced = client.post("/api/advisor/recommendation", json={**ADVISOR_PAYLOAD, "force_refresh": True})
    assert forced.json()["cached"] is False
    # Repeated force_refresh inside the cooldown is served from cache.
    again = client.post("/api/advisor/recommendation", json={**ADVISOR_PAYLOAD, "force_refresh": True})
    assert again.json()["cached"] is True

    monkeypatch.setattr(advisor_router, "ADVISOR_REFRESH_COOLDOWN_SECONDS", 0.0)
    later = client.post("/api/advisor/recommendation", json={**ADVISOR_PAYLOAD, "force_refresh": True})
    assert later.json()["cached"] is False


def test_advisor_cache_is_bounded(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(advisor_router, "ADVISOR_CACHE_MAX_ENTRIES", 1)
    for as_of in ("2025-06-29", "2025-06-30"):
        resp = client.post("/api/advisor/recommendation", json={**ADVISOR_PAYLOAD, "as_of_date": as_of})
        assert resp.status_code == 200
    assert len(advisor_router._ADVISOR_CACHE) == 1
    assert next(iter(advisor_router._ADVISOR_CACHE))[5] == "2025-06-30"


def test_health_does_not_reveal_configuration(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["database"] == "Demo SQLite warehouse"
    assert body["warnings"] == []
    assert "sqlite:///" not in body["database"]
