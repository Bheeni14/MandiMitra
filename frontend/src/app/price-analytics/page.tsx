"use client";

import * as React from "react";
import { BarChart3 } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import {
  CHART_COLORS,
  cvToPct,
  formatCvPct,
  formatTooltipInr,
  formatTooltipPct,
  formatTooltipPct2,
  SEASONAL_BASELINE,
  SEASONAL_HIGH,
  SEASONAL_LOW,
  seasonalIndexColor,
  seasonalIndexDomain,
} from "@/components/charts/chartHelpers";
import {
  ChartCard,
  EmptyState,
  ErrorState,
  KpiCard,
  LoadingState,
  RefetchIndicator,
} from "@/components/common/StateFeedback";
import { PageHeader } from "@/components/common/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { SelectField } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { mandiApi } from "@/lib/api";
import type {
  ArrivalsVsPriceResponse,
  BestMarketsResponse,
  MonthlyTrendsResponse,
  ProductionVsPriceResponse,
  SeasonalityResponse,
  StatePriceGapsResponse,
  VolatilityResponse,
} from "@/lib/types";
import { formatINR, formatNumber } from "@/lib/utils";

const ALL = "All";

const MONTH_OPTIONS = [
  { value: ALL, label: "All months" },
  ...["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].map((m, i) => ({
    value: String(i + 1),
    label: m,
  })),
];

const STATE_PALETTE = [
  CHART_COLORS.green,
  CHART_COLORS.gold,
  CHART_COLORS.terracotta,
  CHART_COLORS.teal,
  "#3B82F6",
];

