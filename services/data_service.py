"""Read-only data access facade and Demo Mode bootstrapper."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text

from config import DEMO_DB_PATH, get_settings
from database.connection import DatabaseManager, get_db_manager
from database.seed import seed_reference_dimensions
from services.concurrency import run_queries

logger = logging.getLogger(__name__)


def ensure_demo_warehouse_ready(db: DatabaseManager | None = None) -> bool:
    """When running in Demo Mode without DATABASE_URL, auto-populate demo DB once from fixtures."""
    db = db or get_db_manager()
    if not db.is_demo:
        return False

    db.initialize_schema()
    t_p = db.table_ref("warehouse", "fact_price_daily")
    with db.engine.connect() as conn:
        count = conn.execute(text(f"SELECT COUNT(*) FROM {t_p}")).scalar() or 0
    if count > 0:
        return True

    logger.info("Demo warehouse is empty; generating and loading clearly labelled demo fixtures...")
    from etl.warehouse_loader import WarehouseLoader
    from ingestion.apy_importer import ApyImporter
    from ingestion.ceda_importer import CedaImporter
    from ingestion.scheduler import finish_pipeline_run, load_sample_fixtures_paths, start_pipeline_run
    from scripts.generate_demo_data import ensure_demo_fixtures_exist

    ensure_demo_fixtures_exist()
    seed_reference_dimensions(db)
    paths = load_sample_fixtures_paths()
    loader = WarehouseLoader(db)

    ceda = CedaImporter()
    price_res = ceda.import_csv_files(
        [paths["prices"]],
        record_kind="price",
        preserve_raw=False,
        source_label="demo_agmarknet_sample",
    )
    run_p = start_pipeline_run(db, "demo_load_prices", "demo_agmarknet_sample")
    res_p = loader.load_prices(price_res.records, source_name="demo_agmarknet_sample", raw_file_path=str(paths["prices"]), run_id=run_p)
    finish_pipeline_run(
        db,
        run_id=run_p,
        status="success",
        rows_fetched=len(price_res.records),
        rows_loaded=res_p["loaded_count"],
        rows_rejected=res_p["rejected_count"],
        metadata={"demo_mode": True, "outliers_flagged": res_p.get("outliers_flagged", 0)},
    )

    arr_res = ceda.import_csv_files(
        [paths["arrivals"]],
        record_kind="arrival",
        preserve_raw=False,
        source_label="demo_ceda_arrivals_sample",
    )
    run_a = start_pipeline_run(db, "demo_load_arrivals", "demo_ceda_arrivals_sample")
    res_a = loader.load_arrivals(arr_res.records, source_name="demo_ceda_arrivals_sample", raw_file_path=str(paths["arrivals"]), run_id=run_a)
    finish_pipeline_run(
        db,
        run_id=run_a,
        status="success",
        rows_fetched=len(arr_res.records),
        rows_loaded=res_a["loaded_count"],
        rows_rejected=res_a["rejected_count"],
        metadata={"demo_mode": True},
    )

    apy = ApyImporter()
    apy_res = apy.import_csv_files(
        [paths["apy"]],
        preserve_raw=False,
        source_label="demo_apy_sample",
    )
    run_y = start_pipeline_run(db, "demo_load_apy", "demo_apy_sample")
    res_y = loader.load_apy_production(apy_res.records, source_name="demo_apy_sample", raw_file_path=str(paths["apy"]), run_id=run_y)
    finish_pipeline_run(
        db,
        run_id=run_y,
        status="success",
        rows_fetched=len(apy_res.records),
        rows_loaded=res_y["loaded_count"],
        rows_rejected=res_y["rejected_count"],
        metadata={"demo_mode": True},
    )
    return True


def get_active_source_names(db: DatabaseManager | None = None) -> list[str]:
    """Return distinct provenance source names currently loaded across price, arrival, and APY fact tables."""
    db = db or get_db_manager()
    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_a = db.table_ref("warehouse", "fact_arrivals_daily")
    t_ap = db.table_ref("warehouse", "fact_crop_production")
    # One statement (one network round trip) instead of one per table.
    sql = (
        f"SELECT source_name FROM {t_p} UNION "
        f"SELECT source_name FROM {t_a} UNION "
        f"SELECT source_name FROM {t_ap}"
    )
    with db.readonly_connection() as conn:
        rows = conn.execute(text(sql)).scalars().all()
    return sorted({str(r) for r in rows if r})


def list_commodities(db: DatabaseManager | None = None) -> pd.DataFrame:
    db = db or get_db_manager()
    t_c = db.table_ref("warehouse", "dim_commodity")
    t_p = db.table_ref("warehouse", "fact_price_daily")
    sql = f"""
    SELECT DISTINCT c.commodity_id, c.commodity_name, c.category, c.storable
    FROM {t_c} c
    JOIN {t_p} p ON p.commodity_id = c.commodity_id
    ORDER BY c.commodity_name
    """
    with db.readonly_connection() as conn:
        res = conn.execute(text(sql))
        df = pd.DataFrame(res.fetchall(), columns=list(res.keys()))
    if not df.empty:
        df["storable"] = df["storable"].astype(bool)
    return df


def list_markets(
    db: DatabaseManager | None = None,
    commodity_name: str | None = None,
    state: str | None = None,
) -> pd.DataFrame:
    db = db or get_db_manager()
    t_m = db.table_ref("warehouse", "dim_market")
    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_c = db.table_ref("warehouse", "dim_commodity")
    clauses = []
    params: dict[str, Any] = {}
    if commodity_name:
        clauses.append("c.commodity_name = :comm")
        params["comm"] = commodity_name
    if state:
        clauses.append("m.state = :state")
        params["state"] = state
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
    SELECT DISTINCT m.market_id, m.market_name, m.district, m.state
    FROM {t_m} m
    JOIN {t_p} p ON p.market_id = m.market_id
    JOIN {t_c} c ON c.commodity_id = p.commodity_id
    {where_sql}
    ORDER BY m.state, m.district, m.market_name
    """
    with db.readonly_connection() as conn:
        res = conn.execute(text(sql), params)
        return pd.DataFrame(res.fetchall(), columns=list(res.keys()))


