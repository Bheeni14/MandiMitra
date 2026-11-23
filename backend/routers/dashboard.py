"""Dashboard overview, reference dimensions, and Market Price Explorer endpoints."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from backend.dependencies import get_db
from backend.schemas import (
    ArrivalRecordItem,
    CommoditiesResponse,
    CommodityItem,
    CoverageSummary,
    DailyPricePoint,
    FocusCommodityMovement,
    MarketComparisonRow,
    MarketHighlightItem,
    MarketItem,
    MarketPriceSummary,
    MarketPricesResponse,
    MarketsResponse,
    MonthlyTrendItem,
    OutlierPoint,
    OverviewResponse,
    PaginationMeta,
    PipelineRunItem,
    PriceRecordItem,
    StateAvgPriceItem,
    VolatilityItem,
)
from backend.serializers import clean_scalar, df_to_records, parse_json_field
from database.connection import DatabaseManager
from services.concurrency import run_bundles, run_queries, use_concurrency
from services.dashboard_service import (
    default_focus_commodity,
    get_overview_bundle,
    get_overview_focus_bundle,
)
from services.data_service import (
    list_commodities,
    list_markets,
    query_market_arrivals,
    query_market_arrivals_daily,
    query_price_explorer,
    query_price_explorer_aggregates,
)

router = APIRouter(prefix="/api", tags=["dashboard"])

# Outlier rows prefetched alongside the other Market Explorer queries (see get_market_prices).
_OUTLIER_PREFETCH_LIMIT = 2000


def _normalize_pipeline_run(row: dict[str, Any]) -> PipelineRunItem:
    return PipelineRunItem(
        run_id=int(row["run_id"]),
        stage=str(row["stage"]),
        source_name=str(row["source_name"]),
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
        rows_fetched=int(row.get("rows_fetched") or 0),
        rows_loaded=int(row.get("rows_loaded") or 0),
        rows_rejected=int(row.get("rows_rejected") or 0),
        status=str(row["status"]),
        error_message=row.get("error_message"),
        metadata_json=parse_json_field(row.get("metadata_json"), default={}),
    )


@router.get("/commodities", response_model=CommoditiesResponse)
def get_commodities(db: DatabaseManager = Depends(get_db)) -> CommoditiesResponse:
    df = list_commodities(db)
    records = df_to_records(df)
    items = [CommodityItem(**r) for r in records]
    return CommoditiesResponse(items=items, total=len(items))


@router.get("/markets", response_model=MarketsResponse)
def get_markets(
    commodity: str | None = Query(default=None),
    state: str | None = Query(default=None),
    district: str | None = Query(default=None),
    db: DatabaseManager = Depends(get_db),
) -> MarketsResponse:
    df = list_markets(db, commodity_name=commodity, state=state)
    if not df.empty and district:
        df = df[df["district"] == district]
    records = df_to_records(df)
    items = [MarketItem(**r) for r in records]
    states = sorted({item.state for item in items})
    districts = sorted({item.district for item in items})
    return MarketsResponse(items=items, states=states, districts=districts, total=len(items))


@router.get("/dashboard/overview", response_model=OverviewResponse)
def get_dashboard_overview(
    commodity: str | None = Query(default=None),
    recent_limit: int = Query(default=25, ge=1, le=100),
    db: DatabaseManager = Depends(get_db),
) -> OverviewResponse:
    # The focus commodity is only known for sure once the commodity list loads, but it is almost always the
    # requested one (or the "Tomato" default), so its queries start alongside the base bundle. A wrong guess
    # just costs one extra lookup below.
    focus_guess = commodity or "Tomato"
    loaded = run_bundles(
        db,
        {
            "base": lambda: get_overview_bundle(db),
            "focus": lambda: get_overview_focus_bundle(db, focus_guess),
        },
    )
    bundle: dict[str, Any] = loaded["base"]
    cov_raw = {k: clean_scalar(v) for k, v in bundle["coverage"].items()}
    vol_df: pd.DataFrame = bundle["volatility"]
    mom_df: pd.DataFrame = bundle["mom_trends"]
    runs_df: pd.DataFrame = bundle["pipeline_runs"]
    recent_df: pd.DataFrame = bundle["recent_prices"]
    if recent_limit != 25:
        recent_df = query_price_explorer(db, limit=recent_limit)

    comm_df: pd.DataFrame = bundle["commodities"]
    comm_list = sorted(comm_df["commodity_name"].astype(str).tolist()) if not comm_df.empty else []

    focus_commodity: str | None = None
    if commodity:
        if commodity in comm_list:
            focus_commodity = commodity
        else:
            raise HTTPException(status_code=404, detail=f"Commodity '{commodity}' not found in warehouse.")
    else:
        focus_commodity = default_focus_commodity(comm_list)

    # Real SQL aggregate for overall and trailing 7-day average modal price
    overall_avg_modal: float | None = clean_scalar(bundle["overall_avg_modal"])
    latest_window_avg_modal: float | None = clean_scalar(bundle["latest_window_avg_modal"])

    # Focus commodity Q3 trend & movement indicator
    focus_mom_df = (
        mom_df[mom_df["commodity_name"] == focus_commodity].copy()
        if (focus_commodity and not mom_df.empty)
        else pd.DataFrame()
    )
    focus_movement: FocusCommodityMovement | None = None
    if focus_commodity and not focus_mom_df.empty:
        latest_m = df_to_records(focus_mom_df.tail(1))[0]
        focus_movement = FocusCommodityMovement(
            commodity_name=focus_commodity,
            latest_month=latest_m.get("month_start"),
            avg_modal_price=latest_m.get("avg_modal_price"),
            prev_month_price=latest_m.get("prev_month_price"),
            mom_abs_change=latest_m.get("mom_abs_change"),
            mom_pct_change=latest_m.get("mom_pct_change"),
            ma_3m=latest_m.get("ma_3m"),
        )

    # Market highlights strictly computed from real Q1 / Q2 / Q3 results
    highlights: list[MarketHighlightItem] = []
    if not mom_df.empty:
        latest_mom = mom_df.sort_values(["year", "month"]).groupby("commodity_name", as_index=False).last()
        valid_mom = latest_mom.dropna(subset=["mom_pct_change"])
        if not valid_mom.empty:
            top_gain = valid_mom.sort_values("mom_pct_change", ascending=False).iloc[0]
            top_drop = valid_mom.sort_values("mom_pct_change", ascending=True).iloc[0]
            gain_pct = float(top_gain["mom_pct_change"])
            drop_pct = float(top_drop["mom_pct_change"])
            highlights.append(
                MarketHighlightItem(
                    commodity_name=str(top_gain["commodity_name"]),
                    metric_label="Strongest Monthly Gain (Q3)",
                    value_label=f"{gain_pct:+.2f}% MoM (₹{float(top_gain['avg_modal_price']):,.2f}/qtl)",
                    direction="up" if gain_pct > 0 else ("down" if gain_pct < 0 else "neutral"),
                )
            )
            highlights.append(
                MarketHighlightItem(
                    commodity_name=str(top_drop["commodity_name"]),
                    metric_label="Sharpest Monthly Decline (Q3)",
                    value_label=f"{drop_pct:+.2f}% MoM (₹{float(top_drop['avg_modal_price']):,.2f}/qtl)",
                    direction="down" if drop_pct < 0 else ("up" if drop_pct > 0 else "neutral"),
                )
            )
    if not vol_df.empty:
        top_v = vol_df.iloc[0]
        highlights.append(
            MarketHighlightItem(
                commodity_name=str(top_v["commodity_name"]),
                metric_label="Highest 12m Volatility (Q1)",
                # `cv` is a decimal ratio (0.3218) everywhere in the API; only this display label is a percentage.
                value_label=f"CV {float(top_v['cv']) * 100:.2f}% (₹{float(top_v['avg_modal_price']):,.2f} avg)",
                direction="neutral",
            )
        )

    # State averages & Q2 spreads for focus commodity
    state_averages: list[StateAvgPriceItem] = []
    state_gaps: list[dict[str, Any]] = []
    if focus_commodity:
        focus_bundle = (
            loaded["focus"] if focus_commodity == focus_guess else get_overview_focus_bundle(db, focus_commodity)
        )
        # SQL aggregation over every matching observation (no row cap, full date range).
        st_agg: pd.DataFrame = focus_bundle["state_averages"]
        state_averages = [StateAvgPriceItem(**r) for r in df_to_records(st_agg)]

        gaps_df: pd.DataFrame = focus_bundle["state_gaps"]
        if not gaps_df.empty:
            state_gaps = df_to_records(gaps_df.head(8))
            top_g = state_gaps[0]
            highlights.append(
                MarketHighlightItem(
                    commodity_name=focus_commodity,
                    metric_label="Latest Inter-State Spread (Q2)",
                    value_label=f"{top_g['highest_state']} vs {top_g['lowest_state']} (+{float(top_g['pct_difference']):.1f}%)",
                    direction="neutral",
                )
            )

    runs_records = df_to_records(runs_df)
    latest_run = _normalize_pipeline_run(runs_records[0]) if runs_records else None

    coverage = CoverageSummary(
        backend=str(cov_raw.get("backend", db.backend)),
        is_demo=bool(cov_raw.get("is_demo", db.is_demo)),
        total_price_records=int(cov_raw.get("total_price_records") or 0),
        commodities_count=int(cov_raw.get("commodities_count") or 0),
        markets_count=int(cov_raw.get("markets_count") or 0),
        states_count=int(cov_raw.get("states_count") or 0),
        min_price_date=cov_raw.get("min_price_date"),
        max_price_date=cov_raw.get("max_price_date"),
        total_arrival_records=int(cov_raw.get("total_arrival_records") or 0),
        total_tonnes=float(cov_raw.get("total_tonnes") or 0.0),
        min_arrival_date=cov_raw.get("min_arrival_date"),
        max_arrival_date=cov_raw.get("max_arrival_date"),
        total_apy_records=int(cov_raw.get("total_apy_records") or 0),
        min_year=cov_raw.get("min_year"),
        max_year=cov_raw.get("max_year"),
        apy_states=int(cov_raw.get("apy_states") or 0),
    )

    return OverviewResponse(
        mode="demo" if db.is_demo else "postgresql",
        is_demo=db.is_demo,
        active_sources=list(bundle["active_sources"]),
        coverage=coverage,
        overall_avg_modal_price=overall_avg_modal,
        latest_window_avg_modal_price=latest_window_avg_modal,
        focus_commodity=focus_commodity,
        focus_movement=focus_movement,
        available_commodities=comm_list,
        market_highlights=highlights,
        volatility=[VolatilityItem(**r) for r in df_to_records(vol_df.head(12))],
        mom_trends=[MonthlyTrendItem(**r) for r in df_to_records(focus_mom_df)],
        state_averages=state_averages,
        state_gaps=state_gaps,
        recent_prices=[PriceRecordItem(**r) for r in df_to_records(recent_df)],
        latest_pipeline_run=latest_run,
    )


@router.get("/market-prices", response_model=MarketPricesResponse)
def get_market_prices(
    commodity: str | None = Query(default=None),
    state: str | None = Query(default=None),
    district: str | None = Query(default=None),
    market: str | None = Query(default=None),
    variety: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: DatabaseManager = Depends(get_db),
) -> MarketPricesResponse:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be less than or equal to end_date.")

    # Query available varieties & natural date bounds for (commodity, state, district, market)
    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_d = db.table_ref("warehouse", "dim_date")
    t_m = db.table_ref("warehouse", "dim_market")
    t_c = db.table_ref("warehouse", "dim_commodity")
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if commodity:
        clauses.append("c.commodity_name = :commodity")
        params["commodity"] = commodity
    if state:
        clauses.append("m.state = :state")
        params["state"] = state
    if district:
        clauses.append("m.district = :district")
        params["district"] = district
    if market:
        clauses.append("m.market_name = :market")
        params["market"] = market
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    def fetch_varieties() -> list[str]:
        with db.readonly_connection() as conn:
            var_rows = conn.execute(
                text(
                    f"""
                    SELECT DISTINCT p.variety
                    FROM {t_p} p
                    JOIN {t_m} m ON m.market_id = p.market_id
                    JOIN {t_c} c ON c.commodity_id = p.commodity_id
                    {where_sql}
                    ORDER BY p.variety
                    """
                ),
                params,
            ).scalars().all()
        return sorted({str(v) for v in var_rows if v})

    price_filters: dict[str, Any] = {
        "state": state,
        "district": district,
        "market": market,
        "commodity": commodity,
        "variety": variety,
        "start_date": start_date,
        "end_date": end_date,
    }
    # Arrivals carry market_id -> dim_market.district, so district filters arrivals exactly like prices.
    arrival_filters: dict[str, Any] = {
        "commodity": commodity,
        "market": market,
        "state": state,
        "district": district,
        "start_date": start_date,
        "end_date": end_date,
    }
    # Everything below is independent, so on PostgreSQL it loads in one concurrent batch (each query is a
    # ~200ms round trip to the remote database). Outlier rows are fetched speculatively with a cap and used
    # only when the aggregate count fits under it; otherwise they are re-fetched exactly as before.
    page_queries: dict[str, Any] = {
        "varieties": fetch_varieties,
        "arr_daily": lambda: query_market_arrivals_daily(db, **arrival_filters),
        "arr_records": lambda: query_market_arrivals(db, limit=200, **arrival_filters),
        "page": lambda: query_price_explorer(db, limit=page_size, offset=(page - 1) * page_size, **price_filters),
    }
    if page != 1:
        page_queries["latest"] = lambda: query_price_explorer(db, limit=1, **price_filters)
    if use_concurrency(db):
        page_queries["outliers"] = lambda: query_price_explorer(
            db, limit=_OUTLIER_PREFETCH_LIMIT, outliers_only=True, **price_filters
        )
    loaded = run_bundles(
        db,
        {
            "agg": lambda: query_price_explorer_aggregates(db, **price_filters),
            "rest": lambda: run_queries(db, page_queries),
        },
    )
    agg: dict[str, Any] = loaded["agg"]
    rest: dict[str, Any] = loaded["rest"]
    available_varieties: list[str] = rest["varieties"]
    arr_daily: pd.DataFrame = rest["arr_daily"]
    arr_records_df: pd.DataFrame = rest["arr_records"]
    arrivals_available = not arr_daily.empty
    total_arrivals = round(float(arr_daily["arrivals_tonnes"].sum()), 2) if arrivals_available else None
    arrivals_records = [ArrivalRecordItem(**r) for r in df_to_records(arr_records_df)]

    total_items = agg["total_observations"]
    if total_items == 0:
        return MarketPricesResponse(
            summary=MarketPriceSummary(
                total_observations=0,
                outliers_flagged=0,
                sources=[],
                available_varieties=available_varieties,
                arrivals_available=arrivals_available,
                total_arrivals_tonnes=total_arrivals,
            ),
            daily_series=[],
            outlier_points=[],
            market_comparison=[],
            arrivals_records=arrivals_records,
            items=[],
            pagination=PaginationMeta(page=page, page_size=page_size, total_items=0, total_pages=0),
        )

    daily: pd.DataFrame = agg["daily"]
    if arrivals_available:
        daily = daily.merge(arr_daily, on="arrival_date", how="left")
    else:
        daily["arrivals_tonnes"] = None

    outliers_flagged = agg["outliers_flagged"]
    if outliers_flagged <= 0:
        outliers_df = pd.DataFrame()
    elif "outliers" in rest and outliers_flagged <= _OUTLIER_PREFETCH_LIMIT:
        outliers_df = rest["outliers"].head(outliers_flagged)
    else:
        outliers_df = query_price_explorer(db, limit=max(outliers_flagged, 1), outliers_only=True, **price_filters)
    outlier_points = [
        OutlierPoint(
            arrival_date=r["arrival_date"],
            state=r["state"],
            market=r["market"],
            variety=r.get("variety"),
            modal_price=float(r["modal_price"]),
        )
        for r in df_to_records(outliers_df)
    ]

    total_pages = max(1, math.ceil(total_items / page_size))
    page_df: pd.DataFrame = rest["page"]
    latest_df: pd.DataFrame = page_df if page == 1 else rest["latest"]
    latest_row = latest_df.iloc[0]

    summary = MarketPriceSummary(
        min_price=agg["min_price"],
        max_price=agg["max_price"],
        avg_modal_price=agg["avg_modal_price"],
        latest_min_price=round(float(latest_row["min_price"]), 2),
        latest_modal_price=round(float(latest_row["modal_price"]), 2),
        latest_max_price=round(float(latest_row["max_price"]), 2),
        latest_date=clean_scalar(latest_row["arrival_date"]),
        min_date=clean_scalar(agg["min_date"]),
        max_date=clean_scalar(agg["max_date"]),
        total_observations=total_items,
        outliers_flagged=agg["outliers_flagged"],
        sources=agg["sources"],
        available_varieties=available_varieties,
        arrivals_available=arrivals_available,
        total_arrivals_tonnes=total_arrivals,
    )
    mkt_comp: pd.DataFrame = agg["market_comparison"]

    return MarketPricesResponse(
        summary=summary,
        daily_series=[DailyPricePoint(**r) for r in df_to_records(daily)],
        outlier_points=outlier_points,
        market_comparison=[MarketComparisonRow(**r) for r in df_to_records(mkt_comp)],
        arrivals_records=arrivals_records,
        items=[PriceRecordItem(**r) for r in df_to_records(page_df)],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
        ),
    )
