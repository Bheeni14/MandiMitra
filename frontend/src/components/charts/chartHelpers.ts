import type { DailyPricePoint, OutlierPoint } from "@/lib/types";

export const CHART_COLORS = {
  green: "#17643A",
  emerald: "#3A9A5B",
  gold: "#D4A02A",
  terracotta: "#D0603F",
  teal: "#0E7C86",
  slate: "#64748B",
  grid: "#EDF1EC",
} as const;

export function toChartNumber(val: unknown): number | null {
  if (typeof val === "number" && Number.isFinite(val)) return val;
  if (typeof val === "string" && val.trim() !== "") {
    const parsed = Number(val);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function formatTooltipInr(val: unknown): string {
  const num = toChartNumber(val);
  if (num === null) return "N/A";
  return `₹${num.toLocaleString("en-IN", { maximumFractionDigits: 2 })}/qtl`;
}

export function formatTooltipPct(val: unknown): string {
  const num = toChartNumber(val);
  if (num === null) return "N/A";
  return `${num.toFixed(1)}%`;
}

export function formatTooltipTonnes(val: unknown): string {
  const num = toChartNumber(val);
  if (num === null) return "N/A";
  return `${num.toLocaleString("en-IN", { maximumFractionDigits: 2 })} t`;
}

/** The API returns coefficient of variation as a decimal ratio (0.3218); the UI shows percentage points (32.18). */
export function cvToPct(cv: number | null | undefined): number | null {
  const num = toChartNumber(cv);
  if (num === null) return null;
  return Math.round(num * 10000) / 100;
}

export function formatCvPct(cv: number | null | undefined): string {
  const pct = cvToPct(cv);
  return pct === null ? "N/A" : `${pct.toFixed(2)}%`;
}

export function formatTooltipPct2(val: unknown): string {
  const num = toChartNumber(val);
  if (num === null) return "N/A";
  return `${num.toFixed(2)}%`;
}

/** Q5 seasonal_index is centered on 100 (= annual commodity average). */
export const SEASONAL_BASELINE = 100;
export const SEASONAL_HIGH = 105;
export const SEASONAL_LOW = 95;

export type SeasonalTone = "above" | "below" | "normal";

export function seasonalIndexTone(index: number): SeasonalTone {
  if (index >= SEASONAL_HIGH) return "above";
  if (index <= SEASONAL_LOW) return "below";
  return "normal";
}

export function seasonalIndexColor(index: number): string {
  const tone = seasonalIndexTone(index);
  if (tone === "above") return CHART_COLORS.green;
  if (tone === "below") return CHART_COLORS.terracotta;
  return CHART_COLORS.gold;
}

/** Y-axis bounds that always include the baseline band and pad around the observed index values. */
export function seasonalIndexDomain(values: number[]): [number, number] {
  const finite = values.filter((v) => Number.isFinite(v));
  const lo = Math.min(SEASONAL_LOW, ...finite);
  const hi = Math.max(SEASONAL_HIGH, ...finite);
  return [Math.max(0, Math.floor((lo - 10) / 10) * 10), Math.ceil((hi + 5) / 10) * 10];
}

const ARRIVAL_KEYS = new Set(["arrivals_tonnes", "quantity_tonnes"]);

/** Tooltip formatter for charts mixing ₹/qtl price series with arrivals (tonnes) and per-market outlier series. */
export function formatPriceArrivalsTooltip(
  value: unknown,
  name: unknown,
  item: { dataKey?: unknown; payload?: unknown } | undefined
): [string, string] {
  const key = typeof item?.dataKey === "string" ? item.dataKey : "";
  const label = name === undefined || name === null ? key : String(name);
  if (ARRIVAL_KEYS.has(key)) return [formatTooltipTonnes(value), label];
  const outlierMatch = /^outlier_(\d+)$/.exec(key);
  if (outlierMatch) {
    const row = item?.payload as DailyChartRow | undefined;
    const pt = row?.outliers?.[Number(outlierMatch[1])];
    const where = pt ? `${pt.market}, ${pt.state}${pt.variety ? ` (${pt.variety})` : ""}` : "";
    return [formatTooltipInr(value), where ? `3-IQR Outlier — ${where}` : label];
  }
  return [formatTooltipInr(value), label];
}

export type DailyChartRow = DailyPricePoint & {
  outliers: OutlierPoint[];
  [outlierKey: `outlier_${number}`]: number | null;
};

/**
 * Attach every outlier to its date. Each same-day outlier gets its own `outlier_<i>` slot so several markets
 * flagged on one date are all plotted (and identifiable) instead of overwriting each other.
 */
export function buildDailyChartRows(
  daily: DailyPricePoint[],
  outliers: OutlierPoint[]
): { rows: DailyChartRow[]; outlierSlots: number } {
  const byDate = new Map<string, OutlierPoint[]>();
  for (const pt of outliers) {
    const list = byDate.get(pt.arrival_date) ?? [];
    list.push(pt);
    byDate.set(pt.arrival_date, list);
  }
  let outlierSlots = 0;
  for (const list of byDate.values()) outlierSlots = Math.max(outlierSlots, list.length);

  const rows = daily.map((d) => {
    const dayOutliers = byDate.get(d.arrival_date) ?? [];
    const row: DailyChartRow = { ...d, outliers: dayOutliers };
    for (let i = 0; i < outlierSlots; i++) {
      row[`outlier_${i}`] = dayOutliers[i]?.modal_price ?? null;
    }
    return row;
  });
  return { rows, outlierSlots };
}
