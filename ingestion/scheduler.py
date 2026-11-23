"""Pipeline ingestion scheduler & operational run logging."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from config import DATA_SAMPLE_DIR
from database.connection import DatabaseManager, get_db_manager
from ingestion.agmarknet_api import AgmarknetClient, IngestionResult
from ingestion.apy_importer import ApyImporter
from ingestion.ceda_importer import CedaImporter

logger = logging.getLogger(__name__)


def start_pipeline_run(
    db: DatabaseManager,
    stage: str,
    source_name: str,
    metadata: dict[str, Any] | None = None,
) -> int:
    t_log = db.table_ref("operational", "pipeline_run_log")
    meta_json = json.dumps(metadata or {}, default=str)
    with db.transaction() as conn:
        if db.backend == "postgresql":
            run_id = conn.execute(
                text(
                    f"""
                    INSERT INTO {t_log} (stage, source_name, rows_fetched, rows_loaded, rows_rejected, status, metadata_json)
                    VALUES (:stage, :source, 0, 0, 0, 'running', CAST(:meta AS jsonb))
                    RETURNING run_id
                    """
                ),
                {"stage": stage, "source": source_name, "meta": meta_json},
            ).scalar_one()
        else:
            res = conn.execute(
                text(
                    f"""
                    INSERT INTO {t_log} (stage, source_name, rows_fetched, rows_loaded, rows_rejected, status, metadata_json)
                    VALUES (:stage, :source, 0, 0, 0, 'running', :meta)
                    """
                ),
                {"stage": stage, "source": source_name, "meta": meta_json},
            )
            run_id = int(res.lastrowid)
    return int(run_id)


def finish_pipeline_run(
    db: DatabaseManager,
    run_id: int,
    status: str,
    rows_fetched: int = 0,
    rows_loaded: int = 0,
    rows_rejected: int = 0,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    t_log = db.table_ref("operational", "pipeline_run_log")
    now_utc = datetime.now(timezone.utc)
    with db.transaction() as conn:
        existing_raw = conn.execute(
            text(f"SELECT metadata_json FROM {t_log} WHERE run_id = :rid"),
            {"rid": run_id},
        ).scalar()
        merged_meta: dict[str, Any] = {}
        if isinstance(existing_raw, dict):
            merged_meta.update(existing_raw)
        elif isinstance(existing_raw, str) and existing_raw.strip():
            try:
                parsed = json.loads(existing_raw)
                if isinstance(parsed, dict):
                    merged_meta.update(parsed)
            except Exception:
                pass
        if metadata:
            merged_meta.update(metadata)
        meta_json = json.dumps(merged_meta, default=str)

        if db.backend == "postgresql":
            conn.execute(
                text(
                    f"""
                    UPDATE {t_log}
                    SET finished_at = :fin,
                        status = :status,
                        rows_fetched = :fetched,
                        rows_loaded = :loaded,
                        rows_rejected = :rejected,
                        error_message = :err,
                        metadata_json = CAST(:meta AS jsonb)
                    WHERE run_id = :rid
                    """
                ),
                {
                    "fin": now_utc,
                    "status": status,
                    "fetched": rows_fetched,
                    "loaded": rows_loaded,
                    "rejected": rows_rejected,
                    "err": error_message,
                    "meta": meta_json,
                    "rid": run_id,
                },
            )
        else:
            conn.execute(
                text(
                    f"""
                    UPDATE {t_log}
                    SET finished_at = :fin,
                        status = :status,
                        rows_fetched = :fetched,
                        rows_loaded = :loaded,
                        rows_rejected = :rejected,
                        error_message = :err,
                        metadata_json = :meta
                    WHERE run_id = :rid
                    """
                ),
                {
                    "fin": now_utc.isoformat(),
                    "status": status,
                    "fetched": rows_fetched,
                    "loaded": rows_loaded,
                    "rejected": rows_rejected,
                    "err": error_message,
                    "meta": meta_json,
                    "rid": run_id,
                },
            )


def update_pipeline_run_metadata(
    db: DatabaseManager,
    run_id: int,
    metadata: dict[str, Any],
) -> None:
    """Merge metadata into an active pipeline_run_log entry without finalizing it."""
    t_log = db.table_ref("operational", "pipeline_run_log")
    with db.transaction() as conn:
        existing_raw = conn.execute(
            text(f"SELECT metadata_json FROM {t_log} WHERE run_id = :rid"),
            {"rid": run_id},
        ).scalar()
        merged: dict[str, Any] = {}
        if isinstance(existing_raw, dict):
            merged.update(existing_raw)
        elif isinstance(existing_raw, str) and existing_raw.strip():
            try:
                parsed = json.loads(existing_raw)
                if isinstance(parsed, dict):
                    merged.update(parsed)
            except Exception:
                pass
        merged.update(metadata)
        meta_json = json.dumps(merged, default=str)
        if db.backend == "postgresql":
            conn.execute(
                text(f"UPDATE {t_log} SET metadata_json = CAST(:meta AS jsonb) WHERE run_id = :rid"),
                {"meta": meta_json, "rid": run_id},
            )
        else:
            conn.execute(
                text(f"UPDATE {t_log} SET metadata_json = :meta WHERE run_id = :rid"),
                {"meta": meta_json, "rid": run_id},
            )


def run_daily_price_ingestion(
    db: DatabaseManager | None = None,
    limit: int = 500,
    max_records: int | None = None,
    filters: dict[str, str] | None = None,
    use_cache: bool = True,
    auto_finish: bool = True,
) -> tuple[int, IngestionResult]:
    """Run the complete daily Agmarknet price API pull (does not re-download historical CEDA archive).

    ``max_records=None`` (the default) fetches every record the feed reports; pass an explicit
    cap only for deliberate partial pulls, which are then reported as truncated.
    """
    db = db or get_db_manager()
    run_id = start_pipeline_run(db, stage="ingestion_agmarknet_daily", source_name="agmarknet_api")
    client = AgmarknetClient()
    result = client.fetch_daily_prices(
        limit=limit,
        max_records=max_records,
        filters=filters,
        use_cache=use_cache,
        save_raw=True,
    )
    meta = {
        **result.metadata,
        "raw_files": [str(p) for p in result.raw_files],
    }
    if auto_finish or not result.records:
        finish_pipeline_run(
            db,
            run_id=run_id,
            status=result.status,
            rows_fetched=result.rows_fetched,
            rows_loaded=0,
            rows_rejected=0,
            error_message=result.error_message,
            metadata=meta,
        )
    else:
        update_pipeline_run_metadata(db, run_id, meta)
    return run_id, result


def run_ceda_backfill(
    db: DatabaseManager | None = None,
    csv_paths: list[Path] | None = None,
    source_label: str = "ceda_archive",
    auto_finish: bool = True,
) -> tuple[int, IngestionResult]:
    """Run one-time or periodic historical CEDA price & arrival import."""
    db = db or get_db_manager()
    run_id = start_pipeline_run(db, stage="ingestion_ceda_backfill", source_name=source_label)
    importer = CedaImporter()
    result = importer.import_csv_files(csv_paths=csv_paths, source_label=source_label)
    meta = {**result.metadata, "raw_files": [str(p) for p in result.raw_files]}
    if auto_finish or not result.records:
        finish_pipeline_run(
            db,
            run_id=run_id,
            status=result.status,
            rows_fetched=result.rows_fetched,
            error_message=result.error_message,
            metadata=meta,
        )
    else:
        update_pipeline_run_metadata(db, run_id, meta)
    return run_id, result


def run_apy_import(
    db: DatabaseManager | None = None,
    csv_paths: list[Path] | None = None,
    source_label: str = "des_apy",
    auto_finish: bool = True,
) -> tuple[int, IngestionResult]:
    """Run APY crop production import."""
    db = db or get_db_manager()
    run_id = start_pipeline_run(db, stage="ingestion_apy", source_name=source_label)
    importer = ApyImporter()
    result = importer.import_csv_files(csv_paths=csv_paths, source_label=source_label)
    meta = {**result.metadata, "raw_files": [str(p) for p in result.raw_files]}
    if auto_finish or not result.records:
        finish_pipeline_run(
            db,
            run_id=run_id,
            status=result.status,
            rows_fetched=result.rows_fetched,
            error_message=result.error_message,
            metadata=meta,
        )
    else:
        update_pipeline_run_metadata(db, run_id, meta)
    return run_id, result


def load_sample_fixtures_paths() -> dict[str, Path]:
    return {
        "prices": DATA_SAMPLE_DIR / "demo_agmarknet_prices.csv",
        "arrivals": DATA_SAMPLE_DIR / "demo_ceda_arrivals.csv",
        "apy": DATA_SAMPLE_DIR / "demo_apy_production.csv",
    }
