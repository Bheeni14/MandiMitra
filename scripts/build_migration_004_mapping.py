"""Build the exact evidence mapping used by migration 004 from the raw Agmarknet pages.

Inputs (read-only): the off-project evidence backup of ``data/raw/agmarknet/2026-11-19/`` and its
SHA-256 manifest. The script first verifies every page byte-for-byte against the manifest (and, when
available, against the in-project copy), then re-cleans the raw records with the production cleaning
rules and selects ONLY the records whose raw commodity string is exactly a Paddy or Onion Green
spelling. Nothing is inferred from names alone: each evidence row carries its raw file, position in
that file, raw commodity string and exact prices.

    python scripts/build_migration_004_mapping.py            # verify + summary only (no writes)
    python scripts/build_migration_004_mapping.py --write    # also write the JSON fixture and SQL block

No database connection is made at any point.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.cleaning import clean_price_records  # noqa: E402
from etl.validation import validate_price_records  # noqa: E402

DEFAULT_EVIDENCE_DIR = Path(r"D:\mandimitra_backups\raw_evidence\agmarknet_2026-11-19")
DEFAULT_MANIFEST = Path(r"D:\mandimitra_backups\raw_evidence\agmarknet_2026-11-19.manifest.sha256")
PROJECT_RAW_DIR = ROOT / "data" / "raw" / "agmarknet" / "2026-11-19"
MIGRATION_PATH = ROOT / "database" / "migrations" / "004_split_paddy_onion_green_normalize_kerala.sql"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "migration_004_split_evidence.json"

BEGIN_MARKER = "-- BEGIN SPLIT EVIDENCE"
END_MARKER = "-- END SPLIT EVIDENCE"

# Values observed by the read-only Phase 2 audit; the build fails loudly if the evidence differs.
EXPECTED_TOTAL_RECORDS = 5000
EXPECTED_MANIFEST_AGGREGATE = "13b8fe7d7de3f984f652df5b3ee4aabe37d17348fafc84a244c3cf089d4fa4e3"
EXPECTED_MOVES = {
    ("Paddy(Common)", "Rice", "Paddy"): 127,
    ("Paddy(Basmati)", "Rice", "Paddy"): 10,
    ("Paddy(Fine/Superfine/Grade A)", "Rice", "Paddy"): 16,
    ("Onion Green", "Onion", "Onion Green"): 62,
}
EXPECTED_UNMOVED = {"Rice": 18, "Onion": 155}
# The raw string -> (stored commodity before 004, target commodity) rule. Exact match only.
SPLIT_RULES: dict[str, tuple[str, str]] = {
    "paddy": ("Rice", "Paddy"),
    "paddy(dhan)": ("Rice", "Paddy"),
    "paddy(dhan)(common)": ("Rice", "Paddy"),
    "paddy(dhan)(basmati)": ("Rice", "Paddy"),
    "paddy (dhan)(basmati)": ("Rice", "Paddy"),
    "paddy(common)": ("Rice", "Paddy"),
    "paddy(basmati)": ("Rice", "Paddy"),
    "paddy(fine/superfine/grade a)": ("Rice", "Paddy"),
    "onion green": ("Onion", "Onion Green"),
}


class EvidenceError(RuntimeError):
    """Raised when the evidence does not match what migration 004 is designed for."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_manifest(manifest_path: Path) -> tuple[dict[str, tuple[str, int]], str]:
    files: dict[str, tuple[str, int]] = {}
    aggregate = ""
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "AGGREGATE_SHA256":
            aggregate = parts[1]
        elif len(parts) == 3 and len(parts[0]) == 64:
            files[parts[1]] = (parts[0], int(parts[2]))
    return files, aggregate


