"use client";

import * as React from "react";
import { AlertTriangle, ChevronRight, RefreshCw, Sprout } from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

type Tone = "default" | "green" | "gold" | "terracotta";

const TONE_CHIP: Record<Tone, string> = {
  default: "bg-tone-slate-soft text-tone-slate",
  green: "bg-tone-green-soft text-tone-green",
  gold: "bg-tone-gold-soft text-tone-gold",
  terracotta: "bg-tone-terracotta-soft text-tone-terracotta",
};

const TONE_BAR: Record<Tone, string> = {
  default: "bg-transparent",
  green: "bg-tone-green",
  gold: "bg-[#d4a02a]",
  terracotta: "bg-tone-terracotta",
};

export function KpiCard({
  label,
  value,
  delta,
  icon,
  tone = "default",
}: {
  label: string;
  value: string;
  delta?: string;
  icon?: React.ReactNode;
  tone?: Tone;
}) {
  return (
    <Card className="relative h-full overflow-hidden transition-shadow hover:shadow-raised">
      <span className={cn("absolute inset-x-0 top-0 h-0.5", TONE_BAR[tone])} aria-hidden="true" />
      <CardContent className="flex h-full flex-col p-4 sm:p-5">
        <div className="flex items-start justify-between gap-3">
          <span className="text-[13px] font-medium leading-snug text-muted-foreground">{label}</span>
          {icon ? (
            <span
              className={cn(
                "flex h-8 w-8 shrink-0 items-center justify-center rounded-xl [&_svg]:h-4 [&_svg]:w-4",
                TONE_CHIP[tone]
              )}
            >
              {icon}
            </span>
          ) : null}
        </div>
        <div className="tabular mt-3 wrap-break-word text-lg font-semibold tracking-tight text-foreground sm:text-2xl">
          {value}
        </div>
        {delta ? <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{delta}</p> : null}
      </CardContent>
    </Card>
  );
}

export function ChartCard({
  title,
  description,
  actions,
  children,
  className,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Card className={cn("min-w-0", className)}>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3 pb-4 sm:pb-4">
        <div className="min-w-0">
          <CardTitle>{title}</CardTitle>
          {description ? <CardDescription className="mt-1">{description}</CardDescription> : null}
        </div>
        {actions ? <div className="shrink-0">{actions}</div> : null}
      </CardHeader>
      <CardContent className="pt-0 sm:pt-0">{children}</CardContent>
    </Card>
  );
}

/** Collapsed-by-default section for analyst-only detail (raw payloads, audit logs) that most users don't need. */
export function TechnicalDetails({
  title = "Technical details",
  description,
  children,
}: {
  title?: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <details className="disclosure group rounded-2xl border border-border bg-card shadow-card">
      <summary className="flex items-center justify-between gap-3 rounded-2xl px-5 py-4 transition-colors hover:bg-muted/50 sm:px-6">
        <div>
          <div className="text-sm font-semibold text-foreground">{title}</div>
          {description ? <div className="mt-0.5 text-xs text-muted-foreground">{description}</div> : null}
        </div>
        <ChevronRight
          className="disclosure-chevron h-4 w-4 shrink-0 text-muted-foreground transition-transform"
          aria-hidden="true"
        />
      </summary>
      <div className="border-t border-border px-5 py-5 sm:px-6">{children}</div>
    </details>
  );
}

export function LoadingState({ label = "Loading MandiMitra analytics..." }: { label?: string }) {
  return (
    <div className="space-y-4" role="status" aria-live="polite" aria-label={label}>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Skeleton className="h-80 w-full" />
        <Skeleton className="h-80 w-full" />
      </div>
      <p className="text-xs text-muted-foreground">{label}</p>
    </div>
  );
}

/** Non-blocking indicator shown while a refetch runs and the previous results stay on screen. */
export function RefetchIndicator({ label = "Updating results..." }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="inline-flex items-center gap-2 rounded-full border border-border bg-white px-3.5 py-1.5 text-xs text-muted-foreground shadow-card"
    >
      <RefreshCw className="h-3.5 w-3.5 animate-spin text-primary" aria-hidden="true" />
      {label}
    </div>
  );
}

export function ErrorState({
  title = "Unable to load data",
  message,
  onRetry,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <Alert variant="destructive" className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-start gap-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-tone-terracotta-soft text-tone-terracotta">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" />
        </span>
        <div>
          <p className="font-semibold">{title}</p>
          <p className="mt-0.5 text-xs opacity-90 sm:text-sm">{message}</p>
        </div>
      </div>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry} className="shrink-0">
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          Retry
        </Button>
      ) : null}
    </Alert>
  );
}

export function EmptyState({
  title = "No records found",
  message,
}: {
  title?: string;
  message: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-[#d3dcd4] bg-muted/40 px-6 py-12 text-center">
      <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-white text-primary/70 shadow-card">
        <Sprout className="h-5 w-5" aria-hidden="true" />
      </span>
      <h4 className="mt-3 text-sm font-semibold text-foreground">{title}</h4>
      <p className="mt-1 max-w-md text-xs leading-relaxed text-muted-foreground sm:text-sm">{message}</p>
    </div>
  );
}
