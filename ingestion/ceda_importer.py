"""CEDA Agmarknet Historical Archive importer (prices and arrival quantities)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from config import CEDA_BASE_URL, DATA_RAW_DIR, get_settings
from ingestion.agmarknet_api import IngestionResult
from ingestion.storage import RawZoneStorage

logger = logging.getLogger(__name__)


@dataclass
class CsvInspectionReport:
    path: str
    row_count: int
    columns: list[str]
    detected_mapping: dict[str, str | None]
    sample_rows: list[dict[str, Any]]


class CedaImporter:
    """Inspects and imports historical price and arrival files/API exports from CEDA Agmarknet."""

    PRICE_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
        "arrival_date": ("date", "arrival_date", "t_date", "reported_date", "Arrival Date", "Price Date"),
        "state": ("state", "state_name", "State", "State Name"),
        "district": ("district", "district_name", "District", "District Name", "census_district"),
        "market": ("market", "market_name", "Market", "Market Name", "mandi"),
        "commodity": ("commodity", "commodity_name", "Commodity", "Commodity Name"),
        "variety": ("variety", "Variety"),
        "grade": ("grade", "Grade"),
        "min_price": ("min_price", "Min Price", "Min Price (Rs./Quintal)", "min_price_rs_qtl"),
        "max_price": ("max_price", "Max Price", "Max Price (Rs./Quintal)", "max_price_rs_qtl"),
        "modal_price": ("modal_price", "Modal Price", "Modal Price (Rs./Quintal)", "modal_price_rs_qtl"),
        "quantity_tonnes": ("quantity_tonnes", "arrivals", "quantity", "Arrivals (Tonnes)", "arrival_quantity", "Arrivals"),
    }

    def __init__(
        self,
        base_url: str = CEDA_BASE_URL,
        storage: RawZoneStorage | None = None,
        timeout: int | None = None,
        session: requests.Session | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = base_url.rstrip("/")
        self.storage = storage or RawZoneStorage()
        self.timeout = timeout or settings.request_timeout_seconds
        self.session = session or requests.Session()

    @classmethod
    def _detect_columns(cls, columns: list[str]) -> dict[str, str | None]:
        lower_lookup = {col.strip().lower(): col for col in columns}
        mapping: dict[str, str | None] = {}
        for canonical, aliases in cls.PRICE_COLUMN_ALIASES.items():
            matched = None
            for alias in aliases:
                if alias in columns:
                    matched = alias
                    break
                if alias.lower() in lower_lookup:
                    matched = lower_lookup[alias.lower()]
                    break
            mapping[canonical] = matched
        return mapping

    def inspect_csv(self, csv_path: Path, sample_n: int = 5) -> CsvInspectionReport:
        """Inspect actual CSV columns and sample rows before importing."""
        df = pd.read_csv(csv_path, nrows=500)
        mapping = self._detect_columns(list(df.columns))
        total_rows = sum(1 for _ in csv_path.open("r", encoding="utf-8", errors="ignore")) - 1
        return CsvInspectionReport(
            path=str(csv_path),
            row_count=max(total_rows, len(df)),
            columns=list(df.columns),
            detected_mapping=mapping,
            sample_rows=df.head(sample_n).to_dict(orient="records"),
        )

    def probe_ceda_portal(self) -> tuple[bool, str]:
        """Check whether the live CEDA Agmarknet portal API is reachable."""
        endpoints = (f"{self.base_url}/api/commodities", f"{self.base_url}/README.html")
        errors: list[str] = []
        for url in endpoints:
            try:
                resp = self.session.get(url, timeout=min(self.timeout, 15), headers={"User-Agent": "MandiMitra/1.0"})
                if resp.status_code == 200:
                    return True, f"CEDA portal reachable ({url})"
                errors.append(f"{url} -> HTTP {resp.status_code}")
            except requests.RequestException as exc:
                errors.append(f"{url} -> {type(exc).__name__}: {exc}")
        return False, "; ".join(errors)

    def fetch_portal_series(
        self,
        commodity_id: int,
        state_id: int,
        from_date: str,
        to_date: str,
        include_arrivals: bool = True,
    ) -> IngestionResult:
        """Disabled: the CEDA portal returns STATE-level aggregates, never market-level prices.

        Loading those into the market-level ``fact_price_daily`` would fabricate market rows. Per the
        approved Phase 2 decision a separate state-level aggregate model will be designed later, so this
        method performs no network call and returns no records. Market-level CEDA history is only
        accepted through ``import_csv_files`` for files that carry real market names.
        """
        return IngestionResult(
            source_name="ceda_archive",
            status="unavailable",
            error_message=(
                "CEDA portal series fetch unavailable: the portal serves state-level aggregates, which are "
                "not loaded into market-level fact_price_daily (pending a separate state-level aggregate model)."
            ),
            metadata={
                "commodity_id": commodity_id,
                "state_id": state_id,
                "from_date": from_date,
                "to_date": to_date,
                "disabled_reason": "state_level_aggregate_not_market_level",
            },
        )

    def import_csv_files(
        self,
        csv_paths: list[Path] | None = None,
        record_kind: str = "auto",
        preserve_raw: bool = True,
        source_label: str = "ceda_archive",
    ) -> IngestionResult:
        """Import CEDA CSV files (prices, arrivals, or combined) after inspecting actual headers."""
        if csv_paths is None:
            ceda_dir = DATA_RAW_DIR / "ceda"
            csv_paths = sorted(ceda_dir.glob("*.csv")) if ceda_dir.exists() else []

        if not csv_paths:
            reachable, probe_msg = self.probe_ceda_portal()
            return IngestionResult(
                source_name=source_label,
                status="unavailable",
                error_message=(
                    "No local CEDA CSV files found in data/raw/ceda/ and remote CEDA portal probe reported: "
                    f"{probe_msg}"
                ),
                metadata={"portal_reachable": reachable, "probe_detail": probe_msg},
            )

        all_records: list[dict[str, Any]] = []
        raw_files: list[Path] = []
        inspected_schemas: list[dict[str, Any]] = []

        try:
            for path in csv_paths:
                if preserve_raw:
                    raw_files.append(self.storage.save_raw_file(source="ceda", src_path=path))
                df = pd.read_csv(path)
                mapping = self._detect_columns(list(df.columns))
                inspected_schemas.append({"file": path.name, "columns": list(df.columns), "mapping": mapping})

                has_price = mapping["modal_price"] is not None
                has_arr = mapping["quantity_tonnes"] is not None
                inferred_kind = record_kind
                if record_kind == "auto":
                    if has_price and has_arr:
                        inferred_kind = "combined"
                    elif has_arr:
                        inferred_kind = "arrival"
                    else:
                        inferred_kind = "price"

                for row in df.to_dict(orient="records"):
                    normalized: dict[str, Any] = {"record_type": inferred_kind, "source_name": source_label}
                    for canon_key, src_col in mapping.items():
                        if src_col is not None and src_col in row:
                            normalized[canon_key] = row[src_col]
                    all_records.append(normalized)

            return IngestionResult(
                source_name=source_label,
                status="success",
                records=all_records,
                raw_files=raw_files,
                rows_fetched=len(all_records),
                metadata={"files": [p.name for p in csv_paths], "inspected_schemas": inspected_schemas},
            )
        except Exception as exc:
            logger.error("Failed importing CEDA CSV files: %s", exc)
            return IngestionResult(
                source_name=source_label,
                status="failed",
                records=all_records,
                raw_files=raw_files,
                rows_fetched=len(all_records),
                error_message=str(exc),
            )
