"""SQL Analytics endpoints (Q1-Q7)."""

from __future__ import annotations

import calendar
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, Query

from analytics.metrics import get_mapping_coverage
from analytics.queries import (
    get_q1_price_volatility,
    get_q2_state_price_gaps,
    get_q3_mom_trends,
    get_q4_arrivals_vs_price,
    get_q5_seasonal_price_index,
    get_q6_best_markets,
    get_q7_production_vs_price,
)
from backend.dependencies import get_db
from backend.schemas import (
    ApySummaryKPIs,
    ArrivalsVsPriceItem,
    ArrivalsVsPriceResponse,
    BestMarketItem,
    BestMarketsResponse,
    MappingCoverageItem,
    MonthlyTrendItem,
    MonthlyTrendsResponse,
    ProductionPriceCorrItem,
    ProductionVsPriceDetailItem,
    ProductionVsPriceResponse,
    SeasonalityItem,
    SeasonalityResponse,
    StateAvgPriceItem,
    StateGapItem,
    StateMonthlyTrendPoint,
    StatePriceGapsResponse,
    StateProductionSummary,
    VolatilityItem,
    VolatilityResponse,
)
from backend.serializers import df_to_records
from database.connection import DatabaseManager
from services.concurrency import run_bundles, run_queries
from services.data_service import (
    query_crop_production,
    query_state_monthly_trends,
    query_state_price_averages,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/price-volatility", response_model=VolatilityResponse)
def get_price_volatility(
    min_observations: int = Query(default=30, ge=1, le=1000),
    months: int = Query(default=12, ge=1, le=60),
    db: DatabaseManager = Depends(get_db),
) -> VolatilityResponse:
    df = get_q1_price_volatility(db, min_observations=min_observations, months=months)
    items = [VolatilityItem(**r) for r in df_to_records(df)]
    return VolatilityResponse(
        months=months,
        min_observations=min_observations,
        items=items,
        total=len(items),
    )


@router.get("/state-price-gaps", response_model=StatePriceGapsResponse)
def get_state_price_gaps(
    commodity: str | None = Query(default=None),
    year: int | None = Query(default=None, ge=1990, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    states: list[str] | None = Query(default=None),
    db: DatabaseManager = Depends(get_db),
) -> StatePriceGapsResponse:
    # The gap table and the state ranking are independent; only the trend query needs the ranking first.
    queries: dict[str, Any] = {
        "gaps": lambda: get_q2_state_price_gaps(db, commodity_name=commodity, year=year, month=month)
    }
    if commodity:
        queries["st_rank"] = lambda: query_state_price_averages(db, commodity=commodity)
    loaded = run_queries(db, queries)
    gaps_df: pd.DataFrame = loaded["gaps"]
    items = [StateGapItem(**r) for r in df_to_records(gaps_df)]

    state_rankings: list[StateAvgPriceItem] = []
    monthly_state_trends: list[StateMonthlyTrendPoint] = []
    available_states: list[str] = []
    default_states: list[str] = []

    if commodity:
        st_rank: pd.DataFrame = loaded["st_rank"]
        if not st_rank.empty:
            state_rankings = [StateAvgPriceItem(**r) for r in df_to_records(st_rank)]
            available_states = sorted(st_rank["state"].dropna().unique().tolist())
            default_states = st_rank.nlargest(5, "obs")["state"].tolist()
            chosen = [s for s in (states or default_states) if s in available_states] or default_states
            trend_df = query_state_monthly_trends(db, commodity=commodity, states=chosen)
            monthly_state_trends = [StateMonthlyTrendPoint(**r) for r in df_to_records(trend_df)]

    return StatePriceGapsResponse(
        commodity=commodity,
        items=items,
        state_rankings=state_rankings,
        monthly_state_trends=monthly_state_trends,
        available_states=available_states,
        default_states=default_states,
    )


@router.get("/monthly-trends", response_model=MonthlyTrendsResponse)
def get_monthly_trends(
    commodity: str | None = Query(default=None),
    db: DatabaseManager = Depends(get_db),
) -> MonthlyTrendsResponse:
    df = get_q3_mom_trends(db, commodity_name=commodity)
    items = [MonthlyTrendItem(**r) for r in df_to_records(df)]
    return MonthlyTrendsResponse(commodity=commodity, items=items, total=len(items))


@router.get("/arrivals-vs-price", response_model=ArrivalsVsPriceResponse)
def get_arrivals_vs_price(
    commodity: str | None = Query(default=None),
    db: DatabaseManager = Depends(get_db),
) -> ArrivalsVsPriceResponse:
    df = get_q4_arrivals_vs_price(db, commodity_name=commodity)
    items = [ArrivalsVsPriceItem(**r) for r in df_to_records(df)]
    return ArrivalsVsPriceResponse(
        commodity=commodity,
        has_arrivals_data=len(items) > 0,
        items=items,
        total=len(items),
    )


@router.get("/seasonality", response_model=SeasonalityResponse)
def get_seasonality(
    commodity: str | None = Query(default=None),
    db: DatabaseManager = Depends(get_db),
) -> SeasonalityResponse:
    df = get_q5_seasonal_price_index(db, commodity_name=commodity)
    if not df.empty:
        df = df.copy()
        df["month_name"] = df["month"].map(lambda m: calendar.month_abbr[int(m)])
    items = [SeasonalityItem(**r) for r in df_to_records(df)]
    return SeasonalityResponse(commodity=commodity, items=items, total=len(items))


@router.get("/best-markets", response_model=BestMarketsResponse)
def get_best_markets(
    commodity: str | None = Query(default=None),
    state: str | None = Query(default=None),
    min_reports: int = Query(default=2, ge=1, le=30),
    db: DatabaseManager = Depends(get_db),
) -> BestMarketsResponse:
    df = get_q6_best_markets(db, state=state, commodity_name=commodity, min_reports=min_reports)
    items = [BestMarketItem(**r) for r in df_to_records(df)]
    return BestMarketsResponse(
        commodity=commodity,
        state=state,
        min_reports=min_reports,
        items=items,
        total=len(items),
    )


@router.get("/production-vs-price", response_model=ProductionVsPriceResponse)
def get_production_vs_price(
    commodity: str | None = Query(default=None),
    state: str | None = Query(default=None),
    db: DatabaseManager = Depends(get_db),
) -> ProductionVsPriceResponse:
    # Q7 runs its own concurrent queries, so it is a bundle alongside the two plain lookups.
    loaded = run_bundles(
        db,
        {
            "q7": lambda: get_q7_production_vs_price(db, commodity_name=commodity),
            "lookups": lambda: run_queries(
                db,
                {
                    "map_cov": lambda: get_mapping_coverage(db),
                    "prod": lambda: query_crop_production(db, commodity_name=commodity, state=state),
                },
            ),
        },
    )
    detail_df, corr_df = loaded["q7"]
    if state and not detail_df.empty:
        detail_df = detail_df[detail_df["state"] == state]

    map_cov: pd.DataFrame = loaded["lookups"]["map_cov"]
    prod_df: pd.DataFrame = loaded["lookups"]["prod"]

    total_prod = float(prod_df["production_t"].sum()) if not prod_df.empty else 0.0
    total_area = float(prod_df["area_ha"].sum()) if not prod_df.empty else 0.0
    avg_yield = round((total_prod / total_area), 4) if total_area > 0 else 0.0

    state_prod_items: list[StateProductionSummary] = []
    if not prod_df.empty:
        st_prod = (
            prod_df.groupby("state", as_index=False)
            .agg(
                area_ha=("area_ha", "sum"),
                production_t=("production_t", "sum"),
                avg_yield=("yield_t_ha", "mean"),
            )
            .sort_values("production_t", ascending=False)
        )
        st_prod["area_ha"] = st_prod["area_ha"].round(2)
        st_prod["production_t"] = st_prod["production_t"].round(2)
        st_prod["avg_yield"] = st_prod["avg_yield"].round(4)
        state_prod_items = [StateProductionSummary(**r) for r in df_to_records(st_prod)]

    return ProductionVsPriceResponse(
        commodity=commodity,
        kpis=ApySummaryKPIs(
            total_area_ha=round(total_area, 2),
            total_production_t=round(total_prod, 2),
            avg_yield_t_ha=avg_yield,
        ),
        detail=[ProductionVsPriceDetailItem(**r) for r in df_to_records(detail_df)],
        correlations=[ProductionPriceCorrItem(**r) for r in df_to_records(corr_df)],
        state_production=state_prod_items,
        mapping_coverage=[MappingCoverageItem(**r) for r in df_to_records(map_cov)],
    )
