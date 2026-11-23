import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AiAdvisorPage from "@/app/ai-advisor/page";
import BacktestingPage from "@/app/backtesting/page";
import MarketExplorerPage from "@/app/market-explorer/page";
import PriceAnalyticsPage from "@/app/price-analytics/page";
import {
  buildDailyChartRows,
  CHART_COLORS,
  cvToPct,
  formatCvPct,
  formatPriceArrivalsTooltip,
  SEASONAL_BASELINE,
  seasonalIndexColor,
  seasonalIndexDomain,
  seasonalIndexTone,
} from "@/components/charts/chartHelpers";
import { describeValidationStatus } from "@/lib/advisor";
import { mandiApi } from "@/lib/api";
import type {
  AdvisorResponse,
  BacktestResponse,
  MarketPricesResponse,
  StatePriceGapsResponse,
  StrategyMetrics,
} from "@/lib/types";

vi.mock("recharts", async () => {
  const actual = await vi.importActual<typeof import("recharts")>("recharts");
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div data-testid="recharts-responsive">{children}</div>
    ),
  };
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeAdvisor(opts: {
  commodity: string;
  market: string;
  state: string;
  verdictLabel: string;
  verdictClass?: "SELL NOW" | "WAIT" | "NEUTRAL";
  validationStatus?: string;
  cv12m?: number | null;
}): AdvisorResponse {
  return {
    cached: false,
    is_demo: true,
    signal: {
      signal_id: 1,
      commodity_id: 1,
      commodity: opts.commodity,
      category: "Vegetable",
      storable: false,
      market_id: 1,
      market: opts.market,
      district: "D",
      state: opts.state,
      signal_date: "2025-06-30",
      verdict_class: opts.verdictClass ?? "WAIT",
      verdict_label: opts.verdictLabel,
      current_modal_price: 2200,
      price_change_4w_pct: 5.2,
      vs_seasonal_avg_pct: -3.4,
      arrivals_trend: "falling",
      arrivals_change_14d_pct: -12.5,
      cv_12m: opts.cv12m === undefined ? 0.3218 : opts.cv12m,
      state_price_gap_pct: 24,
      evidence_completeness: "HIGH",
      observation_count_90d: 38,
      analysis_window_start: "2024-07-01",
      analysis_window_end: "2025-06-30",
      data_sources: ["demo_seed"],
      insufficient_evidence: false,
      rule_breakdown: { triggers: [] },
      thresholds_used: {},
    },
    briefing: {
      briefing_id: 2,
      verdict: opts.verdictLabel,
      reason_en: `${opts.commodity} reason`,
      caution_en: "Caution",
      reason_hi: "कारण",
      caution_hi: "सावधानी",
      validation_status: opts.validationStatus ?? "validated_ai",
      validation_errors: opts.validationStatus === "failed" ? ["price mismatch"] : [],
      model_used: "gemini",
      used_fallback: opts.validationStatus === "fallback_template",
    },
    genai_input: {},
    history_series: [],
    recent_briefings: [],
  };
}

const METRICS: StrategyMetrics = {
  total_cases: 40,
  decisive_cases: 30,
  neutral_cases: 10,
  neutral_coverage_pct: 25,
  accuracy_pct: 60,
  wait_calls: 15,
  wait_precision_pct: 55,
  sell_calls: 15,
  sell_precision_pct: 65,
  avg_wait_move_pct: 2.1,
  avg_sell_move_pct: -1.4,
  avg_captured_move_pct: 1.2,
};

function makeBacktest(page: number, label = "demo_computed"): BacktestResponse {
  return {
    is_demo: true,
    source_label: label,
    used_precomputed_csv: false,
    eval_days: 180,
    step_days: 7,
    thresholds: {},
    mandimitra: METRICS,
    baseline_always_sell: METRICS,
    baseline_momentum_only: METRICS,
    matched_always_sell_accuracy_pct: 50,
    by_commodity: [],
    by_storability: [],
    sample_rows: [],
    pagination: { page, page_size: 20, total_items: 60, total_pages: 3 },
  };
}

