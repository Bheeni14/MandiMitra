"""Regression tests for the final migration audit findings (explorer truncation, outliers, district arrivals, backtest refresh/export)."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.dependencies import get_app_settings, get_db
from backend.main import create_app
from backtesting.engine import RollingOriginBacktester
from config import Settings
from database.connection import DatabaseManager
from etl.warehouse_loader import WarehouseLoader
from services.data_service import query_market_arrivals


@pytest.fixture()
def client(populated_db: DatabaseManager):
    test_settings = Settings(DATABASE_URL="", DEMO_MODE="true", DATA_GOV_IN_API_KEY=" ", GEMINI_API_KEY=" ")
    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db
    app.dependency_overrides[get_app_settings] = lambda: test_settings
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _count_rows(db: DatabaseManager, sql: str, params: dict | None = None) -> int:
    with db.readonly_connection() as conn:
        return int(conn.execute(text(sql), params or {}).scalar_one())


def test_market_prices_aggregates_cover_every_row_and_ignore_legacy_limit(
    client: TestClient, populated_db: DatabaseManager
) -> None:
    expected_total = _count_rows(
        populated_db,
        """
        SELECT COUNT(*) FROM fact_price_daily p
        JOIN dim_commodity c ON c.commodity_id = p.commodity_id
        WHERE c.commodity_name = 'Tomato'
        """,
    )
    expected_dates = _count_rows(
        populated_db,
        """
        SELECT COUNT(DISTINCT p.date_id) FROM fact_price_daily p
        JOIN dim_commodity c ON c.commodity_id = p.commodity_id
        WHERE c.commodity_name = 'Tomato'
        """,
    )
    assert expected_total > 100

    # A legacy `limit` param must no longer truncate summaries, charts, or pagination totals.
    resp = client.get("/api/market-prices", params={"commodity": "Tomato", "page_size": 50, "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total_observations"] == expected_total
    assert body["pagination"]["total_items"] == expected_total
    assert len(body["daily_series"]) == expected_dates
    assert sum(m["reports"] for m in body["market_comparison"]) == expected_total
    assert body["summary"]["min_date"] == "2024-01-01"
    assert body["summary"]["latest_date"] == body["summary"]["max_date"]

    # Walking every page returns each row exactly once, newest first.
    seen: set[tuple[str, str, str]] = set()
    total_pages = body["pagination"]["total_pages"]
    for page in range(1, total_pages + 1):
        page_body = client.get(
            "/api/market-prices", params={"commodity": "Tomato", "page_size": 50, "page": page}
        ).json()
        dates = [r["arrival_date"] for r in page_body["items"]]
        assert dates == sorted(dates, reverse=True)
        for r in page_body["items"]:
            seen.add((r["arrival_date"], r["market"], r["variety"]))
    assert len(seen) == expected_total


def test_market_prices_returns_every_outlier_on_the_same_date(
    client: TestClient, populated_db: DatabaseManager
) -> None:
    with populated_db.transaction() as conn:
        conn.execute(
            text(
                """
                UPDATE fact_price_daily SET outlier_flag = 1
                WHERE date_id = (SELECT date_id FROM dim_date WHERE full_date = '2025-06-02')
                  AND commodity_id = (SELECT commodity_id FROM dim_commodity WHERE commodity_name = 'Tomato')
                """
            )
        )

    body = client.get("/api/market-prices", params={"commodity": "Tomato", "page_size": 5}).json()
    same_day = [p for p in body["outlier_points"] if p["arrival_date"] == "2025-06-02"]
    assert {p["market"] for p in same_day} == {"Lasalgaon", "Bengaluru"}
    assert body["summary"]["outliers_flagged"] == 2
    day_point = next(d for d in body["daily_series"] if d["arrival_date"] == "2025-06-02")
    assert day_point["outlier_count"] == 2


def test_district_filter_applies_to_arrivals_like_prices(
    client: TestClient, populated_db: DatabaseManager
) -> None:
    # Add arrivals for a second Maharashtra district that must be excluded when district=Nashik.
    WarehouseLoader(populated_db).load_arrivals(
        [
            {
                "state": "Maharashtra",
                "district": "Pune",
                "market": "Pune",
                "commodity": "Tomato",
                "arrival_date": "2025-06-02",
                "quantity_tonnes": 999.0,
            }
        ],
        source_name="test_fixture",
    )

    nashik_df = query_market_arrivals(populated_db, commodity="Tomato", state="Maharashtra", district="Nashik", limit=5000)
    state_df = query_market_arrivals(populated_db, commodity="Tomato", state="Maharashtra", limit=5000)
    assert set(nashik_df["market"]) == {"Lasalgaon"}
    assert "Pune" in set(state_df["market"])

    nashik_total = round(float(nashik_df["quantity_tonnes"].astype(float).sum()), 2)
    state_total = round(float(state_df["quantity_tonnes"].astype(float).sum()), 2)

    by_district = client.get(
        "/api/market-prices",
        params={"commodity": "Tomato", "state": "Maharashtra", "district": "Nashik"},
    ).json()
    by_state = client.get(
        "/api/market-prices", params={"commodity": "Tomato", "state": "Maharashtra"}
    ).json()

    assert by_district["summary"]["total_arrivals_tonnes"] == pytest.approx(nashik_total)
    assert by_state["summary"]["total_arrivals_tonnes"] == pytest.approx(state_total)
    assert by_state["summary"]["total_arrivals_tonnes"] - by_district["summary"]["total_arrivals_tonnes"] == pytest.approx(999.0)
    assert all(r["market"] == "Lasalgaon" for r in by_district["arrivals_records"])


def test_backtest_refresh_is_one_shot_and_export_matches_latest_results(
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
        df_res, summary = original_run(self, *args, **kwargs)
        if calls["count"] > 1:
            # Make the refreshed evaluation distinguishable from the first one.
            df_res = df_res.iloc[:-1].reset_index(drop=True)
        return df_res, summary

    monkeypatch.setattr(RollingOriginBacktester, "run_backtest", counting_run)

    test_settings = Settings(DATABASE_URL="", DEMO_MODE="true", GEMINI_API_KEY=" ")
    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db
    app.dependency_overrides[get_app_settings] = lambda: test_settings
    params = {"eval_days": 180, "step_days": 14, "page_size": 10}

    with TestClient(app) as c:
        first = c.get("/api/backtest/results", params={**params, "page": 1}).json()
        initial_total = first["pagination"]["total_items"]
        assert initial_total > 10
        assert calls["count"] == 1

        # Pagination and export reuse the cached evaluation (no recomputation).
        c.get("/api/backtest/results", params={**params, "page": 2})
        export_1 = c.get("/api/backtest/export", params={"eval_days": 180, "step_days": 14})
        assert calls["count"] == 1
        assert len(pd.read_csv(io.StringIO(export_1.text))) == initial_total
        assert "180d_step14d" in export_1.headers["content-disposition"]

        # refresh=true recomputes exactly once ...
        refreshed = c.get("/api/backtest/results", params={**params, "page": 1, "refresh": "true"}).json()
        assert calls["count"] == 2
        assert refreshed["pagination"]["total_items"] == initial_total - 1

        # ... later pages and the export reflect the refreshed results without recomputing.
        page_2 = c.get("/api/backtest/results", params={**params, "page": 2}).json()
        export_2 = c.get("/api/backtest/export", params={"eval_days": 180, "step_days": 14})
        assert calls["count"] == 2
        assert page_2["pagination"]["total_items"] == initial_total - 1
        assert len(pd.read_csv(io.StringIO(export_2.text))) == initial_total - 1

    app.dependency_overrides.clear()
