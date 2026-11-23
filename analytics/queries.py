"""Seven SQL Analytics (Q1-Q7) executed against PostgreSQL/Supabase views or Demo SQLite star schema."""

from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import text

from database.connection import DatabaseManager, get_db_manager
from services.concurrency import run_queries


def _run_df(db: DatabaseManager, sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    with db.readonly_connection() as conn:
        result = conn.execute(text(sql), params or {})
        rows = result.fetchall()
        cols = list(result.keys())
    return pd.DataFrame(rows, columns=cols)


def get_q1_price_volatility(
    db: DatabaseManager | None = None,
    min_observations: int = 30,
    months: int = 12,
) -> pd.DataFrame:
    """Q1: Commodities with highest price volatility (CV = STDDEV / AVG) over trailing 12 months."""
    db = db or get_db_manager()
    if db.backend == "postgresql" and min_observations == 30 and months == 12:
        return _run_df(db, "SELECT * FROM analytics.v_q1_price_volatility ORDER BY cv DESC")

    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_c = db.table_ref("warehouse", "dim_commodity")
    t_d = db.table_ref("warehouse", "dim_date")

    if db.backend == "postgresql":
        date_expr = f"(a.as_of_date - INTERVAL '{int(months)} months')"
        date_cast = f"(MAX(as_of_date) - INTERVAL '{int(months)} months')::date"
        storable_sel = "c.storable"
    else:
        date_expr = f"date(a.as_of_date, '-{int(months)} months')"
        date_cast = f"date(MAX(as_of_date), '-{int(months)} months')"
        storable_sel = "c.storable"

    sql = f"""
    WITH anchor AS (
        SELECT MAX(d.full_date) AS as_of_date
        FROM {t_p} p
        JOIN {t_d} d ON d.date_id = p.date_id
    ),
    windowed AS (
        SELECT
            c.commodity_id,
            c.commodity_name,
            c.category,
            {storable_sel} AS storable,
            p.modal_price,
            a.as_of_date
        FROM {t_p} p
        JOIN {t_c} c ON c.commodity_id = p.commodity_id
        JOIN {t_d} d ON d.date_id = p.date_id
        CROSS JOIN anchor a
        WHERE d.full_date >= {date_expr}
    )
    SELECT
        commodity_id,
        commodity_name,
        category,
        storable,
        COUNT(*) AS observation_count,
        ROUND(AVG(modal_price), 2) AS avg_modal_price,
        ROUND(STDDEV_SAMP(modal_price), 2) AS stddev_modal_price,
        ROUND(STDDEV_SAMP(modal_price) / NULLIF(AVG(modal_price), 0), 4) AS cv,
        ROUND(MIN(modal_price), 2) AS min_modal_price,
        ROUND(MAX(modal_price), 2) AS max_modal_price,
        MAX(as_of_date) AS window_end,
        {date_cast} AS window_start,
        RANK() OVER (ORDER BY (STDDEV_SAMP(modal_price) / NULLIF(AVG(modal_price), 0)) DESC NULLS LAST) AS volatility_rank
    FROM windowed
    GROUP BY commodity_id, commodity_name, category, storable
    HAVING COUNT(*) >= :min_obs
    ORDER BY cv DESC
    """
    df = _run_df(db, sql, {"min_obs": min_observations})
    if not df.empty and "storable" in df.columns:
        df["storable"] = df["storable"].astype(bool)
    return df


def get_q2_state_price_gaps(
    db: DatabaseManager | None = None,
    commodity_name: str | None = None,
    year: int | None = None,
    month: int | None = None,
) -> pd.DataFrame:
    """Q2: Highest and lowest priced states per commodity & month, price spread and % difference."""
    db = db or get_db_manager()
    clauses = []
    params: dict[str, Any] = {}
    if commodity_name:
        clauses.append("commodity_name = :commodity")
        params["commodity"] = commodity_name
    if year is not None:
        clauses.append("year = :year")
        params["year"] = year
    if month is not None:
        clauses.append("month = :month")
        params["month"] = month

    if db.backend == "postgresql":
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return _run_df(
            db,
            f"SELECT * FROM analytics.v_q2_state_price_gaps {where_sql} ORDER BY year DESC, month DESC, pct_difference DESC",
            params,
        )

    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_c = db.table_ref("warehouse", "dim_commodity")
    t_m = db.table_ref("warehouse", "dim_market")
    t_d = db.table_ref("warehouse", "dim_date")

    inner_clauses = []
    if commodity_name:
        inner_clauses.append("c.commodity_name = :commodity")
    if year is not None:
        inner_clauses.append("d.year = :year")
    if month is not None:
        inner_clauses.append("d.month = :month")
    inner_where = f"WHERE {' AND '.join(inner_clauses)}" if inner_clauses else ""

    sql = f"""
    WITH state_monthly AS (
        SELECT
            c.commodity_id,
            c.commodity_name,
            d.year,
            d.month,
            m.state,
            ROUND(AVG(p.modal_price), 2) AS avg_modal_price,
            COUNT(*) AS observation_count
        FROM {t_p} p
        JOIN {t_c} c ON c.commodity_id = p.commodity_id
        JOIN {t_m} m ON m.market_id = p.market_id
        JOIN {t_d} d ON d.date_id = p.date_id
        {inner_where}
        GROUP BY c.commodity_id, c.commodity_name, d.year, d.month, m.state
    ),
    ranked AS (
        SELECT
            *,
            ROW_NUMBER() OVER (PARTITION BY commodity_id, year, month ORDER BY avg_modal_price DESC) AS rn_high,
            ROW_NUMBER() OVER (PARTITION BY commodity_id, year, month ORDER BY avg_modal_price ASC) AS rn_low,
            COUNT(*) OVER (PARTITION BY commodity_id, year, month) AS state_count
        FROM state_monthly
    )
    SELECT
        hi.commodity_id,
        hi.commodity_name,
        hi.year,
        hi.month,
        hi.state AS highest_state,
        hi.avg_modal_price AS highest_avg_price,
        lo.state AS lowest_state,
        lo.avg_modal_price AS lowest_avg_price,
        ROUND(hi.avg_modal_price - lo.avg_modal_price, 2) AS price_spread,
        ROUND(((hi.avg_modal_price - lo.avg_modal_price) / NULLIF(lo.avg_modal_price, 0)) * 100.0, 2) AS pct_difference,
        hi.state_count
    FROM ranked hi
    JOIN ranked lo
        ON hi.commodity_id = lo.commodity_id
       AND hi.year = lo.year
       AND hi.month = lo.month
    WHERE hi.rn_high = 1
      AND lo.rn_low = 1
      AND hi.state_count >= 2
    ORDER BY hi.year DESC, hi.month DESC, pct_difference DESC
    """
    return _run_df(db, sql, params)


def get_q3_mom_trends(
    db: DatabaseManager | None = None,
    commodity_name: str | None = None,
) -> pd.DataFrame:
    """Q3: Monthly average modal price, previous month price, MoM absolute/pct change, and 3-month MA."""
    db = db or get_db_manager()
    params: dict[str, Any] = {}
    if db.backend == "postgresql":
        where_sql = "WHERE commodity_name = :commodity" if commodity_name else ""
        if commodity_name:
            params["commodity"] = commodity_name
        return _run_df(
            db,
            f"SELECT * FROM analytics.v_q3_mom_trends {where_sql} ORDER BY commodity_name, year, month",
            params,
        )

    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_c = db.table_ref("warehouse", "dim_commodity")
    t_d = db.table_ref("warehouse", "dim_date")
    where_sql = "WHERE c.commodity_name = :commodity" if commodity_name else ""
    if commodity_name:
        params["commodity"] = commodity_name

    sql = f"""
    WITH monthly AS (
        SELECT
            c.commodity_id,
            c.commodity_name,
            d.year,
            d.month,
            printf('%04d-%02d-01', d.year, d.month) AS month_start,
            ROUND(AVG(p.modal_price), 2) AS avg_modal_price,
            COUNT(*) AS observation_count
        FROM {t_p} p
        JOIN {t_c} c ON c.commodity_id = p.commodity_id
        JOIN {t_d} d ON d.date_id = p.date_id
        {where_sql}
        GROUP BY c.commodity_id, c.commodity_name, d.year, d.month
    ),
    lagged AS (
        SELECT
            commodity_id,
            commodity_name,
            year,
            month,
            month_start,
            avg_modal_price,
            LAG(avg_modal_price) OVER (PARTITION BY commodity_id ORDER BY year, month) AS prev_month_price,
            ROUND(AVG(avg_modal_price) OVER (PARTITION BY commodity_id ORDER BY year, month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS ma_3m,
            observation_count
        FROM monthly
    )
    SELECT
        commodity_id,
        commodity_name,
        year,
        month,
        month_start,
        avg_modal_price,
        prev_month_price,
        ROUND(avg_modal_price - prev_month_price, 2) AS mom_abs_change,
        ROUND(((avg_modal_price - prev_month_price) / NULLIF(prev_month_price, 0)) * 100.0, 2) AS mom_pct_change,
        ma_3m,
        observation_count
    FROM lagged
    ORDER BY commodity_name, year, month
    """
    return _run_df(db, sql, params)


def get_q4_arrivals_vs_price(
    db: DatabaseManager | None = None,
    commodity_name: str | None = None,
) -> pd.DataFrame:
    """Q4: Same-month and 1-month lagged correlation between actual arrival tonnage and modal prices."""
    db = db or get_db_manager()
    params: dict[str, Any] = {}
    if db.backend == "postgresql":
        where_sql = "WHERE commodity_name = :commodity" if commodity_name else ""
        if commodity_name:
            params["commodity"] = commodity_name
        return _run_df(
            db,
            f"SELECT * FROM analytics.v_q4_arrivals_vs_price {where_sql} ORDER BY corr_same_month ASC NULLS LAST",
            params,
        )

    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_a = db.table_ref("warehouse", "fact_arrivals_daily")
    t_c = db.table_ref("warehouse", "dim_commodity")
    t_d = db.table_ref("warehouse", "dim_date")
    where_sql = "WHERE c.commodity_name = :commodity" if commodity_name else ""
    if commodity_name:
        params["commodity"] = commodity_name

    sql = f"""
    WITH monthly_price AS (
        SELECT
            p.commodity_id,
            d.year,
            d.month,
            printf('%04d-%02d-01', d.year, d.month) AS mth,
            AVG(p.modal_price) AS avg_price
        FROM {t_p} p
        JOIN {t_d} d ON d.date_id = p.date_id
        GROUP BY p.commodity_id, d.year, d.month
    ),
    monthly_arrivals AS (
        SELECT
            a.commodity_id,
            d.year,
            d.month,
            printf('%04d-%02d-01', d.year, d.month) AS mth,
            SUM(a.quantity_tonnes) AS total_arrivals_tonnes
        FROM {t_a} a
        JOIN {t_d} d ON d.date_id = a.date_id
        GROUP BY a.commodity_id, d.year, d.month
    ),
    joined AS (
        SELECT
            mp.commodity_id,
            c.commodity_name,
            mp.mth,
            mp.avg_price,
            ma.total_arrivals_tonnes,
            LAG(ma.total_arrivals_tonnes) OVER (PARTITION BY mp.commodity_id ORDER BY mp.mth) AS arrivals_prev_month
        FROM monthly_price mp
        JOIN monthly_arrivals ma
            ON mp.commodity_id = ma.commodity_id
           AND mp.mth = ma.mth
        JOIN {t_c} c ON c.commodity_id = mp.commodity_id
        {where_sql}
    )
    SELECT
        commodity_id,
        commodity_name,
        COUNT(*) AS paired_months,
        ROUND(AVG(total_arrivals_tonnes), 2) AS avg_monthly_arrivals_tonnes,
        ROUND(AVG(avg_price), 2) AS avg_monthly_price,
        ROUND(CORR(avg_price, total_arrivals_tonnes), 4) AS corr_same_month,
        ROUND(CORR(avg_price, arrivals_prev_month), 4) AS corr_lag_1m
    FROM joined
    GROUP BY commodity_id, commodity_name
    HAVING COUNT(*) >= 3
    ORDER BY corr_same_month ASC NULLS LAST
    """
    return _run_df(db, sql, params)


def get_q5_seasonal_price_index(
    db: DatabaseManager | None = None,
    commodity_name: str | None = None,
) -> pd.DataFrame:
    """Q5: Calendar month seasonal index against annual average price per commodity."""
    db = db or get_db_manager()
    params: dict[str, Any] = {}
    if db.backend == "postgresql":
        where_sql = "WHERE commodity_name = :commodity" if commodity_name else ""
        if commodity_name:
            params["commodity"] = commodity_name
        return _run_df(
            db,
            f"SELECT * FROM analytics.v_q5_seasonal_price_index {where_sql} ORDER BY commodity_name, month",
            params,
        )

    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_c = db.table_ref("warehouse", "dim_commodity")
    t_d = db.table_ref("warehouse", "dim_date")
    where_sql = "WHERE c.commodity_name = :commodity" if commodity_name else ""
    if commodity_name:
        params["commodity"] = commodity_name

    sql = f"""
    WITH monthly_hist AS (
        SELECT
            c.commodity_id,
            c.commodity_name,
            d.month,
            ROUND(AVG(p.modal_price), 2) AS month_avg_price,
            COUNT(*) AS observation_count
        FROM {t_p} p
        JOIN {t_c} c ON c.commodity_id = p.commodity_id
        JOIN {t_d} d ON d.date_id = p.date_id
        {where_sql}
        GROUP BY c.commodity_id, c.commodity_name, d.month
    ),
    with_annual AS (
        SELECT
            commodity_id,
            commodity_name,
            month,
            month_avg_price,
            ROUND(AVG(month_avg_price) OVER (PARTITION BY commodity_id), 2) AS annual_avg_price,
            observation_count
        FROM monthly_hist
    )
    SELECT
        commodity_id,
        commodity_name,
        month,
        month_avg_price,
        annual_avg_price,
        ROUND((month_avg_price / NULLIF(annual_avg_price, 0)) * 100.0, 2) AS seasonal_index,
        RANK() OVER (PARTITION BY commodity_id ORDER BY month_avg_price DESC) AS peak_month_rank,
        observation_count
    FROM with_annual
    ORDER BY commodity_name, month
    """
    return _run_df(db, sql, params)


def get_q6_best_markets(
    db: DatabaseManager | None = None,
    state: str | None = None,
    commodity_name: str | None = None,
    min_reports: int = 2,
) -> pd.DataFrame:
    """Q6: Best-paying markets in a state over the last 7 days with minimum reporting threshold."""
    db = db or get_db_manager()
    if db.backend == "postgresql" and min_reports == 2:
        clauses = []
        params: dict[str, Any] = {}
        if state:
            clauses.append("state = :state")
            params["state"] = state
        if commodity_name:
            clauses.append("commodity_name = :commodity")
            params["commodity"] = commodity_name
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return _run_df(
            db,
            f"SELECT * FROM analytics.v_q6_best_markets {where_sql} ORDER BY state, commodity_name, market_rank",
            params,
        )

    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_m = db.table_ref("warehouse", "dim_market")
    t_c = db.table_ref("warehouse", "dim_commodity")
    t_d = db.table_ref("warehouse", "dim_date")

    clauses = []
    params = {"min_reports": min_reports}
    if state:
        clauses.append("m.state = :state")
        params["state"] = state
    if commodity_name:
        clauses.append("c.commodity_name = :commodity")
        params["commodity"] = commodity_name
    extra_where = f"AND {' AND '.join(clauses)}" if clauses else ""
    date_cond = "d.full_date >= (a.as_of_date - INTERVAL '6 days')" if db.backend == "postgresql" else "d.full_date >= date(a.as_of_date, '-6 days')"

    sql = f"""
    WITH anchor AS (
        SELECT MAX(d.full_date) AS as_of_date
        FROM {t_p} p
        JOIN {t_d} d ON d.date_id = p.date_id
    ),
    recent AS (
        SELECT
            m.state,
            m.district,
            m.market_id,
            m.market_name,
            c.commodity_id,
            c.commodity_name,
            p.modal_price,
            p.min_price,
            p.max_price,
            d.full_date,
            a.as_of_date
        FROM {t_p} p
        JOIN {t_m} m ON m.market_id = p.market_id
        JOIN {t_c} c ON c.commodity_id = p.commodity_id
        JOIN {t_d} d ON d.date_id = p.date_id
        CROSS JOIN anchor a
        WHERE {date_cond}
        {extra_where}
    )
    SELECT
        state,
        district,
        market_id,
        market_name,
        commodity_id,
        commodity_name,
        COUNT(*) AS report_count,
        ROUND(AVG(modal_price), 2) AS avg_modal_price,
        ROUND(MIN(min_price), 2) AS lowest_min_price,
        ROUND(MAX(max_price), 2) AS highest_max_price,
        MAX(full_date) AS latest_report_date,
        MAX(as_of_date) AS window_end,
        RANK() OVER (PARTITION BY state, commodity_id ORDER BY AVG(modal_price) DESC) AS market_rank
    FROM recent
    GROUP BY state, district, market_id, market_name, commodity_id, commodity_name
    HAVING COUNT(*) >= :min_reports
    ORDER BY state, commodity_name, market_rank
    """
    return _run_df(db, sql, params)


def get_q7_production_vs_price(
    db: DatabaseManager | None = None,
    commodity_name: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Q7: State-year crop production vs yearly average mandi modal price + correlation summary."""
    db = db or get_db_manager()
    params: dict[str, Any] = {}
    if db.backend == "postgresql":
        where_sql = "WHERE commodity_name = :commodity" if commodity_name else ""
        if commodity_name:
            params["commodity"] = commodity_name
        # Independent view reads run concurrently (one network round trip instead of two). This makes Q7 a
        # bundle-level call: invoke it directly or via run_bundles, never inside run_queries.
        frames = run_queries(
            db,
            {
                "detail": lambda: _run_df(
                    db,
                    f"SELECT * FROM analytics.v_q7_production_vs_price {where_sql} ORDER BY commodity_name, crop_year DESC, total_production_t DESC",
                    params,
                ),
                "summary": lambda: _run_df(
                    db,
                    f"SELECT * FROM analytics.v_q7_production_price_corr {where_sql} ORDER BY corr_production_vs_price ASC NULLS LAST",
                    params,
                ),
            },
        )
        return frames["detail"], frames["summary"]

    t_fp = db.table_ref("warehouse", "fact_crop_production")
    t_cr = db.table_ref("warehouse", "dim_crop")
    t_mcc = db.table_ref("warehouse", "map_commodity_crop")
    t_dc = db.table_ref("warehouse", "dim_commodity")
    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_d = db.table_ref("warehouse", "dim_date")
    t_m = db.table_ref("warehouse", "dim_market")

    where_sql = "WHERE dc.commodity_name = :commodity" if commodity_name else ""
    if commodity_name:
        params["commodity"] = commodity_name

    detail_sql = f"""
    WITH state_prod AS (
        SELECT
            mcc.commodity_id,
            dc.commodity_name,
            cr.crop_name,
            fp.crop_year,
            fp.state,
            SUM(fp.area_ha) AS total_area_ha,
            SUM(fp.production_t) AS total_production_t,
            CASE WHEN SUM(fp.area_ha) > 0 THEN ROUND(SUM(fp.production_t) / SUM(fp.area_ha), 4) ELSE NULL END AS agg_yield_t_ha
        FROM {t_fp} fp
        JOIN {t_cr} cr ON cr.crop_id = fp.crop_id
        JOIN {t_mcc} mcc ON mcc.crop_id = cr.crop_id
        JOIN {t_dc} dc ON dc.commodity_id = mcc.commodity_id
        {where_sql}
        GROUP BY mcc.commodity_id, dc.commodity_name, cr.crop_name, fp.crop_year, fp.state
    ),
    state_price AS (
        SELECT
            p.commodity_id,
            d.year AS price_year,
            m.state,
            ROUND(AVG(p.modal_price), 2) AS avg_yearly_modal_price,
            COUNT(*) AS price_observations
        FROM {t_p} p
        JOIN {t_d} d ON d.date_id = p.date_id
        JOIN {t_m} m ON m.market_id = p.market_id
        GROUP BY p.commodity_id, d.year, m.state
    )
    SELECT
        sp.commodity_id,
        sp.commodity_name,
        sp.crop_name,
        sp.crop_year,
        sp.state,
        sp.total_area_ha,
        sp.total_production_t,
        sp.agg_yield_t_ha,
        pr.avg_yearly_modal_price,
        pr.price_observations
    FROM state_prod sp
    JOIN state_price pr
        ON sp.commodity_id = pr.commodity_id
       AND sp.crop_year = pr.price_year
       AND sp.state = pr.state
    ORDER BY sp.commodity_name, sp.crop_year DESC, sp.total_production_t DESC
    """
    detail = _run_df(db, detail_sql, params)

    summary_sql = f"""
    WITH joined AS ({detail_sql})
    SELECT
        commodity_id,
        commodity_name,
        crop_name,
        COUNT(*) AS state_year_pairs,
        ROUND(SUM(total_production_t), 2) AS cumulative_production_t,
        ROUND(AVG(avg_yearly_modal_price), 2) AS avg_modal_price,
        ROUND(CORR(total_production_t, avg_yearly_modal_price), 4) AS corr_production_vs_price
    FROM joined
    GROUP BY commodity_id, commodity_name, crop_name
    HAVING COUNT(*) >= 3
    ORDER BY corr_production_vs_price ASC NULLS LAST
    """
    summary = _run_df(db, summary_sql, params)
    return detail, summary