function makeMarketPrices(totalObservations: number): MarketPricesResponse {
  return {
    summary: {
      min_price: 1000,
      max_price: 3000,
      avg_modal_price: 2000,
      latest_min_price: 1500,
      latest_modal_price: 2100,
      latest_max_price: 2600,
      latest_date: "2025-06-30",
      min_date: "2024-01-01",
      max_date: "2025-06-30",
      total_observations: totalObservations,
      outliers_flagged: 0,
      sources: ["demo_seed"],
      available_varieties: [],
      arrivals_available: false,
      total_arrivals_tonnes: null,
    },
    daily_series: [],
    outlier_points: [],
    market_comparison: [],
    arrivals_records: [],
    items: [
      {
        arrival_date: "2025-06-30",
        state: "Maharashtra",
        district: "Nashik",
        market: "Lasalgaon",
        commodity: "Onion",
        variety: "Red",
        grade: "FAQ",
        min_price: 1500,
        max_price: 2600,
        modal_price: 2100,
        outlier_flag: false,
        source_name: "demo_seed",
      },
    ],
    pagination: { page: 1, page_size: 20, total_items: 60, total_pages: 3 },
  };
}

function makeStateGaps(highestState: string): StatePriceGapsResponse {
  return {
    commodity: "Onion",
    items: [
      {
        commodity_id: 1,
        commodity_name: "Onion",
        year: 2025,
        month: 6,
        highest_state: highestState,
        highest_avg_price: 2500,
        lowest_state: "Maharashtra",
        lowest_avg_price: 2000,
        price_spread: 500,
        pct_difference: 25,
        state_count: 2,
      },
    ],
    state_rankings: [],
    monthly_state_trends: [],
    available_states: ["Karnataka", "Maharashtra"],
    default_states: ["Karnataka", "Maharashtra"],
  };
}

function mockPriceAnalyticsApis() {
  vi.spyOn(mandiApi, "getCommodities").mockResolvedValue({
    items: [{ commodity_id: 1, commodity_name: "Onion", category: "Vegetable", storable: true }],
    total: 1,
  });
  vi.spyOn(mandiApi, "getPriceVolatility").mockResolvedValue({
    months: 12,
    min_observations: 10,
    total: 1,
    items: [
      {
        commodity_id: 1,
        commodity_name: "Onion",
        category: "Vegetable",
        storable: true,
        observation_count: 120,
        avg_modal_price: 2000,
        stddev_modal_price: 643.6,
        cv: 0.3218,
        min_modal_price: 900,
        max_modal_price: 3400,
        window_end: "2025-06-30",
        window_start: "2024-07-01",
        volatility_rank: 1,
      },
    ],
  });
  vi.spyOn(mandiApi, "getMonthlyTrends").mockResolvedValue({
    commodity: "Onion",
    total: 2,
    items: [2024, 2025].map((year) => ({
      commodity_id: 1,
      commodity_name: "Onion",
      year,
      month: 6,
      month_start: `${year}-06-01`,
      avg_modal_price: 2000,
      prev_month_price: 1900,
      mom_abs_change: 100,
      mom_pct_change: 5.26,
      ma_3m: 1950,
      observation_count: 30,
    })),
  });
  vi.spyOn(mandiApi, "getArrivalsVsPrice").mockResolvedValue({
    commodity: "Onion",
    has_arrivals_data: false,
    items: [],
    total: 0,
  });
  vi.spyOn(mandiApi, "getSeasonality").mockResolvedValue({
    commodity: "Onion",
    total: 3,
    items: [
      [1, "Jan", 82.5],
      [6, "Jun", 100],
      [10, "Oct", 128.4],
    ].map(([month, month_name, seasonal_index], idx) => ({
      commodity_id: 1,
      commodity_name: "Onion",
      month: month as number,
      month_name: month_name as string,
      month_avg_price: 2000,
      annual_avg_price: 2000,
      seasonal_index: seasonal_index as number,
      peak_month_rank: idx + 1,
      observation_count: 30,
    })),
  });
  vi.spyOn(mandiApi, "getBestMarkets").mockResolvedValue({
    commodity: "Onion",
    state: null,
    min_reports: 2,
    items: [],
    total: 0,
  });
  vi.spyOn(mandiApi, "getProductionVsPrice").mockResolvedValue({
    commodity: "Onion",
    kpis: { total_area_ha: 0, total_production_t: 0, avg_yield_t_ha: 0 },
    detail: [],
    correlations: [],
    state_production: [],
    mapping_coverage: [],
  });
}

