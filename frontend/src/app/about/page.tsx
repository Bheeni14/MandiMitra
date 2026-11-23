import { BookOpen, Database, Layers, ShieldAlert, Sparkles, Users } from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common/PageHeader";

export default function AboutMethodologyPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Methodology"
        title="About MandiMitra"
        icon={BookOpen}
        description="Price intelligence for Indian wholesale mandis — combining market analytics, historical backtesting, and grounded bilingual crop advice."
        badges={<Badge variant="neutral">Next.js · FastAPI · SQL</Badge>}
      />

      <Alert variant="demo" className="flex items-start gap-3">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
        <div className="space-y-1">
          <p className="font-semibold">Informational use only</p>
          <p className="text-xs leading-relaxed sm:text-sm">
            MandiMitra recommendations (<strong>SELL NOW</strong>, <strong>WAIT</strong>, or <strong>NEUTRAL</strong>) are
            informational analytics derived from historical wholesale mandi prices and deterministic rule comparisons. They
            are <strong>not</strong> guaranteed financial, trading, or commodity-brokerage advice. Actual farmer returns
            depend on local crop grade, moisture, transport costs, storage spoilage, and real-time mandi auction liquidity.
          </p>
        </div>
      </Alert>

      {/* Architecture & Core Principle */}
      <section className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-tone-green-soft text-tone-green">
                <Layers className="size-4.5" aria-hidden="true" />
              </span>
              <CardTitle>System Architecture</CardTitle>
            </div>
            <CardDescription>Clean separation between presentation, REST API, analytics, and database layers.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-foreground/90">
            <div className="rounded-xl border border-border bg-muted p-3.5 font-mono text-xs leading-relaxed">
              Next.js App Router UI → FastAPI REST API → Python Services &amp; SQL Queries → PostgreSQL / Demo SQLite
            </div>
            <ul className="list-disc space-y-1.5 pl-5 text-xs sm:text-sm text-muted-foreground">
              <li>
                <strong>Next.js Frontend:</strong> Responsive TypeScript + Tailwind CSS + Recharts + shadcn/ui dashboard
                with zero direct database credentials or LLM keys.
              </li>
              <li>
                <strong>FastAPI Backend:</strong> Typed Pydantic v2 REST endpoints delegating directly to existing Python
                analytics, signal, backtesting, and GenAI modules.
              </li>
              <li>
                <strong>Dual Database Support:</strong> Live Supabase/PostgreSQL star schema (<code>staging</code>{" "}
                → <code>warehouse</code> → <code>operational</code>) for production, plus a self-contained Demo SQLite
                warehouse for offline verification.
              </li>
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-tone-green-soft text-tone-green">
                <Sparkles className="size-4.5" aria-hidden="true" />
              </span>
              <CardTitle>Methodology: SQL Decides, GenAI Explains</CardTitle>
            </div>
            <CardDescription>Zero ungrounded hallucinations in farmer recommendations.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2.5 text-xs sm:text-sm text-muted-foreground">
            <p>
              <strong className="text-foreground">1. SQL Feature Extraction:</strong> Rolling 7d/14d/30d modal
              averages, prior-year seasonal averages, 12-month volatility (CV), and CEDA arrivals pressure are computed
              directly in SQL.
            </p>
            <p>
              <strong className="text-foreground">2. Deterministic Verdict Engine:</strong> Rule-based thresholds (with
              perishable vs. storable crop safeguards and storage cost accounting) deterministically emit{" "}
              <code>SELL NOW</code>, <code>WAIT</code>, or <code>NEUTRAL</code>.
            </p>
            <p>
              <strong className="text-foreground">3. Grounded Bilingual Advisor:</strong> Server-side Google Gemini (
              <code>google-genai</code> SDK) translates the locked SQL verdict into clear English and Hindi
              explanations; a post-generation validator verifies every cited price and percentage against SQL truth and
              falls back to deterministic templates on any API error or quota limit.
            </p>
            <p>
              <strong className="text-foreground">4. Rolling-Origin Backtesting:</strong> Walk-forward evaluation scores
              historical decisions strictly using past data (<code>&lt;= decision_date</code>) against Always-Sell and
              Momentum-Only baselines.
            </p>
          </CardContent>
        </Card>
      </section>

      {/* Data Sources, Limitations & Team */}
      <section className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-tone-green-soft text-tone-green">
                <Database className="size-4.5" aria-hidden="true" />
              </span>
              <CardTitle>Public Data Sources &amp; Quality</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-2 text-xs sm:text-sm text-muted-foreground">
            <ul className="list-disc space-y-1.5 pl-5">
              <li>
                <strong>Agmarknet Daily Prices:</strong>{" "}
                <a
                  href="https://data.gov.in/"
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary underline-offset-2 hover:underline"
                >
                  data.gov.in Resource 9ef84268
                </a>{" "}
                (prices only, no arrival quantities).
              </li>
              <li>
                <strong>CEDA Agmarknet Archive:</strong>{" "}
                <a
                  href="https://agmarknet.ceda.ashoka.edu.in/"
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary underline-offset-2 hover:underline"
                >
                  agmarknet.ceda.ashoka.edu.in
                </a>{" "}
                (historical daily prices &amp; arrival tonnage).
              </li>
              <li>
                <strong>DES Crop APY:</strong>{" "}
                <a
                  href="https://data.desagri.gov.in/website/crops-apy-report-web"
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary underline-offset-2 hover:underline"
                >
                  data.desagri.gov.in
                </a>{" "}
                (state-wise Area, Production &amp; Yield joined via <code>map_commodity_crop</code>).
              </li>
              <li>
                <strong>Quality Guardrails:</strong> Invalid records are quarantined into <code>dq_rejects</code>; extreme
                spikes are flagged via 3-IQR screening.
              </li>
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-tone-green-soft text-tone-green">
                <BookOpen className="size-4.5" aria-hidden="true" />
              </span>
              <CardTitle>Known Limitations</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-2 text-xs sm:text-sm text-muted-foreground">
            <ul className="list-disc space-y-1.5 pl-5">
              <li>
                <strong>Wholesale vs. Farm-Gate:</strong> Agmarknet tracks wholesale APMC transactions; net farmer
                realization differs after transport, grading, and mandi commissions.
              </li>
              <li>
                <strong>Arrival Data Availability:</strong> When CEDA tonnage is absent, arrival trends are clearly shown
                as <em>Unavailable</em> — reporting counts are never substituted for tonnage.
              </li>
              <li>
                <strong>Sparse Reporting Threshold:</strong> Signals require sufficient 90-day observations (&ge; 10
                reports) and default to <code>NEUTRAL</code> when evidence is thin.
              </li>
              <li>
                <strong>Demo vs. Live Separation:</strong> Simulated fixtures are strictly separated from live
                PostgreSQL records and prominently labelled.
              </li>
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-tone-green-soft text-tone-green">
                <Users className="size-4.5" aria-hidden="true" />
              </span>
              <CardTitle>Project Team &amp; Stack</CardTitle>
            </div>
            <CardDescription>HCL Hackathon Capstone · AKGEC Ghaziabad (B.Tech CSE DS, 2027)</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2.5 text-xs sm:text-sm text-muted-foreground">
            <p>
              <strong className="text-foreground">Bheeni Agarwal:</strong> Data Engineering, Pandas validation, ETL
              warehouse loading, PostgreSQL star schema &amp; indexing, 7 SQL analytical views (Q1–Q7), deterministic
              signal engine, GenAI validator/fallback, bilingual prompting, rolling-origin backtesting, dashboard UX,
              and automated test suites.
            </p>
            <p>
              <strong className="text-foreground">Tech Stack:</strong> Next.js (App Router, TypeScript, Tailwind CSS,
              Recharts, shadcn/ui), FastAPI, Pydantic v2, PostgreSQL / Supabase, Google Gemini API, and Pytest / Vitest.
            </p>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