def verify_pages(directory: Path, manifest: dict[str, tuple[str, int]], aggregate: str) -> list[Path]:
    """Verify every manifest file in ``directory`` and the aggregate digest, returning pages in offset order."""
    on_disk = {p.name: p for p in directory.glob("*.json")}
    if set(on_disk) != set(manifest):
        raise EvidenceError(f"File set differs from manifest in {directory}: {sorted(set(on_disk) ^ set(manifest))}")
    for name, (digest, size) in sorted(manifest.items()):
        path = on_disk[name]
        if path.stat().st_size != size or _sha256(path) != digest:
            raise EvidenceError(f"Checksum mismatch for {path}")
    recomputed = hashlib.sha256(
        "\n".join(f"{digest}  {name}  {size}" for name, (digest, size) in sorted(manifest.items())).encode("utf-8")
    ).hexdigest()
    if recomputed != aggregate or aggregate != EXPECTED_MANIFEST_AGGREGATE:
        raise EvidenceError(f"Aggregate digest mismatch: manifest={aggregate} recomputed={recomputed}")
    return sorted(on_disk.values(), key=lambda p: int(re.search(r"offset_(\d+)_", p.name).group(1)))  # type: ignore[union-attr]


def load_records(pages: list[Path]) -> list[tuple[str, int, dict[str, Any]]]:
    out: list[tuple[str, int, dict[str, Any]]] = []
    for page in pages:
        payload = json.loads(page.read_text(encoding="utf-8"))
        for idx, record in enumerate(payload.get("records", [])):
            out.append((page.name, idx, record))
    return out