export default function PriceAnalyticsPage() {
  const [commodities, setCommodities] = React.useState<string[]>([]);
  const [selectedCommodity, setSelectedCommodity] = React.useState<string>("");
  const [volMonths, setVolMonths] = React.useState<number>(12);
  const [bestMarketState, setBestMarketState] = React.useState<string>("All");
  const [bestMarketMinReports, setBestMarketMinReports] = React.useState<number>(2);
  const [prodStateFilter, setProdStateFilter] = React.useState<string>("All");

  const [q2Year, setQ2Year] = React.useState<string>(ALL);
  const [q2Month, setQ2Month] = React.useState<string>(ALL);
  const [q2States, setQ2States] = React.useState<string[]>([]);

  const [commoditiesLoaded, setCommoditiesLoaded] = React.useState<boolean>(false);
  const [initError, setInitError] = React.useState<string | null>(null);

  const [q1ReloadTick, setQ1ReloadTick] = React.useState<number>(0);
  const q1Key = `${volMonths}|${q1ReloadTick}`;
  const [q1State, setQ1State] = React.useState<{
    key: string;
    data: VolatilityResponse | null;
    error: string | null;
  }>({ key: "", data: null, error: null });

  const [q2ReloadTick, setQ2ReloadTick] = React.useState<number>(0);
  const q2Key = `${selectedCommodity}|${q2Year}|${q2Month}|${q2States.join(",")}|${q2ReloadTick}`;
  const [q2State, setQ2State] = React.useState<{
    key: string;
    data: StatePriceGapsResponse | null;
    error: string | null;
  }>({ key: "", data: null, error: null });

  const [reloadTick, setReloadTick] = React.useState<number>(0);
  const requestKey = `${selectedCommodity}|${bestMarketState}|${bestMarketMinReports}|${prodStateFilter}|${reloadTick}`;
  const [bundleState, setBundleState] = React.useState<{
    key: string;
    monthlyTrends: MonthlyTrendsResponse | null;
    arrivalsVsPrice: ArrivalsVsPriceResponse | null;
    seasonality: SeasonalityResponse | null;
    bestMarkets: BestMarketsResponse | null;
    productionVsPrice: ProductionVsPriceResponse | null;
    error: string | null;
  }>({
    key: "",
    monthlyTrends: null,
    arrivalsVsPrice: null,
    seasonality: null,
    bestMarkets: null,
    productionVsPrice: null,
    error: null,
  });

  React.useEffect(() => {
    const controller = new AbortController();
    mandiApi
      .getCommodities(controller.signal)
      .then((res) => {
        const names = res.items.map((c) => c.commodity_name);
        setCommodities(names);
        setCommoditiesLoaded(true);
        if (names.length > 0) {
          setSelectedCommodity((prev) => prev || (names.includes("Onion") ? "Onion" : names[0]));
        }
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setInitError(err instanceof Error ? err.message : "Failed to load commodities");
      });
    return () => controller.abort();
  }, []);

  // Load Q1 volatility (independent of commodity selection)
  React.useEffect(() => {
    const controller = new AbortController();
    let active = true;
    mandiApi
      .getPriceVolatility({ min_observations: 10, months: volMonths }, controller.signal)
      .then((res) => {
        if (active) setQ1State({ key: q1Key, data: res, error: null });
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (!active) return;
        setQ1State((prev) => ({
          key: q1Key,
          data: prev.data,
          error: err instanceof Error ? err.message : "Failed to fetch Q1 volatility",
        }));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [volMonths, q1ReloadTick, q1Key]);

  // Load Q2 state price gaps with its own year / month / states filters
  React.useEffect(() => {
    if (!selectedCommodity) return;
    const controller = new AbortController();
    let active = true;
    mandiApi
      .getStatePriceGaps(
        {
          commodity: selectedCommodity,
          year: q2Year !== ALL ? Number(q2Year) : undefined,
          month: q2Month !== ALL ? Number(q2Month) : undefined,
          states: q2States.length > 0 ? q2States : undefined,
        },
        controller.signal
      )
      .then((res) => {
        if (active) setQ2State({ key: q2Key, data: res, error: null });
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (!active) return;
        setQ2State((prev) => ({
          key: q2Key,
          data: prev.data,
          error: err instanceof Error ? err.message : "Failed to fetch Q2 state price gaps",
        }));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [selectedCommodity, q2Year, q2Month, q2States, q2ReloadTick, q2Key]);

  // Load commodity-scoped analytics Q3-Q7
  React.useEffect(() => {
    if (!selectedCommodity) return;
    const controller = new AbortController();
    let active = true;

    Promise.all([
      mandiApi.getMonthlyTrends({ commodity: selectedCommodity }, controller.signal),
      mandiApi.getArrivalsVsPrice({ commodity: selectedCommodity }, controller.signal),
      mandiApi.getSeasonality({ commodity: selectedCommodity }, controller.signal),
      mandiApi.getBestMarkets(
        {
          commodity: selectedCommodity,
          state: bestMarketState !== "All" ? bestMarketState : undefined,
          min_reports: bestMarketMinReports,
        },
        controller.signal
      ),
      mandiApi.getProductionVsPrice(
        {
          commodity: selectedCommodity,
          state: prodStateFilter !== "All" ? prodStateFilter : undefined,
        },
        controller.signal
      ),
    ])
      .then(([q3, q4, q5, q6, q7]) => {
        if (!active) return;
        setBundleState({
          key: requestKey,
          monthlyTrends: q3,
          arrivalsVsPrice: q4,
          seasonality: q5,
          bestMarkets: q6,
          productionVsPrice: q7,
          error: null,
        });
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (!active) return;
        setBundleState((prev) => ({
          ...prev,
          key: requestKey,
          error: err instanceof Error ? err.message : "Failed to load SQL price analytics",
        }));
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [selectedCommodity, bestMarketState, bestMarketMinReports, prodStateFilter, reloadTick, requestKey]);

  const { monthlyTrends, arrivalsVsPrice, seasonality, bestMarkets, productionVsPrice } = bundleState;
  const stateGaps = q2State.data;
  const volatility = q1State.data;
  const commoditiesPending = !initError && !commoditiesLoaded;
  const q1Loading = q1State.key !== q1Key;
  const q2Loading = commoditiesPending || (selectedCommodity !== "" && q2State.key !== q2Key);
  const bundleLoading = commoditiesPending || (selectedCommodity !== "" && bundleState.key !== requestKey);
  const refetching =
    (q1Loading && volatility !== null) || (q2Loading && stateGaps !== null) || (bundleLoading && monthlyTrends !== null);

  const volatilityChartRows = React.useMemo(
    () => (volatility?.items ?? []).map((v) => ({ ...v, cv_pct: cvToPct(v.cv) })),
    [volatility]
  );

  // Pivot monthly_state_trends (already restricted server-side to the chosen states) for multi-line comparison
  const stateComparisonChart = React.useMemo(() => {
    if (!stateGaps) return { rows: [] as Array<Record<string, string | number>>, stateKeys: [] as string[] };
    const stateKeys = Array.from(new Set(stateGaps.monthly_state_trends.map((r) => r.state)));
    const byMonth = new Map<string, Record<string, string | number>>();
    for (const row of stateGaps.monthly_state_trends) {
      const existing = byMonth.get(row.month) ?? { month: row.month };
      existing[row.state] = row.avg_modal_price;
      byMonth.set(row.month, existing);
    }
    const rows = Array.from(byMonth.values()).sort((a, b) => String(a.month).localeCompare(String(b.month)));
    return { rows, stateKeys };
  }, [stateGaps]);

  const q2YearOptions = React.useMemo(() => {
    const years = Array.from(new Set((monthlyTrends?.items ?? []).map((m) => m.year))).sort((a, b) => b - a);
    return [{ value: ALL, label: "All years" }, ...years.map((y) => ({ value: String(y), label: String(y) }))];
  }, [monthlyTrends]);

  const toggleQ2State = (st: string) => {
    setQ2States((prev) => (prev.includes(st) ? prev.filter((s) => s !== st) : [...prev, st].sort()));
  };

  const bestMarketStateOptions = React.useMemo(() => {
    const states = stateGaps?.available_states ?? [];
    return [{ value: "All", label: "All states" }, ...states.map((s) => ({ value: s, label: s }))];
  }, [stateGaps?.available_states]);

  const prodStateOptions = React.useMemo(() => {
    const states = Array.from(
      new Set([
        ...(productionVsPrice?.state_production.map((s) => s.state) ?? []),
        ...(stateGaps?.available_states ?? []),
      ])
    ).sort();
    return [{ value: "All", label: "All states" }, ...states.map((s) => ({ value: s, label: s }))];
  }, [productionVsPrice?.state_production, stateGaps?.available_states]);

  const mappedCount = React.useMemo(
    () => (productionVsPrice?.mapping_coverage ?? []).filter((m) => m.is_mapped).length,
    [productionVsPrice?.mapping_coverage]
  );

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Markets"
        title="Price Analytics"
        icon={BarChart3}
        description="Deeper price insights for a crop — volatility, state price gaps, trends, arrivals, seasonality, best markets and production."
        actions={
          <div className="w-full sm:w-60">
            <SelectField
              label="Commodity"
              value={selectedCommodity}
              onChange={(e) => {
                setSelectedCommodity(e.target.value);
                setBestMarketState("All");
                setProdStateFilter("All");
                setQ2Year(ALL);
                setQ2Month(ALL);
                setQ2States([]);
              }}
              options={commodities.map((c) => ({ value: c, label: c }))}
            />
          </div>
        }
      />

      {initError ? <ErrorState title="Could not load commodities" message={initError} /> : null}
      {q1State.error ? (
        <ErrorState
          title="Could not load price volatility"
          message={q1State.error}
          onRetry={() => setQ1ReloadTick((t) => t + 1)}
        />
      ) : null}
      {q2State.error ? (
        <ErrorState
          title="Could not load state price gaps"
          message={q2State.error}
          onRetry={() => setQ2ReloadTick((t) => t + 1)}
        />
      ) : null}
      {bundleState.error ? (
        <ErrorState
          title="Could not load price analytics"
          message={bundleState.error}
          onRetry={() => setReloadTick((t) => t + 1)}
        />
      ) : null}

      {refetching ? <RefetchIndicator label="Re-running warehouse analytics for the updated filters..." /> : null}

      <Tabs defaultValue="volatility" className="w-full">
        <TabsList className="w-full justify-start lg:w-auto">
          <TabsTrigger value="volatility">
            Volatility
            <span className="text-[10px] font-semibold opacity-60">Q1</span>
          </TabsTrigger>
          <TabsTrigger value="state-gaps">
            State Gaps
            <span className="text-[10px] font-semibold opacity-60">Q2</span>
          </TabsTrigger>
          <TabsTrigger value="trends">
            Monthly Trend
            <span className="text-[10px] font-semibold opacity-60">Q3</span>
          </TabsTrigger>
          <TabsTrigger value="arrivals">
            Arrivals vs Price
            <span className="text-[10px] font-semibold opacity-60">Q4</span>
          </TabsTrigger>
          <TabsTrigger value="seasonality">
            Seasonality
            <span className="text-[10px] font-semibold opacity-60">Q5</span>
          </TabsTrigger>
          <TabsTrigger value="best-markets">
            Best Markets
            <span className="text-[10px] font-semibold opacity-60">Q6</span>
          </TabsTrigger>
          <TabsTrigger value="production">
            Production
            <span className="text-[10px] font-semibold opacity-60">Q7</span>
          </TabsTrigger>
        </TabsList>

        {/* Q1 Volatility */}
        <TabsContent value="volatility" className="space-y-4">
          <Card>
            <CardContent className="flex flex-wrap items-end justify-between gap-4 p-4">
              <p className="text-sm text-muted-foreground">
                How much each crop&apos;s price swings around its average. A higher percentage means less predictable prices.
              </p>
              <div className="w-44">
                <SelectField
                  label="Period"
                  value={String(volMonths)}
                  onChange={(e) => setVolMonths(Number(e.target.value))}
                  options={[
                    { value: "3", label: "Last 3 months" },
                    { value: "6", label: "Last 6 months" },
                    { value: "12", label: "Last 12 months" },
                    { value: "24", label: "Last 24 months" },
                  ]}
                />
              </div>
            </CardContent>
          </Card>

          {q1Loading && !volatility ? (
            <LoadingState label="Computing Q1 price volatility..." />
          ) : !volatility || volatility.items.length === 0 ? (
            <EmptyState message="Not enough observations to compute price volatility." />
          ) : (
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <ChartCard
                title={`Price swing ranking — last ${volatility.months} months`}
                description="Top 15 crops by coefficient of variation (%). Higher means wider price swings."
              >
                <div className="h-80 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={volatilityChartRows.slice(0, 15)}
                      layout="vertical"
                      margin={{ top: 8, right: 20, left: 28, bottom: 4 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                      <XAxis type="number" unit="%" tick={{ fontSize: 11 }} />
                      <YAxis type="category" dataKey="commodity_name" width={105} tick={{ fontSize: 11 }} />
                      <Tooltip formatter={formatTooltipPct2} />
                      <Legend />
                      <Bar dataKey="cv_pct" name="CV (%)" fill={CHART_COLORS.terracotta} radius={[0, 4, 4, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>

              <ChartCard
                title="Volatility breakdown"
                description="Every crop, ranked. Scroll for more."
              >
                <div className="max-h-[440px] overflow-y-auto rounded-xl">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Rank</TableHead>
                        <TableHead>Commodity</TableHead>
                        <TableHead className="text-right">CV %</TableHead>
                        <TableHead className="text-right">Mean</TableHead>
                        <TableHead className="text-right">StdDev</TableHead>
                        <TableHead className="text-right">Obs</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {volatility.items.map((r) => (
                        <TableRow key={r.commodity_id}>
                          <TableCell>#{r.volatility_rank}</TableCell>
                          <TableCell className="font-medium">{r.commodity_name}</TableCell>
                          <TableCell className="text-right font-semibold text-[#C65132]">
                            {r.cv !== null && r.cv !== undefined ? formatCvPct(r.cv) : "—"}
                          </TableCell>
                          <TableCell className="text-right">{formatINR(r.avg_modal_price)}</TableCell>
                          <TableCell className="text-right">{formatINR(r.stddev_modal_price)}</TableCell>
                          <TableCell className="text-right">{formatNumber(r.observation_count)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </ChartCard>
            </div>
          )}
        </TabsContent>

        {/* Q2 State Price Gaps */}
        <TabsContent value="state-gaps" className="space-y-4">
          <Card>
            <CardContent className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
              <SelectField
                label="Gap Table Year"
                value={q2Year}
                onChange={(e) => setQ2Year(e.target.value)}
                options={q2YearOptions}
              />
              <SelectField
                label="Gap Table Month"
                value={q2Month}
                onChange={(e) => setQ2Month(e.target.value)}
                options={MONTH_OPTIONS}
              />
              <p className="flex items-end text-xs text-muted-foreground">
                Year and month filter the gap table. The states you pick below are compared in the trend chart.
              </p>
              <div className="sm:col-span-2 lg:col-span-3">
                <span className="text-xs font-medium text-muted-foreground">
                  Compare states
                </span>
                <div className="mt-1.5 flex flex-wrap gap-1.5" role="group" aria-label="Compare states">
                  {(stateGaps?.available_states ?? []).map((st) => {
                    const active = q2States.includes(st);
                    return (
                      <button
                        key={st}
                        type="button"
                        aria-pressed={active}
                        onClick={() => toggleQ2State(st)}
                        className={`cursor-pointer rounded-full border px-2.5 py-1 text-xs transition-colors ${
                          active
                            ? "border-primary bg-primary text-primary-foreground"
                            : "border-border bg-white text-foreground hover:bg-secondary"
                        }`}
                      >
                        {st}
                      </button>
                    );
                  })}
                  {q2States.length > 0 ? (
                    <button
                      type="button"
                      onClick={() => setQ2States([])}
                      className="cursor-pointer rounded-full px-2.5 py-1 text-xs text-primary underline-offset-2 hover:underline"
                    >
                      Reset to default states
                    </button>
                  ) : null}
                </div>
              </div>
            </CardContent>
          </Card>

          {q2Loading && !stateGaps ? (
            <LoadingState label="Computing Q2 inter-state price gaps..." />
          ) : !stateGaps || (stateGaps.items.length === 0 && stateComparisonChart.rows.length === 0) ? (
            <EmptyState message={`No multi-state comparison records found for ${selectedCommodity}.`} />
          ) : (
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <ChartCard
                title={`${selectedCommodity} price by state`}
                description="Monthly average modal price in each selected state."
              >
                <div className="h-80 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={stateComparisonChart.rows} margin={{ top: 10, right: 16, left: 4, bottom: 4 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                      <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${v}`} />
                      <Tooltip formatter={formatTooltipInr} />
                      <Legend />
                      {stateComparisonChart.stateKeys.map((st, idx) => (
                        <Line
                          key={st}
                          type="monotone"
                          dataKey={st}
                          name={st}
                          stroke={STATE_PALETTE[idx % STATE_PALETTE.length]}
                          strokeWidth={2}
                          dot={false}
                        />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>

              <ChartCard
                title="State price gaps"
                description="Difference between the cheapest and costliest state each month."
              >
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Period</TableHead>
                      <TableHead>Lowest State</TableHead>
                      <TableHead>Highest State</TableHead>
                      <TableHead className="text-right">Spread</TableHead>
                      <TableHead className="text-right">Gap %</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {stateGaps.items.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={5} className="text-center text-xs text-muted-foreground">
                          No multi-state gap rows for the selected year/month.
                        </TableCell>
                      </TableRow>
                    ) : null}
                    {stateGaps.items.slice(0, 12).map((row, idx) => (
                      <TableRow key={`${row.commodity_id}-${row.year}-${row.month}-${idx}`}>
                        <TableCell>{`${row.year}-${String(row.month).padStart(2, "0")}`}</TableCell>
                        <TableCell>
                          {row.lowest_state} ({formatINR(row.lowest_avg_price)})
                        </TableCell>
                        <TableCell>
                          {row.highest_state} ({formatINR(row.highest_avg_price)})
                        </TableCell>
                        <TableCell className="text-right font-semibold text-primary">
                          {formatINR(row.price_spread)}
                        </TableCell>
                        <TableCell className="text-right">
                          {row.pct_difference !== null && row.pct_difference !== undefined
                            ? `${row.pct_difference.toFixed(1)}%`
                            : "—"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ChartCard>
            </div>
          )}
        </TabsContent>

        {/* Q3 Monthly Trends */}
        <TabsContent value="trends" className="space-y-4">
          {bundleLoading && !monthlyTrends ? (
            <LoadingState label="Computing Q3 monthly trends..." />
          ) : !monthlyTrends || monthlyTrends.items.length === 0 ? (
            <EmptyState message={`No monthly trend data found for ${selectedCommodity}.`} />
          ) : (
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <ChartCard
                title={`${selectedCommodity} monthly price trend`}
                description="Monthly average modal price with a smoothed 3-month average."
              >
                <div className="h-80 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={monthlyTrends.items} margin={{ top: 10, right: 16, left: 4, bottom: 4 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                      <XAxis dataKey="month_start" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${v}`} />
                      <Tooltip formatter={formatTooltipInr} />
                      <Legend />
                      <Line
                        type="monotone"
                        dataKey="avg_modal_price"
                        name="Monthly Avg Modal"
                        stroke={CHART_COLORS.green}
                        strokeWidth={2.5}
                      />
                      <Line
                        type="monotone"
                        dataKey="ma_3m"
                        name="3M Moving Avg"
                        stroke={CHART_COLORS.gold}
                        strokeWidth={2}
                        strokeDasharray="5 5"
                        dot={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>

              <ChartCard
                title="Month-over-month change"
                description="How much the price rose or fell compared with the previous month."
              >
                <div className="h-80 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={monthlyTrends.items} margin={{ top: 10, right: 16, left: 4, bottom: 4 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                      <XAxis dataKey="month_start" tick={{ fontSize: 11 }} />
                      <YAxis unit="%" tick={{ fontSize: 11 }} />
                      <ReferenceLine y={0} stroke="#64748b" />
                      <Tooltip formatter={formatTooltipPct} />
                      <Legend />
                      <Bar dataKey="mom_pct_change" name="MoM Change (%)">
                        {monthlyTrends.items.map((entry, idx) => (
                          <Cell
                            key={`cell-${idx}`}
                            fill={(entry.mom_pct_change ?? 0) >= 0 ? CHART_COLORS.emerald : CHART_COLORS.terracotta}
                          />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>
            </div>
          )}
        </TabsContent>

        {/* Q4 Arrivals vs Price */}
        <TabsContent value="arrivals" className="space-y-4">
          {bundleLoading && !arrivalsVsPrice ? (
            <LoadingState label="Computing Q4 arrivals vs price correlation..." />
          ) : !arrivalsVsPrice || !arrivalsVsPrice.has_arrivals_data || arrivalsVsPrice.items.length === 0 ? (
            <EmptyState
              title="No arrivals data for this crop"
              message={`CEDA arrivals data is required to correlate mandi volume with modal price for ${selectedCommodity}.`}
            />
          ) : (
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <ChartCard
                title={`${selectedCommodity}: do bigger arrivals lower prices?`}
                description="Correlation between monthly arrivals (tonnes) and average modal price, same month and one month later."
              >
                <div className="h-80 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={arrivalsVsPrice.items} margin={{ top: 10, right: 16, left: 4, bottom: 4 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                      <XAxis dataKey="commodity_name" tick={{ fontSize: 11 }} />
                      <YAxis domain={[-1, 1]} tick={{ fontSize: 11 }} />
                      <ReferenceLine y={0} stroke="#475569" />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="corr_same_month" name="Same-Month r" fill={CHART_COLORS.teal} />
                      <Bar dataKey="corr_lag_1m" name="Lag-1M r" fill={CHART_COLORS.gold} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>

              <ChartCard
                title="Arrivals vs price summary"
                description="A negative value means prices tend to fall when more crop arrives at the mandi."
              >
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Commodity</TableHead>
                      <TableHead className="text-right">Paired Months</TableHead>
                      <TableHead className="text-right">Avg Arrivals (t)</TableHead>
                      <TableHead className="text-right">Avg Price</TableHead>
                      <TableHead className="text-right">Same-Mo r</TableHead>
                      <TableHead className="text-right">Lag-1M r</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {arrivalsVsPrice.items.map((c) => (
                      <TableRow key={c.commodity_id}>
                        <TableCell className="font-medium">{c.commodity_name}</TableCell>
                        <TableCell className="text-right">{formatNumber(c.paired_months)}</TableCell>
                        <TableCell className="text-right">{formatNumber(c.avg_monthly_arrivals_tonnes, 1)}</TableCell>
                        <TableCell className="text-right">{formatINR(c.avg_monthly_price)}</TableCell>
                        <TableCell className="text-right font-semibold">
                          {c.corr_same_month !== null && c.corr_same_month !== undefined
                            ? c.corr_same_month.toFixed(3)
                            : "—"}
                        </TableCell>
                        <TableCell className="text-right">
                          {c.corr_lag_1m !== null && c.corr_lag_1m !== undefined ? c.corr_lag_1m.toFixed(3) : "—"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ChartCard>
            </div>
          )}
        </TabsContent>

        {/* Q5 Seasonality */}
        <TabsContent value="seasonality" className="space-y-4">
          {bundleLoading && !seasonality ? (
            <LoadingState label="Computing Q5 seasonal price index..." />
          ) : !seasonality || seasonality.items.length === 0 ? (
            <EmptyState message={`No multi-month seasonality rows found for ${selectedCommodity}.`} />
          ) : (
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <ChartCard
                title={`${selectedCommodity} seasonal price index`}
                description={`${SEASONAL_BASELINE} = annual commodity baseline. Values ≥ ${SEASONAL_HIGH} mark seasonal price peaks; values ≤ ${SEASONAL_LOW} mark seasonal lows.`}
              >
                <div className="h-80 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={seasonality.items} margin={{ top: 10, right: 16, left: 4, bottom: 4 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                      <XAxis dataKey="month_name" tick={{ fontSize: 11 }} />
                      <YAxis
                        domain={seasonalIndexDomain(seasonality.items.map((s) => s.seasonal_index))}
                        tick={{ fontSize: 11 }}
                      />
                      <ReferenceLine y={SEASONAL_BASELINE} stroke="#475569" strokeDasharray="4 4" />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="seasonal_index" name="Seasonal Index">
                        {seasonality.items.map((entry, idx) => (
                          <Cell key={`season-${idx}`} fill={seasonalIndexColor(entry.seasonal_index)} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>

              <ChartCard title="Seasonality by month" description="Typical price level for each calendar month.">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Month</TableHead>
                      <TableHead className="text-right">Month Avg</TableHead>
                      <TableHead className="text-right">Seasonal Index</TableHead>
                      <TableHead className="text-right">Peak Rank</TableHead>
                      <TableHead className="text-right">Obs</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {seasonality.items.map((r) => (
                      <TableRow key={r.month}>
                        <TableCell className="font-medium">{r.month_name}</TableCell>
                        <TableCell className="text-right">{formatINR(r.month_avg_price)}</TableCell>
                        <TableCell className="text-right font-semibold">{r.seasonal_index.toFixed(2)}</TableCell>
                        <TableCell className="text-right">#{r.peak_month_rank}</TableCell>
                        <TableCell className="text-right">{formatNumber(r.observation_count)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ChartCard>
            </div>
          )}
        </TabsContent>

        {/* Q6 Best Markets */}
        <TabsContent value="best-markets" className="space-y-4">
          <Card>
            <CardContent className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
              <SelectField
                label="Filter State"
                value={bestMarketState}
                onChange={(e) => setBestMarketState(e.target.value)}
                options={bestMarketStateOptions}
              />
              <SelectField
                label="Minimum 7d Reports"
                value={String(bestMarketMinReports)}
                onChange={(e) => setBestMarketMinReports(Number(e.target.value))}
                options={[
                  { value: "1", label: "At least 1 report" },
                  { value: "2", label: "At least 2 reports" },
                  { value: "3", label: "At least 3 reports" },
                  { value: "5", label: "At least 5 reports" },
                ]}
              />
              <div className="flex items-end">
                <p className="text-xs text-muted-foreground">
                  Mandis ranked by their average modal price over the last 7 days.
                </p>
              </div>
            </CardContent>
          </Card>

          {bundleLoading && !bestMarkets ? (
            <LoadingState label="Ranking Q6 best markets..." />
          ) : !bestMarkets || bestMarkets.items.length === 0 ? (
            <EmptyState message="No mandis meet the minimum 7-day report threshold for this filter." />
          ) : (
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <ChartCard title={`Top-paying mandis for ${selectedCommodity}`} description="Ranked by 7-day average modal price.">
                <div className="h-80 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={bestMarkets.items.slice(0, 10)}
                      layout="vertical"
                      margin={{ top: 8, right: 20, left: 28, bottom: 4 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                      <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${v}`} />
                      <YAxis type="category" dataKey="market_name" width={115} tick={{ fontSize: 11 }} />
                      <Tooltip formatter={formatTooltipInr} />
                      <Legend />
                      <Bar
                        dataKey="avg_modal_price"
                        name="7d Avg Modal"
                        fill={CHART_COLORS.green}
                        radius={[0, 4, 4, 0]}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>

              <ChartCard title="Best markets leaderboard" description="Recent 7-day average and price range.">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Rank</TableHead>
                      <TableHead>Market</TableHead>
                      <TableHead>State</TableHead>
                      <TableHead className="text-right">7d Modal</TableHead>
                      <TableHead className="text-right">Min / Max</TableHead>
                      <TableHead className="text-right">Reports</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {bestMarkets.items.slice(0, 12).map((r) => (
                      <TableRow key={`${r.market_id}-${r.commodity_id}`}>
                        <TableCell>#{r.market_rank}</TableCell>
                        <TableCell className="font-medium">{r.market_name}</TableCell>
                        <TableCell>{r.state}</TableCell>
                        <TableCell className="text-right font-semibold text-primary">
                          {formatINR(r.avg_modal_price)}
                        </TableCell>
                        <TableCell className="text-right">
                          {formatINR(r.lowest_min_price)} / {formatINR(r.highest_max_price)}
                        </TableCell>
                        <TableCell className="text-right">{formatNumber(r.report_count)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ChartCard>
            </div>
          )}
        </TabsContent>

        {/* Q7 Production vs Price */}
        <TabsContent value="production" className="space-y-4">
          <Card>
            <CardContent className="flex flex-wrap items-end justify-between gap-4 p-4">
              <div className="w-full sm:w-56">
                <SelectField
                  label="State"
                  value={prodStateFilter}
                  onChange={(e) => setProdStateFilter(e.target.value)}
                  options={prodStateOptions}
                />
              </div>
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <Badge variant="default">
                  Mapped crops: {mappedCount} / {productionVsPrice?.mapping_coverage.length ?? 0}
                </Badge>
                <span>Crops matched to government production statistics</span>
              </div>
            </CardContent>
          </Card>

          {productionVsPrice ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <KpiCard label="Total APY Area" value={`${formatNumber(productionVsPrice.kpis.total_area_ha, 0)} ha`} />
              <KpiCard
                label="Total APY Production"
                value={`${formatNumber(productionVsPrice.kpis.total_production_t, 0)} t`}
                tone="green"
              />
              <KpiCard
                label="Average Crop Yield"
                value={`${productionVsPrice.kpis.avg_yield_t_ha.toFixed(2)} t/ha`}
                tone="gold"
              />
            </div>
          ) : null}

          {bundleLoading && !productionVsPrice ? (
            <LoadingState label="Joining Q7 production and price..." />
          ) : !productionVsPrice || productionVsPrice.detail.length === 0 ? (
            <EmptyState
              title="No production data for this crop"
              message={`No state-level annual production & price alignment exists yet for ${selectedCommodity} under the current state filter.`}
            />
          ) : (
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <ChartCard
                title={`${selectedCommodity}: production vs price`}
                description="Annual production (tonnes) against the average market price (₹/qtl)."
              >
                <div className="h-80 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <ScatterChart margin={{ top: 12, right: 20, left: 4, bottom: 8 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                      <XAxis type="number" dataKey="total_production_t" name="Production (t)" tick={{ fontSize: 11 }} />
                      <YAxis
                        type="number"
                        dataKey="avg_yearly_modal_price"
                        name="Avg Modal Price (₹/qtl)"
                        tick={{ fontSize: 11 }}
                        tickFormatter={(v) => `₹${v}`}
                      />
                      <ZAxis type="number" dataKey="agg_yield_t_ha" range={[60, 260]} name="Yield (t/ha)" />
                      <Tooltip cursor={{ strokeDasharray: "3 3" }} />
                      <Legend />
                      <Scatter name="State-Year" data={productionVsPrice.detail} fill={CHART_COLORS.gold} />
                    </ScatterChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>

              <ChartCard
                title="Production & yield by state"
                description="Area, production and yield alongside average market prices."
              >
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>State</TableHead>
                      <TableHead>Year</TableHead>
                      <TableHead className="text-right">Production (t)</TableHead>
                      <TableHead className="text-right">Yield (t/ha)</TableHead>
                      <TableHead className="text-right">Avg Modal</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {productionVsPrice.detail.slice(0, 12).map((r, idx) => (
                      <TableRow key={`${r.state}-${r.crop_year}-${idx}`}>
                        <TableCell className="font-medium">{r.state}</TableCell>
                        <TableCell>{r.crop_year}</TableCell>
                        <TableCell className="text-right">{formatNumber(r.total_production_t, 0)}</TableCell>
                        <TableCell className="text-right">{formatNumber(r.agg_yield_t_ha, 2)}</TableCell>
                        <TableCell className="text-right font-semibold text-primary">
                          {formatINR(r.avg_yearly_modal_price)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ChartCard>
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
