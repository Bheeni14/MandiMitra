import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AiAdvisorPage from "@/app/ai-advisor/page";
import BacktestingPage from "@/app/backtesting/page";
import { checkBacktestExport } from "@/lib/backtestExport";
import { mandiApi } from "@/lib/api";
import type { AdvisorResponse, BacktestResponse, StrategyMetrics } from "@/lib/types";

vi.mock("recharts", async () => {
  const actual = await vi.importActual<typeof import("recharts")>("recharts");
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div data-testid="recharts-responsive">{children}</div>
    ),
  };
});

// jsdom cannot perform real downloads; keep the real header check but stub the file save.
vi.mock("@/lib/backtestExport", async () => {
  const actual = await vi.importActual<typeof import("@/lib/backtestExport")>("@/lib/backtestExport");
  return { ...actual, saveBlobAsFile: vi.fn() };
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
// Fix 3 - AI Advisor must not show the previous market's verdict for a new market
// ---------------------------------------------------------------------------

function advisorFor(market: string, verdictLabel: string): AdvisorResponse {
  return {
    cached: false,
    is_demo: true,
    signal: {
      signal_id: 1,
      commodity_id: 1,
      commodity: "Tomato",
      category: "Vegetable",
      storable: false,
      market_id: 1,
      market,
      district: "Nashik",
      state: "Maharashtra",
      signal_date: "2025-06-30",
      verdict_class: "WAIT",
      verdict_label: verdictLabel,
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
      rule_breakdown: { triggers: [] },
      thresholds_used: {},
    },
    briefing: {
      briefing_id: 2,
      verdict: verdictLabel,
      reason_en: `Reason for ${market}`,
      caution_en: "Caution",
      reason_hi: "कारण",
      caution_hi: "सावधानी",
      validation_status: "validated_ai",
      validation_errors: [],
      model_used: "gemini",
      used_fallback: false,
    },
    genai_input: {},
    history_series: [],
    recent_briefings: [],
  };
}

function mockAdvisorBasics() {
  vi.spyOn(mandiApi, "getCommodities").mockResolvedValue({
    items: [{ commodity_id: 1, commodity_name: "Tomato", category: "Vegetable", storable: false }],
    total: 1,
  });
  vi.spyOn(mandiApi, "getMarkets").mockResolvedValue({
    items: [
      { market_id: 1, market_name: "Lasalgaon", district: "Nashik", state: "Maharashtra" },
      { market_id: 2, market_name: "Pimpalgaon", district: "Nashik", state: "Maharashtra" },
    ],
    states: ["Maharashtra"],
    districts: ["Nashik"],
    total: 2,
  });
}

describe("AI Advisor market switch (stale verdict)", () => {
  it("hides the previous market's verdict while the new market's request is in flight", async () => {
    mockAdvisorBasics();
    const pimpalgaon = deferred<AdvisorResponse>();
    vi.spyOn(mandiApi, "postAdvisorRecommendation").mockImplementation((req) =>
      req.market === "Lasalgaon"
        ? Promise.resolve(advisorFor("Lasalgaon", "SELL NOW LASALGAON"))
        : pimpalgaon.promise
    );

    render(<AiAdvisorPage />);
    expect(await screen.findByRole("heading", { name: "SELL NOW LASALGAON" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Market (Mandi)"), { target: { value: "Pimpalgaon" } });

    // Immediately gone, replaced by the loading state - not shown next to the new selection.
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "SELL NOW LASALGAON" })).not.toBeInTheDocument()
    );
    expect(screen.queryByTestId("validation-status")).not.toBeInTheDocument();
    expect(screen.getByText(/Evaluating SQL price rules/)).toBeInTheDocument();

    await act(async () => {
      pimpalgaon.resolve(advisorFor("Pimpalgaon", "WAIT PIMPALGAON"));
    });
    expect(await screen.findByRole("heading", { name: "WAIT PIMPALGAON" })).toBeInTheDocument();
    expect(screen.queryByText("Reason for Lasalgaon")).not.toBeInTheDocument();
  });

  it("shows only the error when the new market's request fails, and restores the old market's verdict when re-selected", async () => {
    mockAdvisorBasics();
    const postSpy = vi.spyOn(mandiApi, "postAdvisorRecommendation").mockImplementation((req) =>
      req.market === "Lasalgaon"
        ? Promise.resolve(advisorFor("Lasalgaon", "SELL NOW LASALGAON"))
        : Promise.reject(new Error("Could not compute a recommendation for Pimpalgaon"))
    );

    render(<AiAdvisorPage />);
    expect(await screen.findByRole("heading", { name: "SELL NOW LASALGAON" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Market (Mandi)"), { target: { value: "Pimpalgaon" } });

    expect(await screen.findByText("Could not compute a recommendation for Pimpalgaon")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "SELL NOW LASALGAON" })).not.toBeInTheDocument();
    expect(screen.queryByText("Reason for Lasalgaon")).not.toBeInTheDocument();
    expect(screen.queryByTestId("validation-status")).not.toBeInTheDocument();

    // Going back to the market that did succeed shows its own verdict again (from the client cache, no new call).
    const callsBefore = postSpy.mock.calls.length;
    fireEvent.change(screen.getByLabelText("Market (Mandi)"), { target: { value: "Lasalgaon" } });
    expect(await screen.findByRole("heading", { name: "SELL NOW LASALGAON" })).toBeInTheDocument();
    expect(postSpy.mock.calls.length).toBe(callsBefore);
    expect(screen.queryByText("Could not compute a recommendation for Pimpalgaon")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Fix 4 - export provenance: never claim a match that was not observed
// ---------------------------------------------------------------------------

describe("checkBacktestExport", () => {
  const shown = { usedPrecomputedCsv: false, totalRows: 60 };

  it("reports success only when source and row count both match, and says contents are not compared", () => {
    const res = checkBacktestExport(shown, { source: "computed", rows: 60 });
    expect(res.tone).toBe("success");
    expect(res.message).toMatch(/same source and row count/);
    expect(res.message).toMatch(/Row contents are not compared/);
  });

  it("warns when the export came from a different source than the displayed results", () => {
    const res = checkBacktestExport(shown, { source: "precomputed_csv", rows: 60 });
    expect(res.tone).toBe("warning");
    expect(res.message).toMatch(/precomputed results file/);
    expect(res.message).toMatch(/live backtest run/);
    expect(res.message).toMatch(/may not match/);
    expect(checkBacktestExport({ usedPrecomputedCsv: true, totalRows: 60 }, { source: "computed", rows: 60 }).tone).toBe(
      "warning"
    );
  });

  it("warns when the row counts differ", () => {
    const res = checkBacktestExport(shown, { source: "computed", rows: 59 });
    expect(res.tone).toBe("warning");
    expect(res.message).toMatch(/59 evaluations but 60/);
  });

  it("warns instead of claiming a match when the headers are not readable", () => {
    expect(checkBacktestExport(shown, { source: null, rows: null })).toMatchObject({ tone: "warning" });
    const partial = checkBacktestExport(shown, { source: "computed", rows: null });
    expect(partial.tone).toBe("warning");
    expect(partial.message).toMatch(/only the source could be checked/);
  });
});

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

function backtestResponse(usedPrecomputed: boolean): BacktestResponse {
  return {
    is_demo: true,
    source_label: "demo",
    used_precomputed_csv: usedPrecomputed,
    eval_days: 365,
    step_days: 7,
    thresholds: {},
    mandimitra: METRICS,
    baseline_always_sell: METRICS,
    baseline_momentum_only: METRICS,
    matched_always_sell_accuracy_pct: 50,
    by_commodity: [],
    by_storability: [],
    sample_rows: [],
    pagination: { page: 1, page_size: 20, total_items: 60, total_pages: 3 },
  };
}

function exportResult(source: string | null, rows: number | null) {
  return { blob: new Blob(["a,b\n1,2\n"]), filename: "mandimitra_backtest_365d_step7d.csv", source, rows };
}

describe("Backtesting page export check", () => {
  it("downloads the displayed window/step and confirms a matching source and row count", async () => {
    vi.spyOn(mandiApi, "getBacktestResults").mockResolvedValue(backtestResponse(false));
    const exportSpy = vi.spyOn(mandiApi, "downloadBacktestExport").mockResolvedValue(exportResult("computed", 60));

    render(<BacktestingPage />);
    await screen.findByText(/Page 1 of 3/);
    expect(screen.queryByTestId("export-check")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Export CSV/ }));
    const notice = await screen.findByTestId("export-check");
    expect(exportSpy).toHaveBeenCalledWith({ eval_days: 365, step_days: 7 });
    expect(notice).toHaveAttribute("data-tone", "success");
    expect(notice).toHaveTextContent(/Row contents are not compared/);
  });

  it("shows a warning, not a success claim, when the server falls back to a different source", async () => {
    vi.spyOn(mandiApi, "getBacktestResults").mockResolvedValue(backtestResponse(false));
    vi.spyOn(mandiApi, "downloadBacktestExport").mockResolvedValue(exportResult("precomputed_csv", 60));

    render(<BacktestingPage />);
    await screen.findByText(/Page 1 of 3/);
    fireEvent.click(screen.getByRole("button", { name: /Export CSV/ }));

    const notice = await screen.findByTestId("export-check");
    expect(notice).toHaveAttribute("data-tone", "warning");
    expect(notice).toHaveTextContent(/may not match what you see/);
    expect(notice).not.toHaveTextContent(/same source and row count/);
  });

  it("clears the export notice once a re-run changes the displayed evaluation", async () => {
    const refreshed = deferred<BacktestResponse>();
    const resultsSpy = vi
      .spyOn(mandiApi, "getBacktestResults")
      .mockImplementation((params) => (params?.refresh ? refreshed.promise : Promise.resolve(backtestResponse(false))));
    vi.spyOn(mandiApi, "downloadBacktestExport").mockResolvedValue(exportResult("precomputed_csv", 60));

    render(<BacktestingPage />);
    await screen.findByText(/Page 1 of 3/);
    fireEvent.click(screen.getByRole("button", { name: /Export CSV/ }));
    expect(await screen.findByTestId("export-check")).toHaveAttribute("data-tone", "warning");

    fireEvent.click(screen.getByRole("button", { name: /Re-run Backtest/ }));
    await waitFor(() => expect(resultsSpy).toHaveBeenCalledTimes(2));
    await act(async () => {
      refreshed.resolve(backtestResponse(false));
    });
    await waitFor(() => expect(screen.queryByTestId("export-check")).not.toBeInTheDocument());
  });

  it("shows a retryable error when the export request fails", async () => {
    vi.spyOn(mandiApi, "getBacktestResults").mockResolvedValue(backtestResponse(false));
    const exportSpy = vi
      .spyOn(mandiApi, "downloadBacktestExport")
      .mockRejectedValueOnce(new Error("export unavailable"))
      .mockResolvedValue(exportResult("computed", 60));

    render(<BacktestingPage />);
    await screen.findByText(/Page 1 of 3/);
    fireEvent.click(screen.getByRole("button", { name: /Export CSV/ }));
    expect(await screen.findByText("export unavailable")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(exportSpy).toHaveBeenCalledTimes(2));
    expect(await screen.findByTestId("export-check")).toHaveAttribute("data-tone", "success");
  });
});
