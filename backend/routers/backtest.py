"""Historical rolling-origin backtesting & CSV export endpoints."""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from datetime import timedelta
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import text

import config
from backtesting.engine import RollingOriginBacktester
from backtesting.metrics import summarize_backtest_results
from backend.dependencies import get_db
from backend.schemas import (
    BacktestDataCoverage,
    BacktestEvaluationRow,
    BacktestResponse,
    CommodityBacktestItem,
    PaginationMeta,
    StorabilityBacktestItem,
    StrategyMetrics,
)
from backend.serializers import clean_scalar, df_to_records
from database.connection import DatabaseManager

router = APIRouter(prefix="/api/backtest", tags=["backtesting"])

_BacktestResult = tuple[pd.DataFrame, dict[str, Any], config.SignalThresholds, bool]

# Latest evaluation per (database, mode, eval_days, step_days): pagination and CSV export reuse it,
# and refresh=true replaces it, so exports always match the most recently displayed results.
# Bounded (least-recently-used entries are dropped) so callers cycling through eval_days/step_days values cannot grow
# memory without limit on a public deployment.
_BACKTEST_CACHE: OrderedDict[tuple[str, bool, int, int], _BacktestResult] = OrderedDict()
# The price-history snapshot each cached evaluation was computed from. When new prices are ingested the snapshot
# changes and the next request recomputes, instead of serving a result frozen at server start.
_BACKTEST_DATA_VERSION: dict[tuple[str, bool, int, int], tuple[Any, ...]] = {}
# When each cached evaluation was last recomputed because of refresh=true.
_BACKTEST_REFRESHED_AT: dict[tuple[str, bool, int, int], float] = {}
BACKTEST_CACHE_MAX_ENTRIES = 8
# A cached evaluation is recomputed by refresh=true at most once per this many seconds; repeats get the cached one.
BACKTEST_REFRESH_COOLDOWN_SECONDS = 60.0
# Only one backtest computes at a time (each one is CPU-heavy). Others wait up to this long, then get a 503.
BACKTEST_COMPUTE_WAIT_SECONDS = 30.0
_BACKTEST_COMPUTE_LOCK = threading.Lock()

# Each decision is scored against the average price over the following 14 days (see backtesting.engine).
OUTCOME_HORIZON_DAYS = 14


def clear_backtest_cache() -> None:
    _BACKTEST_CACHE.clear()
    _BACKTEST_DATA_VERSION.clear()
    _BACKTEST_REFRESHED_AT.clear()


