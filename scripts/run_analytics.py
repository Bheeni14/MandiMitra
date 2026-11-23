"""Run all 7 SQL analytical queries and generate sample signals + grounded bilingual briefings."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.queries import (
    get_q1_price_volatility,
    get_q2_state_price_gaps,
    get_q3_mom_trends,
    get_q4_arrivals_vs_price,
    get_q5_seasonal_price_index,
    get_q6_best_markets,
    get_q7_production_vs_price,
)
from analytics.signals import compute_signal_for_market
from config import configure_logging, get_settings
from genai.briefing import generate_grounded_briefing
from services.dashboard_service import init_app_context
from services.data_service import list_markets


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute Q1-Q7 SQL analytics and sample bilingual briefings.")
    parser.add_argument("--commodity", type=str, default="Tomato")
    parser.add_argument("--briefings", type=int, default=3, help="Number of sample market briefings to generate.")
    args = parser.parse_args()

    configure_logging()
    db, info = init_app_context()
    print(f"Context: {info['mode']} | {info['database']}")

    print("\n--- Q1: Top Price Volatility (12m CV) ---")
    print(get_q1_price_volatility(db).head(5).to_string(index=False))

    print(f"\n--- Q2: State-wise Price Gaps ({args.commodity}) ---")
    print(get_q2_state_price_gaps(db, commodity_name=args.commodity).head(5).to_string(index=False))

    print(f"\n--- Q3: Month-over-Month Trends ({args.commodity}) ---")
    print(get_q3_mom_trends(db, commodity_name=args.commodity).tail(5).to_string(index=False))

    print("\n--- Q4: Arrivals vs Price Correlation ---")
    print(get_q4_arrivals_vs_price(db).head(5).to_string(index=False))

    print(f"\n--- Q5: Seasonal Price Index ({args.commodity}) ---")
    print(get_q5_seasonal_price_index(db, commodity_name=args.commodity).head(6).to_string(index=False))

    print(f"\n--- Q6: Best Markets Last 7 Days ({args.commodity}) ---")
    print(get_q6_best_markets(db, commodity_name=args.commodity).head(5).to_string(index=False))

    print("\n--- Q7: Production vs Price Correlation ---")
    _, q7_corr = get_q7_production_vs_price(db)
    print(q7_corr.head(5).to_string(index=False))

    mkts = list_markets(db, commodity_name=args.commodity)
    if not mkts.empty and args.briefings > 0:
        print(f"\n--- Deterministic Signals & Grounded Briefings ({args.commodity}) ---")
        persist = get_settings().advisor_persistence_allowed(db.is_demo)
        if not persist:
            print("(no-persist mode: market_signals and briefings are NOT written; set ADVISOR_PERSIST=true to allow)")
        for _, row in mkts.head(args.briefings).iterrows():
            sig = compute_signal_for_market(args.commodity, row["market_name"], row["state"], db=db, persist=persist)
            if sig:
                br = generate_grounded_briefing(sig, db=db, persist=persist)
                print(
                    f"[{row['state']} - {row['market_name']}] Verdict={sig.verdict_label} | "
                    f"4w={sig.price_change_4w_pct}% | Seas={sig.vs_seasonal_avg_pct}% | "
                    f"Arr={sig.arrivals_trend} | Status={br.validation_status}"
                )
                print("  EN:", br.output.reason_en)
                print("  HI:", br.output.reason_hi)


if __name__ == "__main__":
    main()
