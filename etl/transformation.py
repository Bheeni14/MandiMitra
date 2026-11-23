"""Transformation layer: builds dim_date enrichment and staging/warehouse reconciliation."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

import pandas as pd

from etl.cleaning import derive_indian_season


def build_date_dim_rows(dates: list[date]) -> list[dict[str, Any]]:
    """Convert unique dates into dim_date records with YYYYMMDD key and Indian crop season."""
    unique_dates = sorted({d for d in dates if d is not None})
    rows: list[dict[str, Any]] = []
    for d in unique_dates:
        iso_year, iso_week, _ = d.isocalendar()
        rows.append(
            {
                "date_id": int(d.strftime("%Y%m%d")),
                "full_date": d,
                "year": d.year,
                "month": d.month,
                "week": int(iso_week),
                "season": derive_indian_season(d),
            }
        )
    return rows


def build_reconciliation_summary(
    raw_count: int,
    valid_count: int,
    rejected_count: int,
    loaded_count: int,
    aggregated_count: int = 0,
    source_conflict_count: int = 0,
) -> dict[str, Any]:
    """Verify raw -> staging -> warehouse row reconciliation without silent drops.

    ``source_conflict_count`` rows were valid but deliberately not written because a different
    source already owns the fact; they are reported, never dropped silently.
    """
    expected_after_rejects = raw_count - rejected_count - aggregated_count
    return {
        "raw_count": raw_count,
        "valid_count": valid_count,
        "rejected_count": rejected_count,
        "aggregated_count": aggregated_count,
        "source_conflict_count": source_conflict_count,
        "loaded_count": loaded_count,
        "reconciled": (valid_count == expected_after_rejects)
        and (loaded_count + source_conflict_count == valid_count),
    }


def build_ingestion_reconciliation(
    *,
    reported_total: int | None,
    fetched_count: int,
    valid_count: int,
    rejected_count: int,
    loaded_count: int,
    source_conflict_count: int = 0,
    aggregated_count: int = 0,
    fetch_complete: bool = True,
    truncated: bool = False,
    fetched_states: Iterable[str] = (),
    loaded_states: Iterable[str] = (),
    required_states: Iterable[str] = (),
) -> dict[str, Any]:
    """End-to-end accounting: feed-reported -> fetched -> validated/rejected -> loaded, plus states.

    ``issues`` are hard count discrepancies and make ``reconciled`` False. ``warnings`` are
    coverage observations (for example a target state absent from the feed) that need a human look
    but do not by themselves mean records were lost.
    """
    fetched_set = {s for s in fetched_states if s}
    loaded_set = {s for s in loaded_states if s}
    required = sorted({s for s in required_states if s})
    issues: list[str] = []
    warnings: list[str] = []

    if truncated:
        issues.append("fetch was truncated by an explicit record cap")
    if reported_total is not None and fetched_count < reported_total:
        issues.append(f"fetched {fetched_count} of {reported_total} feed-reported records")
    if reported_total is None:
        warnings.append("feed did not report a total; completeness cannot be verified")
    if not fetch_complete and reported_total is not None and not issues:
        issues.append("fetch did not complete cleanly")
    unaccounted = fetched_count - rejected_count - aggregated_count - valid_count
    if unaccounted != 0:
        issues.append(f"{unaccounted} fetched records are neither valid, rejected nor aggregated")
    if loaded_count + source_conflict_count != valid_count:
        issues.append(
            f"{valid_count} valid records but {loaded_count} loaded and {source_conflict_count} source conflicts"
        )

    missing_required = [s for s in required if s not in loaded_set]
    if missing_required:
        warnings.append("target states absent from loaded data: " + ", ".join(missing_required))
    lost_states = sorted(fetched_set - loaded_set)
    if lost_states:
        warnings.append("states fetched but not loaded: " + ", ".join(lost_states))

    return {
        "reported_total": reported_total,
        "fetched_count": fetched_count,
        "valid_count": valid_count,
        "rejected_count": rejected_count,
        "aggregated_count": aggregated_count,
        "source_conflict_count": source_conflict_count,
        "loaded_count": loaded_count,
        "shortfall_vs_reported": None if reported_total is None else max(0, reported_total - fetched_count),
        "fetched_state_count": len(fetched_set),
        "loaded_state_count": len(loaded_set),
        "missing_required_states": missing_required,
        "states_fetched_not_loaded": lost_states,
        "issues": issues,
        "warnings": warnings,
        "reconciled": not issues,
    }
