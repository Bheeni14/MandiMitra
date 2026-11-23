"""Complete daily Agmarknet capture: full pagination -> chunked idempotent load -> reconciliation.

This is the single place that ties the three stages together and records one reconciliation:

    feed-reported total  ->  fetched  ->  valid / rejected  ->  loaded (+ source conflicts)
    and the states present at each stage.

The function performs database writes (pipeline run log, staging, dimensions, facts), so it must only
be called by an explicitly approved operator action or scheduled job, never at import or app start.
"""

from __future__ import annotations

import logging
from typing import Any

from config import TARGET_STATES
from database.connection import DatabaseManager, get_db_manager
from etl.cleaning import normalize_state_name
from etl.transformation import build_ingestion_reconciliation
from etl.warehouse_loader import DEFAULT_LOAD_CHUNK_SIZE, WarehouseLoader
from ingestion.agmarknet_api import AgmarknetClient
from ingestion.scheduler import finish_pipeline_run, start_pipeline_run

logger = logging.getLogger(__name__)

SOURCE_NAME = "agmarknet_api"
_MISSING_STATE = "(missing)"


def run_daily_capture(
    db: DatabaseManager | None = None,
    loader: WarehouseLoader | None = None,
    client: AgmarknetClient | None = None,
    limit: int = 500,
    max_records: int | None = None,
    filters: dict[str, str] | None = None,
    use_cache: bool = True,
    chunk_size: int = DEFAULT_LOAD_CHUNK_SIZE,
) -> dict[str, Any]:
    """Fetch the whole daily feed, load it in chunks, and persist one reconciled run record.

    ``max_records=None`` (default) fetches everything the feed reports. The run is marked
    ``partial_success`` whenever the fetch was incomplete or truncated, counts do not reconcile, or
    facts owned by another source were skipped, so an incomplete day is never reported as success.
    """
    db = db or get_db_manager()
    loader = loader or WarehouseLoader(db)
    client = client or AgmarknetClient()

    run_id = start_pipeline_run(db, stage="ingestion_agmarknet_daily", source_name=SOURCE_NAME)
    result = client.fetch_daily_prices(
        limit=limit,
        max_records=max_records,
        filters=filters,
        use_cache=use_cache,
        save_raw=True,
    )
    fetch_meta = dict(result.metadata)
    fetch_meta["raw_files"] = [str(p) for p in result.raw_files]

    if not result.records:
        finish_pipeline_run(
            db,
            run_id=run_id,
            status=result.status if result.status != "success" else "partial_success",
            rows_fetched=0,
            error_message=result.error_message or "Agmarknet returned no records.",
            metadata={"fetch": fetch_meta},
        )
        return {"run_id": run_id, "status": result.status, "fetch_status": result.status,
                "error": result.error_message, "reconciliation": None, "load": None}

    try:
        load = loader.load_prices(
            result.records,
            source_name=SOURCE_NAME,
            raw_file_path=str(result.raw_files[0]) if result.raw_files else None,
            run_id=run_id,
            chunk_size=chunk_size,
        )
    except Exception as exc:
        finish_pipeline_run(
            db,
            run_id=run_id,
            status="failed",
            rows_fetched=result.rows_fetched,
            error_message=str(exc),
            metadata={"fetch": fetch_meta},
        )
        raise

    fetch_rec = fetch_meta.get("fetch_reconciliation") or {}
    fetched_states = [s for s in (fetch_rec.get("records_by_state") or {}) if s != _MISSING_STATE]
    reconciliation = build_ingestion_reconciliation(
        reported_total=fetch_meta.get("total_reported"),
        fetched_count=result.rows_fetched,
        valid_count=load["valid_count"],
        rejected_count=load["rejected_count"],
        loaded_count=load["loaded_count"],
        source_conflict_count=load.get("source_conflict_count", 0),
        fetch_complete=bool(fetch_rec.get("complete", True)),
        truncated=bool(fetch_rec.get("truncated", False)),
        fetched_states=fetched_states,
        loaded_states=load.get("loaded_states", []),
        required_states=[normalize_state_name(s) for s in TARGET_STATES],
    )
    for warning in reconciliation["warnings"]:
        logger.warning("Daily capture coverage: %s", warning)
    for issue in reconciliation["issues"]:
        logger.error("Daily capture reconciliation issue: %s", issue)

    degraded = (
        result.status != "success"
        or load["status"] != "success"
        or not reconciliation["reconciled"]
    )
    final_status = "partial_success" if degraded else "success"
    load_meta = {k: v for k, v in load.items() if k != "loaded_states"}
    finish_pipeline_run(
        db,
        run_id=run_id,
        status=final_status,
        rows_fetched=result.rows_fetched,
        rows_loaded=load["loaded_count"],
        rows_rejected=load["rejected_count"],
        error_message=result.error_message,
        metadata={"fetch": fetch_meta, "etl_prices": load_meta, "reconciliation": reconciliation},
    )
    return {
        "run_id": run_id,
        "status": final_status,
        "fetch_status": result.status,
        "error": result.error_message,
        "reconciliation": reconciliation,
        "load": load_meta,
    }
