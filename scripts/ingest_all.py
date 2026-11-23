"""One-command ingestion runner for Agmarknet daily API, CEDA CSVs, APY CSVs, or clearly labelled Demo fixtures."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import configure_logging
from database.connection import DatabaseManager, get_db_manager
from database.seed import ensure_reference_dimensions, seed_reference_dimensions
from etl.warehouse_loader import DEFAULT_LOAD_CHUNK_SIZE, WarehouseLoader
from ingestion.apy_importer import ApyImporter
from ingestion.ceda_importer import CedaImporter
from ingestion.daily_capture import run_daily_capture
from ingestion.scheduler import (
    finish_pipeline_run,
    load_sample_fixtures_paths,
    run_apy_import,
    run_ceda_backfill,
    start_pipeline_run,
)
from scripts.generate_demo_data import generate_demo_datasets


def run_demo_end_to_end(db: DatabaseManager, regenerate: bool = False) -> dict[str, dict]:
    if not db.is_demo:
        raise SystemExit(
            "Refusing to load demo fixtures into a non-demo database. Demo data is only ever written to the "
            "disposable Demo SQLite warehouse; use --daily, --ceda or --apy for real sources."
        )
    db.initialize_schema()
    seed_reference_dimensions(db)
    paths = generate_demo_datasets() if regenerate else load_sample_fixtures_paths()
    if not all(p.exists() for p in paths.values()):
        paths = generate_demo_datasets()

    loader = WarehouseLoader(db)
    ceda = CedaImporter()
    apy = ApyImporter()

    p_res = ceda.import_csv_files([paths["prices"]], record_kind="price", preserve_raw=False, source_label="demo_agmarknet_sample")
    run_p = start_pipeline_run(db, "demo_ingest_prices", "demo_agmarknet_sample")
    load_p = loader.load_prices(p_res.records, source_name="demo_agmarknet_sample", raw_file_path=str(paths["prices"]), run_id=run_p)
    finish_pipeline_run(db, run_p, "success", len(p_res.records), load_p["loaded_count"], load_p["rejected_count"])

    a_res = ceda.import_csv_files([paths["arrivals"]], record_kind="arrival", preserve_raw=False, source_label="demo_ceda_arrivals_sample")
    run_a = start_pipeline_run(db, "demo_ingest_arrivals", "demo_ceda_arrivals_sample")
    load_a = loader.load_arrivals(a_res.records, source_name="demo_ceda_arrivals_sample", raw_file_path=str(paths["arrivals"]), run_id=run_a)
    finish_pipeline_run(db, run_a, "success", len(a_res.records), load_a["loaded_count"], load_a["rejected_count"])

    y_res = apy.import_csv_files([paths["apy"]], preserve_raw=False, source_label="demo_apy_sample")
    run_y = start_pipeline_run(db, "demo_ingest_apy", "demo_apy_sample")
    load_y = loader.load_apy_production(y_res.records, source_name="demo_apy_sample", raw_file_path=str(paths["apy"]), run_id=run_y)
    finish_pipeline_run(db, run_y, "success", len(y_res.records), load_y["loaded_count"], load_y["rejected_count"])

    return {"prices": load_p, "arrivals": load_a, "apy": load_y}


def main() -> None:
    parser = argparse.ArgumentParser(description="MandiMitra Ingestion & Warehouse Loader")
    parser.add_argument("--demo", action="store_true", help="Generate and load clearly labelled demo fixtures.")
    parser.add_argument("--regenerate-demo", action="store_true", help="Force regenerate demo CSV fixtures.")
    parser.add_argument("--daily", action="store_true", help="Fetch daily live prices from data.gov.in API.")
    parser.add_argument("--ceda", nargs="*", type=Path, help="Optional CEDA historical price/arrival CSV path(s).")
    parser.add_argument("--apy", nargs="*", type=Path, help="Optional DES APY production CSV path(s).")
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Optional cap on records pulled from the daily API. Default: fetch the complete feed. "
        "A cap marks the run as truncated (partial_success).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_LOAD_CHUNK_SIZE,
        help="Rows per warehouse load transaction (each chunk commits independently and idempotently).",
    )
    parser.add_argument(
        "--seed-reference",
        action="store_true",
        help="Explicitly (re)seed target commodities, crops and mappings. Never done implicitly on PostgreSQL.",
    )
    parser.add_argument("--state", type=str, default=None, help="Optional state filter for daily API pull.")
    parser.add_argument("--commodity", type=str, default=None, help="Optional commodity filter for daily API pull.")
    args = parser.parse_args()

    configure_logging()
    db = get_db_manager()
    db.initialize_schema()
    seeded = ensure_reference_dimensions(db, force=args.seed_reference)
    if not seeded["written"]:
        print("Reference dimensions already complete; no seed writes performed.")

    if args.demo or (not args.daily and args.ceda is None and args.apy is None):
        summary = run_demo_end_to_end(db, regenerate=args.regenerate_demo)
        print("DEMO PIPELINE COMPLETED:", summary)
        return

    loader = WarehouseLoader(db)
    if args.daily:
        filters = {}
        if args.state:
            filters["state"] = args.state
        if args.commodity:
            filters["commodity"] = args.commodity
        summary = run_daily_capture(
            db=db,
            loader=loader,
            max_records=args.max_records,
            filters=filters or None,
            chunk_size=args.chunk_size,
        )
        rec = summary["reconciliation"] or {}
        print(
            f"Daily Agmarknet capture status={summary['status']} fetch={summary['fetch_status']} "
            f"reported={rec.get('reported_total')} fetched={rec.get('fetched_count')} "
            f"loaded={rec.get('loaded_count')} rejected={rec.get('rejected_count')} "
            f"source_conflicts={rec.get('source_conflict_count')} reconciled={rec.get('reconciled')}"
        )
        for issue in rec.get("issues", []):
            print("  ISSUE:", issue)
        for warning in rec.get("warnings", []):
            print("  WARNING:", warning)
        if summary.get("error"):
            print("  ERROR:", summary["error"])

    if args.ceda is not None:
        run_id, res = run_ceda_backfill(db=db, csv_paths=args.ceda or None, auto_finish=False)
        print(f"CEDA backfill status={res.status} fetched={res.rows_fetched} err={res.error_message}")
        if res.records:
            try:
                price_recs = [r for r in res.records if r.get("record_type") in ("price", "combined")]
                arr_recs = [r for r in res.records if r.get("record_type") in ("arrival", "combined")]
                raw_path_arg = str(res.raw_files[0]) if res.raw_files else None
                total_loaded = 0
                total_rejected = 0
                etl_meta: dict[str, dict] = {}
                if price_recs:
                    p_load = loader.load_prices(price_recs, source_name="ceda_archive", raw_file_path=raw_path_arg, run_id=run_id)
                    total_loaded += p_load["loaded_count"]
                    total_rejected += p_load["rejected_count"]
                    etl_meta["etl_prices"] = p_load
                    print("Loaded CEDA prices:", p_load)
                if arr_recs:
                    a_load = loader.load_arrivals(arr_recs, source_name="ceda_archive", raw_file_path=raw_path_arg, run_id=run_id)
                    total_loaded += a_load["loaded_count"]
                    total_rejected += a_load["rejected_count"]
                    etl_meta["etl_arrivals"] = a_load
                    print("Loaded CEDA arrivals:", a_load)
                final_status = "success" if total_loaded > 0 else "partial_success"
                finish_pipeline_run(
                    db,
                    run_id,
                    final_status,
                    rows_fetched=res.rows_fetched,
                    rows_loaded=total_loaded,
                    rows_rejected=total_rejected,
                    error_message=res.error_message,
                    metadata=etl_meta,
                )
            except Exception as exc:
                finish_pipeline_run(
                    db,
                    run_id,
                    "failed",
                    rows_fetched=res.rows_fetched,
                    error_message=str(exc),
                )
                raise

    if args.apy is not None:
        run_id, res = run_apy_import(db=db, csv_paths=args.apy or None, auto_finish=False)
        print(f"APY import status={res.status} fetched={res.rows_fetched} err={res.error_message}")
        if res.records:
            try:
                y_load = loader.load_apy_production(
                    res.records,
                    source_name="des_apy",
                    raw_file_path=str(res.raw_files[0]) if res.raw_files else None,
                    run_id=run_id,
                )
                finish_pipeline_run(
                    db,
                    run_id,
                    y_load["status"],
                    rows_fetched=res.rows_fetched,
                    rows_loaded=y_load["loaded_count"],
                    rows_rejected=y_load["rejected_count"],
                    error_message=res.error_message,
                    metadata={"etl_apy": y_load},
                )
                print("Loaded APY production:", y_load)
            except Exception as exc:
                finish_pipeline_run(
                    db,
                    run_id,
                    "failed",
                    rows_fetched=res.rows_fetched,
                    error_message=str(exc),
                )
                raise


if __name__ == "__main__":
    main()
