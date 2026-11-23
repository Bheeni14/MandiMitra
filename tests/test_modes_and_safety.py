"""Tests for Demo vs Live mode isolation, FastAPI safety guards, and Advisor caching."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.dependencies import get_app_settings, get_db
from backend.main import create_app
from backend.routers.advisor import clear_advisor_cache
from config import Settings, SignalThresholds
from database.connection import DatabaseManager


def test_settings_demo_and_live_modes_without_real_credentials() -> None:
    # 1. Auto without DATABASE_URL -> Demo Mode
    demo_cfg = Settings(DATABASE_URL="", DEMO_MODE="auto")
    assert demo_cfg.is_demo_mode is True
    startup_demo = demo_cfg.validate_startup()
    assert startup_demo["mode"] == "demo"

    # 2. DEMO_MODE=false without DATABASE_URL -> raises ValueError clearly
    bad_live = Settings(DATABASE_URL="", DEMO_MODE="false")
    with pytest.raises(ValueError, match="DEMO_MODE=false requires DATABASE_URL"):
        bad_live.validate_startup()

    # 3. Live Mode with synthetic PostgreSQL URL (no live connection attempted)
    live_cfg = Settings(
        DATABASE_URL="postgresql://user:pass@localhost:5432/synthetic_db",
        DEMO_MODE="false",
    )
    assert live_cfg.is_demo_mode is False
    assert "synthetic_db" in live_cfg.safe_db_summary()
    startup_live = live_cfg.validate_startup()
    assert startup_live["mode"] == "postgresql"


def test_live_mode_never_auto_loads_demo_tuned_thresholds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    demo_file = tmp_path / "demo_tuned.json"
    demo_file.write_text(
        '{"momentum_wait_pct": 9.0, "threshold_source": "demo_tuned_fixture"}',
        encoding="utf-8",
    )
    live_missing = tmp_path / "missing_live_tuned.json"
    monkeypatch.setattr("config.TUNED_THRESHOLDS_PATH", demo_file)
    monkeypatch.setattr("config.LIVE_TUNED_THRESHOLDS_PATH", live_missing)

    demo_thr = SignalThresholds.load_tuned_or_default(is_demo=True)
    live_thr = SignalThresholds.load_tuned_or_default(is_demo=False)

    assert demo_thr.momentum_wait_pct == 9.0
    assert demo_thr.threshold_source == "demo_tuned_fixture"
    assert live_thr.momentum_wait_pct == 3.0  # initial default, NOT demo-tuned!
    assert live_thr.threshold_source == "initial_proposal_placeholder"


def test_advisor_repeat_views_use_cache_and_do_not_duplicate_db_writes(
    populated_db: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.routers.advisor as advisor_router

    clear_advisor_cache()
    call_counter = {"count": 0}
    orig_fn = advisor_router.get_advisor_recommendation

    def wrapped_advisor(*args, **kwargs):
        call_counter["count"] += 1
        return orig_fn(*args, **kwargs)

    monkeypatch.setattr(advisor_router, "get_advisor_recommendation", wrapped_advisor)

    test_settings = Settings(DATABASE_URL="", DEMO_MODE="true", DATA_GOV_IN_API_KEY=" ", GEMINI_API_KEY=" ")
    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db
    app.dependency_overrides[get_app_settings] = lambda: test_settings

    payload = {
        "commodity": "Tomato",
        "market": "Lasalgaon",
        "state": "Maharashtra",
        "as_of_date": "2025-06-30",
        "force_refresh": False,
    }

    with TestClient(app) as client:
        r1 = client.post("/api/advisor/recommendation", json=payload)
        assert r1.status_code == 200
        assert r1.json()["cached"] is False
        assert call_counter["count"] == 1

        with populated_db.readonly_connection() as conn:
            sig_count_1 = conn.execute(text("SELECT COUNT(*) FROM market_signals")).scalar_one()
            br_count_1 = conn.execute(text("SELECT COUNT(*) FROM briefings")).scalar_one()

        # Repeated advisor fetches (e.g. tab/language switches or revisiting) reuse cache without DB writes
        r2 = client.post("/api/advisor/recommendation", json=payload)
        assert r2.status_code == 200
        assert r2.json()["cached"] is True
        assert call_counter["count"] == 1

        with populated_db.readonly_connection() as conn:
            sig_count_2 = conn.execute(text("SELECT COUNT(*) FROM market_signals")).scalar_one()
            br_count_2 = conn.execute(text("SELECT COUNT(*) FROM briefings")).scalar_one()

        assert sig_count_2 == sig_count_1
        assert br_count_2 == br_count_1

    app.dependency_overrides.clear()
    clear_advisor_cache()


def test_backtesting_endpoint_live_mode_never_reads_demo_csv(
    populated_db: DatabaseManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate Live Mode (is_demo=False) while routing SQLite table_ref cleanly to test DB
    populated_db.is_demo = False
    populated_db.table_ref = lambda _schema, table: table  # type: ignore[method-assign]
    demo_dir = tmp_path / "sample"
    live_dir = tmp_path / "live"
    demo_dir.mkdir(parents=True, exist_ok=True)
    live_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.DATA_SAMPLE_DIR", demo_dir)
    monkeypatch.setattr("config.DATA_DIR", live_dir)

    # Plant a trap backtest_results.csv in demo path to guarantee Live Mode ignores it
    demo_trap = demo_dir / "backtest_results.csv"
    pd.DataFrame(
        [
            {
                "decision_date": "2025-01-01",
                "commodity": "TRAP_DEMO_ROW",
                "storable": True,
                "market": "FakeMandi",
                "state": "Nowhere",
                "decision_price": 1000.0,
                "future_avg_14d": 1100.0,
                "future_14d_change_pct": 10.0,
                "actual_outcome": "RISE",
                "verdict_class": "SELL NOW",
                "verdict_label": "SELL NOW",
                "is_correct": False,
                "arrivals_trend": "unavailable",
                "baseline_always_sell": "SELL NOW",
                "baseline_momentum": "SELL NOW",
            }
        ]
    ).to_csv(demo_trap, index=False)

    live_settings = Settings(
        DATABASE_URL="postgresql://user:pass@localhost:5432/synthetic_live_db",
        DEMO_MODE="false",
        GEMINI_API_KEY=" ",
    )
    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db
    app.dependency_overrides[get_app_settings] = lambda: live_settings

    with TestClient(app) as client:
        res = client.get("/api/backtest/results", params={"eval_days": 365, "step_days": 7})
        assert res.status_code == 200
        body = res.json()
        assert body["is_demo"] is False
        assert body["used_precomputed_csv"] is False
        commodities = {c["commodity"] for c in body["by_commodity"]}
        assert "TRAP_DEMO_ROW" not in commodities
        assert commodities <= {"Tomato", "Wheat"}

    app.dependency_overrides.clear()


def test_data_sources_api_is_read_only_and_never_exposes_live_write_routes(
    populated_db: DatabaseManager,
) -> None:
    test_settings = Settings(
        DATABASE_URL="",
        DEMO_MODE="true",
        DATA_GOV_IN_API_KEY=" ",
        GEMINI_API_KEY=" ",
    )
    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db
    app.dependency_overrides[get_app_settings] = lambda: test_settings

    route_paths = {getattr(r, "path", "") for r in app.routes}
    assert not any("ingest" in p or "probe" in p for p in route_paths)

    with TestClient(app) as client:
        res = client.get("/api/data-sources/status")
        assert res.status_code == 200
        assert res.json()["is_demo"] is True

    app.dependency_overrides.clear()
