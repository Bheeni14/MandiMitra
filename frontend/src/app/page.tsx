"use client";

import * as React from "react";
import Link from "next/link";
import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Brain,
  Database,
  IndianRupee,
  LayoutDashboard,
  MapPin,
  Minus,
  Search,
  Sprout,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART_COLORS, cvToPct, formatTooltipInr, formatTooltipPct2 } from "@/components/charts/chartHelpers";
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
import { Card } from "@/components/ui/card";
import { SelectField } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { mandiApi } from "@/lib/api";
import type { OverviewResponse } from "@/lib/types";
import { cn, formatINR, formatNumber, formatSignedPct } from "@/lib/utils";

/** Highlight labels from the API carry internal query ids like "(Q3)" that mean nothing to a farmer or trader. */
function cleanLabel(label: string) {
  return label.replace(/\s*\(Q\d+\)\s*/g, " ").trim();
}

const QUICK_LINKS = [
  {
    href: "/market-explorer",
    icon: Search,
    title: "Market Explorer",
    body: "Filter daily mandi prices by crop, state, district, market and date.",
  },
  {
    href: "/price-analytics",
    icon: BarChart3,
    title: "Price Analytics",
    body: "Volatility, state price gaps, seasonality, best markets and production trends.",
  },
  {
    href: "/ai-advisor",
    icon: Brain,
    title: "AI Crop Advisor",
    body: "Should you sell now or wait? Clear advice in English and Hindi.",
  },
];

