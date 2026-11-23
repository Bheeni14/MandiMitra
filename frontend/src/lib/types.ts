export interface PaginationMeta {
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  mode: "demo" | "postgresql";
  is_demo: boolean;
  backend: string;
  database: string;
  db_healthy: boolean;
  db_message: string;
  active_sources: string[];
  warnings: string[];
}

export interface CommodityItem {
  commodity_id: number;
  commodity_name: string;
  category: string;
  storable: boolean;
}

export interface CommoditiesResponse {
  items: CommodityItem[];
  total: number;
}

export interface MarketItem {
  market_id: number;
  market_name: string;
  district: string;
  state: string;
}

export interface MarketsResponse {
  items: MarketItem[];
  states: string[];
  districts: string[];
  total: number;
}

export interface CoverageSummary {
  backend: string;
  is_demo: boolean;
  total_price_records: number;
  commodities_count: number;
  markets_count: number;
  states_count: number;
  min_price_date: string | null;
  max_price_date: string | null;
  total_arrival_records: number;
  total_tonnes: number;
  min_arrival_date: string | null;
  max_arrival_date: string | null;
  total_apy_records: number;
  min_year: number | null;
  max_year: number | null;
  apy_states: number;
}

export interface PriceSourceBreakdown {
  source_name: string;
  rows: number;
  min_date: string | null;
  max_date: string | null;
}

export interface VolatilityItem {
  commodity_id: number;
  commodity_name: string;
  category: string;
  storable: boolean;
  observation_count: number;
  avg_modal_price: number | null;
  stddev_modal_price: number | null;
  cv: number | null;
  min_modal_price: number | null;
  max_modal_price: number | null;
  window_end: string | null;
  window_start: string | null;
  volatility_rank: number;
}

export interface StateGapItem {
  commodity_id: number;
  commodity_name: string;
  year: number;
  month: number;
  highest_state: string;
  highest_avg_price: number;
  lowest_state: string;
  lowest_avg_price: number;
  price_spread: number;
  pct_difference: number | null;
  state_count: number;
}

export interface MonthlyTrendItem {
  commodity_id: number;
  commodity_name: string;
  year: number;
  month: number;
  month_start: string;
  avg_modal_price: number;
  prev_month_price: number | null;
  mom_abs_change: number | null;
  mom_pct_change: number | null;
  ma_3m: number | null;
  observation_count: number;
}

export interface PriceRecordItem {
  arrival_date: string;
  state: string;
  district: string;
  market: string;
  commodity: string;
  variety: string | null;
  grade: string | null;
  min_price: number;
  max_price: number;
  modal_price: number;
  outlier_flag: boolean;
  source_name: string;
}

export interface StateAvgPriceItem {
  state: string;
  avg_modal: number;
  min_price: number | null;
  max_price: number | null;
  obs: number;
}

export interface PipelineRunItem {
  run_id: number;
  stage: string;
  source_name: string;
  started_at: string | null;
  finished_at: string | null;
  rows_fetched: number;
  rows_loaded: number;
  rows_rejected: number;
  status: string;
  error_message: string | null;
  metadata_json: Record<string, unknown>;
}

export interface MarketHighlightItem {
  commodity_name: string;
  metric_label: string;
  value_label: string;
  direction: "up" | "down" | "neutral";
}

export interface FocusCommodityMovement {
  commodity_name: string;
  latest_month: string | null;
  avg_modal_price: number | null;
  prev_month_price: number | null;
  mom_abs_change: number | null;
  mom_pct_change: number | null;
  ma_3m: number | null;
}

export interface OverviewResponse {
  mode: "demo" | "postgresql";
  is_demo: boolean;
  active_sources: string[];
  coverage: CoverageSummary;
  overall_avg_modal_price: number | null;
  latest_window_avg_modal_price: number | null;
  focus_commodity: string | null;
  focus_movement: FocusCommodityMovement | null;
  available_commodities: string[];
  market_highlights: MarketHighlightItem[];
  volatility: VolatilityItem[];
  mom_trends: MonthlyTrendItem[];
  state_averages: StateAvgPriceItem[];
  state_gaps: StateGapItem[];
  recent_prices: PriceRecordItem[];
  latest_pipeline_run: PipelineRunItem | null;
}

export interface MarketPriceSummary {
  min_price: number | null;
  max_price: number | null;
  avg_modal_price: number | null;
  latest_min_price: number | null;
  latest_modal_price: number | null;
  latest_max_price: number | null;
  latest_date: string | null;
  min_date: string | null;
  max_date: string | null;
  total_observations: number;
  outliers_flagged: number;
  sources: string[];
  available_varieties: string[];
  arrivals_available: boolean;
  total_arrivals_tonnes: number | null;
}

export interface DailyPricePoint {
  arrival_date: string;
  min_price: number;
  modal_price: number;
  max_price: number;
  arrivals_tonnes: number | null;
  outlier_count: number;
}

export interface OutlierPoint {
  arrival_date: string;
  state: string;
  market: string;
  variety: string | null;
  modal_price: number;
}

