"use client";

import * as React from "react";
import {
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  Download,
  RefreshCw,
  ShieldAlert,
  TrendingUp,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART_COLORS, formatTooltipPct } from "@/components/charts/chartHelpers";
import {
  ChartCard,
  EmptyState,
  ErrorState,
  KpiCard,
  LoadingState,
  RefetchIndicator,
} from "@/components/common/StateFeedback";
import { PageHeader } from "@/components/common/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { SelectField } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { mandiApi } from "@/lib/api";
import { checkBacktestExport, saveBlobAsFile, type ExportCheck } from "@/lib/backtestExport";
import type { BacktestDataCoverage, BacktestResponse } from "@/lib/types";
import { formatINR, formatNumber, formatSignedPct } from "@/lib/utils";

export default function BacktestingPage() {
  const [evalDays, setEvalDays] = React.useState<number>(365);
  const [stepDays, setStepDays] = React.useState<number>(7);
  const [page, setPage] = React.useState<number>(1);
  const [refreshTick, setRefreshTick] = React.useState<number>(0);
  // refreshTick value whose refresh=true request has completed; later requests reuse the server's cached results.
  const completedRefreshRef = React.useRef<number>(0);
  const [reloadTick, setReloadTick] = React.useState<number>(0);
  const requestKey = `${evalDays}|${stepDays}|${page}|${refreshTick}|${reloadTick}`;
  const [state, setState] = React.useState<{
    key: string;
    data: BacktestResponse | null;
    error: string | null;
  }>({ key: "", data: null, error: null });

  React.useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const refresh = refreshTick !== completedRefreshRef.current;

    mandiApi
      .getBacktestResults(
        {
          eval_days: evalDays,
          step_days: stepDays,
          refresh,
          page,
          page_size: 20,
        },
        controller.signal
      )
      .then((res) => {
        if (refresh) completedRefreshRef.current = refreshTick;
        if (active) setState({ key: requestKey, data: res, error: null });
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (active) {
          setState((prev) => ({
            key: requestKey,
            data: prev.data,
            error: err instanceof Error ? err.message : "Failed to load backtest results",
          }));
        }
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [evalDays, stepDays, page, refreshTick, reloadTick, requestKey]);

  const { data, error } = state;
  const loading = state.key !== requestKey;

  // Export asks the server for its latest results for the displayed window/step. The server cache is per-process,
  // so the file can come from a different source than the page; the response headers are compared with what is on
  // screen and any mismatch is reported instead of claiming the export matches.
  const [exportState, setExportState] = React.useState<{
    running: boolean;
    forKey: string;
    check: ExportCheck | null;
    error: string | null;
  }>({ running: false, forKey: "", check: null, error: null });
  const exportKey = data ? `${data.eval_days}|${data.step_days}|${refreshTick}` : "";

  const handleExport = async () => {
    if (!data) return;
    const key = exportKey;
    const displayed = { usedPrecomputedCsv: data.used_precomputed_csv, totalRows: data.pagination.total_items };
    setExportState({ running: true, forKey: key, check: null, error: null });
    try {
      const result = await mandiApi.downloadBacktestExport({ eval_days: data.eval_days, step_days: data.step_days });
      saveBlobAsFile(result.blob, result.filename);
      setExportState({ running: false, forKey: key, check: checkBacktestExport(displayed, result), error: null });
    } catch (err: unknown) {
      setExportState({
        running: false,
        forKey: key,
        check: null,
        error: err instanceof Error ? err.message : "Failed to export backtest CSV",
      });
    }
  };
  const visibleExportState = exportState.forKey === exportKey ? exportState : null;

  const strategyComparisonData = React.useMemo(() => {
    if (!data) return [];
    return [
      {
        strategy: "MandiMitra Rules",
        Accuracy: data.mandimitra.accuracy_pct,
        "Captured Move": data.mandimitra.avg_captured_move_pct,
      },
      {
        strategy: "Always-Sell Baseline",
        Accuracy: data.matched_always_sell_accuracy_pct,
        "Captured Move": data.baseline_always_sell.avg_captured_move_pct,
      },
      {
        strategy: "Momentum-Only",
        Accuracy: data.baseline_momentum_only.accuracy_pct,
        "Captured Move": data.baseline_momentum_only.avg_captured_move_pct,
      },
    ];
  }, [data]);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Methodology"
        title="Backtesting"
        icon={TrendingUp}
        description="How well would past SELL NOW / WAIT advice have worked? We replay the rules on historical prices and compare them with simple strategies."
        badges={data?.is_demo ? <Badge variant="gold">Simulated Demo Evaluation</Badge> : null}
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setPage(1);
                setRefreshTick((t) => t + 1);
              }}
              disabled={loading}
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
              Re-run Backtest
            </Button>
            <Button
              variant="default"
              size="sm"
              disabled={!data || loading || exportState.running || data.pagination.total_items === 0}
              onClick={() => void handleExport()}
              title="Downloads the server's latest results for the displayed window and step, then checks them against the page"
            >
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              {exportState.running ? "Exporting..." : "Export CSV"}
            </Button>
          </>
        }
      />

      {/* Parameters Card */}
      <Card>
        <CardContent className="grid grid-cols-1 gap-4 p-5 sm:grid-cols-3 sm:p-6">
          <SelectField
            label="Evaluation period"
            value={String(evalDays)}
            onChange={(e) => {
              setEvalDays(Number(e.target.value));
              setPage(1);
            }}
            options={[
              { value: "90", label: "90 days" },
              { value: "180", label: "180 days" },
              { value: "365", label: "365 days (1 year)" },
            ]}
          />
          <SelectField
            label="Check every"
            value={String(stepDays)}
            onChange={(e) => {
              setStepDays(Number(e.target.value));
              setPage(1);
            }}
            options={[
              { value: "7", label: "Every 7 days" },
              { value: "14", label: "Every 14 days" },
            ]}
          />
          <div className="flex items-end text-xs text-muted-foreground">
            {data ? (
              <span>
                <strong className="font-semibold text-foreground">{formatNumber(data.mandimitra.total_cases)}</strong>{" "}
                past decisions evaluated
              </span>
            ) : (
              <span>Loading...</span>
            )}
          </div>
        </CardContent>
      </Card>

      {visibleExportState?.check ? (
        <Alert
          variant={visibleExportState.check.tone === "success" ? "live" : "demo"}
          className="text-xs sm:text-sm"
          data-testid="export-check"
          data-tone={visibleExportState.check.tone}
        >
          {visibleExportState.check.message}
        </Alert>
      ) : null}
      {visibleExportState?.error ? (
        <ErrorState title="CSV export failed" message={visibleExportState.error} onRetry={() => void handleExport()} />
      ) : null}

      {data?.is_demo ? (
        <Alert variant="demo" className="flex items-start gap-2.5 text-xs sm:text-sm">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <div>
            <strong>Simulated Backtest Warning:</strong> These evaluation metrics are computed over simulated demo
            fixtures — they demonstrate methodology and do not guarantee future mandi returns.
          </div>
        </Alert>
      ) : null}

      {loading && !data ? <LoadingState label="Running the backtest..." /> : null}
      {loading && data ? (
        <RefetchIndicator label="Loading backtest results for the selected page and parameters..." />
      ) : null}
      {error ? (
        <ErrorState
          title="Backtest failed"
          message={error}
          onRetry={() => setReloadTick((t) => t + 1)}
        />
      ) : null}

      {data ? (
        <>
          <p className="text-xs text-muted-foreground">
            Data source: <span className="font-medium text-foreground">{data.source_label}</span>
          </p>
          {data.mandimitra.total_cases === 0 ? (
            data.data_coverage && data.data_coverage.required_span_days > 0 ? (
              <HistoryNeededNotice coverage={data.data_coverage} />
            ) : (
              <EmptyState
                title="No backtest evaluations available"
                message="Not enough historical price depth exists in the active warehouse to score rolling-origin cutoffs."
              />
            )
          ) : (
            <>
              <section className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-5">
                <KpiCard
                  label="Signals Evaluated"
                  value={formatNumber(data.mandimitra.total_cases)}
                  delta={`${data.mandimitra.decisive_cases} decisive (${data.mandimitra.neutral_coverage_pct.toFixed(1)}% neutral)`}
                  tone="green"
                />
                <KpiCard
                  label="MandiMitra Accuracy"
                  value={`${data.mandimitra.accuracy_pct.toFixed(1)}%`}
                  delta={`Always-Sell baseline: ${data.matched_always_sell_accuracy_pct.toFixed(1)}%`}
                  tone="gold"
                />
                <KpiCard
                  label="WAIT Call Precision"
                  value={`${data.mandimitra.wait_precision_pct.toFixed(1)}%`}
                  delta={`${data.mandimitra.wait_calls} WAIT calls · avg move ${formatSignedPct(data.mandimitra.avg_wait_move_pct)}`}
                />
                <KpiCard
                  label="SELL NOW Precision"
                  value={`${data.mandimitra.sell_precision_pct.toFixed(1)}%`}
                  delta={`${data.mandimitra.sell_calls} SELL calls · avg move ${formatSignedPct(data.mandimitra.avg_sell_move_pct)}`}
                />
                <KpiCard
                  label="Avg Captured Move"
                  value={formatSignedPct(data.mandimitra.avg_captured_move_pct)}
                  delta={`Momentum baseline: ${data.baseline_momentum_only.accuracy_pct.toFixed(1)}% acc`}
                  tone={data.mandimitra.avg_captured_move_pct >= 0 ? "green" : "terracotta"}
                />
              </section>

              {/* Charts Row */}
              <section className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                <ChartCard
                  title="MandiMitra vs simple strategies"
                  description="Accuracy and price move captured, compared with always selling immediately or following momentum."
                >
                  <div className="h-72 w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={strategyComparisonData} margin={{ top: 10, right: 16, left: 4, bottom: 8 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                        <XAxis dataKey="strategy" tick={{ fontSize: 11 }} />
                        <YAxis unit="%" tick={{ fontSize: 11 }} />
                        <Tooltip formatter={formatTooltipPct} />
                        <Legend />
                        <Bar dataKey="Accuracy" name="Directional Accuracy (%)" fill={CHART_COLORS.green} radius={[4, 4, 0, 0]} />
                        <Bar dataKey="Captured Move" name="Avg Captured Move (%)" fill={CHART_COLORS.gold} radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </ChartCard>

                <ChartCard
                  title="Perishable vs storable crops"
                  description="Evaluated separately because storage cost and shelf life differ."
                >
                  {data.by_storability.length === 0 ? (
                    <EmptyState message="No storability breakdown available." />
                  ) : (
                    <div className="h-72 w-full">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={data.by_storability} margin={{ top: 10, right: 16, left: 4, bottom: 8 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                          <XAxis dataKey="crop_group" tick={{ fontSize: 11 }} />
                          <YAxis unit="%" tick={{ fontSize: 11 }} />
                          <Tooltip formatter={formatTooltipPct} />
                          <Legend />
                          <Bar
                            dataKey="accuracy_pct"
                            name="MandiMitra Accuracy (%)"
                            fill={CHART_COLORS.emerald}
                            radius={[4, 4, 0, 0]}
                          />
                          <Bar
                            dataKey="always_sell_acc_pct"
                            name="Always-Sell Accuracy (%)"
                            fill={CHART_COLORS.terracotta}
                            radius={[4, 4, 0, 0]}
                          />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </ChartCard>
              </section>

              {/* Commodity Summary + Thresholds */}
              <section className="grid grid-cols-1 gap-5 lg:grid-cols-3">
                <ChartCard
                  title="Results by commodity"
                  description="Accuracy, WAIT precision, and baseline comparison per commodity."
                  className="lg:col-span-2"
                >
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Commodity</TableHead>
                        <TableHead>Storability</TableHead>
                        <TableHead className="text-right">Cases</TableHead>
                        <TableHead className="text-right">Accuracy %</TableHead>
                        <TableHead className="text-right">Always-Sell %</TableHead>
                        <TableHead className="text-right">WAIT Move %</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.by_commodity.map((c) => (
                        <TableRow key={c.commodity}>
                          <TableCell className="font-medium">{c.commodity}</TableCell>
                          <TableCell>{c.storable ? "Storable" : "Perishable"}</TableCell>
                          <TableCell className="text-right">{formatNumber(c.total_cases)}</TableCell>
                          <TableCell className="text-right font-semibold text-primary">
                            {c.accuracy_pct.toFixed(1)}%
                          </TableCell>
                          <TableCell className="text-right">{c.always_sell_acc_pct.toFixed(1)}%</TableCell>
                          <TableCell className="text-right">{formatSignedPct(c.avg_wait_move_pct)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </ChartCard>

                <ChartCard
                  title="Decision thresholds"
                  description="Walk-forward calibrated decision thresholds."
                >
                  <dl className="space-y-2.5 text-xs">
                    {Object.entries(data.thresholds).map(([key, val]) => (
                      <div key={key} className="flex items-center justify-between border-b border-border/60 pb-1.5">
                        <dt className="font-mono text-muted-foreground">{key}</dt>
                        <dd className="font-semibold text-foreground">{String(val)}</dd>
                      </div>
                    ))}
                  </dl>
                </ChartCard>
              </section>

              {/* Sample Evaluation Rows */}
              <ChartCard
                title="Past decisions"
                description={`Page ${data.pagination.page} of ${Math.max(data.pagination.total_pages, 1)} (${formatNumber(data.pagination.total_items)} total evaluations).`}
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
                      <TableHead>Decision Date</TableHead>
                      <TableHead>Commodity</TableHead>
                      <TableHead>Market</TableHead>
                      <TableHead>Verdict</TableHead>
                      <TableHead className="text-right">Decision Price</TableHead>
                      <TableHead className="text-right">Future 14d Avg</TableHead>
                      <TableHead className="text-right">14d Change</TableHead>
                      <TableHead>Outcome</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.sample_rows.map((r, idx) => (
                      <TableRow key={`${r.decision_date}-${r.market}-${idx}`}>
                        <TableCell>{r.decision_date}</TableCell>
                        <TableCell className="font-medium">{r.commodity}</TableCell>
                        <TableCell>
                          {r.market}, {r.state}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              r.verdict_class === "SELL NOW"
                                ? "default"
                                : r.verdict_class === "WAIT"
                                  ? "gold"
                                  : "terracotta"
                            }
                          >
                            {r.verdict_class}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right">{formatINR(r.decision_price)}</TableCell>
                        <TableCell className="text-right">{formatINR(r.future_avg_14d)}</TableCell>
                        <TableCell className="text-right">{formatSignedPct(r.future_14d_change_pct)}</TableCell>
                        <TableCell className="text-xs">
                          {r.is_correct === true ? (
                            <Badge variant="default">Hit ({r.actual_outcome})</Badge>
                          ) : r.is_correct === false ? (
                            <Badge variant="terracotta">Miss ({r.actual_outcome})</Badge>
                          ) : (
                            <Badge variant="neutral">Neutral ({r.actual_outcome})</Badge>
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

function formatDay(isoDate: string | null): string {
  if (!isoDate) return "—";
  return new Date(`${isoDate}T00:00:00Z`).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** Explains an empty backtest: how much price history exists and when the first evaluations can appear. */
function HistoryNeededNotice({ coverage }: { coverage: BacktestDataCoverage }) {
  const span = Math.max(coverage.history_span_days, 0);
  const required = coverage.required_span_days;
  const pct = Math.min(100, Math.round((span / required) * 100));
  const spanReached = span >= required;

  return (
    <Card data-testid="backtest-history-needed">
      <CardContent className="space-y-5 p-6 sm:p-7">
        <div className="flex items-start gap-4">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-tone-gold-soft text-tone-gold">
            <CalendarClock className="h-5 w-5" aria-hidden="true" />
          </span>
          <div className="space-y-1.5">
            <h2 className="text-lg font-semibold tracking-tight text-foreground">Not enough price history yet</h2>
            <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
              A backtest replays past SELL NOW / WAIT decisions and checks each one against what prices actually did
              over the following 14 days. The database has prices for{" "}
              <strong className="text-foreground">
                {coverage.history_days} {coverage.history_days === 1 ? "day" : "days"}
              </strong>{" "}
              ({formatDay(coverage.history_start)} – {formatDay(coverage.history_end)}), so no past decision can be
              scored yet.
            </p>
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>History collected</span>
            <span className="tabular font-medium text-foreground">
              {span} of {required} days
            </span>
          </div>
          <div
            className="h-2.5 w-full overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-label="Price history collected"
            aria-valuemin={0}
            aria-valuemax={required}
            aria-valuenow={Math.min(span, required)}
          >
            <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
          </div>
        </div>

        <div className="rounded-xl border border-border bg-muted/50 p-4 text-sm leading-relaxed text-foreground/90">
          {spanReached ? (
            <>
              The date range is now long enough, but gaps in the daily price capture mean no decision date yet has
              prices both just before it and over the 14 days after it. Results will appear as more consecutive days
              are captured.
            </>
          ) : (
            <>
              If prices keep being captured every day, the first results should appear around{" "}
              <strong>{formatDay(coverage.results_expected_from)}</strong> at the earliest (missed days push this
              later). Accuracy figures become meaningful after several weeks of history, because the signals compare
              recent prices with the previous month.
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
