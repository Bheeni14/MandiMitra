"""Rebuild tests/fixtures/production_commodities_2026_11_20.json from local raw Agmarknet files.

Offline only: reads ``data/raw/agmarknet/<date>/*.json`` and reproduces the commodity names
that the pre-correction normaliser stored in production. No database access.

The ``approved_*``, ``review_status`` and ``review_note`` fields record the approved migration 003
outcome from ``etl/commodity_catalog.py``, so a catalog change only passes the tests once this
fixture has been deliberately regenerated.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import TARGET_COMMODITIES  # noqa: E402
from etl.cleaning import clean_text  # noqa: E402
from etl.commodity_catalog import COMMODITY_REVIEW_NOTES, PRODUCTION_COMMODITY_CATALOG  # noqa: E402

# Raw spellings that the pre-correction normaliser mapped onto target commodities.
_LEGACY_TARGET_ALIASES: dict[str, str] = {
    "onion green": "Onion",
    "groundnut(split)": "Groundnut",
    "soyabean": "Soybean",
    "turmeric(raw)": "Turmeric",
    "rice(fine/superfine/grade a)": "Rice",
    "paddy(basmati)": "Rice",
    "paddy(common)": "Rice",
    "paddy(fine/superfine/grade a)": "Rice",
    "bengal gram(gram)(whole)": "Chickpea",
    "green gram(moong)(whole)": "Green Gram (Moong)",
}


def build(raw_dir: Path) -> list[dict]:
    targets = {s.name: (s.category, s.storable) for s in TARGET_COMMODITIES}
    target_lower = {name.lower(): name for name in targets}
    raw_counts: Counter[str] = Counter()
    for path in sorted(raw_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload if isinstance(payload, list) else payload.get("records") or payload.get("data") or []
        for rec in records:
            raw_counts[rec.get("commodity")] += 1

    grouped: dict[str, dict] = defaultdict(lambda: {"raw_names": [], "price_rows": 0})
    for raw, count in raw_counts.items():
        cleaned = clean_text(raw)
        key = cleaned.lower()
        name = _LEGACY_TARGET_ALIASES.get(key) or target_lower.get(key) or cleaned.title()
        grouped[name]["raw_names"].append(raw)
        grouped[name]["price_rows"] += count
    for name in targets:
        grouped[name]  # seeded by database/seed.py even without price rows

    rows = []
    for name in sorted(grouped):
        category, storable = targets.get(name, ("other", True))
        approved = PRODUCTION_COMMODITY_CATALOG[name]
        rows.append(
            {
                "commodity_name": name,
                "pre_migration_category": category,
                "pre_migration_storable": storable,
                "price_rows": grouped[name]["price_rows"],
                "raw_source_names": sorted(grouped[name]["raw_names"]),
                "approved_category": approved.category,
                "approved_storable": approved.storable,
                "review_status": approved.review_status,
                "review_note": COMMODITY_REVIEW_NOTES.get(name),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default=str(PROJECT_ROOT / "data" / "raw" / "agmarknet" / "2026-11-19"))
    parser.add_argument(
        "--out", default=str(PROJECT_ROOT / "tests" / "fixtures" / "production_commodities_2026_11_20.json")
    )
    args = parser.parse_args()
    rows = build(Path(args.raw_dir))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "source": "Local Agmarknet raw files 2026-11-19, cross-checked against the read-only production audit of 2026-11-20",
                "commodities": rows,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(rows)} production commodity names, {sum(r['price_rows'] for r in rows)} price rows -> {out}")


if __name__ == "__main__":
    main()