export interface MarketComparisonRow {
  state: string;
  district: string;
  market: string;
  avg_modal: number;
  min_modal: number;
  max_modal: number;
  reports: number;
}

export interface ArrivalRecordItem {
  arrival_date: string;
  state: string;
  market: string;
  commodity: string;
  quantity_tonnes: number;
  source_name: string;
}

export interface MarketPricesResponse {
  summary: MarketPriceSummary;
  daily_series: DailyPricePoint[];
  outlier_points: OutlierPoint[];
  market_comparison: MarketComparisonRow[];
  arrivals_records: ArrivalRecordItem[];
  items: PriceRecordItem[];
  pagination: PaginationMeta;
}

export interface VolatilityResponse {
  months: number;
  min_observations: number;
  items: VolatilityItem[];
  total: number;
}

export interface StateMonthlyTrendPoint {
  month: string;
  state: string;
  avg_modal_price: number;
}

export interface StatePriceGapsResponse {
  commodity: string | null;
  items: StateGapItem[];
  state_rankings: StateAvgPriceItem[];
  monthly_state_trends: StateMonthlyTrendPoint[];
  available_states: string[];
  default_states: string[];
}

export interface MonthlyTrendsResponse {
  commodity: string | null;
  items: MonthlyTrendItem[];
  total: number;
}

export interface ArrivalsVsPriceItem {
  commodity_id: number;
  commodity_name: string;
  paired_months: number;
  avg_monthly_arrivals_tonnes: number;
  avg_monthly_price: number;
  corr_same_month: number | null;
  corr_lag_1m: number | null;
}

export interface ArrivalsVsPriceResponse {
  commodity: string | null;
  has_arrivals_data: boolean;
  items: ArrivalsVsPriceItem[];
  total: number;
}

export interface SeasonalityItem {
  commodity_id: number;
  commodity_name: string;
  month: number;
  month_name: string;
  month_avg_price: number;
  annual_avg_price: number;
  seasonal_index: number;
  peak_month_rank: number;
  observation_count: number;
}

export interface SeasonalityResponse {
  commodity: string | null;
  items: SeasonalityItem[];
  total: number;
}

export interface BestMarketItem {
  state: string;
  district: string;
  market_id: number;
  market_name: string;
  commodity_id: number;
  commodity_name: string;
  report_count: number;
  avg_modal_price: number;
  lowest_min_price: number;
  highest_max_price: number;
  latest_report_date: string;
  window_end: string;
  market_rank: number;
}

export interface BestMarketsResponse {
  commodity: string | null;
  state: string | null;
  min_reports: number;
  items: BestMarketItem[];
  total: number;
}

export interface ProductionVsPriceDetailItem {
  commodity_id: number;
  commodity_name: string;
  crop_name: string;
  crop_year: number;
  state: string;
  total_area_ha: number | null;
  total_production_t: number | null;
  agg_yield_t_ha: number | null;
  avg_yearly_modal_price: number;
  price_observations: number;
}

export interface ProductionPriceCorrItem {
  commodity_id: number;
  commodity_name: string;
  crop_name: string;
  state_year_pairs: number;
  cumulative_production_t: number | null;
  avg_modal_price: number | null;
  corr_production_vs_price: number | null;
}

export interface StateProductionSummary {
  state: string;
  area_ha: number;
  production_t: number;
  avg_yield: number | null;
}

export interface MappingCoverageItem {
  commodity_name: string;
  category: string;
  storable: boolean;
  mapped_apy_crop: string | null;
  crop_type: string | null;
  price_rows: number;
  apy_rows: number;
  is_mapped: boolean;
}

export interface ProductionVsPriceResponse {
  commodity: string | null;
  kpis: {
    total_area_ha: number;
    total_production_t: number;
    avg_yield_t_ha: number;
  };
  detail: ProductionVsPriceDetailItem[];
  correlations: ProductionPriceCorrItem[];
  state_production: StateProductionSummary[];
  mapping_coverage: MappingCoverageItem[];
}

export interface AdvisorRequest {
  commodity: string;
  market: string;
  state: string;
  as_of_date?: string | null;
  force_refresh?: boolean;
}

export interface AdvisorSignalSchema {
  signal_id: number | null;
  commodity_id: number;
  commodity: string;
  category: string;
  storable: boolean;
  market_id: number;
  market: string;
  district: string;
  state: string;
  signal_date: string;
  verdict_class: "SELL NOW" | "WAIT" | "NEUTRAL";
  verdict_label: string;
  current_modal_price: number | null;
  price_change_4w_pct: number | null;
  vs_seasonal_avg_pct: number | null;
  arrivals_trend: string;
  arrivals_change_14d_pct: number | null;
  cv_12m: number | null;
  state_price_gap_pct: number | null;
  evidence_completeness: string;
  observation_count_90d: number;
  analysis_window_start: string | null;
  analysis_window_end: string | null;
  data_sources: string[];
  insufficient_evidence: boolean;
  rule_breakdown: {
    triggers?: string[];
    obs_recent_7d?: number;
    obs_prior_7d?: number;
    obs_90d?: number;
    seasonal_years?: number;
    has_arrivals_data?: boolean;
  };
  thresholds_used: Record<string, unknown>;
}