export default function OverviewPage() {
  const [selectedCommodity, setSelectedCommodity] = React.useState<string>("");
  const [reloadTick, setReloadTick] = React.useState(0);
  const requestKey = `${selectedCommodity}|${reloadTick}`;
  const [state, setState] = React.useState<{
    key: string;
    data: OverviewResponse | null;
    error: string | null;
  }>({ key: "", data: null, error: null });

  React.useEffect(() => {
    const controller = new AbortController();
    let active = true;
    mandiApi
      .getOverview(selectedCommodity ? { commodity: selectedCommodity } : undefined, controller.signal)
      .then((res) => {
        if (!active) return;
        // The API already resolved the default focus commodity; the picker displays it without storing it,
        // so the first load doesn't trigger a second identical request.
        setState({ key: requestKey, data: res, error: null });
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (!active) return;
        setState((prev) => ({
          key: requestKey,
          data: prev.data,
          error: err instanceof Error ? err.message : "Failed to load dashboard overview",
        }));
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [selectedCommodity, reloadTick, requestKey]);

  const { data, error } = state;
  const loading = state.key !== requestKey;

  const commodityOptions = React.useMemo(() => {
    const list = data?.available_commodities ?? [];
    return list.map((c) => ({ value: c, label: c }));
  }, [data?.available_commodities]);

  const movementPct = data?.focus_movement?.mom_pct_change ?? null;
  const direction = movementPct === null ? "flat" : movementPct > 0 ? "up" : movementPct < 0 ? "down" : "flat";
  const focusPrice = data?.focus_movement?.avg_modal_price ?? data?.latest_window_avg_modal_price ?? null;

  const volatilityChartRows = React.useMemo(
    () => (data?.volatility ?? []).slice(0, 8).map((v) => ({ ...v, cv_pct: cvToPct(v.cv) })),
    [data?.volatility]
  );

  return (
    <div className="space-y-6 sm:space-y-8">
      <PageHeader
        eyebrow="Markets"
        title="Market Overview"
        icon={LayoutDashboard}
        description="Wholesale mandi prices, monthly trends and price swings across India — at a glance."
        badges={data?.is_demo ? <Badge variant="gold">Simulated Demo Data</Badge> : null}
        actions={
          commodityOptions.length > 0 ? (
            <div className="w-full sm:w-60">
              <SelectField
                label="Focus Commodity"
                value={selectedCommodity || data?.focus_commodity || ""}
                onChange={(e) => setSelectedCommodity(e.target.value)}
                options={commodityOptions}
              />
            </div>
          ) : null
        }
      />

      {loading && !data ? <LoadingState label="Loading market overview..." /> : null}
      {loading && data ? <RefetchIndicator label="Updating overview for the selected commodity..." /> : null}
      {error ? (
        <ErrorState
          title="Could not load the market overview"
          message={error}
          onRetry={() => setReloadTick((t) => t + 1)}
        />
      ) : null}

      {data ? (
        <>
          {/* Focus commodity spotlight + headline numbers */}
          <section aria-label="Market summary" className="grid grid-cols-1 gap-4 lg:grid-cols-5">
            <div className="relative overflow-hidden rounded-3xl bg-linear-to-br from-[#0f3d24] via-[#145332] to-[#1d6b40] p-6 text-white shadow-raised sm:p-7 lg:col-span-3">
              <div
                className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full bg-[#6fd08c]/10 blur-2xl"
                aria-hidden="true"
              />
              <div className="relative flex h-full flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.14em] text-[#a9d9b8]">
                    <Sprout className="h-3.5 w-3.5" aria-hidden="true" />
                    Focus commodity
                    {data.focus_movement?.latest_month ? (
                      <span className="normal-case tracking-normal text-white/50">
                        · {data.focus_movement.latest_month}
                      </span>
                    ) : null}
                  </div>
                  <h2 className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">
                    {data.focus_commodity || "All commodities"}
                  </h2>
                  <div className="mt-4 flex flex-wrap items-end gap-3">
                    <span className="tabular text-4xl font-semibold tracking-tight sm:text-5xl">
                      {formatINR(focusPrice, 0)}
                    </span>
                    <span className="pb-1.5 text-sm text-white/60">per quintal</span>
                  </div>
                  <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
                    <span
                      className={cn(
                        "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold",
                        direction === "up" && "bg-[#6fd08c]/20 text-[#a7ecbb]",
                        direction === "down" && "bg-[#ff9a7a]/20 text-[#ffc0ab]",
                        direction === "flat" && "bg-white/10 text-white/80"
                      )}
                    >
                      {direction === "up" ? (
                        <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
                      ) : direction === "down" ? (
                        <ArrowDownRight className="h-3.5 w-3.5" aria-hidden="true" />
                      ) : (
                        <Minus className="h-3.5 w-3.5" aria-hidden="true" />
                      )}
                      {formatSignedPct(movementPct)}
                    </span>
                    <span className="text-white/60">vs last month</span>
                  </div>
                  <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:max-w-xs">
                    <dt className="text-white/50">Last month</dt>
                    <dt className="text-white/50">3-month average</dt>
                    <dd className="tabular text-sm font-medium">
                      {formatINR(data.focus_movement?.prev_month_price, 0)}
                    </dd>
                    <dd className="tabular text-sm font-medium">{formatINR(data.focus_movement?.ma_3m, 0)}</dd>
                  </dl>
                </div>

                {data.mom_trends.length > 1 ? (
                  <div className="h-24 w-full shrink-0 sm:h-28 sm:w-56" aria-hidden="true">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={data.mom_trends} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
                        <defs>
                          <linearGradient id="heroSpark" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor="#8fe0a6" stopOpacity={0.45} />
                            <stop offset="100%" stopColor="#8fe0a6" stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <YAxis hide domain={["dataMin", "dataMax"]} />
                        <Area
                          type="monotone"
                          dataKey="avg_modal_price"
                          stroke="#a7ecbb"
                          strokeWidth={2}
                          fill="url(#heroSpark)"
                          isAnimationActive={false}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                ) : null}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4 lg:col-span-2">
              <KpiCard
                label="Price reports"
                value={formatNumber(data.coverage.total_price_records)}
                delta={data.coverage.max_price_date ? `Updated ${data.coverage.max_price_date}` : undefined}
                icon={<Database />}
                tone="green"
              />
              <KpiCard
                label="Avg. modal price"
                value={formatINR(data.latest_window_avg_modal_price, 0)}
                delta={`Overall ${formatINR(data.overall_avg_modal_price, 0)}`}
                icon={<IndianRupee />}
                tone="gold"
              />
              <KpiCard
                label="Mandis"
                value={formatNumber(data.coverage.markets_count)}
                delta={`Across ${formatNumber(data.coverage.states_count)} states`}
                icon={<MapPin />}
              />
              <KpiCard
                label="Commodities"
                value={formatNumber(data.coverage.commodities_count)}
                delta="Tracked daily"
                icon={<Sprout />}
              />
            </div>
          </section>

          {/* Market highlights */}
          {data.market_highlights.length > 0 ? (
            <section aria-label="Market highlights">
              <h2 className="mb-3 text-sm font-semibold text-foreground">Market highlights</h2>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {data.market_highlights.map((hl, idx) => {
                  const Icon = hl.direction === "up" ? ArrowUpRight : hl.direction === "down" ? ArrowDownRight : Minus;
                  return (
                    <Card key={`${hl.commodity_name}-${idx}`} className="flex items-start gap-3.5 p-5">
                      <span
                        className={cn(
                          "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl",
                          hl.direction === "up" && "bg-tone-green-soft text-tone-green",
                          hl.direction === "down" && "bg-tone-terracotta-soft text-tone-terracotta",
                          hl.direction === "neutral" && "bg-tone-gold-soft text-tone-gold"
                        )}
                      >
                        <Icon className="h-4 w-4" aria-hidden="true" />
                      </span>
                      <div className="min-w-0">
                        <p className="text-xs text-muted-foreground">{cleanLabel(hl.metric_label)}</p>
                        <p className="mt-0.5 truncate font-semibold text-foreground">{hl.commodity_name}</p>
                        <p className="tabular mt-1 text-[13px] text-foreground/80">{hl.value_label}</p>
                      </div>
                    </Card>
                  );
                })}
              </div>
            </section>
          ) : null}

          {/* Trend & volatility */}
          <section className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <ChartCard
              title={`${data.focus_commodity || "Commodity"} price trend`}
              description="Monthly average modal price with its 3-month moving average."
            >
              {data.mom_trends.length === 0 ? (
                <EmptyState message="No monthly trend observations for the selected commodity." />
              ) : (
                <div className="h-72 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={data.mom_trends} margin={{ top: 10, right: 16, left: 4, bottom: 4 }}>
                      <CartesianGrid vertical={false} stroke={CHART_COLORS.grid} />
                      <XAxis dataKey="month_start" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${v}`} />
                      <Tooltip formatter={formatTooltipInr} />
                      <Legend iconType="circle" iconSize={8} />
                      <Line
                        type="monotone"
                        dataKey="avg_modal_price"
                        name="Monthly avg. modal"
                        stroke={CHART_COLORS.green}
                        strokeWidth={2.5}
                        dot={{ r: 3, strokeWidth: 0, fill: CHART_COLORS.green }}
                        activeDot={{ r: 5 }}
                      />
                      <Line
                        type="monotone"
                        dataKey="ma_3m"
                        name="3-month avg."
                        stroke={CHART_COLORS.gold}
                        strokeWidth={2}
                        strokeDasharray="5 5"
                        dot={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </ChartCard>

            <ChartCard
              title="Most volatile crops"
              description="Price swing over the last 12 months (coefficient of variation). Higher means less predictable prices."
            >
              {data.volatility.length === 0 ? (
                <EmptyState message="No volatility data available yet." />
              ) : (
                <div className="h-72 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={volatilityChartRows}
                      layout="vertical"
                      margin={{ top: 8, right: 20, left: 24, bottom: 4 }}
                    >
                      <CartesianGrid horizontal={false} stroke={CHART_COLORS.grid} />
                      <XAxis type="number" tick={{ fontSize: 11 }} unit="%" />
                      <YAxis type="category" dataKey="commodity_name" width={100} tick={{ fontSize: 11 }} />
                      <Tooltip formatter={formatTooltipPct2} />
                      <Bar
                        dataKey="cv_pct"
                        name="12-month swing (%)"
                        fill={CHART_COLORS.terracotta}
                        radius={[0, 6, 6, 0]}
                        barSize={16}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </ChartCard>
          </section>

          {/* State comparison */}
          <section className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <ChartCard title="Average price by state" description="Average modal price per state in the latest records.">
              {data.state_averages.length === 0 ? (
                <EmptyState message="No state-level price breakdown found." />
              ) : (
                <div className="h-72 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={data.state_averages} margin={{ top: 10, right: 16, left: 4, bottom: 20 }}>
                      <CartesianGrid vertical={false} stroke={CHART_COLORS.grid} />
                      <XAxis dataKey="state" tick={{ fontSize: 11 }} angle={-20} textAnchor="end" height={50} />
                      <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${v}`} />
                      <Tooltip formatter={formatTooltipInr} />
                      <Bar
                        dataKey="avg_modal"
                        name="Avg. modal price"
                        fill={CHART_COLORS.emerald}
                        radius={[6, 6, 0, 0]}
                        maxBarSize={36}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </ChartCard>

            <ChartCard
              title="Biggest price gaps between states"
              description="Where the same crop sells for very different prices in different states."
            >
              {data.state_gaps.length === 0 ? (
                <EmptyState message="No inter-state price gaps available yet." />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Commodity</TableHead>
                      <TableHead>Month</TableHead>
                      <TableHead>Cheapest</TableHead>
                      <TableHead>Costliest</TableHead>
                      <TableHead className="text-right">Gap</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.state_gaps.slice(0, 7).map((row, idx) => (
                      <TableRow key={`${row.commodity_name}-${row.year}-${row.month}-${idx}`}>
                        <TableCell className="font-medium text-foreground">{row.commodity_name}</TableCell>
                        <TableCell className="whitespace-nowrap">{`${row.year}-${String(row.month).padStart(2, "0")}`}</TableCell>
                        <TableCell>
                          <div>{row.lowest_state}</div>
                          <div className="text-[11px] text-muted-foreground">{formatINR(row.lowest_avg_price)}</div>
                        </TableCell>
                        <TableCell>
                          <div>{row.highest_state}</div>
                          <div className="text-[11px] text-muted-foreground">{formatINR(row.highest_avg_price)}</div>
                        </TableCell>
                        <TableCell className="text-right font-semibold text-primary">
                          {formatINR(row.price_spread)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </ChartCard>
          </section>

          {/* Latest prices */}
          <ChartCard
            title="Latest mandi prices"
            description="The most recent daily price reports (₹ per quintal)."
            actions={
              <Link
                href="/market-explorer"
                className="inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline"
              >
                View all <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
              </Link>
            }
          >
            {data.recent_prices.length === 0 ? (
              <EmptyState message="No recent daily mandi price records found." />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead>Commodity</TableHead>
                    <TableHead>Market</TableHead>
                    <TableHead>State</TableHead>
                    <TableHead className="text-right">Min</TableHead>
                    <TableHead className="text-right">Modal</TableHead>
                    <TableHead className="text-right">Max</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.recent_prices.slice(0, 10).map((row, idx) => (
                    <TableRow key={`${row.market}-${row.arrival_date}-${idx}`}>
                      <TableCell className="whitespace-nowrap text-muted-foreground">{row.arrival_date}</TableCell>
                      <TableCell className="font-medium text-foreground">{row.commodity}</TableCell>
                      <TableCell>{row.market}</TableCell>
                      <TableCell>{row.state}</TableCell>
                      <TableCell className="text-right">{formatINR(row.min_price)}</TableCell>
                      <TableCell className="text-right font-semibold text-primary">
                        {formatINR(row.modal_price)}
                      </TableCell>
                      <TableCell className="text-right">{formatINR(row.max_price)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </ChartCard>

          {/* Quick navigation */}
          <section aria-label="Quick navigation" className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {QUICK_LINKS.map(({ href, icon: Icon, title, body }) => (
              <Link
                key={href}
                href={href}
                className="group rounded-2xl border border-border bg-white p-5 shadow-card transition-all hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-raised"
              >
                <div className="flex items-center justify-between">
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-tone-green-soft text-tone-green transition-colors group-hover:bg-primary group-hover:text-white">
                    <Icon className="h-5 w-5" aria-hidden="true" />
                  </span>
                  <ArrowRight
                    className="h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary"
                    aria-hidden="true"
                  />
                </div>
                <h3 className="mt-4 font-semibold text-foreground">{title}</h3>
                <p className="mt-1 text-[13px] leading-relaxed text-muted-foreground">{body}</p>
              </Link>
            ))}
          </section>
        </>
      ) : null}
    </div>
  );
}
