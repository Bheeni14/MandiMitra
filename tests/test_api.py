"""Comprehensive FastAPI backend tests (health, overview, explorer, Q1-Q7, advisor, backtest, pipeline, safety)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.dependencies import get_app_settings, get_db
from backend.main import create_app
from backend.routers.advisor import clear_advisor_cache
from config import Settings
from database.connection import DatabaseManager
from genai.gemini_client import GeminiBriefingClient


@pytest.fixture()
def api_client(populated_db: DatabaseManager, tmp_path: Path):
    """Build a FastAPI TestClient wired strictly to the isolated temporary SQLite warehouse."""
    clear_advisor_cache()
    test_settings = Settings(
        DATABASE_URL="",
        DEMO_MODE="true",
        DATA_GOV_IN_API_KEY=" ",
        GEMINI_API_KEY=" ",
        CORS_ORIGINS="http://localhost:3000,http://127.0.0.1:3000",
    )
    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db
    app.dependency_overrides[get_app_settings] = lambda: test_settings

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
    clear_advisor_cache()


def test_health_endpoint_returns_demo_status_and_no_secrets(api_client: TestClient) -> None:
    resp = api_client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["mode"] == "demo"
    assert body["is_demo"] is True
    assert body["db_healthy"] is True
    assert "test_fixture" in body["active_sources"]
    raw = json.dumps(body)
    assert "AIza" not in raw
    assert "postgres://" not in raw


def test_cors_headers_allow_local_nextjs_origin(api_client: TestClient) -> None:
    resp = api_client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_overview_endpoint_and_unknown_commodity_returns_404(api_client: TestClient) -> None:
    resp = api_client.get("/api/dashboard/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "demo"
    assert data["coverage"]["total_price_records"] > 0
    assert data["overall_avg_modal_price"] > 0
    assert data["focus_commodity"] in {"Tomato", "Wheat"}
    assert len(data["market_highlights"]) >= 1
    assert len(data["volatility"]) >= 1
    assert len(data["recent_prices"]) >= 1

    resp_wheat = api_client.get("/api/dashboard/overview", params={"commodity": "Wheat", "recent_limit": 5})
    assert resp_wheat.status_code == 200
    assert resp_wheat.json()["focus_commodity"] == "Wheat"
    assert len(resp_wheat.json()["recent_prices"]) == 5

    resp_missing = api_client.get("/api/dashboard/overview", params={"commodity": "NonExistentCrop"})
    assert resp_missing.status_code == 404


def test_commodities_and_markets_filtering(api_client: TestClient) -> None:
    c_resp = api_client.get("/api/commodities")
    assert c_resp.status_code == 200
    names = {item["commodity_name"] for item in c_resp.json()["items"]}
    assert {"Tomato", "Wheat"}.issubset(names)

    m_resp = api_client.get("/api/markets", params={"commodity": "Tomato", "state": "Maharashtra"})
    assert m_resp.status_code == 200
    m_data = m_resp.json()
    assert m_data["states"] == ["Maharashtra"]
    assert any(m["market_name"] == "Lasalgaon" for m in m_data["items"])


def test_market_prices_filters_pagination_and_validation(api_client: TestClient) -> None:
    resp = api_client.get(
        "/api/market-prices",
        params={
            "commodity": "Tomato",
            "state": "Maharashtra",
            "market": "Lasalgaon",
            "start_date": "2025-05-01",
            "end_date": "2025-06-30",
            "page": 1,
            "page_size": 5,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total_observations"] > 0
    assert data["summary"]["arrivals_available"] is True
    assert len(data["items"]) <= 5
    assert data["pagination"]["page"] == 1
    assert all(r["market"] == "Lasalgaon" for r in data["items"])
    assert all("2025-05-01" <= r["arrival_date"] <= "2025-06-30" for r in data["items"])

    # Invalid date range -> 422
    bad_dates = api_client.get(
        "/api/market-prices",
        params={"commodity": "Tomato", "start_date": "2025-06-30", "end_date": "2025-05-01"},
    )
    assert bad_dates.status_code == 422

    # Invalid pagination -> 422
    bad_page = api_client.get("/api/market-prices", params={"page": 0, "page_size": 9999})
    assert bad_page.status_code == 422


def test_all_seven_analytics_endpoints_and_validation(api_client: TestClient) -> None:
    q1 = api_client.get("/api/analytics/price-volatility", params={"min_observations": 5, "months": 12})
    assert q1.status_code == 200
    assert q1.json()["total"] >= 1

    q2 = api_client.get("/api/analytics/state-price-gaps", params={"commodity": "Tomato"})
    assert q2.status_code == 200
    assert len(q2.json()["items"]) >= 1
    assert "Maharashtra" in q2.json()["available_states"]

    q3 = api_client.get("/api/analytics/monthly-trends", params={"commodity": "Tomato"})
    assert q3.status_code == 200
    assert q3.json()["total"] >= 1

    q4 = api_client.get("/api/analytics/arrivals-vs-price", params={"commodity": "Tomato"})
    assert q4.status_code == 200
    assert q4.json()["has_arrivals_data"] is True
    assert q4.json()["total"] >= 1

    q5 = api_client.get("/api/analytics/seasonality", params={"commodity": "Tomato"})
    assert q5.status_code == 200
    assert q5.json()["total"] >= 1

    q6 = api_client.get("/api/analytics/best-markets", params={"commodity": "Tomato", "min_reports": 1})
    assert q6.status_code == 200
    assert q6.json()["total"] >= 1

    q7 = api_client.get("/api/analytics/production-vs-price", params={"commodity": "Tomato"})
    assert q7.status_code == 200
    assert q7.json()["kpis"]["total_production_t"] > 0
    assert len(q7.json()["detail"]) >= 1

    # Query param validation checks
    assert api_client.get("/api/analytics/price-volatility", params={"months": 0}).status_code == 422
    assert api_client.get("/api/analytics/best-markets", params={"min_reports": 100}).status_code == 422


def test_advisor_recommendation_fallback_caching_and_refresh(
    api_client: TestClient,
    populated_db: DatabaseManager,
) -> None:
    payload = {
        "commodity": "Tomato",
        "market": "Lasalgaon",
        "state": "Maharashtra",
        "as_of_date": "2025-06-30",
        "force_refresh": False,
    }
    first = api_client.post("/api/advisor/recommendation", json=payload)
    assert first.status_code == 200
    body1 = first.json()
    assert body1["cached"] is False
    assert body1["signal"]["verdict_class"] in {"SELL NOW", "WAIT", "NEUTRAL"}
    assert body1["briefing"]["verdict"] == body1["signal"]["verdict_label"]
    assert body1["briefing"]["used_fallback"] is True
    assert body1["briefing"]["validation_status"] == "fallback_template"
    assert len(body1["briefing"]["reason_en"]) > 10
    assert len(body1["briefing"]["reason_hi"]) > 10
    assert len(body1["history_series"]) > 0

    with populated_db.readonly_connection() as conn:
        briefing_count_1 = conn.execute(text("SELECT COUNT(*) FROM briefings")).scalar_one()

    # Second call without force_refresh returns cached=True without adding duplicate briefing rows
    second = api_client.post("/api/advisor/recommendation", json=payload)
    assert second.status_code == 200
    body2 = second.json()
    assert body2["cached"] is True
    assert body2["signal"]["verdict_label"] == body1["signal"]["verdict_label"]

    with populated_db.readonly_connection() as conn:
        briefing_count_2 = conn.execute(text("SELECT COUNT(*) FROM briefings")).scalar_one()
    assert briefing_count_2 == briefing_count_1

    # force_refresh=True recomputes cleanly
    refreshed = api_client.post("/api/advisor/recommendation", json={**payload, "force_refresh": True})
    assert refreshed.status_code == 200
    assert refreshed.json()["cached"] is False

    # Missing market -> 404; empty input -> 422
    not_found = api_client.post(
        "/api/advisor/recommendation",
        json={"commodity": "Tomato", "market": "NoSuchMandi", "state": "Maharashtra"},
    )
    assert not_found.status_code == 404

    invalid = api_client.post(
        "/api/advisor/recommendation",
        json={"commodity": "   ", "market": "Lasalgaon", "state": "Maharashtra"},
    )
    assert invalid.status_code == 422


def test_advisor_uses_grounded_gemini_when_mocked_and_preserves_sql_verdict(
    populated_db: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_advisor_cache()
    test_settings = Settings(
        DATABASE_URL="",
        DEMO_MODE="true",
        DATA_GOV_IN_API_KEY=" ",
        GEMINI_API_KEY="synthetic-test-key",
    )

    fake_sdk = MagicMock()
    # Provide an exact grounded JSON payload matching the SQL signal
    def fake_generate_briefing(self, signal_input, retry_feedback=None):
        p = round(float(signal_input.current_modal_price or 2000.0), 2)
        return {
            "verdict": signal_input.verdict,
            "reason_en": f"{signal_input.commodity} in {signal_input.market} currently trades at Rs {p} per quintal.",
            "caution_en": "Perishable prices fluctuate rapidly. Informational guidance only, not financial advice.",
            "reason_hi": f"{signal_input.market} में {signal_input.commodity} का भाव {p} रुपये प्रति क्विंटल है।",
            "caution_hi": "मंडी भाव बदल सकते हैं। यह केवल मार्गदर्शन है, वित्तीय सलाह नहीं।",
        }

    monkeypatch.setattr(GeminiBriefingClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(GeminiBriefingClient, "generate_briefing_json", fake_generate_briefing)
    # Gemini is off by default; this test opts in explicitly with a mocked client (no network)
    monkeypatch.setattr("genai.briefing.get_settings", lambda: SimpleNamespace(gemini_enabled=True))

    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db
    app.dependency_overrides[get_app_settings] = lambda: test_settings

    with TestClient(app) as client:
        resp = client.post(
            "/api/advisor/recommendation",
            json={
                "commodity": "Tomato",
                "market": "Lasalgaon",
                "state": "Maharashtra",
                "as_of_date": "2025-06-30",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["briefing"]["used_fallback"] is False
        assert body["briefing"]["validation_status"].startswith("validated_ai")
        assert body["briefing"]["verdict"] == body["signal"]["verdict_label"]
        assert "synthetic-test-key" not in json.dumps(body)

    app.dependency_overrides.clear()
    clear_advisor_cache()


def test_backtest_endpoint_and_live_mode_isolation(
    populated_db: DatabaseManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Verify demo mode backtesting + CSV export
    test_settings = Settings(DATABASE_URL="", DEMO_MODE="true", GEMINI_API_KEY=" ")
    demo_dir = tmp_path / "sample"
    live_dir = tmp_path / "live"
    demo_dir.mkdir(parents=True, exist_ok=True)
    live_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.DATA_SAMPLE_DIR", demo_dir)
    monkeypatch.setattr("config.DATA_DIR", live_dir)

    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db
    app.dependency_overrides[get_app_settings] = lambda: test_settings

    with TestClient(app) as client:
        res = client.get("/api/backtest/results", params={"eval_days": 180, "step_days": 14, "page": 1, "page_size": 10})
        assert res.status_code == 200
        data = res.json()
        assert data["is_demo"] is True
        assert data["mandimitra"]["total_cases"] > 0
        assert len(data["sample_rows"]) <= 10

        csv_res = client.get("/api/backtest/export", params={"eval_days": 180, "step_days": 14})
        assert csv_res.status_code == 200
        assert "text/csv" in csv_res.headers["content-type"]
        assert "decision_date" in csv_res.text

        # 2. Verify Live Mode NEVER reads demo trap CSV
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

        populated_db.is_demo = False
        populated_db.table_ref = lambda _schema, table: table  # type: ignore[method-assign]
        live_res = client.get("/api/backtest/results", params={"eval_days": 365, "step_days": 7})
        assert live_res.status_code == 200
        live_data = live_res.json()
        assert live_data["is_demo"] is False
        assert live_data["used_precomputed_csv"] is False
        assert all(c["commodity"] != "TRAP_DEMO_ROW" for c in live_data["by_commodity"])

    app.dependency_overrides.clear()


def test_data_sources_status_and_pipeline_history_pagination(
    api_client: TestClient,
    populated_db: DatabaseManager,
) -> None:
    with populated_db.transaction() as conn:
        conn.execute(
            text(
                """
                INSERT INTO pipeline_run_log (stage, source_name, rows_fetched, rows_loaded, rows_rejected, status)
                VALUES ('daily_ingest', 'agmarknet_api', 120, 118, 2, 'success')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO dq_rejects (source_name, stage, reject_reason, raw_record)
                VALUES
                    ('agmarknet_api', 'clean_prices', 'modal_price <= 0', '{"modal_price": 0}'),
                    ('agmarknet_api', 'clean_prices', 'min_price > max_price', '{"min_price": 500, "max_price": 400}')
                """
            )
        )

    status_resp = api_client.get("/api/data-sources/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["data_quality"]["total_dq_rejects"] == 2
    assert status_data["latest_run"]["source_name"] == "agmarknet_api"
    assert len(status_data["components"]) == 4

    hist_resp = api_client.get(
        "/api/pipeline/history",
        params={"runs_page": 1, "runs_page_size": 1, "rejects_page": 1, "rejects_page_size": 1},
    )
    assert hist_resp.status_code == 200
    hist_data = hist_resp.json()
    assert len(hist_data["runs"]) == 1
    assert len(hist_data["rejects"]) == 1
    assert hist_data["rejects_pagination"]["total_items"] == 2
    assert len(hist_data["reject_reason_summary"]) == 2