def _price_filter_sql(
    db: DatabaseManager,
    state: str | None = None,
    district: str | None = None,
    market: str | None = None,
    commodity: str | None = None,
    variety: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    outliers_only: bool = False,
) -> tuple[str, dict[str, Any]]:
    """WHERE clause over fact_price_daily p / dim_date d / dim_market m / dim_commodity c."""
    clauses = []
    params: dict[str, Any] = {}
    if state:
        clauses.append("m.state = :state")
        params["state"] = state
    if district:
        clauses.append("m.district = :district")
        params["district"] = district
    if market:
        clauses.append("m.market_name = :market")
        params["market"] = market
    if commodity:
        clauses.append("c.commodity_name = :commodity")
        params["commodity"] = commodity
    if variety and variety != "All":
        clauses.append("p.variety = :variety")
        params["variety"] = variety
    if start_date:
        clauses.append("d.full_date >= :start_date")
        params["start_date"] = start_date.isoformat() if db.backend == "sqlite" else start_date
    if end_date:
        clauses.append("d.full_date <= :end_date")
        params["end_date"] = end_date.isoformat() if db.backend == "sqlite" else end_date
    if outliers_only:
        clauses.append("p.outlier_flag = :outlier_true")
        params["outlier_true"] = True

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


def query_price_explorer(
    db: DatabaseManager | None = None,
    state: str | None = None,
    district: str | None = None,
    market: str | None = None,
    commodity: str | None = None,
    variety: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 5000,
    offset: int = 0,
    outliers_only: bool = False,
) -> pd.DataFrame:
    db = db or get_db_manager()
    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_d = db.table_ref("warehouse", "dim_date")
    t_m = db.table_ref("warehouse", "dim_market")
    t_c = db.table_ref("warehouse", "dim_commodity")

    where_sql, params = _price_filter_sql(
        db, state, district, market, commodity, variety, start_date, end_date, outliers_only
    )
    params["lim"] = limit
    params["off"] = offset
    sql = f"""
    SELECT
        d.full_date AS arrival_date,
        m.state,
        m.district,
        m.market_name AS market,
        c.commodity_name AS commodity,
        p.variety,
        p.grade,
        p.min_price,
        p.max_price,
        p.modal_price,
        p.outlier_flag,
        p.source_name
    FROM {t_p} p
    JOIN {t_d} d ON d.date_id = p.date_id
    JOIN {t_m} m ON m.market_id = p.market_id
    JOIN {t_c} c ON c.commodity_id = p.commodity_id
    {where_sql}
    ORDER BY d.full_date DESC, m.state, m.market_name, p.variety, p.grade, p.market_id
    LIMIT :lim OFFSET :off
    """
    with db.readonly_connection() as conn:
        res = conn.execute(text(sql), params)
        df = pd.DataFrame(res.fetchall(), columns=list(res.keys()))
    if not df.empty:
        df["arrival_date"] = pd.to_datetime(df["arrival_date"]).dt.date
        df["outlier_flag"] = df["outlier_flag"].astype(bool)
    return df


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    return pd.to_datetime(value).date()


