"""Directorate of Economics & Statistics (DES) APY Crop Production importer."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from config import APY_ALT_URL, APY_REPORT_URL, DATA_RAW_DIR, get_settings
from ingestion.agmarknet_api import IngestionResult
from ingestion.storage import RawZoneStorage

logger = logging.getLogger(__name__)


class ApyImporter:
    """Imports district-wise, season-wise Area, Production and Yield (APY) crop statistics."""

    COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
        "crop_year": ("crop_year", "Crop_Year", "year", "Year", "fiscal_year"),
        "state": ("state", "State", "State_Name", "state_name", "State Name"),
        "district": (
            "district",
            "District",
            "District_Name",
            "district_name",
            "district_as_per_lgd",
            "district_as_per_source",
            "District Name",
        ),
        "season": ("season", "Season"),
        "crop_name": ("crop_name", "Crop", "crop", "Crop_Name", "Crop Name"),
        "crop_type": ("crop_type", "Crop_Type", "crop_category", "Crop Category"),
        "area_ha": ("area_ha", "Area", "area", "Area (Ha)"),
        "area_unit": ("area_unit", "Area unit", "Area Unit"),
        "production_t": ("production_t", "Production", "production", "Production (Tonnes)"),
        "production_unit": ("production_unit", "Production unit", "Production Unit"),
        "yield_t_ha": ("yield_t_ha", "Yield", "crop_yield", "yield", "Yield (Tonne/Ha)"),
        "yield_unit": ("yield_unit", "Yield unit", "Yield Unit"),
        "unit": ("unit", "Unit"),
    }

    def __init__(
        self,
        storage: RawZoneStorage | None = None,
        timeout: int | None = None,
        session: requests.Session | None = None,
    ) -> None:
        settings = get_settings()
        self.storage = storage or RawZoneStorage()
        self.timeout = timeout or settings.request_timeout_seconds
        self.session = session or requests.Session()

    @staticmethod
    def parse_crop_year(value: Any) -> int | None:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        s = str(value).strip()
        match = re.search(r"(\d{4})", s)
        if not match:
            return None
        yr = int(match.group(1))
        return yr if 1990 <= yr <= 2100 else None

    @classmethod
    def _detect_columns(cls, columns: list[str]) -> dict[str, str | None]:
        lower_lookup = {col.strip().lower(): col for col in columns}
        mapping: dict[str, str | None] = {}
        for canonical, aliases in cls.COLUMN_ALIASES.items():
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

    def probe_apy_portals(self) -> tuple[bool, str]:
        """Probe DES APY report portals and report exact reachability/TLS status."""
        errors: list[str] = []
        for url in (APY_REPORT_URL, APY_ALT_URL):
            try:
                resp = self.session.get(url, timeout=min(self.timeout, 15), headers={"User-Agent": "MandiMitra/1.0"})
                if resp.status_code == 200:
                    return True, f"APY portal reachable ({url})"
                errors.append(f"{url} -> HTTP {resp.status_code}")
            except requests.RequestException as exc:
                errors.append(f"{url} -> {type(exc).__name__}: {exc}")
        return False, "; ".join(errors)

    def import_csv_files(
        self,
        csv_paths: list[Path] | None = None,
        preserve_raw: bool = True,
        source_label: str = "des_apy",
    ) -> IngestionResult:
        """Import APY production CSVs from data/raw/apy or explicit paths."""
        if csv_paths is None:
            apy_dir = DATA_RAW_DIR / "apy"
            csv_paths = sorted(apy_dir.glob("*.csv")) if apy_dir.exists() else []

        if not csv_paths:
            reachable, probe_msg = self.probe_apy_portals()
            return IngestionResult(
                source_name=source_label,
                status="unavailable",
                error_message=(
                    "No local APY CSV files found in data/raw/apy/ and DES portal probe reported: "
                    f"{probe_msg}"
                ),
                metadata={"portal_reachable": reachable, "probe_detail": probe_msg},
            )

        records: list[dict[str, Any]] = []
        raw_files: list[Path] = []
        schemas: list[dict[str, Any]] = []

        try:
            for path in csv_paths:
                if preserve_raw:
                    raw_files.append(self.storage.save_raw_file(source="apy", src_path=path))
                df = pd.read_csv(path)
                mapping = self._detect_columns(list(df.columns))
                schemas.append({"file": path.name, "columns": list(df.columns), "mapping": mapping})

                for row in df.to_dict(orient="records"):
                    rec: dict[str, Any] = {"source_name": source_label}
                    for canon_key, src_col in mapping.items():
                        if src_col is not None and src_col in row:
                            rec[canon_key] = row[src_col]
                    records.append(rec)

            return IngestionResult(
                source_name=source_label,
                status="success",
                records=records,
                raw_files=raw_files,
                rows_fetched=len(records),
                metadata={"files": [p.name for p in csv_paths], "inspected_schemas": schemas},
            )
        except Exception as exc:
            logger.error("APY CSV import failed: %s", exc)
            return IngestionResult(
                source_name=source_label,
                status="failed",
                records=records,
                raw_files=raw_files,
                rows_fetched=len(records),
                error_message=str(exc),
            )
