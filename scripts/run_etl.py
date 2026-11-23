"""Standalone ETL runner for local CSV/JSON files into staging and star-schema warehouse."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import configure_logging
from database.connection import get_db_manager
from database.seed import ensure_reference_dimensions
from etl.warehouse_loader import WarehouseLoader
from ingestion.apy_importer import ApyImporter
from ingestion.ceda_importer import CedaImporter


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MandiMitra ETL cleaning, validation, and warehouse upsert.")
    parser.add_argument("--prices-csv", type=Path, help="Path to prices CSV file.")
    parser.add_argument("--prices-json", type=Path, help="Path to raw Agmarknet JSON file.")
    parser.add_argument("--arrivals-csv", type=Path, help="Path to CEDA arrivals CSV file.")
    parser.add_argument("--apy-csv", type=Path, help="Path to DES APY CSV file.")
    parser.add_argument("--source-name", type=str, default="manual_etl_import", help="Source provenance tag.")
    parser.add_argument(
        "--seed-reference",
        action="store_true",
        help="Explicitly (re)seed target commodities, crops and mappings. Never done implicitly on PostgreSQL.",
    )
    parser.add_argument(
        "--allow-source-override",
        action="store_true",
        help="Allow this load to overwrite facts currently owned by a different source (default: skipped and logged).",
    )
    args = parser.parse_args()

    configure_logging()
    db = get_db_manager()
    db.initialize_schema()
    ensure_reference_dimensions(db, force=args.seed_reference)
    loader = WarehouseLoader(db)
    override = args.allow_source_override

    if args.prices_json:
        payload = json.loads(args.prices_json.read_text(encoding="utf-8"))
        records = payload.get("records", payload if isinstance(payload, list) else [])
        print("Prices JSON ETL:", loader.load_prices(records, source_name=args.source_name, raw_file_path=str(args.prices_json), allow_source_override=override))

    if args.prices_csv:
        res = CedaImporter().import_csv_files([args.prices_csv], record_kind="price", source_label=args.source_name)
        print("Prices CSV ETL:", loader.load_prices(res.records, source_name=args.source_name, raw_file_path=str(args.prices_csv), allow_source_override=override))

    if args.arrivals_csv:
        res = CedaImporter().import_csv_files([args.arrivals_csv], record_kind="arrival", source_label=args.source_name)
        print("Arrivals CSV ETL:", loader.load_arrivals(res.records, source_name=args.source_name, raw_file_path=str(args.arrivals_csv), allow_source_override=override))

    if args.apy_csv:
        res = ApyImporter().import_csv_files([args.apy_csv], source_label=args.source_name)
        print("APY CSV ETL:", loader.load_apy_production(res.records, source_name=args.source_name, raw_file_path=str(args.apy_csv), allow_source_override=override))


if __name__ == "__main__":
    main()
