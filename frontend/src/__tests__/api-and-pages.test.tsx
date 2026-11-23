import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AiAdvisorPage from "@/app/ai-advisor/page";
import OverviewPage from "@/app/page";
import { EmptyState, ErrorState, LoadingState } from "@/components/common/StateFeedback";
import { ApiError, mandiApi } from "@/lib/api";

// ResponsiveContainer relies on ResizeObserver / layout measurements in jsdom
vi.mock("recharts", async () => {
  const actual = await vi.importActual<typeof import("recharts")>("recharts");
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div data-testid="recharts-responsive">{children}</div>
    ),
  };
});

describe("API client integration & error handling", () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("constructs query parameters cleanly and surfaces ApiError detail on non-OK HTTP responses", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "start_date must be less than or equal to end_date." }), {
        status: 422,
        headers: { "Content-Type": "application/json" },
      })
    );
    global.fetch = fetchMock as unknown as typeof fetch;

    await expect(
      mandiApi.getMarketPrices({
        commodity: "Tomato",
        state: "Maharashtra",
        start_date: "2025-06-30",
        end_date: "2025-05-01",
      })
    ).rejects.toMatchObject<Partial<ApiError>>({
      status: 422,
      detail: "start_date must be less than or equal to end_date.",
    });

    const calledUrl = String(fetchMock.mock.calls[0]?.[0]);
    expect(calledUrl).toContain("/api/market-prices?");
    expect(calledUrl).toContain("commodity=Tomato");
    expect(calledUrl).toContain("state=Maharashtra");
  });
});

describe("StateFeedback components (loading, error, empty)", () => {
  it("renders loading, error retry callback, and empty states accessibly", () => {
    const retryFn = vi.fn();

    render(
      <div>
        <LoadingState label="Fetching test prices..." />
        <ErrorState title="Backend error" message="Connection timed out" onRetry={retryFn} />
        <EmptyState title="No crops" message="Try widening filters." />
      </div>
    );

    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByText("Fetching test prices...")).toBeInTheDocument();
    expect(screen.getByText("Connection timed out")).toBeInTheDocument();
    expect(screen.getByText("No crops")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(retryFn).toHaveBeenCalledTimes(1);
  });
});

