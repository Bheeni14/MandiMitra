"use client";

import * as React from "react";
import {
  AlertCircle,
  Brain,
  CheckCircle2,
  Clock3,
  Hourglass,
  Info,
  MapPin,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART_COLORS, formatCvPct, formatPriceArrivalsTooltip } from "@/components/charts/chartHelpers";
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
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { fieldLabelClass, SelectField } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { describeValidationStatus } from "@/lib/advisor";
import { mandiApi } from "@/lib/api";
import type { AdvisorResponse, MarketItem } from "@/lib/types";
import { cn, formatINR, formatNumber, formatSignedPct, formatTimestamp } from "@/lib/utils";

type LanguageMode = "Both" | "English" | "Hindi";

const ACTION_BADGE_MAP: Record<string, { variant: "default" | "gold" | "terracotta"; label: string }> = {
  "SELL NOW": { variant: "default", label: "SELL NOW" },
  WAIT: { variant: "gold", label: "WAIT" },
  NEUTRAL: { variant: "terracotta", label: "NEUTRAL" },
};

export default function AiAdvisorPage() {
  const [commodities, setCommodities] = React.useState<string[]>([]);
  const [commodity, setCommodity] = React.useState<string>("");
  const [marketItems, setMarketItems] = React.useState<MarketItem[]>([]);
  // Commodity whose markets are currently loaded; recommendations wait until it matches the selected commodity.
  const [marketsFor, setMarketsFor] = React.useState<string | null>(null);
  const [marketsError, setMarketsError] = React.useState<string | null>(null);
  const [marketsReloadTick, setMarketsReloadTick] = React.useState<number>(0);
  const [selectedState, setSelectedState] = React.useState<string>("");
  const [selectedDistrict, setSelectedDistrict] = React.useState<string>("");
  const [selectedMarket, setSelectedMarket] = React.useState<string>("");
  const [language, setLanguage] = React.useState<LanguageMode>("Both");

  // The recommendation is stored with the selection (commodity|state|market) it was computed for and only shown
  // while that selection is still current, so a previous market's verdict is never presented for a new one.
  const [advisorEntry, setAdvisorEntry] = React.useState<{ key: string; data: AdvisorResponse } | null>(null);
  const [loading, setLoading] = React.useState<boolean>(true);
  const [regenerating, setRegenerating] = React.useState<boolean>(false);
  const [error, setError] = React.useState<string | null>(null);

  // Client-side memo cache keyed by commodity|state|market so language switching or revisiting never duplicates calls
  const clientCacheRef = React.useRef<Map<string, AdvisorResponse>>(new Map());
  // Incremented for every recommendation request; responses from older requests are discarded.
  const requestSeqRef = React.useRef<number>(0);

  // 1. Load commodities on mount
  React.useEffect(() => {
    const controller = new AbortController();
    mandiApi
      .getCommodities(controller.signal)
      .then((res) => {
        const names = res.items.map((c) => c.commodity_name);
        setCommodities(names);
        if (names.length > 0) {
          setCommodity(names.includes("Onion") ? "Onion" : names[0]);
        } else {
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Failed to load commodities");
        setLoading(false);
      });
    return () => controller.abort();
  }, []);

  // 2. Load markets when commodity changes
  React.useEffect(() => {
    if (!commodity) return;
    const controller = new AbortController();
    let active = true;
    mandiApi
      .getMarkets({ commodity }, controller.signal)
      .then((res) => {
        if (!active) return;
        setMarketItems(res.items);
        setMarketsError(null);
        if (res.items.length > 0) {
          const first = res.items[0];
          setSelectedState(first.state);
          setSelectedDistrict(first.district);
          setSelectedMarket(first.market_name);
        } else {
          setSelectedState("");
          setSelectedDistrict("");
          setSelectedMarket("");
          setAdvisorEntry(null);
          setLoading(false);
        }
        setMarketsFor(commodity);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (!active) return;
        setMarketsError(err instanceof Error ? err.message : "Failed to load markets");
        setLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [commodity, marketsReloadTick]);

  const selectionReady = Boolean(commodity && marketsFor === commodity && selectedMarket && selectedState);
  const selectionKey = `${commodity}|${selectedState}|${selectedMarket}`;
  // Hidden (null) the moment the selected market changes, until the new selection's request succeeds.
  const advisor = advisorEntry && advisorEntry.key === selectionKey ? advisorEntry.data : null;

  const fetchRecommendation = React.useCallback(
    async (opts: { forceRefresh: boolean; signal?: AbortSignal }) => {
      if (!selectionReady) return;
      const cacheKey = `${commodity}|${selectedState}|${selectedMarket}`;
      const seq = ++requestSeqRef.current;

      if (!opts.forceRefresh) {
        const cachedLocal = clientCacheRef.current.get(cacheKey);
        if (cachedLocal) {
          setAdvisorEntry({ key: cacheKey, data: cachedLocal });
          setLoading(false);
          setRegenerating(false);
          setError(null);
          return;
        }
      }

      if (opts.forceRefresh) {
        setRegenerating(true);
      } else {
        setLoading(true);
      }
      setError(null);

      try {
        const res = await mandiApi.postAdvisorRecommendation(
          {
            commodity,
            market: selectedMarket,
            state: selectedState,
            force_refresh: opts.forceRefresh,
          },
          opts.signal
        );
        clientCacheRef.current.set(cacheKey, res);
        if (seq === requestSeqRef.current) setAdvisorEntry({ key: cacheKey, data: res });
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (seq === requestSeqRef.current) {
          setError(err instanceof Error ? err.message : "Failed to generate advisor recommendation");
        }
      } finally {
        if (seq === requestSeqRef.current) {
          setLoading(false);
          setRegenerating(false);
        }
      }
    },
    [selectionReady, commodity, selectedMarket, selectedState]
  );

  // 3. Auto-fetch recommendation once the selection belongs to the current commodity's market list
  React.useEffect(() => {
    if (!selectionReady) return;
    const controller = new AbortController();
    void fetchRecommendation({ forceRefresh: false, signal: controller.signal });
    return () => controller.abort();
  }, [selectionReady, fetchRecommendation]);

  const handleCommodityChange = (newCommodity: string) => {
    if (newCommodity === commodity) return;
    // Invalidate in-flight requests and drop the previous commodity's market/state before markets reload.
    requestSeqRef.current++;
    setCommodity(newCommodity);
    setMarketsFor(null);
    setMarketItems([]);
    setSelectedState("");
    setSelectedDistrict("");
    setSelectedMarket("");
    setAdvisorEntry(null);
    setError(null);
    setRegenerating(false);
    setLoading(true);
  };

  const statesForCommodity = React.useMemo(
    () => Array.from(new Set(marketItems.map((o) => o.state))).sort(),
    [marketItems]
  );

  const districtsForState = React.useMemo(
    () =>
      Array.from(
        new Set(marketItems.filter((o) => o.state === selectedState).map((o) => o.district))
      ).sort(),
    [marketItems, selectedState]
  );

  const marketsForDistrict = React.useMemo(
    () =>
      marketItems
        .filter((o) => o.state === selectedState && o.district === selectedDistrict)
        .map((o) => o.market_name),
    [marketItems, selectedState, selectedDistrict]
  );

  const handleStateChange = (newState: string) => {
    setSelectedState(newState);
    const matching = marketItems.filter((o) => o.state === newState);
    if (matching.length > 0) {
      setSelectedDistrict(matching[0].district);
      setSelectedMarket(matching[0].market_name);
    }
  };

  const handleDistrictChange = (newDistrict: string) => {
    setSelectedDistrict(newDistrict);
    const matching = marketItems.filter((o) => o.state === selectedState && o.district === newDistrict);
    if (matching.length > 0) {
      setSelectedMarket(matching[0].market_name);
    }
  };

  const validationMeta = advisor ? describeValidationStatus(advisor.briefing.validation_status) : null;

  const actionMeta = advisor
    ? ACTION_BADGE_MAP[advisor.signal.verdict_class] ?? { variant: "neutral" as const, label: advisor.signal.verdict_class }
    : null;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Advisory"
        title="AI Crop Advisor"
        icon={Brain}
        description="Should you sell your crop now or wait? Pick your crop and mandi to get a clear recommendation, explained in English and Hindi."
        actions={
          <Button
            onClick={() => void fetchRecommendation({ forceRefresh: true })}
            disabled={!selectionReady || loading || regenerating}
          >
            <RefreshCw className={`h-4 w-4 ${regenerating ? "animate-spin" : ""}`} aria-hidden="true" />
            {regenerating ? "Regenerating..." : "Regenerate Briefing"}
          </Button>
        }
      />

      {/* Selection & Client-side Language Controls */}
      <Card>
        <CardContent className="grid grid-cols-1 gap-4 p-5 sm:grid-cols-2 sm:p-6 lg:grid-cols-5">
          <SelectField
            label="Commodity"
            value={commodity}
            onChange={(e) => handleCommodityChange(e.target.value)}
            options={commodities.map((c) => ({ value: c, label: c }))}
          />
          <SelectField
            label="State"
            value={selectedState}
            onChange={(e) => handleStateChange(e.target.value)}
            options={statesForCommodity.map((s) => ({ value: s, label: s }))}
          />
          <SelectField
            label="District"
            value={selectedDistrict}
            onChange={(e) => handleDistrictChange(e.target.value)}
            options={districtsForState.map((d) => ({ value: d, label: d }))}
          />
          <SelectField
            label="Market (Mandi)"
            value={selectedMarket}
            onChange={(e) => setSelectedMarket(e.target.value)}
            options={marketsForDistrict.map((m) => ({ value: m, label: m }))}
          />
          <div className="flex flex-col gap-1.5">
            <span className={fieldLabelClass}>Explanation language</span>
            <div
              className="inline-flex h-10 rounded-xl border border-input bg-muted p-1"
              role="group"
              aria-label="Language view"
            >
              {(["Both", "English", "Hindi"] as const).map((lang) => (
                <button
                  key={lang}
                  type="button"
                  onClick={() => setLanguage(lang)}
                  className={cn(
                    "flex-1 cursor-pointer rounded-lg px-2 text-xs font-medium transition-all",
                    language === lang
                      ? "bg-white text-primary shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  {lang}
                </button>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>

      {loading && !advisor ? <LoadingState label="Evaluating SQL price rules and preparing your recommendation..." /> : null}
      {loading && advisor ? <RefetchIndicator label="Evaluating the selected market..." /> : null}
      {marketsError ? (
        <ErrorState
          title="Could not load markets for this commodity"
          message={marketsError}
          onRetry={() => setMarketsReloadTick((t) => t + 1)}
        />
      ) : null}
      {!marketsError && marketsFor === commodity && commodity && marketItems.length === 0 ? (
        <EmptyState
          title="No mandis report this commodity"
          message={`No markets in the warehouse carry price records for ${commodity}.`}
        />
      ) : null}
      {error ? (
        <ErrorState
          title="Could not get a recommendation"
          message={error}
          onRetry={() => void fetchRecommendation({ forceRefresh: false })}
        />
      ) : null}

      {advisor ? (
        <>
          {/* Verdict */}
          <Card
            className={cn(
              "relative overflow-hidden border-0 text-white shadow-raised",
              advisor.signal.verdict_class === "SELL NOW"
                ? "bg-linear-to-br from-[#0f3d24] via-[#145332] to-[#1d6b40]"
                : advisor.signal.verdict_class === "WAIT"
                  ? "bg-linear-to-br from-[#4a3505] via-[#6b4c08] to-[#8a6410]"
                  : "bg-linear-to-br from-[#1e293b] via-[#2b3a4f] to-[#3b4b63]"
            )}
          >
            <div
              className="pointer-events-none absolute -right-20 -top-20 h-64 w-64 rounded-full bg-white/10 blur-3xl"
              aria-hidden="true"
            />
            <div className="relative flex flex-col gap-6 p-6 sm:p-7 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex items-start gap-4">
                <span className="hidden h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-white/15 sm:flex">
                  {advisor.signal.verdict_class === "SELL NOW" ? (
                    <TrendingUp className="h-7 w-7" aria-hidden="true" />
                  ) : advisor.signal.verdict_class === "WAIT" ? (
                    <Hourglass className="h-7 w-7" aria-hidden="true" />
                  ) : (
                    <Info className="h-7 w-7" aria-hidden="true" />
                  )}
                </span>
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="rounded-full bg-white/20 px-2.5 py-1 font-semibold uppercase tracking-wider">
                      {actionMeta?.label}
                    </span>
                    <span className="rounded-full bg-white/10 px-2.5 py-1 text-white/80">
                      As of {advisor.signal.signal_date}
                    </span>
                    <span className="rounded-full bg-white/10 px-2.5 py-1 text-white/80">
                      Evidence: {advisor.signal.evidence_completeness}
                    </span>
                  </div>
                  <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">{advisor.signal.verdict_label}</h2>
                  <p className="flex flex-wrap items-center gap-1.5 text-sm text-white/75">
                    <MapPin className="h-3.5 w-3.5" aria-hidden="true" />
                    {advisor.signal.commodity} · {advisor.signal.market}, {advisor.signal.district},{" "}
                    {advisor.signal.state} · {advisor.signal.storable ? "Storable" : "Perishable"} (
                    {advisor.signal.category})
                  </p>
                </div>
              </div>

              <div className="shrink-0 rounded-2xl bg-white/10 px-4 py-3 text-xs backdrop-blur-sm lg:max-w-xs">
                <div className="flex items-center gap-1.5 text-white/70">
                  <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                  {advisor.briefing.used_fallback ? "Template explanation" : "AI explanation"}
                </div>
                <div
                  className="mt-1.5 flex items-center gap-1.5 font-medium text-white"
                  data-testid="validation-status"
                  data-tone={validationMeta?.tone}
                >
                  {validationMeta?.tone === "success" ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-[#a7ecbb]" aria-hidden="true" />
                  ) : validationMeta?.tone === "info" ? (
                    <Info className="h-4 w-4 shrink-0 text-[#f5d98b]" aria-hidden="true" />
                  ) : (
                    <AlertCircle className="h-4 w-4 shrink-0 text-[#ffb59c]" aria-hidden="true" />
                  )}
                  <span>{validationMeta?.label}</span>
                </div>
              </div>
            </div>
          </Card>

          {/* Supporting Numerical Evidence KPIs */}
          <section aria-label="Supporting market evidence" className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-5">
            <KpiCard
              label="Current price"
              value={formatINR(advisor.signal.current_modal_price)}
              delta={`As of ${advisor.signal.signal_date}`}
              icon={<TrendingUp className="h-4 w-4" />}
              tone="green"
            />
            <KpiCard
              label="4-week price move"
              value={formatSignedPct(advisor.signal.price_change_4w_pct)}
              delta="Compared with the previous 4 weeks"
            />
            <KpiCard
              label="vs. seasonal average"
              value={formatSignedPct(advisor.signal.vs_seasonal_avg_pct)}
              delta={`Based on ${advisor.signal.rule_breakdown.seasonal_years ?? 0} years of history`}
              tone="gold"
            />
            <KpiCard
              label="Arrivals trend (14 days)"
              value={advisor.signal.arrivals_trend}
              delta={formatSignedPct(advisor.signal.arrivals_change_14d_pct)}
            />
            <KpiCard
              label="12-month price swing"
              value={formatCvPct(advisor.signal.cv_12m)}
              delta={`${formatNumber(advisor.signal.observation_count_90d)} price reports in 90 days`}
            />
          </section>

          {/* Bilingual Explanations */}
          <section className={`grid grid-cols-1 gap-5 ${language === "Both" ? "lg:grid-cols-2" : ""}`}>
            {language === "Both" || language === "English" ? (
              <Card>
                <CardHeader>
                  <CardTitle>English Explanation</CardTitle>
                  <CardDescription>Based only on the market numbers shown above.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <p className="text-[15px] leading-relaxed text-foreground">{advisor.briefing.reason_en}</p>
                  <div className="rounded-xl border border-[#ecd9a4] bg-tone-gold-soft p-3.5 text-xs leading-relaxed text-[#5c4304]">
                    <strong>Keep in mind:</strong> {advisor.briefing.caution_en}
                  </div>
                  {(advisor.signal.rule_breakdown.triggers ?? []).length > 0 ? (
                    <div>
                      <h4 className="text-xs font-semibold text-muted-foreground">Why this recommendation</h4>
                      <ul className="mt-2 list-disc space-y-1 pl-5 text-xs sm:text-sm text-foreground/90">
                        {(advisor.signal.rule_breakdown.triggers ?? []).map((trg, idx) => (
                          <li key={idx}>{trg}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </CardContent>
              </Card>
            ) : null}

            {language === "Both" || language === "Hindi" ? (
              <Card>
                <CardHeader>
                  <CardTitle>हिंदी सारांश (Hindi Explanation)</CardTitle>
                  <CardDescription>किसानों के लिए सरल और स्पष्ट मंडी सलाह।</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <p className="text-[15px] leading-loose text-foreground">{advisor.briefing.reason_hi}</p>
                  <div className="rounded-xl border border-[#ecd9a4] bg-tone-gold-soft p-3.5 text-xs leading-relaxed text-[#5c4304]">
                    <strong>सावधानी एवं सीमाएँ:</strong> {advisor.briefing.caution_hi}
                  </div>
                </CardContent>
              </Card>
            ) : null}
          </section>

          {/* Disclaimer */}
          <Alert variant="info" className="flex items-start gap-3">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-tone-gold" aria-hidden="true" />
            <p className="text-xs leading-relaxed text-muted-foreground">
              <strong className="text-foreground">For information only. </strong>
              MandiMitra recommendations are informational analytics based on historical mandi records and deterministic
              rules — not guaranteed financial or trading advice. Verify transport costs, crop grade/moisture, and
              local mandi bidding before selling.
            </p>
          </Alert>

          {/* Market Price & Arrivals Context Chart */}
          <ChartCard
            title={`${advisor.signal.commodity} at ${advisor.signal.market} — recent prices`}
            description="Daily modal price, with arrival volumes when available."
          >
            {advisor.history_series.length === 0 ? (
              <EmptyState message="No recent daily history rows available for this market." />
            ) : (
              <div className="h-72 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={advisor.history_series} margin={{ top: 10, right: 16, left: 4, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                    <XAxis dataKey="arrival_date" tick={{ fontSize: 11 }} />
                    <YAxis yAxisId="price" tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${v}`} />
                    <YAxis
                      yAxisId="arrivals"
                      orientation="right"
                      tick={{ fontSize: 11 }}
                      tickFormatter={(v) => `${v}t`}
                    />
                    <Tooltip formatter={formatPriceArrivalsTooltip} />
                    <Legend />
                    <Bar
                      yAxisId="arrivals"
                      dataKey="quantity_tonnes"
                      name="Arrivals (t)"
                      fill="#DCE8DC"
                      barSize={12}
                    />
                    <Line
                      yAxisId="price"
                      type="monotone"
                      dataKey="modal_price"
                      name="Modal Price"
                      stroke={CHART_COLORS.green}
                      strokeWidth={2.5}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            )}
          </ChartCard>

          {/* Analyst-only detail, collapsed by default */}
          <TechnicalDetails
            title="Technical details"
            description={`Model: ${advisor.briefing.used_fallback ? "Deterministic template" : advisor.briefing.model_used}${advisor.cached ? " · cached result" : ""} · signal inputs and recent briefings`}
          >
            <section className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <ChartCard
                title="Signal inputs"
                description="Exact numbers given to the AI and checked after generation."
              >
                <div className="space-y-2 text-xs">
                  {validationMeta && validationMeta.tone !== "success" && advisor.briefing.validation_errors.length > 0 ? (
                    <p className="rounded-lg border border-[#ecd9a4] bg-tone-gold-soft p-2.5 text-[#5c4304]">
                      <strong>Validation notes:</strong> {advisor.briefing.validation_errors.join("; ")}
                    </p>
                  ) : null}
                  <p>
                    <strong>Analysis Window:</strong> {advisor.signal.analysis_window_start ?? "N/A"} →{" "}
                    {advisor.signal.analysis_window_end ?? "N/A"} | <strong>Sources:</strong>{" "}
                    {advisor.signal.data_sources.join(", ") || "N/A"}
                  </p>
                  <pre className="max-h-60 overflow-auto rounded-lg border border-border bg-background p-3 font-mono text-[11px]">
                    {JSON.stringify(advisor.genai_input, null, 2)}
                  </pre>
                </div>
              </ChartCard>

              <ChartCard
                title="Recent briefings"
                description="Previously generated recommendations."
              >
                {advisor.recent_briefings.length === 0 ? (
                  <EmptyState message="No persisted briefings found in the warehouse yet." />
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Generated</TableHead>
                        <TableHead>Crop &amp; Market</TableHead>
                        <TableHead>Verdict</TableHead>
                        <TableHead>Source</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {advisor.recent_briefings.slice(0, 8).map((b) => (
                        <TableRow key={b.briefing_id}>
                          <TableCell className="whitespace-nowrap">
                            <span className="inline-flex items-center gap-1 text-xs">
                              <Clock3 className="h-3 w-3 text-muted-foreground" />
                              {formatTimestamp(b.generated_at)}
                            </span>
                          </TableCell>
                          <TableCell>
                            <div className="font-medium">{b.commodity}</div>
                            <div className="text-xs text-muted-foreground">
                              {b.market}, {b.state}
                            </div>
                          </TableCell>
                          <TableCell>
                            <Badge
                              variant={
                                b.verdict === "SELL NOW" ? "default" : b.verdict === "WAIT" ? "gold" : "terracotta"
                              }
                            >
                              {b.verdict}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-xs">
                            {b.used_fallback ? "Template Fallback" : b.model_used || "AI"}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </ChartCard>
            </section>
          </TechnicalDetails>
        </>
      ) : null}
    </div>
  );
}
