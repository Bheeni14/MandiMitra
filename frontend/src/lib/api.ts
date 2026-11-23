import type {
  AdvisorRequest,
  AdvisorResponse,
  ArrivalsVsPriceResponse,
  BacktestResponse,
  BestMarketsResponse,
  CommoditiesResponse,
  DataSourcesStatusResponse,
  HealthResponse,
  MarketPricesResponse,
  MarketsResponse,
  MonthlyTrendsResponse,
  OverviewResponse,
  PipelineHistoryResponse,
  ProductionVsPriceResponse,
  SeasonalityResponse,
  StatePriceGapsResponse,
  VolatilityResponse,
} from "./types";

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

export function getApiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (configured && configured.trim().length > 0) {
    return configured.trim().replace(/\/+$/, "");
  }
  return DEFAULT_API_BASE_URL;
}

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

type QueryValue = string | number | boolean | null | undefined | Array<string | number>;

function buildQuery(params?: Record<string, QueryValue>): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, val] of Object.entries(params)) {
    if (val === undefined || val === null || val === "") continue;
    if (Array.isArray(val)) {
      for (const item of val) {
        if (item !== undefined && item !== null && String(item).trim() !== "") {
          search.append(key, String(item));
        }
      }
    } else {
      search.set(key, String(val));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

async function requestJson<T>(
  path: string,
  options?: {
    method?: "GET" | "POST";
    params?: Record<string, QueryValue>;
    body?: unknown;
    signal?: AbortSignal;
  }
): Promise<T> {
  const base = getApiBaseUrl();
  const qs = buildQuery(options?.params);
  const url = `${base}${path}${qs}`;

  const response = await fetch(url, {
    method: options?.method ?? "GET",
    headers: options?.body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: options?.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options?.signal,
    cache: "no-store",
  });

  if (!response.ok) {
    throw await toApiError(response);
  }

  return (await response.json()) as T;
}

async function toApiError(response: Response): Promise<ApiError> {
  let detail = `Request failed with status ${response.status}`;
  try {
    const errBody = (await response.json()) as { detail?: string };
    if (errBody?.detail) {
      detail = errBody.detail;
    }
  } catch {
    // keep default detail
  }
  return new ApiError(response.status, detail);
}

export interface BacktestExportResult {
  blob: Blob;
  filename: string;
  /** `X-Backtest-Source` response header: "precomputed_csv" or "computed" (null if the header is unreadable). */
  source: string | null;
  /** `X-Backtest-Rows` response header: number of evaluations in the exported CSV (null if unreadable). */
  rows: number | null;
}

export const mandiApi = {
  getHealth(signal?: AbortSignal): Promise<HealthResponse> {
    return requestJson<HealthResponse>("/api/health", { signal });
  },

  getOverview(params?: { commodity?: string; recent_limit?: number }, signal?: AbortSignal): Promise<OverviewResponse> {
    return requestJson<OverviewResponse>("/api/dashboard/overview", { params, signal });
  },

  getCommodities(signal?: AbortSignal): Promise<CommoditiesResponse> {
    return requestJson<CommoditiesResponse>("/api/commodities", { signal });
  },

  getMarkets(
    params?: { commodity?: string; state?: string; district?: string },
    signal?: AbortSignal
  ): Promise<MarketsResponse> {
    return requestJson<MarketsResponse>("/api/markets", { params, signal });
  },

  getMarketPrices(
    params?: {
      commodity?: string;
      state?: string;
      district?: string;
      market?: string;
      variety?: string;
      start_date?: string;
      end_date?: string;
      page?: number;
      page_size?: number;
    },
    signal?: AbortSignal
  ): Promise<MarketPricesResponse> {
    return requestJson<MarketPricesResponse>("/api/market-prices", { params, signal });
  },

  getPriceVolatility(
    params?: { min_observations?: number; months?: number },
    signal?: AbortSignal
  ): Promise<VolatilityResponse> {
    return requestJson<VolatilityResponse>("/api/analytics/price-volatility", { params, signal });
  },

  getStatePriceGaps(
    params?: { commodity?: string; year?: number; month?: number; states?: string[] },
    signal?: AbortSignal
  ): Promise<StatePriceGapsResponse> {
    return requestJson<StatePriceGapsResponse>("/api/analytics/state-price-gaps", { params, signal });
  },

  getMonthlyTrends(params?: { commodity?: string }, signal?: AbortSignal): Promise<MonthlyTrendsResponse> {
    return requestJson<MonthlyTrendsResponse>("/api/analytics/monthly-trends", { params, signal });
  },

  getArrivalsVsPrice(params?: { commodity?: string }, signal?: AbortSignal): Promise<ArrivalsVsPriceResponse> {
    return requestJson<ArrivalsVsPriceResponse>("/api/analytics/arrivals-vs-price", { params, signal });
  },

  getSeasonality(params?: { commodity?: string }, signal?: AbortSignal): Promise<SeasonalityResponse> {
    return requestJson<SeasonalityResponse>("/api/analytics/seasonality", { params, signal });
  },

  getBestMarkets(
    params?: { commodity?: string; state?: string; min_reports?: number },
    signal?: AbortSignal
  ): Promise<BestMarketsResponse> {
    return requestJson<BestMarketsResponse>("/api/analytics/best-markets", { params, signal });
  },

  getProductionVsPrice(
    params?: { commodity?: string; state?: string },
    signal?: AbortSignal
  ): Promise<ProductionVsPriceResponse> {
    return requestJson<ProductionVsPriceResponse>("/api/analytics/production-vs-price", { params, signal });
  },

  postAdvisorRecommendation(payload: AdvisorRequest, signal?: AbortSignal): Promise<AdvisorResponse> {
    return requestJson<AdvisorResponse>("/api/advisor/recommendation", {
      method: "POST",
      body: payload,
      signal,
    });
  },

  getBacktestResults(
    params?: { eval_days?: number; step_days?: number; refresh?: boolean; page?: number; page_size?: number },
    signal?: AbortSignal
  ): Promise<BacktestResponse> {
    return requestJson<BacktestResponse>("/api/backtest/results", { params, signal });
  },

  getBacktestExportUrl(params?: { eval_days?: number; step_days?: number; refresh?: boolean }): string {
    return `${getApiBaseUrl()}/api/backtest/export${buildQuery(params)}`;
  },

  async downloadBacktestExport(
    params: { eval_days: number; step_days: number },
    signal?: AbortSignal
  ): Promise<BacktestExportResult> {
    const response = await fetch(mandiApi.getBacktestExportUrl(params), { signal, cache: "no-store" });
    if (!response.ok) {
      throw await toApiError(response);
    }
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const nameMatch = /filename="?([^";]+)"?/i.exec(disposition);
    const rowsHeader = response.headers.get("X-Backtest-Rows");
    const rows = rowsHeader !== null && rowsHeader.trim() !== "" ? Number(rowsHeader) : NaN;
    return {
      blob: await response.blob(),
      filename: nameMatch?.[1] ?? `mandimitra_backtest_${params.eval_days}d_step${params.step_days}d.csv`,
      source: response.headers.get("X-Backtest-Source"),
      rows: Number.isFinite(rows) ? rows : null,
    };
  },

  getDataSourcesStatus(signal?: AbortSignal): Promise<DataSourcesStatusResponse> {
    return requestJson<DataSourcesStatusResponse>("/api/data-sources/status", { signal });
  },

  getPipelineHistory(
    params?: { runs_page?: number; runs_page_size?: number; rejects_page?: number; rejects_page_size?: number },
    signal?: AbortSignal
  ): Promise<PipelineHistoryResponse> {
    return requestJson<PipelineHistoryResponse>("/api/pipeline/history", { params, signal });
  },
};
