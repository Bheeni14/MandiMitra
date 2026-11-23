"use client";

import * as React from "react";
import {
  ChevronLeft,
  ChevronRight,
  IndianRupee,
  ListFilter,
  MapPin,
  PackageCheck,
  RotateCcw,
  Search,
  TrendingDown,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  buildDailyChartRows,
  CHART_COLORS,
  formatPriceArrivalsTooltip,
  formatTooltipInr,
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
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fieldControlClass, fieldLabelClass, SelectField } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { mandiApi } from "@/lib/api";
import type { MarketPricesResponse } from "@/lib/types";
import { formatINR, formatNumber } from "@/lib/utils";

const ALL_OPTION = "All";

export default function MarketExplorerPage() {
  const [commodities, setCommodities] = React.useState<string[]>([]);
  const [commodity, setCommodity] = React.useState<string>("");
  const [states, setStates] = React.useState<string[]>([]);
  const [stateFilter, setStateFilter] = React.useState<string>(ALL_OPTION);
  const [districts, setDistricts] = React.useState<string[]>([]);
  const [districtFilter, setDistrictFilter] = React.useState<string>(ALL_OPTION);
  const [markets, setMarkets] = React.useState<string[]>([]);
  const [marketFilter, setMarketFilter] = React.useState<string>(ALL_OPTION);
  const [varietyFilter, setVarietyFilter] = React.useState<string>(ALL_OPTION);
  const [startDate, setStartDate] = React.useState<string>("");
  const [endDate, setEndDate] = React.useState<string>("");
  const [page, setPage] = React.useState<number>(1);
  const [reloadTick, setReloadTick] = React.useState<number>(0);
  const [initError, setInitError] = React.useState<string | null>(null);
  const [marketsError, setMarketsError] = React.useState<string | null>(null);
  const [marketsReloadTick, setMarketsReloadTick] = React.useState<number>(0);
  const requestKey = `${commodity}|${stateFilter}|${districtFilter}|${marketFilter}|${varietyFilter}|${startDate}|${endDate}|${page}|${reloadTick}`;
  const [queryState, setQueryState] = React.useState<{
    key: string;
    data: MarketPricesResponse | null;
    error: string | null;
  }>({ key: "", data: null, error: null });

  // Load initial commodity list
  React.useEffect(() => {
    const controller = new AbortController();
    mandiApi
      .getCommodities(controller.signal)
      .then((res) => {
        const names = res.items.map((c) => c.commodity_name);
        setCommodities(names);
        if (names.length > 0) {
          setCommodity((prev) => prev || (names.includes("Onion") ? "Onion" : names[0]));
        } else {
          setQueryState({ key: "empty", data: null, error: null });
        }
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setInitError(err instanceof Error ? err.message : "Failed to load commodities");
      });
    return () => controller.abort();
  }, []);

  // Cascade state -> district -> market lists whenever commodity/state/district change
  React.useEffect(() => {
    if (!commodity) return;
    const controller = new AbortController();
    let active = true;
    mandiApi
      .getMarkets(
        {
          commodity,
          state: stateFilter !== ALL_OPTION ? stateFilter : undefined,
          district: districtFilter !== ALL_OPTION ? districtFilter : undefined,
        },
        controller.signal
      )
      .then((res) => {
        if (!active) return;
        setStates(res.states);
        setDistricts(res.districts);
        setMarkets(Array.from(new Set(res.items.map((m) => m.market_name))).sort());
        setMarketsError(null);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (!active) return;
        // Keep the previous options usable, but tell the user the filter lists may be stale.
        setMarketsError(err instanceof Error ? err.message : "Failed to load state, district, and market options");
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [commodity, stateFilter, districtFilter, marketsReloadTick]);

  // Fetch filtered market prices
  React.useEffect(() => {
    if (!commodity) return;
    const controller = new AbortController();
    let active = true;

    mandiApi
      .getMarketPrices(
        {
          commodity,
          state: stateFilter !== ALL_OPTION ? stateFilter : undefined,
          district: districtFilter !== ALL_OPTION ? districtFilter : undefined,
          market: marketFilter !== ALL_OPTION ? marketFilter : undefined,
          variety: varietyFilter !== ALL_OPTION ? varietyFilter : undefined,
          start_date: startDate || undefined,
          end_date: endDate || undefined,
          page,
          page_size: 20,
        },
        controller.signal
      )
      .then((res) => {
        if (active) setQueryState({ key: requestKey, data: res, error: null });
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (active) {
          setQueryState((prev) => ({
            key: requestKey,
            data: prev.data,
            error: err instanceof Error ? err.message : "Failed to fetch mandi price records",
          }));
        }
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [commodity, stateFilter, districtFilter, marketFilter, varietyFilter, startDate, endDate, page, reloadTick, requestKey]);

  const data = queryState.data;
  const error = initError ?? queryState.error;
  const loading = !initError && (commodity ? queryState.key !== requestKey : queryState.key !== "empty");

  const handleCommodityChange = (val: string) => {
    setCommodity(val);
    setStateFilter(ALL_OPTION);
    setDistrictFilter(ALL_OPTION);
    setMarketFilter(ALL_OPTION);
    setVarietyFilter(ALL_OPTION);
    setStartDate("");
    setEndDate("");
    setPage(1);
  };

  const handleResetFilters = () => {
    setStateFilter(ALL_OPTION);
    setDistrictFilter(ALL_OPTION);
    setMarketFilter(ALL_OPTION);
    setVarietyFilter(ALL_OPTION);
    setStartDate("");
    setEndDate("");
    setPage(1);
  };

  const dailyChart = React.useMemo(
    () => (data ? buildDailyChartRows(data.daily_series, data.outlier_points) : { rows: [], outlierSlots: 0 }),
    [data]
  );

  const varietyOptions = React.useMemo(() => {
    const vars = data?.summary.available_varieties ?? [];
    return [{ value: ALL_OPTION, label: "All varieties" }, ...vars.map((v) => ({ value: v, label: v }))];
  }, [data?.summary.available_varieties]);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Markets"
        title="Market Explorer"
        icon={Search}
        description="Look up daily mandi prices for any crop — filter by state, district, market, variety and dates."
        actions={
          <Button variant="outline" size="sm" onClick={handleResetFilters}>
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            Reset filters
          </Button>
        }
      />

      {/* Filter Toolbar */}
      <Card>
        <div className="flex items-center gap-2 border-b border-border px-5 py-3 text-[13px] font-medium text-foreground sm:px-6">
          <ListFilter className="h-4 w-4 text-primary" aria-hidden="true" />
          Filters
        </div>
        <CardContent className="grid grid-cols-1 gap-4 p-5 sm:grid-cols-2 sm:p-6 lg:grid-cols-4 xl:grid-cols-7">
          <SelectField
            label="Commodity"
            value={commodity}
            onChange={(e) => handleCommodityChange(e.target.value)}
            options={commodities.map((c) => ({ value: c, label: c }))}
          />
          <SelectField
            label="State"
            value={stateFilter}
            onChange={(e) => {
              setStateFilter(e.target.value);
              setDistrictFilter(ALL_OPTION);
              setMarketFilter(ALL_OPTION);
              setPage(1);
            }}
            options={[{ value: ALL_OPTION, label: "All states" }, ...states.map((s) => ({ value: s, label: s }))]}
          />
          <SelectField
            label="District"
            value={districtFilter}
            onChange={(e) => {
              setDistrictFilter(e.target.value);
              setMarketFilter(ALL_OPTION);
              setPage(1);
            }}
            options={[
              { value: ALL_OPTION, label: "All districts" },
              ...districts.map((d) => ({ value: d, label: d })),
            ]}
          />
          <SelectField
            label="Market"
            value={marketFilter}
            onChange={(e) => {
              setMarketFilter(e.target.value);
              setPage(1);
            }}
            options={[{ value: ALL_OPTION, label: "All markets" }, ...markets.map((m) => ({ value: m, label: m }))]}
          />
          <SelectField
            label="Variety"
            value={varietyFilter}
            onChange={(e) => {
              setVarietyFilter(e.target.value);
              setPage(1);
            }}
            options={varietyOptions}
          />
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="explorer-start-date"
              className={fieldLabelClass}
            >
              From
            </label>
            <input
              id="explorer-start-date"
              type="date"
              value={startDate}
              min={data?.summary.min_date ?? undefined}
              max={endDate || data?.summary.max_date || undefined}
              onChange={(e) => {
                setStartDate(e.target.value);
                setPage(1);
              }}
              className={fieldControlClass}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="explorer-end-date"
              className={fieldLabelClass}
            >
              To
            </label>
            <input
              id="explorer-end-date"
              type="date"
              value={endDate}
              min={startDate || data?.summary.min_date || undefined}
              max={data?.summary.max_date ?? undefined}
              onChange={(e) => {
                setEndDate(e.target.value);
                setPage(1);
              }}
              className={fieldControlClass}
            />
          </div>
        </CardContent>
      </Card>

      {loading && !data ? <LoadingState label="Loading mandi prices..." /> : null}
      {loading && data ? <RefetchIndicator label="Updating mandi prices for the selected filters..." /> : null}
      {marketsError ? (
        <ErrorState
          title="Could not load state, district, and market options"
          message={marketsError}
          onRetry={() => setMarketsReloadTick((t) => t + 1)}
        />
      ) : null}
      {error ? (
        <ErrorState title="Could not load mandi prices" message={error} onRetry={() => setReloadTick((t) => t + 1)} />
      ) : null}

      {data ? (
        <>
          <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-5">
            <KpiCard
              label="Avg. modal price"
              value={formatINR(data.summary.avg_modal_price)}
              delta={`Latest: ${formatINR(data.summary.latest_modal_price)} (${data.summary.latest_date ?? "N/A"})`}
              icon={<IndianRupee className="h-4 w-4" />}
              tone="green"
            />
            <KpiCard
              label="Lowest / highest"
              value={`${formatINR(data.summary.min_price, 0)} – ${formatINR(data.summary.max_price, 0)}`}
              delta="Price range in this selection"
              icon={<TrendingDown className="h-4 w-4" />}
            />
            <KpiCard
              label="Price reports"
              value={formatNumber(data.summary.total_observations)}
              delta={`${data.summary.outliers_flagged} unusual prices flagged`}
              tone={data.summary.outliers_flagged > 0 ? "terracotta" : "default"}
            />
            <KpiCard
              label="Markets compared"
              value={formatNumber(data.market_comparison.length)}
              delta="Mandis in this selection"
              icon={<MapPin className="h-4 w-4" />}
            />
            <KpiCard
              label="Total arrivals"
              value={
                data.summary.arrivals_available && data.summary.total_arrivals_tonnes !== null
                  ? `${formatNumber(data.summary.total_arrivals_tonnes, 1)} t`
                  : "Unavailable"
              }
              delta={
                data.summary.arrivals_available
                  ? "Crop volume reaching these mandis"
                  : "No arrival data for this selection"
              }
              icon={<PackageCheck className="h-4 w-4" />}
              tone="gold"
            />
          </div>

          {data.items.length === 0 ? (
            <EmptyState
              title="No mandi price records match these filters"
              message="Try widening the date range or resetting state, district, or market filters."
            />
          ) : (
            <>
              {/* Price Trend + Outliers + Arrivals Chart */}
              <ChartCard
                title={`${commodity} daily price trend`}
                description="Min, modal and max price averaged across the selected markets. Red dots mark unusual prices; bars show arrivals."
                actions={
                  data.summary.outliers_flagged > 0 ? (
                    <Badge variant="terracotta">{data.summary.outliers_flagged} unusual prices</Badge>
                  ) : (
                    <Badge variant="default">No unusual prices</Badge>
                  )
                }
              >
                <div className="h-80 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart data={dailyChart.rows} margin={{ top: 10, right: 20, left: 4, bottom: 8 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                      <XAxis dataKey="arrival_date" tick={{ fontSize: 11 }} />
                      <YAxis yAxisId="price" tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${v}`} />
                      {data.summary.arrivals_available ? (
                        <YAxis
                          yAxisId="arrivals"
                          orientation="right"
                          tick={{ fontSize: 11 }}
                          tickFormatter={(v) => `${v}t`}
                        />
                      ) : null}
                      <Tooltip formatter={formatPriceArrivalsTooltip} />
                      <Legend />
                      {data.summary.arrivals_available ? (
                        <Bar
                          yAxisId="arrivals"
                          dataKey="arrivals_tonnes"
                          name="Arrivals (tonnes)"
                          fill="#D7E6D5"
                          barSize={12}
                        />
                      ) : null}
                      <Line
                        yAxisId="price"
                        type="monotone"
                        dataKey="max_price"
                        name="Max Price"
                        stroke={CHART_COLORS.gold}
                        strokeWidth={1.5}
                        dot={false}
                      />
                      <Line
                        yAxisId="price"
                        type="monotone"
                        dataKey="modal_price"
                        name="Modal Price"
                        stroke={CHART_COLORS.green}
                        strokeWidth={2.5}
                        dot={false}
                      />
                      <Line
                        yAxisId="price"
                        type="monotone"
                        dataKey="min_price"
                        name="Min Price"
                        stroke={CHART_COLORS.teal}
                        strokeWidth={1.5}
                        dot={false}
                      />
                      {Array.from({ length: dailyChart.outlierSlots }, (_, i) => (
                        <Scatter
                          key={`outlier_${i}`}
                          yAxisId="price"
                          dataKey={`outlier_${i}`}
                          name="3-IQR Outlier"
                          legendType={i === 0 ? "circle" : "none"}
                          fill={CHART_COLORS.terracotta}
                        />
                      ))}
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>

              {/* Market Comparison Chart + Table */}
              <section className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                <ChartCard
                  title="Best-paying markets"
                  description="Average modal price at each mandi in your selection."
                >
                  {data.market_comparison.length === 0 ? (
                    <EmptyState message="Not enough markets to compare." />
                  ) : (
                    <div className="h-72 w-full">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart
                          data={data.market_comparison.slice(0, 10)}
                          layout="vertical"
                          margin={{ top: 6, right: 20, left: 28, bottom: 4 }}
                        >
                          <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                          <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${v}`} />
                          <YAxis type="category" dataKey="market" width={115} tick={{ fontSize: 11 }} />
                          <Tooltip formatter={formatTooltipInr} />
                          <Legend />
                          <Bar
                            dataKey="avg_modal"
                            name="Avg Modal Price"
                            fill={CHART_COLORS.emerald}
                            radius={[0, 4, 4, 0]}
                          />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </ChartCard>

                <ChartCard
                  title="Market summary"
                  description="Average, lowest and highest modal price per mandi."
                >
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Market</TableHead>
                        <TableHead>State</TableHead>
                        <TableHead className="text-right">Avg Modal</TableHead>
                        <TableHead className="text-right">Min / Max</TableHead>
                        <TableHead className="text-right">Reports</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.market_comparison.slice(0, 10).map((m) => (
                        <TableRow key={`${m.state}-${m.market}`}>
                          <TableCell className="font-medium">{m.market}</TableCell>
                          <TableCell>{m.state}</TableCell>
                          <TableCell className="text-right font-semibold text-primary">
                            {formatINR(m.avg_modal)}
                          </TableCell>
                          <TableCell className="text-right">
                            {formatINR(m.min_modal)} / {formatINR(m.max_modal)}
                          </TableCell>
                          <TableCell className="text-right">{formatNumber(m.reports)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </ChartCard>
              </section>

              {/* Paginated Raw Daily Records Table */}
              <ChartCard
                title="Daily price records"
                description={`Showing page ${data.pagination.page} of ${Math.max(data.pagination.total_pages, 1)} (${formatNumber(data.pagination.total_items)} total rows).`}
                actions={
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={page <= 1}
                      onClick={() => setPage((p) => Math.max(1, p - 1))}
                    >
                      <ChevronLeft className="h-4 w-4" />
                      Prev
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={page >= data.pagination.total_pages}
                      onClick={() => setPage((p) => p + 1)}
                    >
                      Next
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                  </div>
                }
              >
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Date</TableHead>
                      <TableHead>State</TableHead>
                      <TableHead>District</TableHead>
                      <TableHead>Market</TableHead>
                      <TableHead>Variety</TableHead>
                      <TableHead className="text-right">Min</TableHead>
                      <TableHead className="text-right">Modal</TableHead>
                      <TableHead className="text-right">Max</TableHead>
                      <TableHead>Check</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.items.map((row, idx) => (
                      <TableRow key={`${row.arrival_date}-${row.market}-${idx}`}>
                        <TableCell>{row.arrival_date}</TableCell>
                        <TableCell>{row.state}</TableCell>
                        <TableCell>{row.district}</TableCell>
                        <TableCell className="font-medium">{row.market}</TableCell>
                        <TableCell>{row.variety || "—"}</TableCell>
                        <TableCell className="text-right">{formatINR(row.min_price)}</TableCell>
                        <TableCell className="text-right font-semibold text-primary">
                          {formatINR(row.modal_price)}
                        </TableCell>
                        <TableCell className="text-right">{formatINR(row.max_price)}</TableCell>
                        <TableCell>
                          {row.outlier_flag ? (
                            <Badge variant="terracotta">Unusual</Badge>
                          ) : (
                            <Badge variant="default">OK</Badge>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ChartCard>
            </>
          )}
        </>
      ) : null}
    </div>
  );
}
