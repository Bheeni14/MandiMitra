"use client";

import * as React from "react";
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, Database, ShieldCheck } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { CHART_COLORS } from "@/components/charts/chartHelpers";
import {
  ChartCard,
  EmptyState,
  ErrorState,
  KpiCard,
  LoadingState,
  RefetchIndicator,
  TechnicalDetails,
} from "@/components/common/StateFeedback";
import { PageHeader } from "@/components/common/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { mandiApi } from "@/lib/api";
import type { DataSourcesStatusResponse, PipelineHistoryResponse } from "@/lib/types";
import { formatNumber, formatTimestamp } from "@/lib/utils";

export default function DataSourcesPage() {
  const [runsPage, setRunsPage] = React.useState<number>(1);
  const [rejectsPage, setRejectsPage] = React.useState<number>(1);
  const [reloadTick, setReloadTick] = React.useState<number>(0);
  const requestKey = `${runsPage}|${rejectsPage}|${reloadTick}`;
  const [state, setState] = React.useState<{
    key: string;
    status: DataSourcesStatusResponse | null;
    history: PipelineHistoryResponse | null;
    error: string | null;
  }>({ key: "", status: null, history: null, error: null });

  React.useEffect(() => {
    const controller = new AbortController();
    let active = true;

    Promise.all([
      mandiApi.getDataSourcesStatus(controller.signal),
      mandiApi.getPipelineHistory(
        { runs_page: runsPage, runs_page_size: 15, rejects_page: rejectsPage, rejects_page_size: 15 },
        controller.signal
      ),
    ])
      .then(([s, h]) => {
        if (active) setState({ key: requestKey, status: s, history: h, error: null });
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (active) {
          setState((prev) => ({
            key: requestKey,
            status: prev.status,
            history: prev.history,
            error: err instanceof Error ? err.message : "Failed to load data sources and pipeline history",
          }));
        }
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [runsPage, rejectsPage, reloadTick, requestKey]);

  const { status, history, error } = state;
  const loading = state.key !== requestKey;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Methodology"
        title="Data Sources"
        icon={Database}
        description="Where MandiMitra&apos;s prices come from, and how every record is quality-checked before it reaches you."
      />

      {loading && !status ? <LoadingState label="Loading data sources..." /> : null}
      {loading && status ? <RefetchIndicator label="Loading..." /> : null}
      {error ? (
        <ErrorState
          title="Could not load data sources"
          message={error}
          onRetry={() => setReloadTick((t) => t + 1)}
        />
      ) : null}

      {status && history ? (
        <>
          <section className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-5">
            <KpiCard
              label="Data updates"
              value={formatNumber(history.runs_pagination.total_items)}
              delta={
                status.latest_run?.started_at
                  ? `Last: ${formatTimestamp(status.latest_run.started_at)}`
                  : "No runs recorded"
              }
              icon={<Database className="h-4 w-4" />}
              tone="green"
            />
            <KpiCard
              label="Last update status"
              value={status.latest_run?.status || "N/A"}
              delta={status.latest_run?.source_name || "—"}
            />
            <KpiCard
              label="Price records"
              value={formatNumber(status.data_quality.total_price_records)}
              delta={`${formatNumber(status.coverage.commodities_count)} crops · ${formatNumber(status.coverage.markets_count)} mandis`}
            />
            <KpiCard
              label="Unusual prices flagged"
              value={formatNumber(status.data_quality.outliers_flagged_count)}
              delta={`${status.data_quality.outliers_flagged_pct.toFixed(2)}% of all price records`}
              icon={<AlertTriangle className="h-4 w-4" />}
              tone="gold"
            />
            <KpiCard
              label="Records rejected"
              value={formatNumber(status.data_quality.total_dq_rejects)}
              delta="Failed automatic quality checks"
              icon={<ShieldCheck className="h-4 w-4" />}
              tone={status.data_quality.total_dq_rejects > 0 ? "terracotta" : "default"}
            />
          </section>

          {/* Provenance & quality */}
          <section className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <ChartCard
              title="Where the prices come from"
              description="Price records by source and the dates they cover."
            >
              {status.price_sources.length === 0 ? (
                <EmptyState message="No source records available yet." />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Source</TableHead>
                      <TableHead className="text-right">Rows</TableHead>
                      <TableHead>Earliest</TableHead>
                      <TableHead>Latest</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {status.price_sources.map((p) => (
                      <TableRow key={p.source_name}>
                        <TableCell className="font-medium">{p.source_name}</TableCell>
                        <TableCell className="text-right">{formatNumber(p.rows)}</TableCell>
                        <TableCell>{p.min_date || "—"}</TableCell>
                        <TableCell>{p.max_date || "—"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </ChartCard>

            <ChartCard
              title="Why records were rejected"
              description="Records that failed format, range or date checks during import."
            >
              {history.reject_reason_summary.length === 0 ? (
                <EmptyState
                  title="No rejected records"
                  message="All ingested records passed automated data-quality checks."
                />
              ) : (
                <div className="h-72 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={history.reject_reason_summary}
                      margin={{ top: 10, right: 16, left: 4, bottom: 20 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                      <XAxis dataKey="reject_reason" tick={{ fontSize: 11 }} angle={-15} textAnchor="end" height={50} />
                      <YAxis tick={{ fontSize: 11 }} />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="count" name="Rejected records" fill={CHART_COLORS.terracotta} radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </ChartCard>
          </section>

          <TechnicalDetails
            title="Import logs"
            description="Rejected-record log and the history of every data update."
          >
            <div className="space-y-5">
              <ChartCard
                title="Rejected records log"
                description={`Page ${history.rejects_pagination.page} of ${Math.max(history.rejects_pagination.total_pages, 1)} (${formatNumber(history.rejects_pagination.total_items)} total rejects).`}
                actions={
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={rejectsPage <= 1}
                      onClick={() => setRejectsPage((p) => Math.max(1, p - 1))}
                    >
                      <ChevronLeft className="h-4 w-4" />
                      Prev
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={rejectsPage >= history.rejects_pagination.total_pages}
                      onClick={() => setRejectsPage((p) => p + 1)}
                    >
                      Next
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                  </div>
                }
              >
                {history.rejects.length === 0 ? (
                  <EmptyState message="No rejected records on this page." />
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Source</TableHead>
                        <TableHead>Stage</TableHead>
                        <TableHead>Reason</TableHead>
                        <TableHead>Logged At</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {history.rejects.map((rej) => (
                        <TableRow key={rej.reject_id}>
                          <TableCell className="font-medium">{rej.source_name}</TableCell>
                          <TableCell>{rej.stage}</TableCell>
                          <TableCell className="max-w-xs truncate" title={rej.reject_reason}>
                            <Badge variant="terracotta">{rej.reject_reason}</Badge>
                          </TableCell>
                          <TableCell>{formatTimestamp(rej.rejected_at)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </ChartCard>

              <ChartCard
              title="Data update history"
              description={`Page ${history.runs_pagination.page} of ${Math.max(history.runs_pagination.total_pages, 1)} (${formatNumber(history.runs_pagination.total_items)} runs).`}
              actions={
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={runsPage <= 1}
                    onClick={() => setRunsPage((p) => Math.max(1, p - 1))}
                  >
                    <ChevronLeft className="h-4 w-4" />
                    Prev
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={runsPage >= history.runs_pagination.total_pages}
                    onClick={() => setRunsPage((p) => p + 1)}
                  >
                    Next
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
              }
              >
              {history.runs.length === 0 ? (
                <EmptyState message="No ETL pipeline runs recorded yet." />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Run ID</TableHead>
                      <TableHead>Stage</TableHead>
                      <TableHead>Source</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="text-right">Fetched</TableHead>
                      <TableHead className="text-right">Loaded</TableHead>
                      <TableHead className="text-right">Rejected</TableHead>
                      <TableHead>Started At</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {history.runs.map((run) => (
                      <TableRow key={run.run_id}>
                        <TableCell className="font-mono text-xs">#{run.run_id}</TableCell>
                        <TableCell>{run.stage}</TableCell>
                        <TableCell className="font-medium">{run.source_name}</TableCell>
                        <TableCell>
                          <Badge variant={run.status.toUpperCase() === "SUCCESS" ? "default" : "terracotta"}>
                            {run.status.toUpperCase() === "SUCCESS" ? <CheckCircle2 className="h-3 w-3" /> : null}
                            {run.status.toUpperCase()}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right">{formatNumber(run.rows_fetched)}</TableCell>
                        <TableCell className="text-right">{formatNumber(run.rows_loaded)}</TableCell>
                        <TableCell className="text-right">{formatNumber(run.rows_rejected)}</TableCell>
                        <TableCell>{formatTimestamp(run.started_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
              </ChartCard>
            </div>
          </TechnicalDetails>
        </>
      ) : null}
    </div>
  );
}
