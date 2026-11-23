"""Rolling-origin historical backtesting engine with strict zero future-data leakage."""

from __future__ import annotations

import bisect
import logging
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text

from analytics.metrics import prior_year_seasonal_windows
from analytics.signals import evaluate_signal_rules
from backtesting.metrics import summarize_backtest_results
from config import SignalThresholds
from database.connection import DatabaseManager, get_db_manager

logger = logging.getLogger(__name__)


class RollingOriginBacktester:
    """Evaluates deterministic signals on held-out history using strictly past data (<= decision_date)."""

    def __init__(
        self,
        db: DatabaseManager | None = None,
        thresholds: SignalThresholds | None = None,
    ) -> None:
        self.db = db or get_db_manager()
        self.thresholds = thresholds or SignalThresholds.load_tuned_or_default(is_demo=self.db.is_demo)

    def load_historical_frames(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        t_p = self.db.table_ref("warehouse", "fact_price_daily")
        t_a = self.db.table_ref("warehouse", "fact_arrivals_daily")
        t_c = self.db.table_ref("warehouse", "dim_commodity")
        t_m = self.db.table_ref("warehouse", "dim_market")
        t_d = self.db.table_ref("warehouse", "dim_date")

        with self.db.readonly_connection() as conn:
            p_res = conn.execute(
                text(
                    f"""
                    SELECT p.commodity_id, c.commodity_name AS commodity, c.category, c.storable,
                           p.market_id, m.market_name AS market, m.district, m.state,
                           d.full_date, d.year, d.week, AVG(p.modal_price) AS modal_price
                    FROM {t_p} p
                    JOIN {t_c} c ON c.commodity_id = p.commodity_id
                    JOIN {t_m} m ON m.market_id = p.market_id
                    JOIN {t_d} d ON d.date_id = p.date_id
                    GROUP BY p.commodity_id, c.commodity_name, c.category, c.storable,
                             p.market_id, m.market_name, m.district, m.state,
                             d.full_date, d.year, d.week
                    ORDER BY p.commodity_id, p.market_id, d.full_date
                    """
                )
            )
            price_df = pd.DataFrame(p_res.fetchall(), columns=list(p_res.keys()))

            a_res = conn.execute(
                text(
                    f"""
                    SELECT a.commodity_id, a.market_id, d.full_date, a.quantity_tonnes
                    FROM {t_a} a
                    JOIN {t_d} d ON d.date_id = a.date_id
                    ORDER BY a.commodity_id, a.market_id, d.full_date
                    """
                )
            )
            arr_df = pd.DataFrame(a_res.fetchall(), columns=list(a_res.keys()))

        if not price_df.empty:
            price_df["full_date"] = pd.to_datetime(price_df["full_date"]).dt.date
            price_df["modal_price"] = price_df["modal_price"].astype(float)
            price_df["storable"] = price_df["storable"].astype(bool)
        if not arr_df.empty:
            arr_df["full_date"] = pd.to_datetime(arr_df["full_date"]).dt.date
            arr_df["quantity_tonnes"] = arr_df["quantity_tonnes"].astype(float)

        return price_df, arr_df

    @staticmethod
    def build_features_up_to_date(
        pair_prices: pd.DataFrame,
        comm_prices: pd.DataFrame,
        pair_arrivals: pd.DataFrame,
        decision_date: date,
        meta: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Compute exact point-in-time features using ONLY observations with full_date <= decision_date."""
        p_hist = pair_prices[pair_prices["full_date"] <= decision_date]
        if p_hist.empty:
            return None

        d_6 = decision_date - timedelta(days=6)
        d_21 = decision_date - timedelta(days=21)
        d_34 = decision_date - timedelta(days=34)
        d_90 = decision_date - timedelta(days=90)
        d_365 = decision_date - timedelta(days=365)

        recent_slice = p_hist[p_hist["full_date"] >= d_6]["modal_price"]
        prior_slice = p_hist[(p_hist["full_date"] >= d_34) & (p_hist["full_date"] <= d_21)]["modal_price"]
        slice_90 = p_hist[p_hist["full_date"] >= d_90]["modal_price"]
        slice_12m = p_hist[p_hist["full_date"] >= d_365]

        cv_12m: float | None = None
        if len(slice_12m) >= 2 and slice_12m["modal_price"].mean() > 0:
            cv_12m = float(slice_12m["modal_price"].std(ddof=1) / slice_12m["modal_price"].mean())
        else:
            c_12m = comm_prices[(comm_prices["full_date"] <= decision_date) & (comm_prices["full_date"] >= d_365)]["modal_price"]
            if len(c_12m) >= 2 and c_12m.mean() > 0:
                cv_12m = float(c_12m.std(ddof=1) / c_12m.mean())

        seas_windows = prior_year_seasonal_windows(decision_date, years_back=3, half_days=14)
        pair_seas_mask = pd.Series(False, index=p_hist.index)
        for ws, we in seas_windows:
            pair_seas_mask = pair_seas_mask | ((p_hist["full_date"] >= ws) & (p_hist["full_date"] <= we))
        seas_pair = p_hist[pair_seas_mask]
        if seas_pair.empty:
            c_hist = comm_prices[comm_prices["full_date"] <= decision_date]
            comm_seas_mask = pd.Series(False, index=c_hist.index)
            for ws, we in seas_windows:
                comm_seas_mask = comm_seas_mask | ((c_hist["full_date"] >= ws) & (c_hist["full_date"] <= we))
            seas_slice = c_hist[comm_seas_mask]
        else:
            seas_slice = seas_pair

        seasonal_avg = float(seas_slice["modal_price"].mean()) if not seas_slice.empty else None
        seasonal_years = int(seas_slice["year"].nunique()) if not seas_slice.empty else 0

        arr_obs_recent = 0
        arr_recent_t = 0.0
        arr_obs_prev = 0
        arr_prev_t = 0.0
        if not pair_arrivals.empty:
            a_hist = pair_arrivals[pair_arrivals["full_date"] <= decision_date]
            a_r = a_hist[a_hist["full_date"] >= (decision_date - timedelta(days=13))]
            a_p = a_hist[
                (a_hist["full_date"] >= (decision_date - timedelta(days=27)))
                & (a_hist["full_date"] <= (decision_date - timedelta(days=14)))
            ]
            arr_obs_recent = len(a_r)
            arr_recent_t = float(a_r["quantity_tonnes"].sum()) if not a_r.empty else 0.0
            arr_obs_prev = len(a_p)
            arr_prev_t = float(a_p["quantity_tonnes"].sum()) if not a_p.empty else 0.0

        return {
            **meta,
            "as_of_date": decision_date,
            "obs_recent_7d": len(recent_slice),
            "recent_7d_avg": float(recent_slice.mean()) if not recent_slice.empty else None,
            "obs_prior_7d": len(prior_slice),
            "prior_4w_avg": float(prior_slice.mean()) if not prior_slice.empty else None,
            "obs_90d": len(slice_90),
            "obs_12m": len(slice_12m),
            "window_start": slice_12m["full_date"].min() if not slice_12m.empty else None,
            "window_end": p_hist["full_date"].max(),
            "cv_12m": cv_12m,
            "seasonal_avg_price": seasonal_avg,
            "seasonal_obs": len(seas_slice),
            "seasonal_years": seasonal_years,
            "arr_obs_recent": arr_obs_recent,
            "arr_recent_tonnes": arr_recent_t,
            "arr_obs_prev": arr_obs_prev,
            "arr_prev_tonnes": arr_prev_t,
            "state_price_gap_pct": None,
            "data_sources": ["historical_backtest"],
        }

    def run_backtest(
        self,
        price_df: pd.DataFrame | None = None,
        arr_df: pd.DataFrame | None = None,
        eval_days: int = 365,
        step_days: int = 7,
        end_date_override: date | None = None,
        start_date_override: date | None = None,
        thresholds: SignalThresholds | None = None,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Execute fast zero-leakage rolling-origin backtest using pre-sorted numpy arrays & binary search."""
        cfg = thresholds or self.thresholds
        if price_df is None or arr_df is None:
            price_df, arr_df = self.load_historical_frames()

        if price_df.empty:
            empty_df = pd.DataFrame()
            return empty_df, summarize_backtest_results(empty_df)

        max_date = end_date_override or price_df["full_date"].max()
        eval_end = max_date - timedelta(days=14)
        eval_start = start_date_override or (eval_end - timedelta(days=eval_days))
        if eval_start >= eval_end:
            empty_df = pd.DataFrame()
            return empty_df, summarize_backtest_results(empty_df)

        decision_dates: list[date] = []
        cur = eval_start
        while cur <= eval_end:
            decision_dates.append(cur)
            cur += timedelta(days=step_days)

        # Pre-index commodity series for seasonal & CV fallback
        comm_arrays: dict[int, tuple[list[int], np.ndarray, np.ndarray, np.ndarray]] = {}
        for cid, cgrp in price_df.groupby("commodity_id"):
            cgrp_s = cgrp.sort_values("full_date")
            comm_arrays[int(cid)] = (
                [d.toordinal() for d in cgrp_s["full_date"]],
                cgrp_s["modal_price"].to_numpy(dtype=float),
                cgrp_s["year"].to_numpy(dtype=int),
                cgrp_s["week"].to_numpy(dtype=int),
            )

        arr_arrays: dict[tuple[int, int], tuple[list[int], np.ndarray]] = {}
        if not arr_df.empty:
            for (cid, mid), agrp in arr_df.groupby(["commodity_id", "market_id"]):
                agrp_s = agrp.sort_values("full_date")
                arr_arrays[(int(cid), int(mid))] = (
                    [d.toordinal() for d in agrp_s["full_date"]],
                    agrp_s["quantity_tonnes"].to_numpy(dtype=float),
                )

        # Exact pre-filter (results are unchanged): a (commodity, market) pair can only produce an evaluation if
        # it has an observation on/before the last decision date AND at least two observations inside some
        # 14-day outcome window, all of which lie in (first decision, last decision + 14d]. Skipping pairs that
        # fail either necessary condition avoids per-pair pandas overhead that otherwise dominates the runtime
        # (e.g. on a warehouse holding only a few recent days, every pair is skipped).
        pair_keys = ["commodity_id", "market_id"]
        first_decision, last_decision = decision_dates[0], decision_dates[-1]
        obs_dates = price_df["full_date"]
        eligibility = (
            price_df[pair_keys]
            .assign(
                _has_history=obs_dates <= last_decision,
                _in_outcome=(obs_dates > first_decision) & (obs_dates <= last_decision + timedelta(days=14)),
            )
            .groupby(pair_keys)
            .agg(has_history=("_has_history", "any"), outcome_obs=("_in_outcome", "sum"))
        )
        eligible_pairs = eligibility.index[eligibility["has_history"] & (eligibility["outcome_obs"] >= 2)]
        pair_source = price_df[pd.MultiIndex.from_frame(price_df[pair_keys]).isin(eligible_pairs)]

        rows: list[dict[str, Any]] = []
        for (cid, mid), pair_df in pair_source.groupby(pair_keys):
            pair_df = pair_df.sort_values("full_date")
            p_ords = [d.toordinal() for d in pair_df["full_date"]]
            p_vals = pair_df["modal_price"].to_numpy(dtype=float)
            p_years = pair_df["year"].to_numpy(dtype=int)
            p_weeks = pair_df["week"].to_numpy(dtype=int)
            p_dates = list(pair_df["full_date"])

            c_ords, c_vals, c_years, c_weeks = comm_arrays[int(cid)]
            a_entry = arr_arrays.get((int(cid), int(mid)))

            first_row = pair_df.iloc[0]
            meta = {
                "commodity_id": int(cid),
                "commodity": str(first_row["commodity"]),
                "category": str(first_row["category"]),
                "storable": bool(first_row["storable"]),
                "market_id": int(mid),
                "market": str(first_row["market"]),
                "district": str(first_row["district"]),
                "state": str(first_row["state"]),
            }

            for d_date in decision_dates:
                d_ord = d_date.toordinal()
                # Strictly <= decision_date boundary
                hi_hist = bisect.bisect_right(p_ords, d_ord)
                if hi_hist == 0:
                    continue

                # Outcome strictly in (decision_date, decision_date + 14]
                lo_fut = hi_hist
                hi_fut = bisect.bisect_right(p_ords, d_ord + 14)
                if (hi_fut - lo_fut) < 2:
                    continue

                lo_cur = bisect.bisect_left(p_ords, d_ord - 3, 0, hi_hist)
                if lo_cur >= hi_hist:
                    continue

                decision_price = float(p_vals[hi_hist - 1])
                future_avg_14d = float(p_vals[lo_fut:hi_fut].mean())
                if decision_price <= 0:
                    continue

                # Feature slices strictly inside [0, hi_hist) -> full_date <= decision_date
                lo_6 = bisect.bisect_left(p_ords, d_ord - 6, 0, hi_hist)
                lo_34 = bisect.bisect_left(p_ords, d_ord - 34, 0, hi_hist)
                hi_21 = bisect.bisect_right(p_ords, d_ord - 21, 0, hi_hist)
                lo_90 = bisect.bisect_left(p_ords, d_ord - 90, 0, hi_hist)
                lo_365 = bisect.bisect_left(p_ords, d_ord - 365, 0, hi_hist)

                rec_slice = p_vals[lo_6:hi_hist]
                pri_slice = p_vals[lo_34:hi_21]
                s12 = p_vals[lo_365:hi_hist]

                cv_12m: float | None = None
                if len(s12) >= 2 and s12.mean() > 0:
                    cv_12m = float(s12.std(ddof=1) / s12.mean())
                else:
                    c_hi = bisect.bisect_right(c_ords, d_ord)
                    c_lo = bisect.bisect_left(c_ords, d_ord - 365, 0, c_hi)
                    cs12 = c_vals[c_lo:c_hi]
                    if len(cs12) >= 2 and cs12.mean() > 0:
                        cv_12m = float(cs12.std(ddof=1) / cs12.mean())

                seas_windows = prior_year_seasonal_windows(d_date, years_back=3, half_days=14)
                pair_seas_vals_list: list[np.ndarray] = []
                pair_seas_yrs: list[int] = []
                for ws, we in seas_windows:
                    w_lo_i = bisect.bisect_left(p_ords, ws.toordinal(), 0, hi_hist)
                    w_hi_i = bisect.bisect_right(p_ords, we.toordinal(), 0, hi_hist)
                    if w_hi_i > w_lo_i:
                        pair_seas_vals_list.append(p_vals[w_lo_i:w_hi_i])
                        pair_seas_yrs.append(ws.year)

                if pair_seas_vals_list:
                    seas_vals = np.concatenate(pair_seas_vals_list)
                    seas_yrs = len(set(pair_seas_yrs))
                else:
                    c_hi = bisect.bisect_right(c_ords, d_ord)
                    comm_seas_vals_list: list[np.ndarray] = []
                    comm_seas_yrs: list[int] = []
                    for ws, we in seas_windows:
                        c_wlo = bisect.bisect_left(c_ords, ws.toordinal(), 0, c_hi)
                        c_whi = bisect.bisect_right(c_ords, we.toordinal(), 0, c_hi)
                        if c_whi > c_wlo:
                            comm_seas_vals_list.append(c_vals[c_wlo:c_whi])
                            comm_seas_yrs.append(ws.year)
                    if comm_seas_vals_list:
                        seas_vals = np.concatenate(comm_seas_vals_list)
                        seas_yrs = len(set(comm_seas_yrs))
                    else:
                        seas_vals = np.empty(0)
                        seas_yrs = 0

                seasonal_avg = float(seas_vals.mean()) if len(seas_vals) > 0 else None

                arr_obs_recent = 0
                arr_recent_t = 0.0
                arr_obs_prev = 0
                arr_prev_t = 0.0
                if a_entry is not None:
                    a_ords, a_vals = a_entry
                    a_hi = bisect.bisect_right(a_ords, d_ord)
                    a_lo_r = bisect.bisect_left(a_ords, d_ord - 13, 0, a_hi)
                    a_lo_p = bisect.bisect_left(a_ords, d_ord - 27, 0, a_hi)
                    a_hi_p = bisect.bisect_right(a_ords, d_ord - 14, 0, a_hi)
                    arr_obs_recent = a_hi - a_lo_r
                    arr_recent_t = float(a_vals[a_lo_r:a_hi].sum()) if arr_obs_recent > 0 else 0.0
                    arr_obs_prev = a_hi_p - a_lo_p
                    arr_prev_t = float(a_vals[a_lo_p:a_hi_p].sum()) if arr_obs_prev > 0 else 0.0

                features = {
                    **meta,
                    "as_of_date": d_date,
                    "obs_recent_7d": len(rec_slice),
                    "recent_7d_avg": float(rec_slice.mean()) if len(rec_slice) > 0 else None,
                    "obs_prior_7d": len(pri_slice),
                    "prior_4w_avg": float(pri_slice.mean()) if len(pri_slice) > 0 else None,
                    "obs_90d": hi_hist - lo_90,
                    "obs_12m": len(s12),
                    "window_start": p_dates[lo_365] if len(s12) > 0 else None,
                    "window_end": p_dates[hi_hist - 1],
                    "cv_12m": cv_12m,
                    "seasonal_avg_price": seasonal_avg,
                    "seasonal_obs": len(seas_vals),
                    "seasonal_years": seas_yrs,
                    "arr_obs_recent": arr_obs_recent,
                    "arr_recent_tonnes": arr_recent_t,
                    "arr_obs_prev": arr_obs_prev,
                    "arr_prev_tonnes": arr_prev_t,
                    "state_price_gap_pct": None,
                    "data_sources": ["historical_backtest"],
                }

                sig = evaluate_signal_rules(features, thresholds=cfg)
                future_change_pct = round(((future_avg_14d - decision_price) / decision_price) * 100.0, 2)

                mom = sig.price_change_4w_pct
                if mom is None or sig.insufficient_evidence:
                    mom_verdict = "NEUTRAL"
                elif mom >= cfg.momentum_wait_pct:
                    mom_verdict = "WAIT"
                elif mom <= cfg.momentum_sell_pct:
                    mom_verdict = "SELL NOW"
                else:
                    mom_verdict = "NEUTRAL"

                actual_outcome = "WAIT" if future_change_pct >= 2.0 else "SELL NOW"
                is_correct = (
                    (sig.verdict_class == actual_outcome)
                    if sig.verdict_class in ("WAIT", "SELL NOW")
                    else None
                )

                rows.append(
                    {
                        "decision_date": d_date,
                        "commodity_id": meta["commodity_id"],
                        "commodity": meta["commodity"],
                        "storable": meta["storable"],
                        "market_id": meta["market_id"],
                        "market": meta["market"],
                        "state": meta["state"],
                        "decision_price": round(decision_price, 2),
                        "future_avg_14d": round(future_avg_14d, 2),
                        "future_14d_change_pct": future_change_pct,
                        "actual_outcome": actual_outcome,
                        "verdict_class": sig.verdict_class,
                        "verdict_label": sig.verdict_label,
                        "is_correct": is_correct,
                        "price_change_4w_pct": sig.price_change_4w_pct,
                        "vs_seasonal_avg_pct": sig.vs_seasonal_avg_pct,
                        "arrivals_trend": sig.arrivals_trend,
                        "arrivals_change_14d_pct": sig.arrivals_change_14d_pct,
                        "cv_12m": sig.cv_12m,
                        "baseline_always_sell": "SELL NOW",
                        "baseline_momentum": mom_verdict,
                    }
                )

        results_df = pd.DataFrame(rows)
        summary = summarize_backtest_results(results_df)
        return results_df, summary
