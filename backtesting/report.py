"""Walk-forward threshold tuning, CSV export, and Markdown report generation."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from backtesting.engine import RollingOriginBacktester
from config import DATA_DIR, DATA_SAMPLE_DIR, LIVE_TUNED_THRESHOLDS_PATH, SignalThresholds, TUNED_THRESHOLDS_PATH


def tune_thresholds_walkforward(
    backtester: RollingOriginBacktester,
    price_df: pd.DataFrame,
    arr_df: pd.DataFrame,
    tune_days: int = 180,
    heldout_days: int = 180,
    step_days: int = 14,
) -> tuple[SignalThresholds, pd.DataFrame]:
    """Tune thresholds strictly on an earlier tuning window, BEFORE the final held-out period."""
    if price_df.empty:
        return SignalThresholds(), pd.DataFrame()

    max_date = price_df["full_date"].max()
    heldout_end = max_date - timedelta(days=14)
    heldout_start = heldout_end - timedelta(days=heldout_days)
    tune_end = heldout_start - timedelta(days=15)
    tune_start = tune_end - timedelta(days=tune_days)

    if tune_start >= tune_end:
        return SignalThresholds(), pd.DataFrame()

    candidates: list[dict[str, float]] = [
        {"momentum_wait_pct": 2.0, "momentum_sell_pct": -2.0, "seasonal_peak_pct": 0.0, "arrivals_rise_pct": 10.0, "arrivals_not_rising_pct": 0.0},
        {"momentum_wait_pct": 3.0, "momentum_sell_pct": -3.0, "seasonal_peak_pct": 4.0, "arrivals_rise_pct": 12.0, "arrivals_not_rising_pct": 0.0},
        {"momentum_wait_pct": 3.0, "momentum_sell_pct": -3.0, "seasonal_peak_pct": 10.0, "arrivals_rise_pct": 15.0, "arrivals_not_rising_pct": 5.0},
        {"momentum_wait_pct": 4.0, "momentum_sell_pct": -2.5, "seasonal_peak_pct": 0.0, "arrivals_rise_pct": 12.0, "arrivals_not_rising_pct": -2.0},
        {"momentum_wait_pct": 5.0, "momentum_sell_pct": -2.0, "seasonal_peak_pct": 2.0, "arrivals_rise_pct": 10.0, "arrivals_not_rising_pct": 0.0},
        {"momentum_wait_pct": 6.0, "momentum_sell_pct": -2.0, "seasonal_peak_pct": 0.0, "arrivals_rise_pct": 10.0, "arrivals_not_rising_pct": -3.0},
    ]

    records: list[dict[str, Any]] = []
    best_score = -1.0
    best_cfg = SignalThresholds()

    for idx, cand in enumerate(candidates):
        cfg = SignalThresholds(
            momentum_wait_pct=cand["momentum_wait_pct"],
            momentum_sell_pct=cand["momentum_sell_pct"],
            seasonal_peak_pct=cand["seasonal_peak_pct"],
            arrivals_rise_pct=cand["arrivals_rise_pct"],
            arrivals_not_rising_pct=cand["arrivals_not_rising_pct"],
            threshold_source="walk_forward_backtest_tuned",
        )
        _, summary = backtester.run_backtest(
            price_df=price_df,
            arr_df=arr_df,
            start_date_override=tune_start,
            end_date_override=tune_end + timedelta(days=14),
            step_days=step_days,
            thresholds=cfg,
        )
        m = summary["mandimitra"]
        # Balanced score: decisive accuracy weighted with non-trivial decisive coverage
        cov_factor = max(0.2, min(1.0, (100.0 - m["neutral_coverage_pct"]) / 50.0))
        score = m["accuracy_pct"] * cov_factor
        records.append(
            {
                "candidate_id": idx + 1,
                **cand,
                "accuracy_pct": m["accuracy_pct"],
                "wait_precision_pct": m["wait_precision_pct"],
                "neutral_coverage_pct": m["neutral_coverage_pct"],
                "decisive_cases": m["decisive_cases"],
                "score": round(score, 2),
            }
        )
        if score > best_score and m["decisive_cases"] >= 10:
            best_score = score
            best_cfg = cfg

    tuning_df = pd.DataFrame(records).sort_values("score", ascending=False).reset_index(drop=True)
    return best_cfg, tuning_df


def save_tuned_thresholds(
    cfg: SignalThresholds,
    path: Path | None = None,
    is_demo: bool = True,
) -> Path:
    target = path or (TUNED_THRESHOLDS_PATH if is_demo else LIVE_TUNED_THRESHOLDS_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cfg.model_dump(), indent=2), encoding="utf-8")
    return target


def export_backtest_artifacts(
    results_df: pd.DataFrame,
    summary: dict[str, Any],
    out_dir: Path | None = None,
    is_demo: bool = True,
) -> dict[str, Path]:
    out_dir = out_dir or (DATA_SAMPLE_DIR if is_demo else DATA_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    prefix = "" if is_demo else "live_"
    csv_path = out_dir / f"{prefix}backtest_results.csv"
    md_path = out_dir / f"{prefix}backtest_report.md"

    results_df.to_csv(csv_path, index=False)

    m = summary["mandimitra"]
    b_sell = summary["baseline_always_sell"]
    b_mom = summary["baseline_momentum_only"]

    lines = [
        "# MandiMitra Historical Rolling-Origin Backtest Report",
        "",
        "## Overall Performance Summary",
        "",
        f"- **Total Evaluated Windows**: {m['total_cases']}",
        f"- **Decisive Calls (SELL NOW / WAIT)**: {m['decisive_cases']} (NEUTRAL coverage: {m['neutral_coverage_pct']}%)",
        f"- **MandiMitra Decisive Accuracy**: {m['accuracy_pct']}%",
        f"- **WAIT Precision**: {m['wait_precision_pct']}% ({m['wait_calls']} WAIT calls, avg 14d move: {m['avg_wait_move_pct']:+.2f}%)",
        f"- **SELL NOW Precision**: {m['sell_precision_pct']}% ({m['sell_calls']} SELL calls, avg 14d move: {m['avg_sell_move_pct']:+.2f}%)",
        f"- **Always SELL NOW Baseline Accuracy (all rows)**: {b_sell['accuracy_pct']}%",
        f"- **Always SELL NOW Baseline (matched decisive rows)**: {summary['matched_always_sell_accuracy_pct']}%",
        f"- **Momentum-Only Baseline Accuracy**: {b_mom['accuracy_pct']}% (WAIT precision: {b_mom['wait_precision_pct']}%)",
        "",
        "## Perishable vs Storable Breakdown",
        "",
    ]
    by_stor = summary["by_storability"]
    if isinstance(by_stor, pd.DataFrame) and not by_stor.empty:
        lines.append(by_stor.to_string(index=False))
    lines.extend(["", "## Commodity Performance Breakdown", ""])
    by_comm = summary["by_commodity"]
    if isinstance(by_comm, pd.DataFrame) and not by_comm.empty:
        lines.append(by_comm.to_string(index=False))

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"csv": csv_path, "markdown": md_path}
