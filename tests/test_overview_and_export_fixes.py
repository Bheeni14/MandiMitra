"""Regression tests for the hackathon-demo fixes: Overview CV label, untruncated state averages, export provenance."""

from __future__ import annotations

import io
import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.dependencies import get_app_settings, get_db
from backend.main import create_app
from config import Settings
from database.connection import DatabaseManager
from etl.warehouse_loader import WarehouseLoader
from services.data_service import query_state_price_averages

TEST_SETTINGS = Settings(DATABASE_URL="", DEMO_MODE="true", DATA_GOV_IN_API_KEY=" ", GEMINI_API_KEY=" ")


@pytest.fixture()
def client(populated_db: DatabaseManager):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db
    app.dependency_overrides[get_app_settings] = lambda: TEST_SETTINGS
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _bulk_tomato_rows(varieties: int, days: int) -> list[dict]:
    """Extra Tomato observations (distinct varieties => distinct primary keys) with prices far from the base data."""
    start = date(2024, 1, 1)
    rows: list[dict] = []
    for v in range(varieties):
        for d in range(days):
            day = (start + timedelta(days=d)).isoformat()
            rows.append(
                {
                    "state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Tomato",
                    "variety": f"Bulk{v}", "grade": "FAQ", "arrival_date": day,
                    "min_price": 4000.0, "max_price": 6000.0, "modal_price": 5000.0 + v,
                }
            )
            rows.append(
                {
                    "state": "Karnataka", "district": "Bengaluru Urban", "market": "Bengaluru", "commodity": "Tomato",
                    "variety": f"Bulk{v}", "grade": "FAQ", "arrival_date": day,
                    "min_price": 900.0, "max_price": 1300.0, "modal_price": 1000.0 + v,
                }
            )
    return rows


def test_overview_cv_highlight_is_a_percentage_while_api_values_stay_decimal(client: TestClient) -> None:
    overview = client.get("/api/dashboard/overview").json()
    label = next(h for h in overview["market_highlights"] if h["metric_label"] == "Highest 12m Volatility (Q1)")

    top = overview["volatility"][0]
    assert 0 < top["cv"] < 5, "API `cv` must remain a decimal ratio"
    expected_pct = f"{top['cv'] * 100:.2f}%"
    assert label["value_label"].startswith(f"CV {expected_pct} (")
    assert re.match(r"^CV \d+\.\d{2}% \(₹[\d,]+\.\d{2} avg\)$", label["value_label"])
    assert not re.match(r"^CV 0\.\d{3} ", label["value_label"])

    # The standalone Q1 endpoint is unchanged (decimal).
    q1 = client.get("/api/analytics/price-volatility").json()["items"][0]
    assert q1["cv"] == top["cv"]


def test_overview_state_averages_cover_every_observation_beyond_4000_rows(
    client: TestClient, populated_db: DatabaseManager
) -> None:
    WarehouseLoader(populated_db).load_prices(_bulk_tomato_rows(varieties=5, days=450), source_name="test_fixture")

    with populated_db.readonly_connection() as conn:
        total = int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM fact_price_daily p JOIN dim_commodity c ON c.commodity_id = p.commodity_id "
                    "WHERE c.commodity_name = 'Tomato'"
                )
            ).scalar_one()
        )
        expected = {
            row[0]: (float(row[1]), int(row[2]))
            for row in conn.execute(
                text(
                    """
                    SELECT m.state, AVG(p.modal_price), COUNT(*)
                    FROM fact_price_daily p
                    JOIN dim_market m ON m.market_id = p.market_id
                    JOIN dim_commodity c ON c.commodity_id = p.commodity_id
                    WHERE c.commodity_name = 'Tomato'
                    GROUP BY m.state
                    """
                )
            )
        }
    assert total > 4000, "fixture must exceed the former 4,000-row cap"

    body = client.get("/api/dashboard/overview", params={"commodity": "Tomato"}).json()
    items = {s["state"]: s for s in body["state_averages"]}
    assert set(items) == set(expected)
    assert sum(s["obs"] for s in body["state_averages"]) == total
    for state, (avg, obs) in expected.items():
        assert items[state]["obs"] == obs
        assert items[state]["avg_modal"] == pytest.approx(avg, abs=0.01)
    # Sorted by average modal price, highest first, with the response shape unchanged.
    averages = [s["avg_modal"] for s in body["state_averages"]]
    assert averages == sorted(averages, reverse=True)
    assert set(body["state_averages"][0]) == {"state", "avg_modal", "min_price", "max_price", "obs"}


def test_state_price_averages_handle_empty_and_unknown_commodity(populated_db: DatabaseManager) -> None:
    assert query_state_price_averages(populated_db, commodity="Dragonfruit").empty
    all_states = query_state_price_averages(populated_db)
    assert not all_states.empty
    assert list(all_states.columns) == ["state", "avg_modal", "min_price", "max_price", "obs"]


def test_backtest_export_exposes_source_and_row_count_headers(
    populated_db: DatabaseManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("config.DATA_SAMPLE_DIR", tmp_path / "sample")
    monkeypatch.setattr("config.DATA_DIR", tmp_path / "live")
    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db
    app.dependency_overrides[get_app_settings] = lambda: TEST_SETTINGS
    origin = TEST_SETTINGS.cors_origins_list[0]
    params = {"eval_days": 180, "step_days": 14}

    with TestClient(app) as c:
        shown = c.get("/api/backtest/results", params={**params, "page_size": 10}).json()
        resp = c.get("/api/backtest/export", params=params, headers={"Origin": origin})

    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.headers["x-backtest-source"] == "computed"
    assert int(resp.headers["x-backtest-rows"]) == shown["pagination"]["total_items"]
    assert int(resp.headers["x-backtest-rows"]) == len(pd.read_csv(io.StringIO(resp.text)))
    # Cross-origin browsers can only read these headers if they are exposed.
    exposed = {h.strip().lower() for h in resp.headers["access-control-expose-headers"].split(",")}
    assert {"x-backtest-source", "x-backtest-rows", "content-disposition"} <= exposed
