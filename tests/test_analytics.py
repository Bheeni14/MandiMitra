"""Unit tests verifying all 7 SQL analytical queries (Q1-Q7) against deterministic fixture data."""

from __future__ import annotations

from analytics.queries import (
    get_q1_price_volatility,
    get_q2_state_price_gaps,
    get_q3_mom_trends,
    get_q4_arrivals_vs_price,
    get_q5_seasonal_price_index,
    get_q6_best_markets,
    get_q7_production_vs_price,
)
from database.connection import DatabaseManager


def test_q1_volatility_ranks_tomato_above_wheat(populated_db: DatabaseManager) -> None:
    q1 = get_q1_price_volatility(populated_db, min_observations=30)
    assert not q1.empty
    top_comm = q1.iloc[0]["commodity_name"]
    assert top_comm == "Tomato"
    tomato_cv = float(q1[q1["commodity_name"] == "Tomato"].iloc[0]["cv"])
    wheat_cv = float(q1[q1["commodity_name"] == "Wheat"].iloc[0]["cv"])
    assert tomato_cv > wheat_cv


def test_q2_state_price_gaps_exact_spread(populated_db: DatabaseManager) -> None:
    q2 = get_q2_state_price_gaps(populated_db, commodity_name="Tomato")
    assert not q2.empty
    row = q2.iloc[0]
    # Karnataka was generated at 1.25x Maharashtra -> ~25% price gap
    assert row["highest_state"] == "Karnataka"
    assert row["lowest_state"] == "Maharashtra"
    assert 24.0 <= float(row["pct_difference"]) <= 26.0


def test_q3_mom_trends_and_ma3(populated_db: DatabaseManager) -> None:
    q3 = get_q3_mom_trends(populated_db, commodity_name="Wheat")
    assert not q3.empty
    assert "prev_month_price" in q3.columns
    assert "ma_3m" in q3.columns
    assert q3.iloc[0]["prev_month_price"] != q3.iloc[0]["prev_month_price"] or q3.iloc[0]["prev_month_price"] is None


def test_q4_arrivals_vs_price_negative_correlation(populated_db: DatabaseManager) -> None:
    q4 = get_q4_arrivals_vs_price(populated_db, commodity_name="Tomato")
    assert not q4.empty
    assert float(q4.iloc[0]["corr_same_month"]) < -0.5


def test_q5_seasonal_price_index_peaks_in_q3_monsoon(populated_db: DatabaseManager) -> None:
    q5 = get_q5_seasonal_price_index(populated_db, commodity_name="Tomato")
    assert len(q5) == 12
    top_months = set(q5[q5["peak_month_rank"] <= 3]["month"].tolist())
    assert top_months == {7, 8, 9}


def test_q6_best_markets_last_7_days(populated_db: DatabaseManager) -> None:
    q6 = get_q6_best_markets(populated_db, state="Karnataka", commodity_name="Tomato", min_reports=2)
    assert not q6.empty
    assert q6.iloc[0]["market_name"] == "Bengaluru"
    assert int(q6.iloc[0]["market_rank"]) == 1


def test_q7_production_vs_price_via_mapping_table(populated_db: DatabaseManager) -> None:
    detail, corr_df = get_q7_production_vs_price(populated_db, commodity_name="Tomato")
    assert not detail.empty
    assert set(detail["state"].unique()) == {"Maharashtra", "Karnataka"}
    assert not corr_df.empty
    # Maharashtra has higher production (240k-250k vs 144k-150k) and lower price -> negative correlation
    assert float(corr_df.iloc[0]["corr_production_vs_price"]) < -0.5