export interface AdvisorBriefingSchema {
  briefing_id: number | null;
  verdict: string;
  reason_en: string;
  caution_en: string;
  reason_hi: string;
  caution_hi: string;
  validation_status: string;
  validation_errors: string[];
  model_used: string;
  used_fallback: boolean;
}

export interface AdvisorHistoryPoint {
  arrival_date: string;
  modal_price: number | null;
  quantity_tonnes: number | null;
}

export interface RecentBriefingItem {
  briefing_id: number;
  commodity: string;
  market: string;
  state: string;
  generated_at: string;
  verdict: string;
  validation_status: string;
  model_used: string;
  used_fallback: boolean;
}

export interface AdvisorResponse {
  cached: boolean;
  is_demo: boolean;
  signal: AdvisorSignalSchema;
  briefing: AdvisorBriefingSchema;
  genai_input: Record<string, unknown>;
  history_series: AdvisorHistoryPoint[];
  recent_briefings: RecentBriefingItem[];
}

export interface StrategyMetrics {
  total_cases: number;
  decisive_cases: number;
  neutral_cases: number;
  neutral_coverage_pct: number;
  accuracy_pct: number;
  wait_calls: number;
  wait_precision_pct: number;
  sell_calls: number;
  sell_precision_pct: number;
  avg_wait_move_pct: number;
  avg_sell_move_pct: number;
  avg_captured_move_pct: number;
}

export interface CommodityBacktestItem {
  commodity: string;
  storable: boolean;
  total_cases: number;
  decisive_cases: number;
  accuracy_pct: number;
  wait_precision_pct: number;
  neutral_coverage_pct: number;
  avg_wait_move_pct: number;
  always_sell_acc_pct: number;
  momentum_acc_pct: number;
}

export interface StorabilityBacktestItem {
  crop_group: string;
  storable: boolean;
  total_cases: number;
  decisive_cases: number;
  accuracy_pct: number;
  wait_precision_pct: number;
  neutral_coverage_pct: number;
  avg_wait_move_pct: number;
  always_sell_acc_pct: number;
  momentum_acc_pct: number;
}

export interface BacktestEvaluationRow {
  decision_date: string;
  commodity: string;
  storable: boolean;
  market: string;
  state: string;
  decision_price: number;
  future_avg_14d: number;
  future_14d_change_pct: number;
  actual_outcome: string;
  verdict_class: string;
  verdict_label: string;
  is_correct: boolean | null;
  price_change_4w_pct: number | null;
  vs_seasonal_avg_pct: number | null;
  arrivals_trend: string;
  arrivals_change_14d_pct: number | null;
  cv_12m: number | null;
  baseline_always_sell: string;
  baseline_momentum: string;
}

export interface BacktestResponse {
  is_demo: boolean;
  source_label: string;
  used_precomputed_csv: boolean;
  eval_days: number;
  step_days: number;
  thresholds: Record<string, unknown>;
  mandimitra: StrategyMetrics;
  baseline_always_sell: StrategyMetrics;
  baseline_momentum_only: StrategyMetrics;
  matched_always_sell_accuracy_pct: number;
  by_commodity: CommodityBacktestItem[];
  by_storability: StorabilityBacktestItem[];
  sample_rows: BacktestEvaluationRow[];
  pagination: PaginationMeta;
  data_coverage?: BacktestDataCoverage | null;
}

/** Price history available to the backtest, and how much the first evaluation needs. */
export interface BacktestDataCoverage {
  history_start: string | null;
  history_end: string | null;
  history_days: number;
  history_span_days: number;
  required_span_days: number;
  results_expected_from: string | null;
}

export interface SourceComponentStatus {
  component: string;
  configured: boolean;
  status_label: string;
  details: string;
}

export interface DataQualityIndicators {
  total_price_records: number;
  outliers_flagged_count: number;
  outliers_flagged_pct: number;
  total_dq_rejects: number;
  total_arrival_records: number;
  total_apy_records: number;
}

export interface DataSourcesStatusResponse {
  mode: "demo" | "postgresql";
  is_demo: boolean;
  backend: string;
  active_sources: string[];
  components: SourceComponentStatus[];
  coverage: CoverageSummary;
  price_sources: PriceSourceBreakdown[];
  data_quality: DataQualityIndicators;
  latest_run: PipelineRunItem | null;
  last_successful_run: PipelineRunItem | null;
}

export interface DqRejectItem {
  reject_id: number;
  run_id: number | null;
  source_name: string;
  stage: string;
  reject_reason: string;
  raw_record: Record<string, unknown>;
  rejected_at: string | null;
}

export interface RejectReasonCount {
  reject_reason: string;
  count: number;
}

export interface PipelineHistoryResponse {
  is_demo: boolean;
  runs: PipelineRunItem[];
  runs_pagination: PaginationMeta;
  rejects: DqRejectItem[];
  rejects_pagination: PaginationMeta;
  reject_reason_summary: RejectReasonCount[];
}
