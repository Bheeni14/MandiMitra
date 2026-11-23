"""Tests for idempotent warehouse upserts, reject persistence, reconciliation, and optional live integration."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from database.connection import DatabaseManager
from etl.warehouse_loader import WarehouseLoader


def test_warehouse_idempotent_upsert_and_reject_logging(temp_db: DatabaseManager) -> None:
    loader = WarehouseLoader(temp_db)
    batch1 = [
        {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "variety": "Red", "grade": "FAQ", "arrival_date": "2026-11-10", "min_price": 1800, "max_price": 2300, "modal_price": 2000},
        {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "variety": "Red", "grade": "FAQ", "arrival_date": "2026-11-11", "min_price": 1800, "max_price": 2000, "modal_price": 2500},  # invalid modal
    ]
    res1 = loader.load_prices(batch1, source_name="test_src")
    assert res1["loaded_count"] == 1
    assert res1["rejected_count"] == 1
    assert res1["reconciled"] is True

    # Re-run exact same natural key with updated modal price -> UPSERT must update, NOT duplicate
    batch2 = [
        {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "variety": "Red", "grade": "FAQ", "arrival_date": "2026-11-10", "min_price": 1850, "max_price": 2350, "modal_price": 2150},
    ]
    res2 = loader.load_prices(batch2, source_name="test_src")
    assert res2["loaded_count"] == 1

    with temp_db.readonly_connection() as conn:
        fact_count = conn.execute(text("SELECT COUNT(*) FROM fact_price_daily")).scalar_one()
        modal_val = conn.execute(text("SELECT modal_price FROM fact_price_daily")).scalar_one()
        rej_count = conn.execute(text("SELECT COUNT(*) FROM dq_rejects")).scalar_one()

    assert fact_count == 1
    assert float(modal_val) == 2150.0
    assert rej_count == 1


def test_pipeline_run_log_bookkeeping_preserves_metadata_and_counts(
    temp_db: DatabaseManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from unittest.mock import MagicMock

    import pandas as pd

    from ingestion.scheduler import finish_pipeline_run, run_ceda_backfill, run_daily_price_ingestion

    # 1. CEDA backfill bookkeeping across prices + arrivals on shared run_id
    ceda_csv = tmp_path / "ceda_combo.csv"
    pd.DataFrame(
        [
            {"date": "2026-11-10", "state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "min_price": 1700, "max_price": 2200, "modal_price": 1900, "quantity_tonnes": 110.5},
            {"date": "2026-11-11", "state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "min_price": 1700, "max_price": 2000, "modal_price": 2500, "quantity_tonnes": 95.0},
        ]
    ).to_csv(ceda_csv, index=False)

    monkeypatch.setattr("ingestion.storage.RawZoneStorage._maybe_upload_supabase", lambda *a, **kw: False)
    run_id, res = run_ceda_backfill(db=temp_db, csv_paths=[ceda_csv], auto_finish=False)
    loader = WarehouseLoader(temp_db)
    p_load = loader.load_prices(res.records, source_name="ceda_archive", run_id=run_id)
    a_load = loader.load_arrivals(res.records, source_name="ceda_archive", run_id=run_id)
    finish_pipeline_run(
        temp_db,
        run_id,
        "success",
        rows_fetched=res.rows_fetched,
        rows_loaded=p_load["loaded_count"] + a_load["loaded_count"],
        rows_rejected=p_load["rejected_count"] + a_load["rejected_count"],
        metadata={"etl_prices": p_load, "etl_arrivals": a_load},
    )

    # 2. Daily Agmarknet ingestion bookkeeping keeps raw_files metadata after ETL finish
    mock_client = MagicMock()
    from ingestion.agmarknet_api import IngestionResult

    raw_json_path = tmp_path / "raw_page.json"
    raw_json_path.write_text("{}", encoding="utf-8")
    mock_client.fetch_daily_prices.return_value = IngestionResult(
        source_name="agmarknet_api",
        status="success",
        records=[
            {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "arrival_date": "2026-11-12", "min_price": 1800, "max_price": 2200, "modal_price": 2000},
        ],
        raw_files=[raw_json_path],
        rows_fetched=1,
        metadata={"pages_fetched": 1},
    )
    monkeypatch.setattr("ingestion.scheduler.AgmarknetClient", lambda: mock_client)
    d_run_id, d_res = run_daily_price_ingestion(db=temp_db, auto_finish=False)
    d_load = loader.load_prices(d_res.records, source_name="agmarknet_api", run_id=d_run_id)
    finish_pipeline_run(
        temp_db,
        d_run_id,
        d_load["status"],
        rows_fetched=d_res.rows_fetched,
        rows_loaded=d_load["loaded_count"],
        rows_rejected=d_load["rejected_count"],
        metadata={"etl_prices": d_load},
    )

    with temp_db.readonly_connection() as conn:
        ceda_row = conn.execute(
            text("SELECT rows_fetched, rows_loaded, rows_rejected, status, metadata_json FROM pipeline_run_log WHERE run_id = :rid"),
            {"rid": run_id},
        ).mappings().one()
        daily_row = conn.execute(
            text("SELECT rows_fetched, rows_loaded, rows_rejected, status, metadata_json FROM pipeline_run_log WHERE run_id = :rid"),
            {"rid": d_run_id},
        ).mappings().one()

    assert ceda_row["rows_fetched"] == 2
    assert ceda_row["rows_loaded"] == 3  # 1 valid price + 2 valid arrivals
    assert ceda_row["rows_rejected"] == 1  # 1 invalid modal price
    ceda_meta = json.loads(ceda_row["metadata_json"]) if isinstance(ceda_row["metadata_json"], str) else ceda_row["metadata_json"]
    assert "raw_files" in ceda_meta and "etl_prices" in ceda_meta and "etl_arrivals" in ceda_meta

    assert daily_row["rows_fetched"] == 1
    assert daily_row["rows_loaded"] == 1
    assert daily_row["rows_rejected"] == 0
    daily_meta = json.loads(daily_row["metadata_json"]) if isinstance(daily_row["metadata_json"], str) else daily_row["metadata_json"]
    assert "raw_files" in daily_meta and "etl_prices" in daily_meta



@pytest.mark.integration
def test_live_postgresql_and_agmarknet_connectivity() -> None:
    """Runs only when DATABASE_URL and DATA_GOV_IN_API_KEY are explicitly set in environment."""
    db_url = os.environ.get("DATABASE_URL", "").strip()
    api_key = os.environ.get("DATA_GOV_IN_API_KEY", "").strip()
    if not db_url:
        pytest.skip("DATABASE_URL not configured; skipping live PostgreSQL integration test.")

    db = DatabaseManager(database_url=db_url)
    ok, msg = db.health_check()
    assert ok, msg

    if api_key:
        from ingestion.agmarknet_api import AgmarknetClient

        res = AgmarknetClient(api_key=api_key).fetch_daily_prices(limit=5, max_records=5, use_cache=False, save_raw=False)
        assert res.status == "success"
        assert res.rows_fetched > 0