def query_price_explorer_aggregates(
    db: DatabaseManager | None = None,
    state: str | None = None,
    district: str | None = None,
    market: str | None = None,
    commodity: str | None = None,
    variety: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    """SQL-side summary, daily series, and market comparison over ALL rows matching the filters (no row cap).

    Its four queries run concurrently on PostgreSQL, so call it at bundle level (directly, or via
    ``run_bundles``), never as a task inside ``run_queries``.
    """
    db = db or get_db_manager()
    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_d = db.table_ref("warehouse", "dim_date")
    t_m = db.table_ref("warehouse", "dim_market")
    t_c = db.table_ref("warehouse", "dim_commodity")
    where_sql, params = _price_filter_sql(db, state, district, market, commodity, variety, start_date, end_date)
    from_sql = f"""
    FROM {t_p} p
    JOIN {t_d} d ON d.date_id = p.date_id
    JOIN {t_m} m ON m.market_id = p.market_id
    JOIN {t_c} c ON c.commodity_id = p.commodity_id
    {where_sql}
    """
    outlier_expr = "CASE WHEN p.outlier_flag THEN 1 ELSE 0 END"

    def fetch_summary() -> Any:
        with db.readonly_connection() as conn:
            return conn.execute(
                text(
                    f"""
                    SELECT
                        COUNT(*) AS total_observations,
                        MIN(p.min_price) AS min_price,
                        MAX(p.max_price) AS max_price,
                        AVG(p.modal_price) AS avg_modal_price,
                        MIN(d.full_date) AS min_date,
                        MAX(d.full_date) AS max_date,
                        SUM({outlier_expr}) AS outliers_flagged
                    {from_sql}
                    """
                ),
                params,
            ).mappings().one()

    def fetch_sources() -> list[Any]:
        with db.readonly_connection() as conn:
            return list(conn.execute(text(f"SELECT DISTINCT p.source_name {from_sql}"), params).scalars().all())

    def fetch_frame(sql: str) -> pd.DataFrame:
        with db.readonly_connection() as conn:
            res = conn.execute(text(sql), params)
            return pd.DataFrame(res.fetchall(), columns=list(res.keys()))

    fetched = run_queries(
        db,
        {
            "summary": fetch_summary,
            "sources": fetch_sources,
            "daily": lambda: fetch_frame(
                f"""
                SELECT
                    d.full_date AS arrival_date,
                    MIN(p.min_price) AS min_price,
                    AVG(p.modal_price) AS modal_price,
                    MAX(p.max_price) AS max_price,
                    SUM({outlier_expr}) AS outlier_count
                {from_sql}
                GROUP BY d.full_date
                ORDER BY d.full_date
                """
            ),
            "market_comparison": lambda: fetch_frame(
                f"""
                SELECT
                    m.state,
                    m.district,
                    m.market_name AS market,
                    AVG(p.modal_price) AS avg_modal,
                    MIN(p.modal_price) AS min_modal,
                    MAX(p.modal_price) AS max_modal,
                    COUNT(*) AS reports
                {from_sql}
                GROUP BY m.state, m.district, m.market_name
                ORDER BY AVG(p.modal_price) DESC
                """
            ),
        },
    )
    summary_row = fetched["summary"]
    sources = fetched["sources"]
    daily: pd.DataFrame = fetched["daily"]
    market_comparison: pd.DataFrame = fetched["market_comparison"]

    if not daily.empty:
        daily["arrival_date"] = pd.to_datetime(daily["arrival_date"]).dt.date
        for col in ("min_price", "modal_price", "max_price"):
            daily[col] = daily[col].astype(float).round(2)
        daily["outlier_count"] = daily["outlier_count"].fillna(0).astype(int)
    if not market_comparison.empty:
        for col in ("avg_modal", "min_modal", "max_modal"):
            market_comparison[col] = market_comparison[col].astype(float).round(2)
        market_comparison["reports"] = market_comparison["reports"].astype(int)

    def _num(val: Any) -> float | None:
        return round(float(val), 2) if val is not None else None

    return {
        "total_observations": int(summary_row["total_observations"] or 0),
        "min_price": _num(summary_row["min_price"]),
        "max_price": _num(summary_row["max_price"]),
        "avg_modal_price": _num(summary_row["avg_modal_price"]),
        "min_date": _to_date(summary_row["min_date"]),
        "max_date": _to_date(summary_row["max_date"]),
        "outliers_flagged": int(summary_row["outliers_flagged"] or 0),
        "sources": sorted({str(s) for s in sources if s}),
        "daily": daily,
        "market_comparison": market_comparison,
    }


def query_state_price_averages(
    db: DatabaseManager | None = None,
    commodity: str | None = None,
) -> pd.DataFrame:
    """Per-state average min/modal/max price and observation count over ALL matching rows (no row cap).

    Columns: state, avg_modal, min_price, max_price, obs - highest average modal price first.
    Rounding happens in pandas (PostgreSQL ROUND(double precision, int) is not available).
    """
    db = db or get_db_manager()
    where_sql, params = _price_filter_sql(db, commodity=commodity)
    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_m = db.table_ref("warehouse", "dim_market")
    t_c = db.table_ref("warehouse", "dim_commodity")
    sql = f"""
    SELECT
        m.state AS state,
        AVG(p.modal_price) AS avg_modal,
        AVG(p.min_price) AS min_price,
        AVG(p.max_price) AS max_price,
        COUNT(*) AS obs
    FROM {t_p} p
    JOIN {t_m} m ON m.market_id = p.market_id
    JOIN {t_c} c ON c.commodity_id = p.commodity_id
    {where_sql}
    GROUP BY m.state
    ORDER BY AVG(p.modal_price) DESC, m.state
    """
    with db.readonly_connection() as conn:
        res = conn.execute(text(sql), params)
        df = pd.DataFrame(res.fetchall(), columns=list(res.keys()))
    if not df.empty:
        for col in ("avg_modal", "min_price", "max_price"):
            df[col] = df[col].astype(float).round(2)
        df["obs"] = df["obs"].astype(int)
    return df


def query_state_monthly_trends(
    db: DatabaseManager | None = None,
    commodity: str | None = None,
    states: list[str] | None = None,
) -> pd.DataFrame:
    """Monthly average modal price per state computed directly in SQL across ALL matching rows."""
    db = db or get_db_manager()
    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_d = db.table_ref("warehouse", "dim_date")
    t_m = db.table_ref("warehouse", "dim_market")
    t_c = db.table_ref("warehouse", "dim_commodity")

    clauses: list[str] = []
    params: dict[str, Any] = {}
    if commodity:
        clauses.append("c.commodity_name = :commodity")
        params["commodity"] = commodity

    clean_states = sorted({s for s in (states or []) if s})
    if clean_states:
        placeholders = []
        for idx, state_name in enumerate(clean_states):
            key = f"st_{idx}"
            placeholders.append(f":{key}")
            params[key] = state_name
        clauses.append(f"m.state IN ({', '.join(placeholders)})")

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
    SELECT
        d.year AS year,
        d.month AS month_num,
        m.state AS state,
        AVG(p.modal_price) AS avg_modal_price
    FROM {t_p} p
    JOIN {t_d} d ON d.date_id = p.date_id
    JOIN {t_m} m ON m.market_id = p.market_id
    JOIN {t_c} c ON c.commodity_id = p.commodity_id
    {where_sql}
    GROUP BY d.year, d.month, m.state
    ORDER BY d.year, d.month, m.state
    """
    with db.readonly_connection() as conn:
        res = conn.execute(text(sql), params)
        df = pd.DataFrame(res.fetchall(), columns=list(res.keys()))

    if df.empty:
        return pd.DataFrame(columns=["month", "state", "avg_modal_price"])

    df["month"] = [f"{int(y):04d}-{int(m):02d}" for y, m in zip(df["year"], df["month_num"])]
    df["avg_modal_price"] = df["avg_modal_price"].astype(float).round(2)
    return df[["month", "state", "avg_modal_price"]]


def _arrivals_filter_sql(
    db: DatabaseManager,
    commodity: str | None = None,
    market: str | None = None,
    state: str | None = None,
    district: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[str, dict[str, Any]]:
    clauses = []
    params: dict[str, Any] = {}
    if commodity:
        clauses.append("c.commodity_name = :comm")
        params["comm"] = commodity
    if market:
        clauses.append("m.market_name = :mkt")
        params["mkt"] = market
    if state:
        clauses.append("m.state = :state")
        params["state"] = state
    if district:
        clauses.append("m.district = :district")
        params["district"] = district
    if start_date:
        clauses.append("d.full_date >= :start_date")
        params["start_date"] = start_date.isoformat() if db.backend == "sqlite" else start_date
    if end_date:
        clauses.append("d.full_date <= :end_date")
        params["end_date"] = end_date.isoformat() if db.backend == "sqlite" else end_date
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


def query_market_arrivals_daily(
    db: DatabaseManager | None = None,
    commodity: str | None = None,
    market: str | None = None,
    state: str | None = None,
    district: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    """Daily summed arrival tonnage over ALL rows matching the filters (no row cap)."""
    db = db or get_db_manager()
    t_a = db.table_ref("warehouse", "fact_arrivals_daily")
    t_d = db.table_ref("warehouse", "dim_date")
    t_m = db.table_ref("warehouse", "dim_market")
    t_c = db.table_ref("warehouse", "dim_commodity")
    where_sql, params = _arrivals_filter_sql(db, commodity, market, state, district, start_date, end_date)
    sql = f"""
    SELECT d.full_date AS arrival_date, SUM(a.quantity_tonnes) AS arrivals_tonnes
    FROM {t_a} a
    JOIN {t_d} d ON d.date_id = a.date_id
    JOIN {t_m} m ON m.market_id = a.market_id
    JOIN {t_c} c ON c.commodity_id = a.commodity_id
    {where_sql}
    GROUP BY d.full_date
    ORDER BY d.full_date
    """
    with db.readonly_connection() as conn:
        res = conn.execute(text(sql), params)
        df = pd.DataFrame(res.fetchall(), columns=list(res.keys()))
    if not df.empty:
        df["arrival_date"] = pd.to_datetime(df["arrival_date"]).dt.date
        df["arrivals_tonnes"] = df["arrivals_tonnes"].astype(float).round(2)
    return df


def query_market_arrivals(
    db: DatabaseManager | None = None,
    commodity: str | None = None,
    market: str | None = None,
    state: str | None = None,
    limit: int = 1000,
    district: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    db = db or get_db_manager()
    t_a = db.table_ref("warehouse", "fact_arrivals_daily")
    t_d = db.table_ref("warehouse", "dim_date")
    t_m = db.table_ref("warehouse", "dim_market")
    t_c = db.table_ref("warehouse", "dim_commodity")

    where_sql, params = _arrivals_filter_sql(db, commodity, market, state, district, start_date, end_date)
    params["lim"] = limit
    sql = f"""
    SELECT d.full_date AS arrival_date, m.state, m.market_name AS market,
           c.commodity_name AS commodity, a.quantity_tonnes, a.source_name
    FROM {t_a} a
    JOIN {t_d} d ON d.date_id = a.date_id
    JOIN {t_m} m ON m.market_id = a.market_id
    JOIN {t_c} c ON c.commodity_id = a.commodity_id
    {where_sql}
    ORDER BY d.full_date DESC
    LIMIT :lim
    """
    with db.readonly_connection() as conn:
        res = conn.execute(text(sql), params)
        df = pd.DataFrame(res.fetchall(), columns=list(res.keys()))
    if not df.empty:
        df["arrival_date"] = pd.to_datetime(df["arrival_date"]).dt.date
    return df


def query_crop_production(
    db: DatabaseManager | None = None,
    commodity_name: str | None = None,
    state: str | None = None,
) -> pd.DataFrame:
    db = db or get_db_manager()
    t_fp = db.table_ref("warehouse", "fact_crop_production")
    t_cr = db.table_ref("warehouse", "dim_crop")
    t_mcc = db.table_ref("warehouse", "map_commodity_crop")
    t_dc = db.table_ref("warehouse", "dim_commodity")

    clauses = []
    params: dict[str, Any] = {}
    if commodity_name:
        clauses.append("dc.commodity_name = :comm")
        params["comm"] = commodity_name
    if state:
        clauses.append("fp.state = :state")
        params["state"] = state
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    sql = f"""
    SELECT fp.crop_year, fp.season, fp.state, fp.district,
           cr.crop_name, cr.crop_type, dc.commodity_name,
           fp.area_ha, fp.production_t, fp.yield_t_ha, fp.source_name
    FROM {t_fp} fp
    JOIN {t_cr} cr ON cr.crop_id = fp.crop_id
    LEFT JOIN {t_mcc} mcc ON mcc.crop_id = cr.crop_id
    LEFT JOIN {t_dc} dc ON dc.commodity_id = mcc.commodity_id
    {where_sql}
    ORDER BY fp.crop_year DESC, fp.production_t DESC
    """
    with db.readonly_connection() as conn:
        res = conn.execute(text(sql), params)
        return pd.DataFrame(res.fetchall(), columns=list(res.keys()))


def query_pipeline_runs(db: DatabaseManager | None = None, limit: int = 50) -> pd.DataFrame:
    db = db or get_db_manager()
    t_log = db.table_ref("operational", "pipeline_run_log")
    sql = f"""
    SELECT run_id, stage, source_name, started_at, finished_at,
           rows_fetched, rows_loaded, rows_rejected, status, error_message, metadata_json
    FROM {t_log}
    ORDER BY run_id DESC
    LIMIT :lim
    """
    with db.readonly_connection() as conn:
        res = conn.execute(text(sql), {"lim": limit})
        return pd.DataFrame(res.fetchall(), columns=list(res.keys()))


def query_dq_rejects(db: DatabaseManager | None = None, limit: int = 100) -> pd.DataFrame:
    db = db or get_db_manager()
    t_rej = db.table_ref("operational", "dq_rejects")
    sql = f"""
    SELECT reject_id, run_id, source_name, stage, reject_reason, raw_record, rejected_at
    FROM {t_rej}
    ORDER BY reject_id DESC
    LIMIT :lim
    """
    with db.readonly_connection() as conn:
        res = conn.execute(text(sql), {"lim": limit})
        return pd.DataFrame(res.fetchall(), columns=list(res.keys()))


def query_recent_briefings(
    db: DatabaseManager | None = None,
    commodity: str | None = None,
    limit: int = 20,
) -> pd.DataFrame:
    db = db or get_db_manager()
    t_b = db.table_ref("analytics", "briefings")
    t_c = db.table_ref("warehouse", "dim_commodity")
    t_m = db.table_ref("warehouse", "dim_market")
    where_sql = "WHERE c.commodity_name = :comm" if commodity else ""
    params: dict[str, Any] = {"lim": limit}
    if commodity:
        params["comm"] = commodity
    sql = f"""
    SELECT b.briefing_id, c.commodity_name AS commodity, m.market_name AS market, m.state,
           b.generated_at, b.verdict, b.validation_status, b.model_used, b.used_fallback
    FROM {t_b} b
    JOIN {t_c} c ON c.commodity_id = b.commodity_id
    JOIN {t_m} m ON m.market_id = b.market_id
    {where_sql}
    ORDER BY b.briefing_id DESC
    LIMIT :lim
    """
    with db.readonly_connection() as conn:
        res = conn.execute(text(sql), params)
        return pd.DataFrame(res.fetchall(), columns=list(res.keys()))