def _money(value: Any) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def build_evidence(records: list[tuple[str, int, dict[str, Any]]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(records) != EXPECTED_TOTAL_RECORDS:
        raise EvidenceError(f"Expected {EXPECTED_TOTAL_RECORDS} raw records, found {len(records)}")

    raws = [r for _, _, r in records]
    cleaned = clean_price_records(raws, source_name="agmarknet_api")
    validation = validate_price_records(cleaned, source_name="agmarknet_api")
    if len(validation.valid_df) != EXPECTED_TOTAL_RECORDS or validation.rejects:
        raise EvidenceError(
            f"Expected all {EXPECTED_TOTAL_RECORDS} records valid; got valid={len(validation.valid_df)} "
            f"rejects={len(validation.rejects)}"
        )
    key_cols = ["arrival_date", "state", "district", "market", "commodity", "variety", "grade"]
    if cleaned.duplicated(subset=key_cols).any():
        raise EvidenceError("Natural-key collision after re-cleaning; the Paddy split would be ambiguous.")

    rows: list[dict[str, Any]] = []
    unmoved: Counter[str] = Counter()
    for i, ((file_name, idx, raw), cl) in enumerate(zip(records, cleaned.to_dict(orient="records"), strict=True)):
        raw_commodity = str(raw.get("commodity") or "").strip()
        rule = SPLIT_RULES.get(raw_commodity.lower())
        if rule is None:
            if cl["commodity"] in EXPECTED_UNMOVED:
                unmoved[cl["commodity"]] += 1
            continue
        source, target = rule
        if cl["commodity"] != target:
            raise EvidenceError(f"Cleaning rules disagree with SPLIT_RULES for {raw_commodity!r}: {cl['commodity']!r}")
        rows.append(
            {
                "evidence_id": len(rows) + 1,
                "raw_file": file_name,
                "raw_index": idx,
                "raw_commodity": raw_commodity,
                "source_commodity": source,
                "target_commodity": target,
                "arrival_date": cl["arrival_date"].isoformat(),
                "state": cl["state"],
                "district": cl["district"],
                "market_name": cl["market"],
                "variety": cl["variety"],
                "grade": cl["grade"],
                "min_price": _money(cl["min_price"]),
                "max_price": _money(cl["max_price"]),
                "modal_price": _money(cl["modal_price"]),
            }
        )

    moves = Counter((r["raw_commodity"], r["source_commodity"], r["target_commodity"]) for r in rows)
    if dict(moves) != EXPECTED_MOVES:
        raise EvidenceError(f"Move counts differ from the audit: {dict(moves)} != {EXPECTED_MOVES}")
    if dict(unmoved) != EXPECTED_UNMOVED:
        raise EvidenceError(f"Unmoved Rice/Onion counts differ from the audit: {dict(unmoved)} != {EXPECTED_UNMOVED}")

    summary = {
        "total_records": len(records),
        "valid_records": len(validation.valid_df),
        "rejected_records": len(validation.rejects),
        "moves_by_raw_commodity": {f"{raw} | {src} -> {dst}": n for (raw, src, dst), n in sorted(moves.items())},
        "paddy_moves": sum(n for (_, _, dst), n in moves.items() if dst == "Paddy"),
        "onion_green_moves": sum(n for (_, _, dst), n in moves.items() if dst == "Onion Green"),
        "rice_unmoved": unmoved["Rice"],
        "onion_unmoved": unmoved["Onion"],
        "moved_rows_in_kerala_state": sum(1 for r in rows if r["state"] == "Kerala"),
        "paddy_variety_distribution": dict(Counter(r["variety"] for r in rows if r["target_commodity"] == "Paddy").most_common()),
        "sum_modal_price_moved": str(sum(Decimal(r["modal_price"]) for r in rows)),
    }
    return rows, summary


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render_sql_block(rows: list[dict[str, Any]]) -> str:
    lines = [
        BEGIN_MARKER,
        "INSERT INTO operational.migration_004_split_evidence (",
        "    evidence_id, raw_file, raw_index, raw_commodity, source_commodity, target_commodity,",
        "    arrival_date, state, district, market_name, variety, grade, min_price, max_price, modal_price",
        ")",
        "VALUES",
    ]
    values = []
    for r in rows:
        values.append(
            "    ("
            + ", ".join(
                [
                    str(r["evidence_id"]),
                    _sql_str(r["raw_file"]),
                    str(r["raw_index"]),
                    _sql_str(r["raw_commodity"]),
                    _sql_str(r["source_commodity"]),
                    _sql_str(r["target_commodity"]),
                    _sql_str(r["arrival_date"]),
                    _sql_str(r["state"]),
                    _sql_str(r["district"]),
                    _sql_str(r["market_name"]),
                    _sql_str(r["variety"]),
                    _sql_str(r["grade"]),
                    r["min_price"],
                    r["max_price"],
                    r["modal_price"],
                ]
            )
            + ")"
        )
    lines.append(",\n".join(values) + ";")
    lines.append(END_MARKER)
    block = "\n".join(lines)
    # The migrator runs SQLite statements through text(), where a colon followed by a word is a bind
    # parameter, and splits statements on semicolons: neither may appear inside the evidence values.
    body = block.replace(";\n" + END_MARKER, "\n" + END_MARKER)
    if ":" in body or ";" in body:
        raise EvidenceError("Evidence contains a colon or semicolon that would break SQL portability.")
    return block


def replace_sql_block(sql_text: str, block: str) -> str:
    start = sql_text.index(BEGIN_MARKER)
    end = sql_text.index(END_MARKER) + len(END_MARKER)
    return sql_text[:start] + block + sql_text[end:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--write", action="store_true", help="Write the JSON fixture and refresh the SQL evidence block.")
    args = parser.parse_args()

    manifest, aggregate = read_manifest(args.manifest)
    pages = verify_pages(args.evidence_dir, manifest, aggregate)
    print(f"Evidence backup verified: {len(pages)} pages, aggregate sha256 {aggregate}")
    if PROJECT_RAW_DIR.exists():
        verify_pages(PROJECT_RAW_DIR, manifest, aggregate)
        print(f"In-project copy matches the manifest byte-for-byte: {PROJECT_RAW_DIR}")

    rows, summary = build_evidence(load_records(pages))
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    block = render_sql_block(rows)
    if args.write:
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE_PATH.write_text(
            json.dumps(
                {"manifest_aggregate_sha256": aggregate, "summary": summary, "rows": rows},
                indent=1,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        MIGRATION_PATH.write_text(replace_sql_block(MIGRATION_PATH.read_text(encoding="utf-8"), block), encoding="utf-8")
        print(f"Wrote {FIXTURE_PATH} and refreshed the evidence block in {MIGRATION_PATH}")
    else:
        print("Dry run: nothing written (use --write).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
