/** Trigger a browser download for an in-memory file. */
export function saveBlobAsFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export type ExportCheckTone = "success" | "warning";

export interface ExportCheck {
  tone: ExportCheckTone;
  message: string;
}

const SOURCE_LABELS: Record<string, string> = {
  precomputed_csv: "the precomputed results file",
  computed: "a live backtest run",
};

function describeSource(source: string): string {
  return SOURCE_LABELS[source] ?? `"${source}"`;
}

/**
 * Compare what the server exported (response headers) with the evaluation currently on screen.
 *
 * The server keeps one in-memory result per (eval window, step) and the export is served from that, but the
 * cache is per-process and is lost on restart, so the export can legitimately come from a different source than
 * the displayed page. Matching source and row count is evidence of consistency, not proof, so the success
 * message says exactly what was compared.
 */
export function checkBacktestExport(
  displayed: { usedPrecomputedCsv: boolean; totalRows: number },
  exported: { source: string | null; rows: number | null }
): ExportCheck {
  if (exported.source === null) {
    return {
      tone: "warning",
      message:
        "Export downloaded, but the server's source header was not readable, so it could not be checked against the results on screen.",
    };
  }

  const displayedSource = displayed.usedPrecomputedCsv ? "precomputed_csv" : "computed";
  if (exported.source !== displayedSource) {
    return {
      tone: "warning",
      message: `The exported CSV came from ${describeSource(exported.source)}, but the results on screen came from ${describeSource(displayedSource)}. The file may not match what you see; use Re-run Backtest and export again for a consistent pair.`,
    };
  }

  if (exported.rows !== null && exported.rows !== displayed.totalRows) {
    return {
      tone: "warning",
      message: `The exported CSV has ${exported.rows} evaluations but ${displayed.totalRows} are shown on screen, so it does not match the displayed results. Use Re-run Backtest and export again.`,
    };
  }

  if (exported.rows === null) {
    return {
      tone: "warning",
      message: `Export came from the same source as the results on screen (${describeSource(exported.source)}), but its row count was not readable, so only the source could be checked.`,
    };
  }

  return {
    tone: "success",
    message: `Export came from ${describeSource(exported.source)} with ${exported.rows} evaluations, the same source and row count as the results on screen. Row contents are not compared.`,
  };
}
