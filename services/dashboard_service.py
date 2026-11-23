"""High-level orchestration service for MandiMitra FastAPI endpoints and workflows."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text

from analytics.metrics import get_mapping_coverage, get_source_coverage
from analytics.queries import (
    get_q1_price_volatility,
    get_q2_state_price_gaps,
    get_q3_mom_trends,
    get_q4_arrivals_vs_price,
    get_q5_seasonal_price_index,
    get_q6_best_markets,
    get_q7_production_vs_price,
)
from analytics.signals import SignalResult, compute_signal_for_market
from backtesting.engine import RollingOriginBacktester
from config import get_settings
from database.connection import DatabaseManager, get_db_manager
from services.concurrency import run_bundles, run_queries, use_concurrency
from genai.briefing import BriefingRecord, generate_grounded_briefing
from services.data_service import (
    ensure_demo_warehouse_ready,
    get_active_source_names,
    list_commodities,
    list_markets,
    query_crop_production,
    query_dq_rejects,
    query_market_arrivals,
    query_pipeline_runs,
    query_price_explorer,
    query_recent_briefings,
    query_state_price_averages,
)


def init_app_context() -> tuple[DatabaseManager, dict[str, Any]]:
    """Startup check + auto demo hydration if no DATABASE_URL is configured."""
    settings = get_settings()
    startup_info = settings.validate_startup()
    db = get_db_manager()
    if db.is_demo:
        ensure_demo_warehouse_ready(db)
    return db, startup_info


# --- Overview performance -------------------------------------------------------------------------------
# The overview's independent queries run concurrently (see services.concurrency) and the assembled bundles are
# cached briefly. Demo SQLite stays uncached because tests mutate it between calls.

OVERVIEW_CACHE_TTL_SECONDS = 300

_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
_cache_lock = threading.Lock()

logger = logging.getLogger(__name__)


def _cached(db: DatabaseManager, key: tuple[Any, ...], loader: Callable[[], Any]) -> Any:
    """Short-lived cache for read-only bundles. Callers must treat returned DataFrames as read-only."""
    if not use_concurrency(db):
        return loader()
    full_key = (db.database_url, *key)
    with _cache_lock:
        hit = _cache.get(full_key)
    if hit is not None and time.monotonic() - hit[0] < OVERVIEW_CACHE_TTL_SECONDS:
        return hit[1]
    value = loader()
    with _cache_lock:
        _cache[full_key] = (time.monotonic(), value)
    return value


def clear_overview_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _overall_avg_modal(db: DatabaseManager) -> Any:
    t_p = db.table_ref("warehouse", "fact_price_daily")
    with db.readonly_connection() as conn:
        return conn.execute(text(f"SELECT ROUND(AVG(modal_price), 2) FROM {t_p}")).scalar()


def _latest_window_avg_modal(db: DatabaseManager) -> Any:
    """Average modal price over the 7 days ending at the latest price date."""
    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_d = db.table_ref("warehouse", "dim_date")
    window = "(a.max_dt - INTERVAL '6 days')" if db.backend == "postgresql" else "date(a.max_dt, '-6 days')"
    sql = f"""
    WITH anchor AS (
        SELECT MAX(d.full_date) AS max_dt
        FROM {t_p} p JOIN {t_d} d ON d.date_id = p.date_id
    )
    SELECT ROUND(AVG(p.modal_price), 2)
    FROM {t_p} p
    JOIN {t_d} d ON d.date_id = p.date_id
    CROSS JOIN anchor a
    WHERE d.full_date >= {window}
    """
    with db.readonly_connection() as conn:
        return conn.execute(text(sql)).scalar()


def get_overview_bundle(db: DatabaseManager) -> dict[str, Any]:
    """Commodity-independent part of the dashboard overview."""

    def load() -> dict[str, Any]:
        return run_queries(
            db,
            {
                "coverage": lambda: get_source_coverage(db),
                "volatility": lambda: get_q1_price_volatility(db),
                "mom_trends": lambda: get_q3_mom_trends(db),
                "pipeline_runs": lambda: query_pipeline_runs(db, limit=10),
                "recent_prices": lambda: query_price_explorer(db, limit=25),
                "commodities": lambda: list_commodities(db),
                "overall_avg_modal": lambda: _overall_avg_modal(db),
                "latest_window_avg_modal": lambda: _latest_window_avg_modal(db),
                "active_sources": lambda: get_active_source_names(db),
            },
        )

    return _cached(db, ("overview_base",), load)


def get_overview_focus_bundle(db: DatabaseManager, commodity: str) -> dict[str, Any]:
    """Commodity-specific part of the dashboard overview (state averages and Q2 inter-state gaps)."""

    def load() -> dict[str, Any]:
        return run_queries(
            db,
            {
                "state_averages": lambda: query_state_price_averages(db, commodity=commodity),
                "state_gaps": lambda: get_q2_state_price_gaps(db, commodity_name=commodity),
            },
        )

    return _cached(db, ("overview_focus", commodity), load)


def default_focus_commodity(commodity_names: list[str]) -> str | None:
    if not commodity_names:
        return None
    return "Tomato" if "Tomato" in commodity_names else sorted(commodity_names)[0]


def prime_overview_cache(db: DatabaseManager) -> None:
    """Warm the connection pool and the default overview so the first page visit is fast."""
    if not use_concurrency(db):
        return
    started = time.monotonic()
    try:
        base = get_overview_bundle(db)
        comm_df = base["commodities"]
        names = comm_df["commodity_name"].astype(str).tolist() if not comm_df.empty else []
        focus = default_focus_commodity(names)
        if focus:
            get_overview_focus_bundle(db, focus)
        logger.info("Overview cache primed in %.1fs", time.monotonic() - started)
    except Exception:  # noqa: BLE001 - warm-up is best effort; requests still compute on demand
        logger.warning("Overview cache warm-up failed; the first request will load it instead.", exc_info=True)


def get_advisor_recommendation(
    db: DatabaseManager,
    commodity: str,
    market: str,
    state: str,
    as_of_date: date | None = None,
    persist: bool | None = None,
) -> tuple[SignalResult | None, BriefingRecord | None]:
    """Compute the advisor signal and explanation.

    ``persist=None`` follows configuration: the disposable Demo SQLite warehouse may persist audit rows,
    but a non-demo database is read-only for the advisor unless ``ADVISOR_PERSIST=true``. In no-persist
    mode neither ``market_signals`` nor ``briefings`` is written.
    """
    if persist is None:
        persist = get_settings().advisor_persistence_allowed(db.is_demo)
    signal = compute_signal_for_market(
        commodity_name=commodity,
        market_name=market,
        state=state,
        db=db,
        as_of_date=as_of_date,
        persist=persist,
    )
    if signal is None:
        return None, None
    briefing = generate_grounded_briefing(signal=signal, db=db, persist=persist)
    return signal, briefing


def get_comparison_bundle(db: DatabaseManager, commodity: str, state: str | None = None) -> dict[str, Any]:
    gaps = get_q2_state_price_gaps(db, commodity_name=commodity)
    best_markets = get_q6_best_markets(db, state=state, commodity_name=commodity)
    prices = query_price_explorer(db, commodity=commodity, limit=6000)
    return {
        "state_gaps": gaps,
        "best_markets": best_markets,
        "prices": prices,
    }


def get_crop_analytics_bundle(db: DatabaseManager, commodity: str) -> dict[str, Any]:
    seasonal = get_q5_seasonal_price_index(db, commodity_name=commodity)
    arrivals_corr = get_q4_arrivals_vs_price(db)
    q7_detail, q7_corr = get_q7_production_vs_price(db, commodity_name=commodity)
    mapping_cov = get_mapping_coverage(db)
    prod_df = query_crop_production(db, commodity_name=commodity)
    return {
        "seasonal": seasonal,
        "arrivals_corr": arrivals_corr,
        "q7_detail": q7_detail,
        "q7_corr": q7_corr,
        "mapping_coverage": mapping_cov,
        "production": prod_df,
    }


def run_cached_backtest(db: DatabaseManager, eval_days: int = 365, step_days: int = 7) -> tuple[pd.DataFrame, dict[str, Any]]:
    bt = RollingOriginBacktester(db=db)
    return bt.run_backtest(eval_days=eval_days, step_days=step_days)