describe("OverviewPage API integration", () => {
  beforeEach(() => {
    vi.spyOn(mandiApi, "getOverview").mockResolvedValue({
      mode: "demo",
      is_demo: true,
      active_sources: ["demo_seed"],
      coverage: {
        backend: "sqlite",
        is_demo: true,
        total_price_records: 1200,
        commodities_count: 8,
        markets_count: 14,
        states_count: 6,
        min_price_date: "2024-01-01",
        max_price_date: "2025-06-30",
        total_arrival_records: 450,
        total_tonnes: 15400,
        min_arrival_date: "2024-01-01",
        max_arrival_date: "2025-06-30",
        total_apy_records: 84,
        min_year: 2023,
        max_year: 2025,
        apy_states: 6,
      },
      overall_avg_modal_price: 2350,
      latest_window_avg_modal_price: 2490,
      focus_commodity: "Tomato",
      focus_movement: {
        commodity_name: "Tomato",
        latest_month: "2025-06",
        avg_modal_price: 2490,
        prev_month_price: 2200,
        mom_abs_change: 290,
        mom_pct_change: 13.18,
        ma_3m: 2310,
      },
      available_commodities: ["Tomato", "Wheat"],
      market_highlights: [
        {
          commodity_name: "Tomato",
          metric_label: "Strongest Monthly Gain (Q3)",
          value_label: "+13.18% MoM (₹2,490.00/qtl)",
          direction: "up",
        },
      ],
      volatility: [],
      mom_trends: [],
      state_averages: [],
      state_gaps: [],
      recent_prices: [],
      latest_pipeline_run: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders demo badge and updates API query when focus commodity changes", async () => {
    render(<OverviewPage />);

    await waitFor(() => {
      expect(screen.getByText(/Simulated Demo Data/i)).toBeInTheDocument();
    });
    expect(screen.getByText("1,200")).toBeInTheDocument();

    const select = screen.getByLabelText(/Focus Commodity/i);
    fireEvent.change(select, { target: { value: "Wheat" } });

    await waitFor(() => {
      expect(mandiApi.getOverview).toHaveBeenLastCalledWith(
        { commodity: "Wheat" },
        expect.any(AbortSignal)
      );
    });
  });
});

describe("AiAdvisorPage language toggle & regenerate behavior", () => {
  beforeEach(() => {
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
    vi.spyOn(mandiApi, "postAdvisorRecommendation").mockResolvedValue({
      cached: false,
      is_demo: true,
      signal: {
        signal_id: 10,
        commodity_id: 1,
        commodity: "Tomato",
        category: "Vegetable",
        storable: false,
        market_id: 1,
        market: "Lasalgaon",
        district: "Nashik",
        state: "Maharashtra",
        signal_date: "2025-06-30",
        verdict_class: "WAIT",
        verdict_label: "WAIT 1 WEEK",
        current_modal_price: 2200,
        price_change_4w_pct: 5.2,
        vs_seasonal_avg_pct: -3.4,
        arrivals_trend: "falling",
        arrivals_change_14d_pct: -12.5,
        cv_12m: 0.21,
        state_price_gap_pct: 24,
        evidence_completeness: "HIGH",
        observation_count_90d: 38,
        analysis_window_start: "2024-07-01",
        analysis_window_end: "2025-06-30",
        data_sources: ["demo_seed"],
        insufficient_evidence: false,
        rule_breakdown: { triggers: ["4w momentum +5.2%"], seasonal_years: 2 },
        thresholds_used: {},
      },
      briefing: {
        briefing_id: 21,
        verdict: "WAIT 1 WEEK",
        reason_en: "Tomato prices in Lasalgaon rose 5.2% to Rs 2200 per quintal.",
        caution_en: "Perishable crop; do not hold past 1 week.",
        reason_hi: "लासलगांव में टमाटर का भाव 2200 रुपये प्रति क्विंटल है।",
        caution_hi: "जल्दी खराब होने वाली फसल है, केवल 1 सप्ताह रुकें।",
        validation_status: "fallback_template",
        validation_errors: [],
        model_used: "deterministic_template",
        used_fallback: true,
      },
      genai_input: { verdict: "WAIT 1 WEEK", current_modal_price: 2200 },
      history_series: [],
      recent_briefings: [],
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not duplicate POST calls on language toggle and calls force_refresh=true on Regenerate", async () => {
    render(<AiAdvisorPage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /WAIT 1 WEEK/i })).toBeInTheDocument();
    });
    expect(mandiApi.postAdvisorRecommendation).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/English Explanation/i)).toBeInTheDocument();
    expect(screen.getByText(/हिंदी सारांश/i)).toBeInTheDocument();

    // Switch language to Hindi -> must NOT trigger another API call
    fireEvent.click(screen.getByRole("button", { name: "Hindi" }));
    expect(screen.queryByText(/English Explanation/i)).not.toBeInTheDocument();
    expect(screen.getByText(/हिंदी सारांश/i)).toBeInTheDocument();
    expect(mandiApi.postAdvisorRecommendation).toHaveBeenCalledTimes(1);

    // Click Regenerate Briefing -> sends force_refresh: true
    fireEvent.click(screen.getByRole("button", { name: /Regenerate Briefing/i }));
    await waitFor(() => {
      expect(mandiApi.postAdvisorRecommendation).toHaveBeenCalledTimes(2);
    });
    expect(mandiApi.postAdvisorRecommendation).toHaveBeenLastCalledWith(
      {
        commodity: "Tomato",
        market: "Lasalgaon",
        state: "Maharashtra",
        force_refresh: true,
      },
      undefined
    );
  });
});
