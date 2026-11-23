"""Unit tests for rolling-origin backtesting and zero future-data leakage guarantee."""

from __future__ import annotations

from datetime import date

from backtesting.engine import RollingOriginBacktester
from config import SignalThresholds
from database.connection import DatabaseManager


def test_backtest_runs_and_compares_baselines(populated_db: DatabaseManager) -> None:
    bt = RollingOriginBacktester(db=populated_db, thresholds=SignalThresholds())
    results_df, summary = bt.run_backtest(eval_days=180, step_days=7)

    assert not results_df.empty
    assert "baseline_always_sell" in results_df.columns
    assert "baseline_momentum" in results_df.columns
    assert not summary["by_commodity"].empty
    assert not summary["by_storability"].empty


def test_backtest_zero_future_data_leakage(populated_db: DatabaseManager) -> None:
    """Mutating future prices strictly after decision_date must NEVER change features or verdicts at decision_date."""
    bt = RollingOriginBacktester(db=populated_db, thresholds=SignalThresholds())
    price_df, arr_df = bt.load_historical_frames()

    cutoff = date(2025, 5, 15)
    pair_prices = price_df[(price_df["commodity"] == "Tomato") & (price_df["market"] == "Lasalgaon")].copy()
    comm_prices = price_df[price_df["commodity"] == "Tomato"].copy()
    cid = int(pair_prices.iloc[0]["commodity_id"])
    mid = int(pair_prices.iloc[0]["market_id"])
    pair_arr = arr_df[(arr_df["commodity_id"] == cid) & (arr_df["market_id"] == mid)].copy()
    meta = {
        "commodity_id": cid,
        "commodity": "Tomato",
        "category": "vegetable",
        "storable": False,
        "market_id": mid,
        "market": "Lasalgaon",
        "district": "Nashik",
        "state": "Maharashtra",
    }

    feat_before = bt.build_features_up_to_date(pair_prices, comm_prices, pair_arr, cutoff, meta)

    # Corrupt all prices and arrivals strictly AFTER cutoff
    mutated_pair = pair_prices.copy()
    mutated_pair.loc[mutated_pair["full_date"] > cutoff, "modal_price"] = 999999.0
    mutated_comm = comm_prices.copy()
    mutated_comm.loc[mutated_comm["full_date"] > cutoff, "modal_price"] = 999999.0
    mutated_arr = pair_arr.copy()
    mutated_arr.loc[mutated_arr["full_date"] > cutoff, "quantity_tonnes"] = 999999.0

    feat_after = bt.build_features_up_to_date(mutated_pair, mutated_comm, mutated_arr, cutoff, meta)
    assert feat_before == feat_after

    # Also verify run_backtest predictions for decision_date == cutoff remain invariant
    res_orig, _ = bt.run_backtest(
        price_df=pair_prices,
        arr_df=pair_arr,
        start_date_override=cutoff,
        end_date_override=date(2025, 6, 15),
        step_days=7,
    )
    res_mut, _ = bt.run_backtest(
        price_df=mutated_pair,
        arr_df=mutated_arr,
        start_date_override=cutoff,
        end_date_override=date(2025, 6, 15),
        step_days=7,
    )
    row_orig = res_orig[res_orig["decision_date"] == cutoff].iloc[0]
    row_mut = res_mut[res_mut["decision_date"] == cutoff].iloc[0]
    assert row_orig["verdict_class"] == row_mut["verdict_class"]
    assert row_orig["price_change_4w_pct"] == row_mut["price_change_4w_pct"]
    assert row_orig["future_avg_14d"] != row_mut["future_avg_14d"]


def test_backtester_seasonal_dec_jan_wrap_around(temp_db: DatabaseManager) -> None:
    """Backtest at Dec 30 and Jan 3 wraps across year boundary without dropping adjacent winter weeks."""
    from datetime import timedelta
    from etl.warehouse_loader import WarehouseLoader

    loader = WarehouseLoader(temp_db)
    recs = []
    # Dense history 2023-12-01 through 2025-02-01
    cur = date(2023, 12, 1)
    while cur <= date(2025, 2, 1):
        recs.append(
            {
                "state": "Punjab",
                "district": "Ludhiana",
                "market": "Ludhiana",
                "commodity": "Wheat",
                "variety": "Lokwan",
                "grade": "FAQ",
                "arrival_date": cur.isoformat(),
                "min_price": 2200.0,
                "max_price": 2600.0,
                "modal_price": 2400.0 if cur.year < 2025 else 2500.0,
            }
        )
        cur += timedelta(days=2)
    loader.load_prices(recs, source_name="test_wrap_bt")

    bt = RollingOriginBacktester(db=temp_db, thresholds=SignalThresholds())
    price_df, arr_df = bt.load_historical_frames()
    meta = {
        "commodity_id": int(price_df.iloc[0]["commodity_id"]),
        "commodity": "Wheat",
        "category": "cereal",
        "storable": True,
        "market_id": int(price_df.iloc[0]["market_id"]),
        "market": "Ludhiana",
        "district": "Ludhiana",
        "state": "Punjab",
    }
    feats_dec = bt.build_features_up_to_date(price_df, price_df, arr_df, date(2024, 12, 30), meta)
    feats_jan = bt.build_features_up_to_date(price_df, price_df, arr_df, date(2025, 1, 3), meta)

    assert feats_dec is not None and feats_dec["seasonal_obs"] >= 10
    assert feats_jan is not None and feats_jan["seasonal_obs"] >= 10

