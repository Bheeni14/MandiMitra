"""Typed Pydantic v2 request and response schemas for the MandiMitra FastAPI backend."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    mode: Literal["demo", "postgresql"]
    is_demo: bool
    backend: str
    database: str
    db_healthy: bool
    db_message: str
    active_sources: list[str]
    warnings: list[str]


class CommodityItem(BaseModel):
    commodity_id: int
    commodity_name: str
    category: str
    storable: bool


class CommoditiesResponse(BaseModel):
    items: list[CommodityItem]
    total: int


class MarketItem(BaseModel):
    market_id: int
    market_name: str
    district: str
    state: str


class MarketsResponse(BaseModel):
    items: list[MarketItem]
    states: list[str]
    districts: list[str]
    total: int


class CoverageSummary(BaseModel):
    backend: str
    is_demo: bool
    total_price_records: int
    commodities_count: int
    markets_count: int
    states_count: int
    min_price_date: str | None = None
    max_price_date: str | None = None
    total_arrival_records: int
    total_tonnes: float
    min_arrival_date: str | None = None
    max_arrival_date: str | None = None
    total_apy_records: int
    min_year: int | None = None
    max_year: int | None = None
    apy_states: int


class PriceSourceBreakdown(BaseModel):
    source_name: str
    rows: int
    min_date: str | None = None
    max_date: str | None = None


class VolatilityItem(BaseModel):
    commodity_id: int
    commodity_name: str
    category: str
    storable: bool
    observation_count: int
    avg_modal_price: float | None = None
    stddev_modal_price: float | None = None
    cv: float | None = None
    min_modal_price: float | None = None
    max_modal_price: float | None = None
    window_end: str | None = None
    window_start: str | None = None
    volatility_rank: int


class StateGapItem(BaseModel):
    commodity_id: int
    commodity_name: str
    year: int
    month: int
    highest_state: str
    highest_avg_price: float
    lowest_state: str
    lowest_avg_price: float
    price_spread: float
    pct_difference: float | None = None
    state_count: int


class MonthlyTrendItem(BaseModel):
    commodity_id: int
    commodity_name: str
    year: int
    month: int
    month_start: str
    avg_modal_price: float
    prev_month_price: float | None = None
    mom_abs_change: float | None = None
    mom_pct_change: float | None = None
    ma_3m: float | None = None
    observation_count: int


class PriceRecordItem(BaseModel):
    arrival_date: str
    state: str
    district: str
    market: str
    commodity: str
    variety: str | None = None
    grade: str | None = None
    min_price: float
    max_price: float
    modal_price: float
    outlier_flag: bool
    source_name: str


class StateAvgPriceItem(BaseModel):
    state: str
    avg_modal: float
    min_price: float | None = None
    max_price: float | None = None
    obs: int


class PipelineRunItem(BaseModel):
    run_id: int
    stage: str
    source_name: str
    started_at: str | None = None
    finished_at: str | None = None
    rows_fetched: int
    rows_loaded: int
    rows_rejected: int
    status: str
    error_message: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class MarketHighlightItem(BaseModel):
    commodity_name: str
    metric_label: str
    value_label: str
    direction: Literal["up", "down", "neutral"]


class FocusCommodityMovement(BaseModel):
    commodity_name: str
    latest_month: str | None = None
    avg_modal_price: float | None = None
    prev_month_price: float | None = None
    mom_abs_change: float | None = None
    mom_pct_change: float | None = None
    ma_3m: float | None = None


class OverviewResponse(BaseModel):
    mode: Literal["demo", "postgresql"]
    is_demo: bool
    active_sources: list[str]
    coverage: CoverageSummary
    overall_avg_modal_price: float | None = None
    latest_window_avg_modal_price: float | None = None
    focus_commodity: str | None = None
    focus_movement: FocusCommodityMovement | None = None
    available_commodities: list[str]
    market_highlights: list[MarketHighlightItem]
    volatility: list[VolatilityItem]
    mom_trends: list[MonthlyTrendItem]
    state_averages: list[StateAvgPriceItem]
    state_gaps: list[StateGapItem]
    recent_prices: list[PriceRecordItem]
    latest_pipeline_run: PipelineRunItem | None = None


class MarketPriceSummary(BaseModel):
    min_price: float | None = None
    max_price: float | None = None
    avg_modal_price: float | None = None
    latest_min_price: float | None = None
    latest_modal_price: float | None = None
    latest_max_price: float | None = None
    latest_date: str | None = None
    min_date: str | None = None
    max_date: str | None = None
    total_observations: int
    outliers_flagged: int
    sources: list[str]
    available_varieties: list[str]
    arrivals_available: bool
    total_arrivals_tonnes: float | None = None


class DailyPricePoint(BaseModel):
    arrival_date: str
    min_price: float
    modal_price: float
    max_price: float
    arrivals_tonnes: float | None = None
    outlier_count: int = 0


class OutlierPoint(BaseModel):
    arrival_date: str
    state: str
    market: str
    variety: str | None = None
    modal_price: float


class MarketComparisonRow(BaseModel):
    state: str
    district: str
    market: str
    avg_modal: float
    min_modal: float
    max_modal: float
    reports: int


class ArrivalRecordItem(BaseModel):
    arrival_date: str
    state: str
    market: str
    commodity: str
    quantity_tonnes: float
    source_name: str


class MarketPricesResponse(BaseModel):
    summary: MarketPriceSummary
    daily_series: list[DailyPricePoint]
    outlier_points: list[OutlierPoint]
    market_comparison: list[MarketComparisonRow]
    arrivals_records: list[ArrivalRecordItem]
    items: list[PriceRecordItem]
    pagination: PaginationMeta


class VolatilityResponse(BaseModel):
    months: int
    min_observations: int
    items: list[VolatilityItem]
    total: int


class StateMonthlyTrendPoint(BaseModel):
    month: str
    state: str
    avg_modal_price: float


class StatePriceGapsResponse(BaseModel):
    commodity: str | None = None
    items: list[StateGapItem]
    state_rankings: list[StateAvgPriceItem]
    monthly_state_trends: list[StateMonthlyTrendPoint]
    available_states: list[str]
    default_states: list[str]


class MonthlyTrendsResponse(BaseModel):
    commodity: str | None = None
    items: list[MonthlyTrendItem]
    total: int


class ArrivalsVsPriceItem(BaseModel):
    commodity_id: int
    commodity_name: str
    paired_months: int
    avg_monthly_arrivals_tonnes: float
    avg_monthly_price: float
    corr_same_month: float | None = None
    corr_lag_1m: float | None = None


class ArrivalsVsPriceResponse(BaseModel):
    commodity: str | None = None
    has_arrivals_data: bool
    items: list[ArrivalsVsPriceItem]
    total: int


class SeasonalityItem(BaseModel):
    commodity_id: int
    commodity_name: str
    month: int
    month_name: str
    month_avg_price: float
    annual_avg_price: float
    seasonal_index: float
    peak_month_rank: int
    observation_count: int


class SeasonalityResponse(BaseModel):
    commodity: str | None = None
    items: list[SeasonalityItem]
    total: int


class BestMarketItem(BaseModel):
    state: str
    district: str
    market_id: int
    market_name: str
    commodity_id: int
    commodity_name: str
    report_count: int
    avg_modal_price: float
    lowest_min_price: float
    highest_max_price: float
    latest_report_date: str
    window_end: str
    market_rank: int


class BestMarketsResponse(BaseModel):
    commodity: str | None = None
    state: str | None = None
    min_reports: int
    items: list[BestMarketItem]
    total: int


class ProductionVsPriceDetailItem(BaseModel):
    commodity_id: int
    commodity_name: str
    crop_name: str
    crop_year: int
    state: str
    total_area_ha: float | None = None
    total_production_t: float | None = None
    agg_yield_t_ha: float | None = None
    avg_yearly_modal_price: float
    price_observations: int


class ProductionPriceCorrItem(BaseModel):
    commodity_id: int
    commodity_name: str
    crop_name: str
    state_year_pairs: int
    cumulative_production_t: float | None = None
    avg_modal_price: float | None = None
    corr_production_vs_price: float | None = None


class StateProductionSummary(BaseModel):
    state: str
    area_ha: float
    production_t: float
    avg_yield: float | None = None


class MappingCoverageItem(BaseModel):
    commodity_name: str
    category: str
    storable: bool
    mapped_apy_crop: str | None = None
    crop_type: str | None = None
    price_rows: int
    apy_rows: int
    is_mapped: bool


class ApySummaryKPIs(BaseModel):
    total_area_ha: float
    total_production_t: float
    avg_yield_t_ha: float


class ProductionVsPriceResponse(BaseModel):
    commodity: str | None = None
    kpis: ApySummaryKPIs
    detail: list[ProductionVsPriceDetailItem]
    correlations: list[ProductionPriceCorrItem]
    state_production: list[StateProductionSummary]
    mapping_coverage: list[MappingCoverageItem]


class AdvisorRequest(BaseModel):
    commodity: str = Field(..., min_length=1, max_length=100)
    market: str = Field(..., min_length=1, max_length=150)
    state: str = Field(..., min_length=1, max_length=100)
    as_of_date: date | None = None
    force_refresh: bool = False

    @field_validator("commodity", "market", "state")
    @classmethod
    def _strip_non_empty(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field must not be empty.")
        return cleaned


class AdvisorSignalSchema(BaseModel):
    signal_id: int | None = None
    commodity_id: int
    commodity: str
    category: str
    storable: bool
    market_id: int
    market: str
    district: str
    state: str
    signal_date: str
    verdict_class: str
    verdict_label: str
    current_modal_price: float | None = None
    price_change_4w_pct: float | None = None
    vs_seasonal_avg_pct: float | None = None
    arrivals_trend: str
    arrivals_change_14d_pct: float | None = None
    cv_12m: float | None = None
    state_price_gap_pct: float | None = None
    evidence_completeness: str
    observation_count_90d: int
    analysis_window_start: str | None = None
    analysis_window_end: str | None = None
    data_sources: list[str]
    insufficient_evidence: bool
    rule_breakdown: dict[str, Any]
    thresholds_used: dict[str, Any]


class AdvisorBriefingSchema(BaseModel):
    briefing_id: int | None = None
    verdict: str
    reason_en: str
    caution_en: str
    reason_hi: str
    caution_hi: str
    validation_status: str
    validation_errors: list[str]
    model_used: str
    used_fallback: bool


class AdvisorHistoryPoint(BaseModel):
    arrival_date: str
    modal_price: float | None = None
    quantity_tonnes: float | None = None


class RecentBriefingItem(BaseModel):
    briefing_id: int
    commodity: str
    market: str
    state: str
    generated_at: str
    verdict: str
    validation_status: str
    model_used: str
    used_fallback: bool


class AdvisorResponse(BaseModel):
    cached: bool
    is_demo: bool
    signal: AdvisorSignalSchema
    briefing: AdvisorBriefingSchema
    genai_input: dict[str, Any]
    history_series: list[AdvisorHistoryPoint]
    recent_briefings: list[RecentBriefingItem]


class StrategyMetrics(BaseModel):
    total_cases: int
    decisive_cases: int
    neutral_cases: int
    neutral_coverage_pct: float
    accuracy_pct: float
    wait_calls: int
    wait_precision_pct: float
    sell_calls: int
    sell_precision_pct: float
    avg_wait_move_pct: float
    avg_sell_move_pct: float
    avg_captured_move_pct: float


class CommodityBacktestItem(BaseModel):
    commodity: str
    storable: bool
    total_cases: int
    decisive_cases: int
    accuracy_pct: float
    wait_precision_pct: float
    neutral_coverage_pct: float
    avg_wait_move_pct: float
    always_sell_acc_pct: float
    momentum_acc_pct: float


class StorabilityBacktestItem(BaseModel):
    crop_group: str
    storable: bool
    total_cases: int
    decisive_cases: int
    accuracy_pct: float
    wait_precision_pct: float
    neutral_coverage_pct: float
    avg_wait_move_pct: float
    always_sell_acc_pct: float
    momentum_acc_pct: float


class BacktestEvaluationRow(BaseModel):
    decision_date: str
    commodity: str
    storable: bool
    market: str
    state: str
    decision_price: float
    future_avg_14d: float
    future_14d_change_pct: float
    actual_outcome: str
    verdict_class: str
    verdict_label: str
    is_correct: bool | None = None
    price_change_4w_pct: float | None = None
    vs_seasonal_avg_pct: float | None = None
    arrivals_trend: str
    arrivals_change_14d_pct: float | None = None
    cv_12m: float | None = None
    baseline_always_sell: str
    baseline_momentum: str


class BacktestDataCoverage(BaseModel):
    """Price history available to the backtest, and how much the first evaluation needs."""

    history_start: date | None = None
    history_end: date | None = None
    history_days: int = 0
    history_span_days: int = 0
    required_span_days: int = 0
    results_expected_from: date | None = None


class BacktestResponse(BaseModel):
    is_demo: bool
    source_label: str
    used_precomputed_csv: bool
    eval_days: int
    step_days: int
    thresholds: dict[str, Any]
    mandimitra: StrategyMetrics
    baseline_always_sell: StrategyMetrics
    baseline_momentum_only: StrategyMetrics
    matched_always_sell_accuracy_pct: float
    by_commodity: list[CommodityBacktestItem]
    by_storability: list[StorabilityBacktestItem]
    sample_rows: list[BacktestEvaluationRow]
    pagination: PaginationMeta
    data_coverage: BacktestDataCoverage | None = None


class SourceComponentStatus(BaseModel):
    component: str
    configured: bool
    status_label: str
    details: str


class DataQualityIndicators(BaseModel):
    total_price_records: int
    outliers_flagged_count: int
    outliers_flagged_pct: float
    total_dq_rejects: int
    total_arrival_records: int
    total_apy_records: int


class DataSourcesStatusResponse(BaseModel):
    mode: Literal["demo", "postgresql"]
    is_demo: bool
    backend: str
    active_sources: list[str]
    components: list[SourceComponentStatus]
    coverage: CoverageSummary
    price_sources: list[PriceSourceBreakdown]
    data_quality: DataQualityIndicators
    latest_run: PipelineRunItem | None = None
    last_successful_run: PipelineRunItem | None = None


class DqRejectItem(BaseModel):
    reject_id: int
    run_id: int | None = None
    source_name: str
    stage: str
    reject_reason: str
    raw_record: dict[str, Any]
    rejected_at: str | None = None


class RejectReasonCount(BaseModel):
    reject_reason: str
    count: int


class PipelineHistoryResponse(BaseModel):
    is_demo: bool
    runs: list[PipelineRunItem]
    runs_pagination: PaginationMeta
    rejects: list[DqRejectItem]
    rejects_pagination: PaginationMeta
    reject_reason_summary: list[RejectReasonCount]
