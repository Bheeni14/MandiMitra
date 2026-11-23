"""Data sources status, DQ indicators, and pipeline run history endpoints."""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from analytics.metrics import get_source_coverage
from backend.dependencies import get_app_settings, get_db
from backend.routers.dashboard import _normalize_pipeline_run
from backend.schemas import (
    CoverageSummary,
    DataQualityIndicators,
    DataSourcesStatusResponse,
    DqRejectItem,
    PaginationMeta,
    PipelineHistoryResponse,
    PriceSourceBreakdown,
    RejectReasonCount,
    SourceComponentStatus,
)
from backend.serializers import clean_scalar, df_to_records, parse_json_field
from config import Settings
from database.connection import DatabaseManager
from services.concurrency import run_queries
from services.data_service import get_active_source_names, query_dq_rejects, query_pipeline_runs

router = APIRouter(prefix="/api", tags=["pipeline"])

# Status labels only reflect whether a credential is *present* in the environment. The Data Sources
# endpoint never opens a connection to these services, so it must not claim they are connected.
CREDENTIALS_PRESENT_LABEL = "Credentials present (not tested)"


@router.get("/data-sources/status", response_model=DataSourcesStatusResponse)
def get_data_sources_status(
    db: DatabaseManager = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> DataSourcesStatusResponse:
    t_p = db.table_ref("warehouse", "fact_price_daily")
    t_rej = db.table_ref("operational", "dq_rejects")
    outlier_cond = "outlier_flag IS TRUE" if db.backend == "postgresql" else "outlier_flag = 1"

    def fetch_dq_counts() -> Any:
        # Both counts in one statement: one network round trip instead of two.
        with db.readonly_connection() as conn:
            return conn.execute(
                text(
                    f"SELECT (SELECT COUNT(*) FROM {t_p} WHERE {outlier_cond}) AS outliers, "
                    f"(SELECT COUNT(*) FROM {t_rej}) AS rejects"
                )
            ).mappings().one()

    loaded = run_queries(
        db,
        {
            "coverage": lambda: get_source_coverage(db),
            "runs": lambda: query_pipeline_runs(db, limit=50),
            "dq_counts": fetch_dq_counts,
            "active_sources": lambda: get_active_source_names(db),
        },
    )
    cov_raw = {k: clean_scalar(v) for k, v in loaded["coverage"].items()}
    runs_records = df_to_records(loaded["runs"])
    latest_run = _normalize_pipeline_run(runs_records[0]) if runs_records else None
    success_runs = [r for r in runs_records if r.get("status") == "success"]
    last_successful_run = _normalize_pipeline_run(success_runs[0]) if success_runs else None

    outliers_count = int(loaded["dq_counts"]["outliers"] or 0)
    total_rejects = int(loaded["dq_counts"]["rejects"] or 0)

    total_prices = int(cov_raw.get("total_price_records") or 0)
    outlier_pct = round((outliers_count / total_prices) * 100.0, 2) if total_prices > 0 else 0.0

    # Strictly masked/safe source indicators — never expose raw URLs, keys, or passwords
    if not db.is_demo and settings.has_database_url:
        db_status_label = "PostgreSQL Configured"
    elif db.is_demo and settings.has_database_url:
        db_status_label = "Demo SQLite Override (DEMO_MODE=true)"
    else:
        db_status_label = "Demo SQLite Fallback"

    components = [
        SourceComponentStatus(
            component="Warehouse Database",
            configured=settings.has_database_url,
            status_label=db_status_label,
            details=settings.safe_db_summary(),
        ),
        SourceComponentStatus(
            component="Supabase Raw Object Storage",
            configured=settings.storage_upload_active,
            status_label=(
                "Upload enabled (not tested)"
                if settings.storage_upload_active
                else "Uploads disabled by configuration - local data/raw/ only"
            ),
            details=f"Bucket: {settings.supabase_storage_bucket}; RAW_STORAGE_UPLOAD_ENABLED={settings.raw_storage_upload_enabled}",
        ),
        SourceComponentStatus(
            component="Agmarknet API (data.gov.in)",
            configured=settings.has_data_gov_key,
            status_label=CREDENTIALS_PRESENT_LABEL if settings.has_data_gov_key else "Key Not Configured",
            details="Resource 9ef84268-d588-465a-a308-a864a43d0070 (prices only, no arrivals)",
        ),
        SourceComponentStatus(
            component="Google Gemini API",
            configured=settings.gemini_active,
            status_label=(
                "Enabled (not tested)"
                if settings.gemini_active
                else "Disabled by configuration - deterministic template explanations"
            ),
            details=f"Model: {settings.gemini_model}; GEMINI_ENABLED={settings.gemini_enabled}",
        ),
    ]

    coverage = CoverageSummary(
        backend=str(cov_raw.get("backend", db.backend)),
        is_demo=bool(cov_raw.get("is_demo", db.is_demo)),
        total_price_records=total_prices,
        commodities_count=int(cov_raw.get("commodities_count") or 0),
        markets_count=int(cov_raw.get("markets_count") or 0),
        states_count=int(cov_raw.get("states_count") or 0),
        min_price_date=cov_raw.get("min_price_date"),
        max_price_date=cov_raw.get("max_price_date"),
        total_arrival_records=int(cov_raw.get("total_arrival_records") or 0),
        total_tonnes=float(cov_raw.get("total_tonnes") or 0.0),
        min_arrival_date=cov_raw.get("min_arrival_date"),
        max_arrival_date=cov_raw.get("max_arrival_date"),
        total_apy_records=int(cov_raw.get("total_apy_records") or 0),
        min_year=cov_raw.get("min_year"),
        max_year=cov_raw.get("max_year"),
        apy_states=int(cov_raw.get("apy_states") or 0),
    )

    price_sources = [PriceSourceBreakdown(**ps) for ps in cov_raw.get("price_sources", [])]

    return DataSourcesStatusResponse(
        mode="demo" if db.is_demo else "postgresql",
        is_demo=db.is_demo,
        backend=db.backend,
        active_sources=list(loaded["active_sources"]),
        components=components,
        coverage=coverage,
        price_sources=price_sources,
        data_quality=DataQualityIndicators(
            total_price_records=total_prices,
            outliers_flagged_count=outliers_count,
            outliers_flagged_pct=outlier_pct,
            total_dq_rejects=total_rejects,
            total_arrival_records=coverage.total_arrival_records,
            total_apy_records=coverage.total_apy_records,
        ),
        latest_run=latest_run,
        last_successful_run=last_successful_run,
    )


@router.get("/pipeline/history", response_model=PipelineHistoryResponse)
def get_pipeline_history(
    runs_page: int = Query(default=1, ge=1),
    runs_page_size: int = Query(default=20, ge=1, le=100),
    rejects_page: int = Query(default=1, ge=1),
    rejects_page_size: int = Query(default=25, ge=1, le=200),
    db: DatabaseManager = Depends(get_db),
) -> PipelineHistoryResponse:
    loaded = run_queries(
        db,
        {
            "runs": lambda: query_pipeline_runs(db, limit=500),
            "rejects": lambda: query_dq_rejects(db, limit=1000),
        },
    )
    all_runs_df = loaded["runs"]
    all_rejects_df = loaded["rejects"]

    runs_total = len(all_runs_df)
    runs_pages = max(1, math.ceil(runs_total / runs_page_size)) if runs_total > 0 else 0
    r_start = (runs_page - 1) * runs_page_size
    runs_slice = df_to_records(all_runs_df.iloc[r_start : r_start + runs_page_size]) if runs_total > 0 else []

    rej_total = len(all_rejects_df)
    rej_pages = max(1, math.ceil(rej_total / rejects_page_size)) if rej_total > 0 else 0
    j_start = (rejects_page - 1) * rejects_page_size
    rej_slice = df_to_records(all_rejects_df.iloc[j_start : j_start + rejects_page_size]) if rej_total > 0 else []

    reject_items = [
        DqRejectItem(
            reject_id=int(r["reject_id"]),
            run_id=r.get("run_id"),
            source_name=str(r["source_name"]),
            stage=str(r["stage"]),
            reject_reason=str(r["reject_reason"]),
            raw_record=parse_json_field(r.get("raw_record"), default={}),
            rejected_at=r.get("rejected_at"),
        )
        for r in rej_slice
    ]

    reason_summary: list[RejectReasonCount] = []
    if not all_rejects_df.empty:
        grouped = (
            all_rejects_df.groupby("reject_reason", as_index=False)["reject_id"]
            .count()
            .rename(columns={"reject_id": "count"})
            .sort_values("count", ascending=False)
        )
        reason_summary = [RejectReasonCount(**r) for r in df_to_records(grouped)]

    return PipelineHistoryResponse(
        is_demo=db.is_demo,
        runs=[_normalize_pipeline_run(r) for r in runs_slice],
        runs_pagination=PaginationMeta(
            page=runs_page,
            page_size=runs_page_size,
            total_items=runs_total,
            total_pages=runs_pages,
        ),
        rejects=reject_items,
        rejects_pagination=PaginationMeta(
            page=rejects_page,
            page_size=rejects_page_size,
            total_items=rej_total,
            total_pages=rej_pages,
        ),
        reject_reason_summary=reason_summary,
    )