def _price_history(db: DatabaseManager) -> dict[str, Any]:
    """Date coverage of the price warehouse (one cheap aggregate query)."""
    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_d = db.table_ref("warehouse", "dim_date")
    with db.readonly_connection() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT MIN(d.full_date) AS start_date, MAX(d.full_date) AS end_date,
                       COUNT(DISTINCT d.full_date) AS distinct_days, COUNT(*) AS total_rows
                FROM {t_p} p
                JOIN {t_d} d ON d.date_id = p.date_id
                """
            )
        ).mappings().one()
    start = pd.to_datetime(row["start_date"]).date() if row["start_date"] is not None else None
    end = pd.to_datetime(row["end_date"]).date() if row["end_date"] is not None else None
    return {
        "start": start,
        "end": end,
        "distinct_days": int(row["distinct_days"] or 0),
        "total_rows": int(row["total_rows"] or 0),
    }


def _load_or_run_backtest(
    db: DatabaseManager,
    eval_days: int,
    step_days: int,
    refresh: bool,
    history: dict[str, Any] | None = None,
) -> _BacktestResult:
    cache_key = (db.database_url, db.is_demo, eval_days, step_days)
    history = history if history is not None else _price_history(db)
    version = (history["end"], history["total_rows"])

    def cached() -> _BacktestResult | None:
        if cache_key not in _BACKTEST_CACHE or _BACKTEST_DATA_VERSION.get(cache_key) != version:
            return None
        if refresh:
            refreshed_at = _BACKTEST_REFRESHED_AT.get(cache_key)
            if refreshed_at is None or time.monotonic() - refreshed_at >= BACKTEST_REFRESH_COOLDOWN_SECONDS:
                return None
        _BACKTEST_CACHE.move_to_end(cache_key)
        return _BACKTEST_CACHE[cache_key]

    hit = cached()
    if hit is not None:
        return hit
    if not _BACKTEST_COMPUTE_LOCK.acquire(timeout=BACKTEST_COMPUTE_WAIT_SECONDS):
        raise HTTPException(
            status_code=503,
            detail="Another backtest is being computed. Please try again in a minute.",
            headers={"Retry-After": "60"},
        )
    try:
        # A request that waited for the lock may now find the evaluation it needs already computed.
        hit = cached()
        if hit is not None:
            return hit
        result = _compute_backtest(db, eval_days, step_days, refresh)
        _BACKTEST_CACHE[cache_key] = result
        _BACKTEST_CACHE.move_to_end(cache_key)
        _BACKTEST_DATA_VERSION[cache_key] = version
        if refresh:
            _BACKTEST_REFRESHED_AT[cache_key] = time.monotonic()
        while len(_BACKTEST_CACHE) > BACKTEST_CACHE_MAX_ENTRIES:
            evicted, _ = _BACKTEST_CACHE.popitem(last=False)
            _BACKTEST_DATA_VERSION.pop(evicted, None)
            _BACKTEST_REFRESHED_AT.pop(evicted, None)
        return result
    finally:
        _BACKTEST_COMPUTE_LOCK.release()


def _history_requirement(history: dict[str, Any], eval_days: int, step_days: int) -> dict[str, Any]:
    """How much price history the first evaluation needs, and when it should exist.

    Decision dates step back from (latest price date - 14 days); the latest one sits
    ``eval_days % step_days`` days earlier still. A decision also needs prices on/before it, so the first
    evaluation appears once the history spans ``14 + eval_days % step_days`` days, assuming daily capture.
    """
    required_span = OUTCOME_HORIZON_DAYS + (eval_days % step_days)
    start, end = history["start"], history["end"]
    span = (end - start).days if start and end else 0
    expected = (start + timedelta(days=required_span)) if start else None
    return {
        "history_start": start,
        "history_end": end,
        "history_days": history["distinct_days"],
        "history_span_days": span,
        "required_span_days": required_span,
        "results_expected_from": expected,
    }


def prime_backtest_cache(db: DatabaseManager) -> None:
    """Pre-compute the default view (365-day window, 7-day step) so the Backtesting page opens from cache."""
    _load_or_run_backtest(db, eval_days=365, step_days=7, refresh=False)


def _compute_backtest(
    db: DatabaseManager,
    eval_days: int,
    step_days: int,
    refresh: bool,
) -> _BacktestResult:
    cfg = config.SignalThresholds.load_tuned_or_default(is_demo=db.is_demo)
    demo_csv = config.DATA_SAMPLE_DIR / "backtest_results.csv"
    live_csv = config.DATA_DIR / "live_backtest_results.csv"

    used_csv = False
    if not refresh and db.is_demo and demo_csv.exists() and eval_days == 365 and step_days == 7:
        df_res = pd.read_csv(demo_csv)
        summary = summarize_backtest_results(df_res)
        used_csv = True
    elif not refresh and (not db.is_demo) and live_csv.exists() and eval_days == 365 and step_days == 7:
        df_res = pd.read_csv(live_csv)
        summary = summarize_backtest_results(df_res)
        used_csv = True
    else:
        bt = RollingOriginBacktester(db=db, thresholds=cfg)
        df_res, summary = bt.run_backtest(eval_days=eval_days, step_days=step_days, thresholds=cfg)

    return df_res, summary, cfg, used_csv


@router.get("/results", response_model=BacktestResponse)
def get_backtest_results(
    eval_days: int = Query(default=365, ge=30, le=1095),
    step_days: int = Query(default=7, ge=1, le=60),
    refresh: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    db: DatabaseManager = Depends(get_db),
) -> BacktestResponse:
    history = _price_history(db)
    df_res, summary, cfg, used_csv = _load_or_run_backtest(db, eval_days, step_days, refresh, history=history)
    source_label = "Simulated Demo Fixtures" if db.is_demo else "Live PostgreSQL Warehouse"

    by_comm_df: pd.DataFrame = summary["by_commodity"]
    by_stor_df: pd.DataFrame = summary["by_storability"]

    total_items = len(df_res)
    total_pages = max(1, math.ceil(total_items / page_size)) if total_items > 0 else 0
    start_idx = (page - 1) * page_size
    sample_df = df_res.iloc[start_idx : start_idx + page_size] if total_items > 0 else pd.DataFrame()

    return BacktestResponse(
        is_demo=db.is_demo,
        source_label=source_label,
        used_precomputed_csv=used_csv,
        eval_days=eval_days,
        step_days=step_days,
        thresholds=clean_scalar(cfg.model_dump()),
        mandimitra=StrategyMetrics(**clean_scalar(summary["mandimitra"])),
        baseline_always_sell=StrategyMetrics(**clean_scalar(summary["baseline_always_sell"])),
        baseline_momentum_only=StrategyMetrics(**clean_scalar(summary["baseline_momentum_only"])),
        matched_always_sell_accuracy_pct=float(summary["matched_always_sell_accuracy_pct"]),
        by_commodity=[CommodityBacktestItem(**r) for r in df_to_records(by_comm_df)],
        by_storability=[StorabilityBacktestItem(**r) for r in df_to_records(by_stor_df)],
        sample_rows=[BacktestEvaluationRow(**r) for r in df_to_records(sample_df)],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
        ),
        data_coverage=BacktestDataCoverage(**_history_requirement(history, eval_days, step_days)),
    )


@router.get("/export")
def export_backtest_csv(
    eval_days: int = Query(default=365, ge=30, le=1095),
    step_days: int = Query(default=7, ge=1, le=60),
    refresh: bool = Query(default=False),
    db: DatabaseManager = Depends(get_db),
) -> Response:
    df_res, _, _, used_csv = _load_or_run_backtest(db, eval_days, step_days, refresh)
    if df_res.empty:
        raise HTTPException(status_code=404, detail="No backtest evaluations available to export.")
    csv_bytes = df_res.to_csv(index=False).encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="mandimitra_backtest_{eval_days}d_step{step_days}d.csv"'
            ),
            "X-Backtest-Source": "precomputed_csv" if used_csv else "computed",
            # Lets the UI verify the export has the same number of evaluations as the results on screen.
            "X-Backtest-Rows": str(len(df_res)),
        },
    )
