"""Unit tests for deterministic SELL NOW / WAIT / NEUTRAL signal engine."""

from __future__ import annotations

from datetime import date

from analytics.metrics import fetch_signal_features_sql
from analytics.signals import compute_signal_for_market, evaluate_signal_rules
from config import SignalThresholds
from database.connection import DatabaseManager
from etl.warehouse_loader import WarehouseLoader


def _base_features(**overrides) -> dict:
    base = {
        "commodity_id": 1,
        "commodity": "Tomato",
        "category": "vegetable",
        "storable": False,
        "market_id": 1,
        "market": "Lasalgaon",
        "district": "Nashik",
        "state": "Maharashtra",
        "as_of_date": date(2025, 6, 30),
        "obs_recent_7d": 3,
        "recent_7d_avg": 2200.0,
        "obs_prior_7d": 3,
        "prior_4w_avg": 2000.0,  # +10% 4w momentum
        "obs_90d": 35,
        "obs_12m": 150,
        "window_start": date(2024, 7, 1),
        "window_end": date(2025, 6, 30),
        "cv_12m": 0.25,
        "seasonal_avg_price": 2350.0,  # -6.38% vs seasonal avg (below peak)
        "seasonal_obs": 15,
        "seasonal_years": 2,
        "arr_obs_recent": 4,
        "arr_recent_tonnes": 150.0,
        "arr_obs_prev": 4,
        "arr_prev_tonnes": 220.0,  # -31.8% falling arrivals
        "state_price_gap_pct": 25.0,
        "data_sources": ["test_fixture"],
    }
    base.update(overrides)
    return base


def test_perishable_wait_capped_at_one_week() -> None:
    cfg = SignalThresholds(momentum_wait_pct=3.0, momentum_sell_pct=-3.0, seasonal_peak_pct=10.0)
    sig = evaluate_signal_rules(_base_features(storable=False), thresholds=cfg)
    assert sig.verdict_class == "WAIT"
    assert sig.verdict_label == "WAIT 1 WEEK"


def test_storable_wait_allows_one_to_two_weeks() -> None:
    cfg = SignalThresholds(momentum_wait_pct=3.0, momentum_sell_pct=-3.0, seasonal_peak_pct=10.0)
    sig = evaluate_signal_rules(_base_features(commodity="Wheat", storable=True), thresholds=cfg)
    assert sig.verdict_class == "WAIT"
    assert sig.verdict_label == "WAIT 1-2 WEEKS"


def test_sell_now_on_negative_momentum_or_arrival_surge_or_seasonal_peak() -> None:
    cfg = SignalThresholds(momentum_wait_pct=3.0, momentum_sell_pct=-3.0, seasonal_peak_pct=10.0, arrivals_rise_pct=15.0)
    # Negative momentum
    s1 = evaluate_signal_rules(_base_features(recent_7d_avg=1850.0, prior_4w_avg=2000.0), thresholds=cfg)
    assert s1.verdict_class == "SELL NOW"

    # Sharp arrival surge when momentum flat
    s2 = evaluate_signal_rules(
        _base_features(recent_7d_avg=2010.0, prior_4w_avg=2000.0, arr_recent_tonnes=300.0, arr_prev_tonnes=200.0),
        thresholds=cfg,
    )
    assert s2.verdict_class == "SELL NOW"

    # Already in seasonal peak
    s3 = evaluate_signal_rules(
        _base_features(recent_7d_avg=2650.0, seasonal_avg_price=2200.0, arr_recent_tonnes=210.0, arr_prev_tonnes=200.0),
        thresholds=cfg,
    )
    assert s3.verdict_class == "SELL NOW"


def test_missing_arrival_data_and_insufficient_evidence() -> None:
    cfg = SignalThresholds()
    # Missing arrivals still works honestly
    s_no_arr = evaluate_signal_rules(
        _base_features(arr_obs_recent=0, arr_obs_prev=0, arr_recent_tonnes=0.0, arr_prev_tonnes=0.0),
        thresholds=cfg,
    )
    assert s_no_arr.arrivals_trend == "unavailable"
    assert s_no_arr.arrivals_change_14d_pct is None

    # Insufficient 90d observations forces NEUTRAL
    s_sparse = evaluate_signal_rules(_base_features(obs_90d=3, obs_recent_7d=1), thresholds=cfg)
    assert s_sparse.verdict_class == "NEUTRAL"
    assert s_sparse.insufficient_evidence is True
    assert s_sparse.evidence_completeness == "LOW"


def test_compute_and_persist_signal_from_populated_db(populated_db: DatabaseManager) -> None:
    sig = compute_signal_for_market(
        commodity_name="Tomato",
        market_name="Lasalgaon",
        state="Maharashtra",
        db=populated_db,
        as_of_date=date(2025, 6, 30),
        persist=True,
    )
    assert sig is not None
    assert sig.signal_id is not None
    assert sig.verdict_class in {"WAIT", "SELL NOW", "NEUTRAL"}


def test_signal_counts_distinct_reporting_dates_not_varieties(temp_db: DatabaseManager) -> None:
    """Multiple varieties reported on 1 single day must count as 1 trading day, not inflate coverage."""
    loader = WarehouseLoader(temp_db)
    records = [
        {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "variety": f"Var{i}", "grade": "FAQ", "arrival_date": "2026-11-18", "min_price": 1800, "max_price": 2200, "modal_price": 2000}
        for i in range(5)
    ] + [
        {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "variety": f"Var{i}", "grade": "FAQ", "arrival_date": "2026-10-20", "min_price": 1600, "max_price": 1900, "modal_price": 1750}
        for i in range(5)
    ]
    loader.load_prices(records, source_name="test_multi_var")

    feats = fetch_signal_features_sql(temp_db, "Onion", "Lasalgaon", "Maharashtra", as_of_date=date(2026, 11, 18))
    assert feats is not None
    assert feats["obs_recent_7d"] == 1
    assert feats["obs_prior_7d"] == 1
    assert feats["obs_90d"] == 2

    sig = evaluate_signal_rules(feats, thresholds=SignalThresholds())
    assert sig.verdict_class == "NEUTRAL"
    assert sig.insufficient_evidence is True


def test_seasonal_window_wraps_across_december_january(temp_db: DatabaseManager) -> None:
    """Evaluating in early January must pull prior-year late December observations across the year boundary."""
    loader = WarehouseLoader(temp_db)
    records = [
        # Prior winter season straddling Dec 2023 -> Jan 2024
        {"state": "Punjab", "district": "Ludhiana", "market": "Ludhiana", "commodity": "Potato", "variety": "Jyoti", "grade": "FAQ", "arrival_date": "2023-12-26", "min_price": 1000, "max_price": 1400, "modal_price": 1200},
        {"state": "Punjab", "district": "Ludhiana", "market": "Ludhiana", "commodity": "Potato", "variety": "Jyoti", "grade": "FAQ", "arrival_date": "2024-01-08", "min_price": 1200, "max_price": 1600, "modal_price": 1400},
        # Recent window for Jan 2025 decision
        {"state": "Punjab", "district": "Ludhiana", "market": "Ludhiana", "commodity": "Potato", "variety": "Jyoti", "grade": "FAQ", "arrival_date": "2025-01-03", "min_price": 1300, "max_price": 1700, "modal_price": 1500},
    ]
    loader.load_prices(records, source_name="test_wrap")

    feats = fetch_signal_features_sql(temp_db, "Potato", "Ludhiana", "Punjab", as_of_date=date(2025, 1, 3))
    assert feats is not None
    assert feats["seasonal_obs"] == 2
    assert feats["seasonal_avg_price"] == 1300.0

