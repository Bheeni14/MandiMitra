"""Coverage metrics & SQL-driven signal feature extraction (leak-free up to as_of_date)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import text

from database.connection import DatabaseManager, get_db_manager
from services.concurrency import run_queries, use_concurrency


def get_source_coverage(db: DatabaseManager | None = None) -> dict[str, Any]:
    """Summarize real/demo source provenance, freshness, and entity counts."""
    db = db or get_db_manager()
    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_a = db.table_ref("warehouse", "fact_arrivals_daily")
    t_ap = db.table_ref("warehouse", "fact_crop_production")
    t_d = db.table_ref("warehouse", "dim_date")
    t_c = db.table_ref("warehouse", "dim_commodity")
    t_m = db.table_ref("warehouse", "dim_market")

    # The three single-row summaries are fetched in one statement: each extra statement costs a full network
    # round trip against a remote database.
    with db.readonly_connection() as conn:
        summary = conn.execute(
            text(
                f"""
                SELECT ps.*, ars.*, apy.*
                FROM (
                    SELECT
                        COUNT(*) AS total_price_records,
                        COUNT(DISTINCT p.commodity_id) AS commodities_count,
                        COUNT(DISTINCT p.market_id) AS markets_count,
                        COUNT(DISTINCT m.state) AS states_count,
                        MIN(d.full_date) AS min_price_date,
                        MAX(d.full_date) AS max_price_date
                    FROM {t_p} p
                    JOIN {t_d} d ON d.date_id = p.date_id
                    JOIN {t_m} m ON m.market_id = p.market_id
                ) ps
                CROSS JOIN (
                    SELECT
                        COUNT(*) AS total_arrival_records,
                        COALESCE(SUM(quantity_tonnes), 0) AS total_tonnes,
                        MIN(d.full_date) AS min_arrival_date,
                        MAX(d.full_date) AS max_arrival_date
                    FROM {t_a} a
                    JOIN {t_d} d ON d.date_id = a.date_id
                ) ars
                CROSS JOIN (
                    SELECT
                        COUNT(*) AS total_apy_records,
                        MIN(crop_year) AS min_year,
                        MAX(crop_year) AS max_year,
                        COUNT(DISTINCT state) AS apy_states
                    FROM {t_ap}
                ) apy
                """
            )
        ).mappings().one()

        price_sources = conn.execute(
            text(
                f"""
                SELECT source_name, COUNT(*) AS rows, MIN(d.full_date) AS min_date, MAX(d.full_date) AS max_date
                FROM {t_p} p
                JOIN {t_d} d ON d.date_id = p.date_id
                GROUP BY source_name
                ORDER BY rows DESC
                """
            )
        ).mappings().all()

    return {
        "backend": db.backend,
        "is_demo": db.is_demo,
        **dict(summary),
        "price_sources": [dict(r) for r in price_sources],
    }


def get_mapping_coverage(db: DatabaseManager | None = None) -> pd.DataFrame:
    """Return commodity-to-crop mapping coverage across price commodities and APY production rows."""
    db = db or get_db_manager()
    t_c = db.table_ref("warehouse", "dim_commodity")
    t_cr = db.table_ref("warehouse", "dim_crop")
    t_mcc = db.table_ref("warehouse", "map_commodity_crop")
    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_ap = db.table_ref("warehouse", "fact_crop_production")

    sql = f"""
    SELECT
        dc.commodity_name,
        dc.category,
        dc.storable,
        cr.crop_name AS mapped_apy_crop,
        cr.crop_type,
        (SELECT COUNT(*) FROM {t_p} p WHERE p.commodity_id = dc.commodity_id) AS price_rows,
        (SELECT COUNT(*) FROM {t_ap} ap WHERE ap.crop_id = cr.crop_id) AS apy_rows
    FROM {t_c} dc
    LEFT JOIN {t_mcc} mcc ON mcc.commodity_id = dc.commodity_id
    LEFT JOIN {t_cr} cr ON cr.crop_id = mcc.crop_id
    ORDER BY dc.commodity_name
    """
    with db.readonly_connection() as conn:
        res = conn.execute(text(sql))
        df = pd.DataFrame(res.fetchall(), columns=list(res.keys()))
    if not df.empty:
        df["storable"] = df["storable"].astype(bool)
        df["is_mapped"] = df["mapped_apy_crop"].notna()
    return df


def prior_year_seasonal_windows(
    as_of_date: date,
    years_back: int = 3,
    half_days: int = 14,
) -> list[tuple[date, date]]:
    """Build exact prior-year +/- half_days windows that wrap seamlessly across Dec/Jan."""
    windows: list[tuple[date, date]] = []
    for k in range(1, years_back + 1):
        try:
            anchor = date(as_of_date.year - k, as_of_date.month, as_of_date.day)
        except ValueError:
            anchor = date(as_of_date.year - k, 2, 28)
        windows.append((anchor - timedelta(days=half_days), anchor + timedelta(days=half_days)))
    return windows


def fetch_signal_features_sql(
    db: DatabaseManager,
    commodity_name: str,
    market_name: str,
    state: str,
    as_of_date: date | None = None,
) -> dict[str, Any] | None:
    """Extract all deterministic signal metrics from SQL strictly <= as_of_date (zero leakage)."""
    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_a = db.table_ref("warehouse", "fact_arrivals_daily")
    t_c = db.table_ref("warehouse", "dim_commodity")
    t_m = db.table_ref("warehouse", "dim_market")
    t_d = db.table_ref("warehouse", "dim_date")

    with db.readonly_connection() as conn:
        meta = conn.execute(
            text(
                f"""
                SELECT c.commodity_id, c.commodity_name, c.category, c.storable,
                       m.market_id, m.market_name, m.district, m.state
                FROM {t_c} c
                JOIN {t_m} m ON m.market_name = :mkt AND m.state = :st
                WHERE c.commodity_name = :comm
                LIMIT 1
                """
            ),
            {"comm": commodity_name, "mkt": market_name, "st": state},
        ).mappings().first()
        if not meta:
            return None

        comm_id = int(meta["commodity_id"])
        mkt_id = int(meta["market_id"])

        if as_of_date is None:
            latest_raw = conn.execute(
                text(
                    f"""
                    SELECT MAX(d.full_date)
                    FROM {t_p} p
                    JOIN {t_d} d ON d.date_id = p.date_id
                    WHERE p.commodity_id = :cid AND p.market_id = :mid
                    """
                ),
                {"cid": comm_id, "mid": mkt_id},
            ).scalar()
            if latest_raw is None:
                return None
            as_of_date = pd.to_datetime(latest_raw).date()

        d_6 = as_of_date - timedelta(days=6)
        d_21 = as_of_date - timedelta(days=21)
        d_34 = as_of_date - timedelta(days=34)
        d_90 = as_of_date - timedelta(days=90)
        d_365 = as_of_date - timedelta(days=365)

        p_recent_start = d_6.isoformat() if db.backend == "sqlite" else d_6
        p_as_of = as_of_date.isoformat() if db.backend == "sqlite" else as_of_date
        p_prev_start = d_34.isoformat() if db.backend == "sqlite" else d_34
        p_prev_end = d_21.isoformat() if db.backend == "sqlite" else d_21
        p_90 = d_90.isoformat() if db.backend == "sqlite" else d_90
        p_365 = d_365.isoformat() if db.backend == "sqlite" else d_365

        # Pre-aggregate by reporting date so multiple varieties/grades on one day count as 1 trading day
        def _fetch_price_metrics() -> Any:
            with db.readonly_connection() as qconn:
                return qconn.execute(
                    text(
                        f"""
                        WITH daily_prices AS (
                            SELECT
                                d.full_date,
                                AVG(p.modal_price) AS modal_price
                            FROM {t_p} p
                            JOIN {t_d} d ON d.date_id = p.date_id
                            WHERE p.commodity_id = :cid
                              AND p.market_id = :mid
                              AND d.full_date <= :as_of
                            GROUP BY d.full_date
                        )
                        SELECT
                            COUNT(CASE WHEN full_date BETWEEN :r_start AND :as_of THEN 1 END) AS obs_recent_7d,
                            AVG(CASE WHEN full_date BETWEEN :r_start AND :as_of THEN modal_price END) AS recent_7d_avg,
                            COUNT(CASE WHEN full_date BETWEEN :p_start AND :p_end THEN 1 END) AS obs_prior_7d,
                            AVG(CASE WHEN full_date BETWEEN :p_start AND :p_end THEN modal_price END) AS prior_4w_avg,
                            COUNT(CASE WHEN full_date BETWEEN :d90 AND :as_of THEN 1 END) AS obs_90d,
                            MIN(CASE WHEN full_date BETWEEN :d365 AND :as_of THEN full_date END) AS window_start,
                            MAX(full_date) AS window_end,
                            STDDEV_SAMP(CASE WHEN full_date BETWEEN :d365 AND :as_of THEN modal_price END)
                              / NULLIF(AVG(CASE WHEN full_date BETWEEN :d365 AND :as_of THEN modal_price END), 0) AS market_cv_12m,
                            COUNT(CASE WHEN full_date BETWEEN :d365 AND :as_of THEN 1 END) AS obs_12m
                        FROM daily_prices
                        """
                    ),
                    {
                        "cid": comm_id,
                        "mid": mkt_id,
                        "r_start": p_recent_start,
                        "as_of": p_as_of,
                        "p_start": p_prev_start,
                        "p_end": p_prev_end,
                        "d90": p_90,
                        "d365": p_365,
                    },
                ).mappings().one()


        # Commodity-wide 12m CV on daily market-date averages (matches Q1 if market 12m is sparse)
        def _fetch_comm_cv_row() -> Any:
            with db.readonly_connection() as qconn:
                return qconn.execute(
                    text(
                        f"""
                        WITH comm_daily AS (
                            SELECT p.market_id, d.full_date, AVG(p.modal_price) AS modal_price
                            FROM {t_p} p
                            JOIN {t_d} d ON d.date_id = p.date_id
                            WHERE p.commodity_id = :cid
                              AND d.full_date BETWEEN :d365 AND :as_of
                            GROUP BY p.market_id, d.full_date
                        )
                        SELECT
                            STDDEV_SAMP(modal_price) / NULLIF(AVG(modal_price), 0) AS comm_cv_12m
                        FROM comm_daily
                        """
                    ),
                    {"cid": comm_id, "d365": p_365, "as_of": p_as_of},
                ).mappings().one()


        # Seasonal position: exact prior-year +/- 14-day windows across previous 3 years (wraps Dec/Jan cleanly)
        seas_windows = prior_year_seasonal_windows(as_of_date, years_back=3, half_days=14)
        seas_params: dict[str, Any] = {"cid": comm_id, "mid": mkt_id}
        win_clauses: list[str] = []
        for idx, (ws, we) in enumerate(seas_windows):
            sk, ek = f"ws_{idx}", f"we_{idx}"
            seas_params[sk] = ws.isoformat() if db.backend == "sqlite" else ws
            seas_params[ek] = we.isoformat() if db.backend == "sqlite" else we
            win_clauses.append(f"(d.full_date BETWEEN :{sk} AND :{ek})")
        win_sql = " OR ".join(win_clauses)

        def _fetch_seasonal_row() -> Any:
            with db.readonly_connection() as qconn:
                return qconn.execute(
                    text(
                        f"""
                        WITH pair_seas_daily AS (
                            SELECT d.full_date, d.year, AVG(p.modal_price) AS modal_price
                            FROM {t_p} p
                            JOIN {t_d} d ON d.date_id = p.date_id
                            WHERE p.commodity_id = :cid
                              AND p.market_id = :mid
                              AND ({win_sql})
                            GROUP BY d.full_date, d.year
                        )
                        SELECT
                            AVG(modal_price) AS seasonal_avg_price,
                            COUNT(*) AS seasonal_obs,
                            COUNT(DISTINCT year) AS seasonal_years
                        FROM pair_seas_daily
                        """
                    ),
                    seas_params,
                ).mappings().one()


        comm_seas_params = {k: v for k, v in seas_params.items() if k != "mid"}

        def _fetch_comm_seasonal_row() -> Any:
            with db.readonly_connection() as qconn:
                return qconn.execute(
                    text(
                        f"""
                        WITH comm_seas_daily AS (
                            SELECT p.market_id, d.full_date, d.year, AVG(p.modal_price) AS modal_price
                            FROM {t_p} p
                            JOIN {t_d} d ON d.date_id = p.date_id
                            WHERE p.commodity_id = :cid
                              AND ({win_sql})
                            GROUP BY p.market_id, d.full_date, d.year
                        )
                        SELECT
                            AVG(modal_price) AS seasonal_avg_price,
                            COUNT(*) AS seasonal_obs,
                            COUNT(DISTINCT year) AS seasonal_years
                        FROM comm_seas_daily
                        """
                    ),
                    comm_seas_params,
                ).mappings().one()

        # Arrivals trend: 14-day vs previous 14-day ACTUAL tonnage ONLY (never market count)
        a_r_start = (as_of_date - timedelta(days=13)).isoformat() if db.backend == "sqlite" else (as_of_date - timedelta(days=13))
        a_p_start = (as_of_date - timedelta(days=27)).isoformat() if db.backend == "sqlite" else (as_of_date - timedelta(days=27))
        a_p_end = (as_of_date - timedelta(days=14)).isoformat() if db.backend == "sqlite" else (as_of_date - timedelta(days=14))

        def _fetch_arr_row() -> Any:
            with db.readonly_connection() as qconn:
                return qconn.execute(
                    text(
                        f"""
                        SELECT
                            COUNT(CASE WHEN d.full_date BETWEEN :ar_start AND :as_of THEN 1 END) AS arr_obs_recent,
                            SUM(CASE WHEN d.full_date BETWEEN :ar_start AND :as_of THEN a.quantity_tonnes ELSE 0 END) AS arr_recent_tonnes,
                            COUNT(CASE WHEN d.full_date BETWEEN :ap_start AND :ap_end THEN 1 END) AS arr_obs_prev,
                            SUM(CASE WHEN d.full_date BETWEEN :ap_start AND :ap_end THEN a.quantity_tonnes ELSE 0 END) AS arr_prev_tonnes
                        FROM {t_a} a
                        JOIN {t_d} d ON d.date_id = a.date_id
                        WHERE a.commodity_id = :cid
                          AND a.market_id = :mid
                          AND d.full_date BETWEEN :ap_start AND :as_of
                        """
                    ),
                    {
                        "cid": comm_id,
                        "mid": mkt_id,
                        "ar_start": a_r_start,
                        "as_of": p_as_of,
                        "ap_start": a_p_start,
                        "ap_end": a_p_end,
                    },
                ).mappings().one()


        # State price gap for latest month <= as_of_date
        def _fetch_gap_row() -> Any:
            with db.readonly_connection() as qconn:
                return qconn.execute(
                    text(
                        f"""
                        WITH state_m AS (
                            SELECT m.state, AVG(p.modal_price) AS st_avg
                            FROM {t_p} p
                            JOIN {t_d} d ON d.date_id = p.date_id
                            JOIN {t_m} m ON m.market_id = p.market_id
                            WHERE p.commodity_id = :cid
                              AND d.year = :yr AND d.month = :mo
                              AND d.full_date <= :as_of
                            GROUP BY m.state
                        )
                        SELECT
                            (MAX(st_avg) - MIN(st_avg)) / NULLIF(MIN(st_avg), 0) * 100.0 AS state_gap_pct
                        FROM state_m
                        HAVING COUNT(*) >= 2
                        """
                    ),
                    {"cid": comm_id, "yr": as_of_date.year, "mo": as_of_date.month, "as_of": p_as_of},
                ).mappings().first()


        # Sources present for this market-commodity
        def _fetch_src_rows() -> Any:
            with db.readonly_connection() as qconn:
                return qconn.execute(
                    text(
                        f"""
                        SELECT DISTINCT p.source_name
                        FROM {t_p} p
                        JOIN {t_d} d ON d.date_id = p.date_id
                        WHERE p.commodity_id = :cid AND p.market_id = :mid AND d.full_date <= :as_of
                        """
                    ),
                    {"cid": comm_id, "mid": mkt_id, "as_of": p_as_of},
                ).scalars().all()

        # Everything above only depends on the ids and as_of_date, so the queries run concurrently on
        # PostgreSQL (one ~200ms round trip each against the remote database). The commodity-wide seasonal
        # fallback is fetched speculatively there and used only when the market has no seasonal history.
        feature_queries: dict[str, Any] = {
            "price_metrics": _fetch_price_metrics,
            "comm_cv_row": _fetch_comm_cv_row,
            "seasonal_row": _fetch_seasonal_row,
            "arr_row": _fetch_arr_row,
            "gap_row": _fetch_gap_row,
            "src_rows": _fetch_src_rows,
        }
        if use_concurrency(db):
            feature_queries["comm_seasonal_row"] = _fetch_comm_seasonal_row
        fetched = run_queries(db, feature_queries)
        price_metrics = fetched["price_metrics"]
        comm_cv_row = fetched["comm_cv_row"]
        seasonal_row = fetched["seasonal_row"]
        arr_row = fetched["arr_row"]
        gap_row = fetched["gap_row"]
        src_rows = fetched["src_rows"]

        # Fallback to commodity-wide prior-year seasonal baseline if market lacks multi-year history
        if seasonal_row["seasonal_avg_price"] is None:
            seasonal_row = (
                fetched["comm_seasonal_row"] if "comm_seasonal_row" in fetched else _fetch_comm_seasonal_row()
            )

    return {
        "commodity_id": comm_id,
        "commodity": meta["commodity_name"],
        "category": meta["category"],
        "storable": bool(meta["storable"]),
        "market_id": mkt_id,
        "market": meta["market_name"],
        "district": meta["district"],
        "state": meta["state"],
        "as_of_date": as_of_date,
        "obs_recent_7d": int(price_metrics["obs_recent_7d"] or 0),
        "recent_7d_avg": float(price_metrics["recent_7d_avg"]) if price_metrics["recent_7d_avg"] is not None else None,
        "obs_prior_7d": int(price_metrics["obs_prior_7d"] or 0),
        "prior_4w_avg": float(price_metrics["prior_4w_avg"]) if price_metrics["prior_4w_avg"] is not None else None,
        "obs_90d": int(price_metrics["obs_90d"] or 0),
        "obs_12m": int(price_metrics["obs_12m"] or 0),
        "window_start": pd.to_datetime(price_metrics["window_start"]).date() if price_metrics["window_start"] else None,
        "window_end": pd.to_datetime(price_metrics["window_end"]).date() if price_metrics["window_end"] else as_of_date,
        "cv_12m": float(price_metrics["market_cv_12m"]) if price_metrics["market_cv_12m"] is not None else (
            float(comm_cv_row["comm_cv_12m"]) if comm_cv_row and comm_cv_row["comm_cv_12m"] is not None else None
        ),
        "seasonal_avg_price": float(seasonal_row["seasonal_avg_price"]) if seasonal_row["seasonal_avg_price"] is not None else None,
        "seasonal_obs": int(seasonal_row["seasonal_obs"] or 0),
        "seasonal_years": int(seasonal_row["seasonal_years"] or 0),
        "arr_obs_recent": int(arr_row["arr_obs_recent"] or 0),
        "arr_recent_tonnes": float(arr_row["arr_recent_tonnes"] or 0.0),
        "arr_obs_prev": int(arr_row["arr_obs_prev"] or 0),
        "arr_prev_tonnes": float(arr_row["arr_prev_tonnes"] or 0.0),
        "state_price_gap_pct": float(gap_row["state_gap_pct"]) if gap_row and gap_row["state_gap_pct"] is not None else None,
        "data_sources": sorted({str(s) for s in src_rows}),
    }