// ---------------------------------------------------------------------------
// Phase 1 — CV percentage scaling and seasonal index baseline
// ---------------------------------------------------------------------------

describe("CV percentage conversion", () => {
  it("converts the API decimal ratio 0.3218 to 32.18% exactly once", () => {
    expect(cvToPct(0.3218)).toBe(32.18);
    expect(formatCvPct(0.3218)).toBe("32.18%");
    expect(formatCvPct(0.21)).toBe("21.00%");
    expect(formatCvPct(null)).toBe("N/A");
    expect(cvToPct(undefined)).toBeNull();
  });

  it("renders Q1 volatility table CV as a percentage, not a raw decimal", async () => {
    mockPriceAnalyticsApis();
    vi.spyOn(mandiApi, "getStatePriceGaps").mockResolvedValue(makeStateGaps("Karnataka"));
    render(<PriceAnalyticsPage />);

    expect(await screen.findByText("32.18%")).toBeInTheDocument();
    expect(screen.queryByText("0.3%")).not.toBeInTheDocument();
  });
});

describe("Seasonal index baseline (centered on 100)", () => {
  it("classifies against 105 / 95 thresholds and colours accordingly", () => {
    expect(SEASONAL_BASELINE).toBe(100);
    expect(seasonalIndexTone(105)).toBe("above");
    expect(seasonalIndexTone(128.4)).toBe("above");
    expect(seasonalIndexTone(100)).toBe("normal");
    expect(seasonalIndexTone(104.9)).toBe("normal");
    expect(seasonalIndexTone(95)).toBe("below");
    expect(seasonalIndexTone(82.5)).toBe("below");
    expect(seasonalIndexColor(110)).toBe(CHART_COLORS.green);
    expect(seasonalIndexColor(90)).toBe(CHART_COLORS.terracotta);
    expect(seasonalIndexColor(100)).toBe(CHART_COLORS.gold);
  });

  it("produces a Y domain that contains 80–130 values and the 100 baseline", () => {
    const [lo, hi] = seasonalIndexDomain([80, 100, 130]);
    expect(lo).toBeLessThanOrEqual(80);
    expect(hi).toBeGreaterThanOrEqual(130);
    expect(lo).toBeLessThan(SEASONAL_BASELINE);
    expect(hi).toBeGreaterThan(SEASONAL_BASELINE);
    // A flat series still shows the 95–105 band around the baseline.
    const [flatLo, flatHi] = seasonalIndexDomain([100, 100]);
    expect(flatLo).toBeLessThanOrEqual(95);
    expect(flatHi).toBeGreaterThanOrEqual(105);
  });

  it("labels the Q5 chart with the 100 baseline subtitle", async () => {
    mockPriceAnalyticsApis();
    vi.spyOn(mandiApi, "getStatePriceGaps").mockResolvedValue(makeStateGaps("Karnataka"));
    render(<PriceAnalyticsPage />);
    await screen.findByText("32.18%");

    fireEvent.mouseDown(screen.getByRole("tab", { name: /Q5/ }));
    expect(await screen.findByText(/100 = annual commodity baseline/)).toBeInTheDocument();
    expect(screen.getByText(/≥ 105 mark seasonal price peaks; values ≤ 95 mark seasonal lows/)).toBeInTheDocument();
    expect(screen.queryByText(/1\.00 = annual/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Phase 2 — AI Advisor
// ---------------------------------------------------------------------------

describe("AI validation status mapping", () => {
  it("maps every backend validation_status to the correct tone", () => {
    expect(describeValidationStatus("validated_ai").tone).toBe("success");
    expect(describeValidationStatus("validated_ai_retry")).toMatchObject({ tone: "success" });
    expect(describeValidationStatus("validated_ai_retry").label).toMatch(/retry/i);
    expect(describeValidationStatus("fallback_template").tone).toBe("info");
    expect(describeValidationStatus("failed").tone).toBe("error");
    expect(describeValidationStatus("something_else").tone).toBe("error");
  });

  it.each([
    ["validated_ai", "success"],
    ["validated_ai_retry", "success"],
    ["fallback_template", "info"],
    ["failed", "error"],
  ])("renders %s with a %s badge on the advisor page", async (status, tone) => {
    vi.spyOn(mandiApi, "getCommodities").mockResolvedValue({
      items: [{ commodity_id: 1, commodity_name: "Tomato", category: "Vegetable", storable: false }],
      total: 1,
    });
    vi.spyOn(mandiApi, "getMarkets").mockResolvedValue({
      items: [{ market_id: 1, market_name: "Lasalgaon", district: "Nashik", state: "Maharashtra" }],
      states: ["Maharashtra"],
      districts: ["Nashik"],
      total: 1,
    });
    vi.spyOn(mandiApi, "postAdvisorRecommendation").mockResolvedValue(
      makeAdvisor({
        commodity: "Tomato",
        market: "Lasalgaon",
        state: "Maharashtra",
        verdictLabel: "WAIT 1 WEEK",
        validationStatus: status,
      })
    );

    render(<AiAdvisorPage />);
    const badge = await screen.findByTestId("validation-status");
    expect(badge).toHaveAttribute("data-tone", tone);
    // cv_12m 0.3218 is shown as a percentage on the evidence card
    expect(screen.getByText("32.18%")).toBeInTheDocument();
  });
});

describe("AI Advisor commodity-switch race", () => {
  it("never submits the new commodity with the previous commodity's market and ignores stale responses", async () => {
    vi.spyOn(mandiApi, "getCommodities").mockResolvedValue({
      items: [
        { commodity_id: 1, commodity_name: "Onion", category: "Vegetable", storable: true },
        { commodity_id: 2, commodity_name: "Tomato", category: "Vegetable", storable: false },
      ],
      total: 2,
    });
    const tomatoMarkets = deferred<Awaited<ReturnType<typeof mandiApi.getMarkets>>>();
    vi.spyOn(mandiApi, "getMarkets").mockImplementation((params) => {
      if (params?.commodity === "Tomato") return tomatoMarkets.promise;
      return Promise.resolve({
        items: [{ market_id: 1, market_name: "Lasalgaon", district: "Nashik", state: "Maharashtra" }],
        states: ["Maharashtra"],
        districts: ["Nashik"],
        total: 1,
      });
    });
    const onionAdvice = deferred<AdvisorResponse>();
    const postSpy = vi.spyOn(mandiApi, "postAdvisorRecommendation").mockImplementation((req) => {
      if (req.commodity === "Onion") return onionAdvice.promise;
      return Promise.resolve(
        makeAdvisor({ commodity: "Tomato", market: "Kolar", state: "Karnataka", verdictLabel: "WAIT 1 WEEK" })
      );
    });

    render(<AiAdvisorPage />);
    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(1));
    expect(postSpy.mock.calls[0][0]).toMatchObject({ commodity: "Onion", market: "Lasalgaon" });

    // Switch commodity while the Onion recommendation and Tomato markets are both still pending.
    fireEvent.change(screen.getByLabelText("Commodity"), { target: { value: "Tomato" } });

    // The stale Onion response now arrives; it must not be displayed.
    await act(async () => {
      onionAdvice.resolve(
        makeAdvisor({
          commodity: "Onion",
          market: "Lasalgaon",
          state: "Maharashtra",
          verdictLabel: "SELL NOW",
          verdictClass: "SELL NOW",
        })
      );
    });
    expect(screen.queryByRole("heading", { name: "SELL NOW" })).not.toBeInTheDocument();
    // No request for Tomato may be sent until Tomato's own markets are known.
    expect(postSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      tomatoMarkets.resolve({
        items: [{ market_id: 7, market_name: "Kolar", district: "Kolar", state: "Karnataka" }],
        states: ["Karnataka"],
        districts: ["Kolar"],
        total: 1,
      });
    });

    expect(await screen.findByRole("heading", { name: /WAIT 1 WEEK/ })).toBeInTheDocument();
    const tomatoCalls = postSpy.mock.calls.filter(([req]) => req.commodity === "Tomato");
    expect(tomatoCalls.length).toBeGreaterThan(0);
    for (const [req] of tomatoCalls) {
      expect(req).toMatchObject({ commodity: "Tomato", market: "Kolar", state: "Karnataka" });
    }
  });
});

// ---------------------------------------------------------------------------
// Phase 2/3 — tooltip units and same-date outliers
// ---------------------------------------------------------------------------

describe("Chart tooltip formatting", () => {
  it("formats arrivals in tonnes and prices in INR/qtl", () => {
    expect(formatPriceArrivalsTooltip(1234.5, "Arrivals (t)", { dataKey: "arrivals_tonnes" })).toEqual([
      "1,234.5 t",
      "Arrivals (t)",
    ]);
    expect(formatPriceArrivalsTooltip(80, "Arrivals", { dataKey: "quantity_tonnes" })[0]).toBe("80 t");
    expect(formatPriceArrivalsTooltip(2200, "Modal Price", { dataKey: "modal_price" })).toEqual([
      "₹2,200/qtl",
      "Modal Price",
    ]);
  });

  it("keeps every outlier on the same date with its market identity", () => {
    const daily = [
      { arrival_date: "2025-06-02", min_price: 1000, modal_price: 1500, max_price: 2000, arrivals_tonnes: 10, outlier_count: 2 },
      { arrival_date: "2025-06-03", min_price: 1000, modal_price: 1500, max_price: 2000, arrivals_tonnes: null, outlier_count: 0 },
    ];
    const outliers = [
      { arrival_date: "2025-06-02", state: "Maharashtra", market: "Lasalgaon", variety: "Local", modal_price: 9000 },
      { arrival_date: "2025-06-02", state: "Karnataka", market: "Bengaluru", variety: null, modal_price: 150 },
    ];
    const { rows, outlierSlots } = buildDailyChartRows(daily, outliers);
    expect(outlierSlots).toBe(2);
    expect(rows[0].outlier_0).toBe(9000);
    expect(rows[0].outlier_1).toBe(150);
    expect(rows[1].outlier_0).toBeNull();
    expect(rows[1].outlier_1).toBeNull();

    expect(formatPriceArrivalsTooltip(9000, "outlier_0", { dataKey: "outlier_0", payload: rows[0] })).toEqual([
      "₹9,000/qtl",
      "3-IQR Outlier — Lasalgaon, Maharashtra (Local)",
    ]);
    expect(formatPriceArrivalsTooltip(150, "outlier_1", { dataKey: "outlier_1", payload: rows[0] })[1]).toBe(
      "3-IQR Outlier — Bengaluru, Karnataka"
    );
  });
});

// ---------------------------------------------------------------------------
// Phase 3 — Market Explorer
// ---------------------------------------------------------------------------

describe("Market Explorer requests, markets errors and refetch state", () => {
  function mockExplorerBasics() {
    vi.spyOn(mandiApi, "getCommodities").mockResolvedValue({
      items: [{ commodity_id: 1, commodity_name: "Onion", category: "Vegetable", storable: true }],
      total: 1,
    });
  }

  it("requests paginated server-side data without a truncating limit and keeps data visible while refetching", async () => {
    mockExplorerBasics();
    vi.spyOn(mandiApi, "getMarkets").mockResolvedValue({
      items: [{ market_id: 1, market_name: "Lasalgaon", district: "Nashik", state: "Maharashtra" }],
      states: ["Maharashtra"],
      districts: ["Nashik"],
      total: 1,
    });
    const page2 = deferred<MarketPricesResponse>();
    const pricesSpy = vi
      .spyOn(mandiApi, "getMarketPrices")
      .mockResolvedValueOnce(makeMarketPrices(12345))
      .mockReturnValueOnce(page2.promise);

    render(<MarketExplorerPage />);
    expect(await screen.findByText("12,345")).toBeInTheDocument();
    const firstParams = pricesSpy.mock.calls[0][0] ?? {};
    expect(firstParams).not.toHaveProperty("limit");
    expect(firstParams).toMatchObject({ commodity: "Onion", page: 1, page_size: 20 });

    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    await waitFor(() => expect(pricesSpy).toHaveBeenCalledTimes(2));
    expect(pricesSpy.mock.calls[1][0]).toMatchObject({ page: 2 });
    expect(screen.getByText(/Updating mandi prices for the selected filters/)).toBeInTheDocument();
    // Previous results stay on screen during the refetch instead of an empty state.
    expect(screen.getByText("12,345")).toBeInTheDocument();
    expect(screen.queryByText(/No mandi price records match/)).not.toBeInTheDocument();

    await act(async () => {
      page2.resolve(makeMarketPrices(12345));
    });
    expect(screen.queryByText(/Updating mandi prices/)).not.toBeInTheDocument();
  });

  it("shows a visible, retryable error when market options fail to load", async () => {
    mockExplorerBasics();
    const marketsSpy = vi
      .spyOn(mandiApi, "getMarkets")
      .mockRejectedValueOnce(new Error("markets endpoint down"))
      .mockResolvedValue({
        items: [{ market_id: 1, market_name: "Lasalgaon", district: "Nashik", state: "Maharashtra" }],
        states: ["Maharashtra"],
        districts: ["Nashik"],
        total: 1,
      });
    vi.spyOn(mandiApi, "getMarketPrices").mockResolvedValue(makeMarketPrices(10));

    render(<MarketExplorerPage />);
    expect(await screen.findByText(/Could not load state, district, and market options/)).toBeInTheDocument();
    expect(screen.getByText("markets endpoint down")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(marketsSpy).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.queryByText(/Could not load state, district, and market options/)).not.toBeInTheDocument()
    );
  });
});

// ---------------------------------------------------------------------------
// Phase 5 — Price Analytics Q2 filters
// ---------------------------------------------------------------------------

describe("Price Analytics Q2 filters", () => {
  it("propagates year, month and states to the state-price-gaps API and shows refetch feedback", async () => {
    mockPriceAnalyticsApis();
    const pending = deferred<StatePriceGapsResponse>();
    const gapsSpy = vi.spyOn(mandiApi, "getStatePriceGaps").mockImplementation((params) => {
      if (params?.month === 6) return pending.promise;
      return Promise.resolve(makeStateGaps(params?.year === 2025 ? "Gujarat" : "Karnataka"));
    });

    render(<PriceAnalyticsPage />);
    await waitFor(() => expect(gapsSpy).toHaveBeenCalled());
    expect(gapsSpy.mock.calls[0][0]).toEqual({
      commodity: "Onion",
      year: undefined,
      month: undefined,
      states: undefined,
    });

    fireEvent.mouseDown(screen.getByRole("tab", { name: /Q2/ }));
    await screen.findByLabelText("Gap Table Year");
    await waitFor(() =>
      expect(within(screen.getByLabelText("Gap Table Year")).getByRole("option", { name: "2025" })).toBeInTheDocument()
    );

    fireEvent.change(screen.getByLabelText("Gap Table Year"), { target: { value: "2025" } });
    await waitFor(() =>
      expect(gapsSpy).toHaveBeenLastCalledWith(
        { commodity: "Onion", year: 2025, month: undefined, states: undefined },
        expect.any(AbortSignal)
      )
    );
    expect(await screen.findByText(/^Gujarat \(/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Karnataka" }));
    await waitFor(() =>
      expect(gapsSpy).toHaveBeenLastCalledWith(
        { commodity: "Onion", year: 2025, month: undefined, states: ["Karnataka"] },
        expect.any(AbortSignal)
      )
    );
    expect(screen.getByRole("button", { name: "Karnataka" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.change(screen.getByLabelText("Gap Table Month"), { target: { value: "6" } });
    await waitFor(() =>
      expect(gapsSpy).toHaveBeenLastCalledWith(
        { commodity: "Onion", year: 2025, month: 6, states: ["Karnataka"] },
        expect.any(AbortSignal)
      )
    );
    // Refetch in flight: indicator visible, previous Q2 results preserved.
    expect(screen.getByText(/Re-running warehouse analytics/)).toBeInTheDocument();
    expect(screen.getByText(/^Gujarat \(/)).toBeInTheDocument();

    await act(async () => {
      pending.resolve(makeStateGaps("Punjab"));
    });
    expect(await screen.findByText(/^Punjab \(/)).toBeInTheDocument();
    expect(screen.queryByText(/Re-running warehouse analytics/)).not.toBeInTheDocument();
  });

  it("isolates a Q1 failure from the commodity-scoped tabs and retries only Q1", async () => {
    mockPriceAnalyticsApis();
    vi.spyOn(mandiApi, "getStatePriceGaps").mockResolvedValue(makeStateGaps("Karnataka"));
    const volSpy = vi
      .spyOn(mandiApi, "getPriceVolatility")
      .mockRejectedValueOnce(new Error("q1 exploded"))
      .mockResolvedValue({ months: 12, min_observations: 10, total: 0, items: [] });

    render(<PriceAnalyticsPage />);
    expect(await screen.findByText("q1 exploded")).toBeInTheDocument();
    const trendCalls = vi.mocked(mandiApi.getMonthlyTrends).mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(volSpy).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText("q1 exploded")).not.toBeInTheDocument());
    expect(vi.mocked(mandiApi.getMonthlyTrends).mock.calls.length).toBe(trendCalls);
  });
});

// ---------------------------------------------------------------------------
// Phase 6 — Backtesting
// ---------------------------------------------------------------------------

describe("Backtesting one-shot refresh and export", () => {
  it("sends refresh=true exactly once and exports the displayed parameters", async () => {
    const refreshPending = deferred<BacktestResponse>();
    const spy = vi
      .spyOn(mandiApi, "getBacktestResults")
      .mockImplementation((params) =>
        params?.refresh ? refreshPending.promise : Promise.resolve(makeBacktest(params?.page ?? 1))
      );

    render(<BacktestingPage />);
    await screen.findByText(/Page 1 of 3/);
    expect(spy.mock.calls[0][0]).toMatchObject({ refresh: false, page: 1 });

    // Export is a button (it fetches the file so the response headers can be checked); enabled once data is shown.
    expect(screen.getByRole("button", { name: /Export CSV/ })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: /Re-run Backtest/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(spy.mock.calls[1][0]).toMatchObject({ refresh: true, page: 1 });
    // While recomputing: previous results remain, a refetch indicator shows, and export is disabled.
    expect(screen.getByText(/Loading backtest results/)).toBeInTheDocument();
    expect(screen.getByText(/Page 1 of 3/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Export CSV/ })).toBeDisabled();

    await act(async () => {
      refreshPending.resolve(makeBacktest(1, "refreshed_run"));
    });
    expect(await screen.findByText("refreshed_run")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Export CSV/ })).toBeEnabled();

    // Pagination after a refresh must reuse the refreshed results instead of recomputing.
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(3));
    expect(spy.mock.calls[2][0]).toMatchObject({ refresh: false, page: 2 });
    await screen.findByText(/Page 2 of 3/);

    const refreshCalls = spy.mock.calls.filter(([params]) => params?.refresh === true);
    expect(refreshCalls).toHaveLength(1);
  });
});
