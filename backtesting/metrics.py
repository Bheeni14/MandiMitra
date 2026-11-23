"""Backtesting evaluation metrics across baselines, commodities, and storability groups."""

from __future__ import annotations

from typing import Any

import pandas as pd


def _eval_strategy_metrics(
    df: pd.DataFrame,
    pred_col: str,
    target_threshold_pct: float = 2.0,
) -> dict[str, Any]:
    if df.empty:
        return {
            "total_cases": 0,
            "decisive_cases": 0,
            "neutral_cases": 0,
            "neutral_coverage_pct": 0.0,
            "accuracy_pct": 0.0,
            "wait_calls": 0,
            "wait_precision_pct": 0.0,
            "sell_calls": 0,
            "sell_precision_pct": 0.0,
            "avg_wait_move_pct": 0.0,
            "avg_sell_move_pct": 0.0,
            "avg_captured_move_pct": 0.0,
        }

    total = len(df)
    preds = df[pred_col]
    future = df["future_14d_change_pct"]
    actual_wait = future >= target_threshold_pct

    is_wait = preds == "WAIT"
    is_sell = preds == "SELL NOW"
    is_neutral = preds == "NEUTRAL"
    is_decisive = is_wait | is_sell

    decisive_n = int(is_decisive.sum())
    neutral_n = int(is_neutral.sum())
    wait_n = int(is_wait.sum())
    sell_n = int(is_sell.sum())

    wait_correct = int((is_wait & actual_wait).sum())
    sell_correct = int((is_sell & (~actual_wait)).sum())
    decisive_correct = wait_correct + sell_correct

    accuracy = round((decisive_correct / decisive_n) * 100.0, 2) if decisive_n > 0 else 0.0
    wait_prec = round((wait_correct / wait_n) * 100.0, 2) if wait_n > 0 else 0.0
    sell_prec = round((sell_correct / sell_n) * 100.0, 2) if sell_n > 0 else 0.0
    neutral_cov = round((neutral_n / total) * 100.0, 2)

    avg_wait_move = round(float(future[is_wait].mean()), 2) if wait_n > 0 else 0.0
    avg_sell_move = round(float(future[is_sell].mean()), 2) if sell_n > 0 else 0.0
    # Captured move: WAIT captures future_14d_change_pct; SELL NOW avoids negative drops (0 baseline at decision date)
    captured = future.where(is_wait, 0.0)
    avg_captured = round(float(captured.mean()), 2)

    return {
        "total_cases": total,
        "decisive_cases": decisive_n,
        "neutral_cases": neutral_n,
        "neutral_coverage_pct": neutral_cov,
        "accuracy_pct": accuracy,
        "wait_calls": wait_n,
        "wait_precision_pct": wait_prec,
        "sell_calls": sell_n,
        "sell_precision_pct": sell_prec,
        "avg_wait_move_pct": avg_wait_move,
        "avg_sell_move_pct": avg_sell_move,
        "avg_captured_move_pct": avg_captured,
    }


def summarize_backtest_results(results_df: pd.DataFrame) -> dict[str, Any]:
    """Compute overall, baseline, commodity-wise, and perishable-vs-storable performance metrics."""
    if results_df.empty:
        empty_metrics = _eval_strategy_metrics(results_df, "verdict_class")
        return {
            "mandimitra": empty_metrics,
            "baseline_always_sell": empty_metrics,
            "baseline_momentum_only": empty_metrics,
            "matched_always_sell_accuracy_pct": 0.0,
            "by_commodity": pd.DataFrame(),
            "by_storability": pd.DataFrame(),
        }

    mandi_metrics = _eval_strategy_metrics(results_df, "verdict_class")
    always_sell_metrics = _eval_strategy_metrics(results_df, "baseline_always_sell")
    momentum_metrics = _eval_strategy_metrics(results_df, "baseline_momentum")

    decisive_subset = results_df[results_df["verdict_class"].isin(["WAIT", "SELL NOW"])]
    matched_sell = _eval_strategy_metrics(decisive_subset, "baseline_always_sell")

    # By commodity
    comm_rows: list[dict[str, Any]] = []
    for comm, grp in results_df.groupby("commodity"):
        m = _eval_strategy_metrics(grp, "verdict_class")
        b_sell = _eval_strategy_metrics(grp, "baseline_always_sell")
        b_mom = _eval_strategy_metrics(grp, "baseline_momentum")
        comm_rows.append(
            {
                "commodity": comm,
                "storable": bool(grp["storable"].iloc[0]),
                "total_cases": m["total_cases"],
                "decisive_cases": m["decisive_cases"],
                "accuracy_pct": m["accuracy_pct"],
                "wait_precision_pct": m["wait_precision_pct"],
                "neutral_coverage_pct": m["neutral_coverage_pct"],
                "avg_wait_move_pct": m["avg_wait_move_pct"],
                "always_sell_acc_pct": b_sell["accuracy_pct"],
                "momentum_acc_pct": b_mom["accuracy_pct"],
            }
        )
    by_commodity = pd.DataFrame(comm_rows).sort_values("accuracy_pct", ascending=False).reset_index(drop=True)

    # Perishable vs Storable
    stor_rows: list[dict[str, Any]] = []
    for storable_flag, grp in results_df.groupby("storable"):
        label = "Storable" if bool(storable_flag) else "Perishable"
        m = _eval_strategy_metrics(grp, "verdict_class")
        b_sell = _eval_strategy_metrics(grp, "baseline_always_sell")
        b_mom = _eval_strategy_metrics(grp, "baseline_momentum")
        stor_rows.append(
            {
                "crop_group": label,
                "storable": bool(storable_flag),
                "total_cases": m["total_cases"],
                "decisive_cases": m["decisive_cases"],
                "accuracy_pct": m["accuracy_pct"],
                "wait_precision_pct": m["wait_precision_pct"],
                "neutral_coverage_pct": m["neutral_coverage_pct"],
                "avg_wait_move_pct": m["avg_wait_move_pct"],
                "always_sell_acc_pct": b_sell["accuracy_pct"],
                "momentum_acc_pct": b_mom["accuracy_pct"],
            }
        )
    by_storability = pd.DataFrame(stor_rows)

    return {
        "mandimitra": mandi_metrics,
        "baseline_always_sell": always_sell_metrics,
        "baseline_momentum_only": momentum_metrics,
        "matched_always_sell_accuracy_pct": matched_sell["accuracy_pct"],
        "by_commodity": by_commodity,
        "by_storability": by_storability,
    }
