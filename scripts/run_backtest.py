"""Execute walk-forward threshold tuning and rolling-origin historical backtest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtesting.engine import RollingOriginBacktester
from backtesting.report import export_backtest_artifacts, save_tuned_thresholds, tune_thresholds_walkforward
from config import SignalThresholds, configure_logging
from services.dashboard_service import init_app_context


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MandiMitra rolling-origin backtest.")
    parser.add_argument("--tune", action="store_true", help="Tune thresholds via walk-forward split and save tuned_thresholds.json.")
    parser.add_argument("--eval-days", type=int, default=365, help="Held-out evaluation horizon in days.")
    parser.add_argument("--step-days", type=int, default=7, help="Rolling origin step in days.")
    args = parser.parse_args()

    configure_logging()
    db, info = init_app_context()
    print(f"Running backtest on {info['mode']} ({info['database']})...")

    bt = RollingOriginBacktester(db=db)
    price_df, arr_df = bt.load_historical_frames()

    thresholds = SignalThresholds.load_tuned_or_default(is_demo=db.is_demo)
    if args.tune:
        best_cfg, tuning_df = tune_thresholds_walkforward(bt, price_df, arr_df)
        save_tuned_thresholds(best_cfg, is_demo=db.is_demo)
        thresholds = best_cfg
        print("\nWalk-forward tuning candidates:")
        print(tuning_df.to_string(index=False))
        print("Saved tuned thresholds:", best_cfg.model_dump())

    results_df, summary = bt.run_backtest(
        price_df=price_df,
        arr_df=arr_df,
        eval_days=args.eval_days,
        step_days=args.step_days,
        thresholds=thresholds,
    )
    paths = export_backtest_artifacts(results_df, summary, is_demo=db.is_demo)

    m = summary["mandimitra"]
    print("\n=== BACKTEST RESULTS ===")
    print(f"Total evaluations : {m['total_cases']}")
    print(f"Decisive calls    : {m['decisive_cases']} (NEUTRAL coverage: {m['neutral_coverage_pct']}%)")
    print(f"Decisive accuracy : {m['accuracy_pct']}%")
    print(f"WAIT precision    : {m['wait_precision_pct']}% ({m['wait_calls']} calls, avg 14d move: {m['avg_wait_move_pct']:+.2f}%)")
    print(f"SELL precision    : {m['sell_precision_pct']}% ({m['sell_calls']} calls, avg 14d move: {m['avg_sell_move_pct']:+.2f}%)")
    print(f"Always SELL base  : {summary['baseline_always_sell']['accuracy_pct']}% (matched={summary['matched_always_sell_accuracy_pct']}%)")
    print(f"Momentum-only base: {summary['baseline_momentum_only']['accuracy_pct']}% (WAIT prec={summary['baseline_momentum_only']['wait_precision_pct']}%)")
    print(f"Artifacts saved   : {paths}")


if __name__ == "__main__":
    main()
