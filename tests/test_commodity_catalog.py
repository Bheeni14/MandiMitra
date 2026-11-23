"""Reviewed commodity catalog: Python vs migration 003 parity, merge policy and classifier fixes."""

from __future__ import annotations

import re

import pytest

from config import DATABASE_DIR, TARGET_COMMODITIES
from etl.cleaning import (
    COMMODITY_ALIASES,
    DISTINCT_COMMODITY_KEYS,
    classify_commodity,
    classify_commodity_storability,
    normalize_commodity_name,
)
from etl.commodity_catalog import (
    COMMODITIES_REQUIRING_REVIEW,
    COMMODITY_REVIEW_NOTES,
    PRODUCTION_COMMODITY_CATALOG,
    PRODUCTION_COMMODITY_CLASSIFICATION,
    PRODUCTION_COMMODITY_REVIEW_STATUS,
    PROVISIONAL_CLASSIFICATION,
    REVIEW_STATUS_PROVISIONAL,
    REVIEW_STATUS_REQUIRED,
    REVIEW_STATUS_REVIEWED,
    REVIEW_STATUSES,
    UNCLASSIFIED_CATEGORY,
    compact_commodity_key,
    heuristic_commodity_classification,
    lookup_catalog_classification,
)
from tests.production_like import load_production_fixture

MIGRATION_003 = DATABASE_DIR / "migrations" / "003_commodity_aliases_and_storability.sql"
MERGE_TARGETS = {"Pigeon Pea (Tur)", "Green Gram (Moong)", "Chickpea"}


def _sql_text() -> str:
    return MIGRATION_003.read_text(encoding="utf-8")


def _sql_reviewed_classification() -> list[tuple[str, str, bool, str]]:
    block = _sql_text().split("-- BEGIN REVIEWED CLASSIFICATION")[1].split("-- END REVIEWED CLASSIFICATION")[0]
    rows = re.findall(r"\('((?:[^']|'')*)',\s*'([a-z_]+)',\s*(TRUE|FALSE),\s*'([a-z_]+)'\)", block)
    assert len(rows) == block.count("\n    ("), "unparsed rows in the migration 003 classification block"
    return [(name.replace("''", "'"), cat, flag == "TRUE", status) for name, cat, flag, status in rows]


def _sql_alias_map() -> dict[str, str]:
    section = _sql_text().split("INSERT INTO operational.migration_003_alias_map")[1].split(";")[0]
    rows = re.findall(r"\('((?:[^']|'')*)',\s*'((?:[^']|'')*)'\)", section)
    assert len(rows) == len({r[0] for r in rows}), "duplicate alias keys in migration 003"
    return dict(rows)


def test_catalog_covers_exactly_the_190_production_names() -> None:
    fixture_names = [c["commodity_name"] for c in load_production_fixture()]
    assert len(fixture_names) == 190
    assert set(PRODUCTION_COMMODITY_CLASSIFICATION) == set(fixture_names)


def test_catalog_targets_match_config() -> None:
    for spec in TARGET_COMMODITIES:
        assert PRODUCTION_COMMODITY_CLASSIFICATION[spec.name] == (spec.category, spec.storable)


def test_migration_003_classification_matches_python_catalog_for_all_190_names() -> None:
    sql_rows = _sql_reviewed_classification()
    assert len(sql_rows) == 190
    assert len({r[0] for r in sql_rows}) == 190, "duplicate commodity names in migration 003"
    sql_map = {name: (cat, storable) for name, cat, storable, _ in sql_rows}
    assert sql_map == PRODUCTION_COMMODITY_CLASSIFICATION
    assert {name: status for name, _, _, status in sql_rows} == PRODUCTION_COMMODITY_REVIEW_STATUS


def test_fixture_records_the_approved_outcome_for_all_190_names() -> None:
    for entry in load_production_fixture():
        name = entry["commodity_name"]
        expected = PRODUCTION_COMMODITY_CATALOG[name]
        assert (entry["approved_category"], entry["approved_storable"], entry["review_status"]) == expected, name
        assert entry["review_note"] == COMMODITY_REVIEW_NOTES.get(name), name


def test_python_classifier_matches_catalog_for_all_190_names() -> None:
    mismatches = {
        name: (classify_commodity_storability(name), expected)
        for name, expected in PRODUCTION_COMMODITY_CLASSIFICATION.items()
        if classify_commodity_storability(name) != expected
    }
    assert mismatches == {}


def test_python_classifier_matches_catalog_for_raw_source_spellings() -> None:
    for entry in load_production_fixture():
        for raw in entry["raw_source_names"]:
            canonical = normalize_commodity_name(raw)
            if canonical != entry["commodity_name"]:
                # Pre-correction merges (Paddy -> Rice, Onion Green -> Onion) are no longer performed;
                # pulse aliases now normalise straight to their merge target.
                assert raw.strip().lower().startswith(("paddy", "onion green")) or canonical in MERGE_TARGETS, raw
                if canonical in MERGE_TARGETS:
                    assert classify_commodity_storability(raw) == PRODUCTION_COMMODITY_CLASSIFICATION[canonical]
                continue
            assert classify_commodity_storability(raw) == PRODUCTION_COMMODITY_CLASSIFICATION[entry["commodity_name"]], raw


def test_migration_003_alias_map_matches_python_aliases() -> None:
    expected = {
        key: target
        for key, target in COMMODITY_ALIASES.items()
        if target in MERGE_TARGETS and key != target.lower()
    }
    assert _sql_alias_map() == expected


def test_only_alias_names_normalize_away_from_their_stored_name() -> None:
    alias_map = _sql_alias_map()
    for name in PRODUCTION_COMMODITY_CLASSIFICATION:
        canonical = normalize_commodity_name(name)
        if canonical != name:
            assert alias_map.get(name.lower()) == canonical, name
    assert normalize_commodity_name("Red Gram/Arhar/Tur(Whole)") == "Pigeon Pea (Tur)"


@pytest.mark.parametrize(
    "raw",
    [
        "Paddy(Dhan)",
        "Paddy(Dhan)(Common)",
        "Paddy(Dhan)(Basmati)",
        "Paddy(Common)",
        "Paddy(Basmati)",
        "Paddy(Fine/Superfine/Grade A)",
        "paddy dhan",
        "Onion Green",
        "onion green",
        "Chillies",
        "Groundnut pods(raw)",
        "Groundnut Pods(Raw)",
        "GROUNDNUT PODS (RAW)",
        "Groundnut Pods ( Raw )",
        "Groundnut Pods",
    ],
)
def test_merge_policy_keeps_distinct_commodities_separate(raw: str) -> None:
    assert normalize_commodity_name(raw) not in {"Rice", "Onion", "Green Chilli", "Groundnut"}


def test_distinct_keys_are_never_aliases() -> None:
    assert not DISTINCT_COMMODITY_KEYS & set(COMMODITY_ALIASES)
    assert "groundnut pods" not in COMMODITY_ALIASES


def test_explicit_green_chilli_spellings_still_normalize() -> None:
    for raw in ("Chillies(Green)", "Chillies Green", "Green Chillies", "Chilli(Green)"):
        assert normalize_commodity_name(raw) == "Green Chilli"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Cinamon(Dalchini)", ("spice", True)),
        ("Cummin Seed(Jeera)", ("spice", True)),
        ("Corriander Seed", ("spice", True)),
        ("Methi Seeds", ("spice", True)),
        ("Nigella", ("spice", True)),
        ("Black Pepper", ("spice", True)),
        ("Ginger(Dry)", ("spice", True)),
        ("Bay Leaf(Tejpatta)", ("spice", True)),
        ("Almond(Badam)", ("dry_fruit_nut", True)),
        ("Walnut", ("dry_fruit_nut", True)),
        ("Cashewnuts", ("dry_fruit_nut", True)),
        ("Dried Mango", ("dry_fruit_nut", True)),
        ("Kodo Millet(Varagu)", ("cereal", True)),
        ("Same/Savi", ("cereal", True)),
        ("Bajra(Pearl Millet/Cumbu)", ("cereal", True)),
        ("Lentil(Masur)(Whole)", ("pulse", True)),
        ("Masur Dal", ("pulse", True)),
        ("Moath Dal", ("pulse", True)),
        ("Peas(Dry)", ("pulse", True)),
        ("Green Gram Dal(Moong Dal)", ("pulse", True)),
        ("Castor Seed", ("oilseed", True)),
        ("Coconut Oil", ("oilseed", True)),
        ("Coriander(Leaves)", ("vegetable", False)),
        ("Onion Green", ("vegetable", False)),
        ("Chillies", (UNCLASSIFIED_CATEGORY, False)),
        ("Something Unheard Of", (UNCLASSIFIED_CATEGORY, False)),
    ],
)
def test_keyword_fallback_fixes(name: str, expected: tuple[str, bool]) -> None:
    assert heuristic_commodity_classification(name) == expected


def test_dal_matches_only_as_a_whole_word() -> None:
    assert heuristic_commodity_classification("Dalchini")[0] != "pulse"
    assert heuristic_commodity_classification("Urad Dal")[0] == "pulse"


def test_unknown_commodities_are_flagged_not_silently_classified() -> None:
    result = classify_commodity("Unknown Exotic Crop")
    assert (result.category, result.storable, result.source) == (UNCLASSIFIED_CATEGORY, False, "unclassified")
    assert classify_commodity("Mystery Dal").source == "heuristic"
    # Paddy / Onion Green gain their own canonical rows in migration 004; both are reviewed
    assert classify_commodity("Paddy(Common)").source == "reviewed"
    assert classify_commodity("Onion Green").source == "reviewed"
    assert classify_commodity("Paddy").category == "cereal"
    assert classify_commodity("Paddy").storable is True
    assert classify_commodity("Onion Green").category == "vegetable"
    assert classify_commodity("Onion Green").storable is False
    assert classify_commodity("Carrot").source == "reviewed"
    assert classify_commodity("Tomato").source == "target"
    for name in COMMODITIES_REQUIRING_REVIEW:
        assert PRODUCTION_COMMODITY_CLASSIFICATION[name] == (UNCLASSIFIED_CATEGORY, False)


@pytest.mark.parametrize(
    ("name", "category", "storable", "status"),
    [
        ("Groundnut Pods(Raw)", UNCLASSIFIED_CATEGORY, False, REVIEW_STATUS_REQUIRED),
        ("Field Pea", "pulse", True, REVIEW_STATUS_PROVISIONAL),
        ("Coconut", "cash_crop", True, REVIEW_STATUS_REVIEWED),
        ("Bop", UNCLASSIFIED_CATEGORY, False, REVIEW_STATUS_REQUIRED),
        ("Buttery", UNCLASSIFIED_CATEGORY, False, REVIEW_STATUS_REQUIRED),
        ("Guar", UNCLASSIFIED_CATEGORY, False, REVIEW_STATUS_REQUIRED),
        ("Season Leaves", UNCLASSIFIED_CATEGORY, False, REVIEW_STATUS_REQUIRED),
        ("Basil", "spice", True, REVIEW_STATUS_REVIEWED),
    ],
)
def test_approved_commodity_decisions(name: str, category: str, storable: bool, status: str) -> None:
    assert PRODUCTION_COMMODITY_CATALOG[name] == (category, storable, status)
    sql = {r[0]: r[1:] for r in _sql_reviewed_classification()}
    assert sql[name] == (category, storable, status)
    result = classify_commodity(name)
    assert (result.category, result.storable, result.source) == (category, storable, status)
    assert result.is_reviewed is (status == REVIEW_STATUS_REVIEWED)
    assert result.requires_review is (status != REVIEW_STATUS_REVIEWED)


def test_review_status_counts() -> None:
    counts = {s: sum(v == s for v in PRODUCTION_COMMODITY_REVIEW_STATUS.values()) for s in REVIEW_STATUSES}
    assert counts == {REVIEW_STATUS_REVIEWED: 184, REVIEW_STATUS_PROVISIONAL: 1, REVIEW_STATUS_REQUIRED: 5}
    assert COMMODITIES_REQUIRING_REVIEW == {"Bop", "Buttery", "Guar", "Groundnut Pods(Raw)", "Season Leaves"}
    assert set(PROVISIONAL_CLASSIFICATION) == {"Field Pea"}


def test_unclassified_is_never_marked_reviewed_or_provisional() -> None:
    for name, entry in PRODUCTION_COMMODITY_CATALOG.items():
        is_unclassified = entry.category == UNCLASSIFIED_CATEGORY
        assert is_unclassified is (entry.review_status == REVIEW_STATUS_REQUIRED), name
        if is_unclassified:
            assert entry.storable is False, name


def test_every_flagged_name_has_a_review_note() -> None:
    flagged = {n for n, s in PRODUCTION_COMMODITY_REVIEW_STATUS.items() if s != REVIEW_STATUS_REVIEWED}
    assert flagged == COMMODITIES_REQUIRING_REVIEW | set(PROVISIONAL_CLASSIFICATION)
    assert set(COMMODITY_REVIEW_NOTES) == flagged
    assert all(note.strip() for note in COMMODITY_REVIEW_NOTES.values())


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        ("groundnut pods (raw)", REVIEW_STATUS_REQUIRED),
        ("GROUNDNUT PODS(RAW)", REVIEW_STATUS_REQUIRED),
        ("field pea", REVIEW_STATUS_PROVISIONAL),
        ("FIELD PEA", REVIEW_STATUS_PROVISIONAL),
        ("season leaves", REVIEW_STATUS_REQUIRED),
        ("basil", REVIEW_STATUS_REVIEWED),
    ],
)
def test_review_status_survives_spelling_variants(raw: str, status: str) -> None:
    result = classify_commodity(raw)
    assert result.source == status
    assert lookup_catalog_classification(normalize_commodity_name(raw)).review_status == status


def test_groundnut_pods_raw_stays_separate_and_unclassified() -> None:
    for raw in ("Groundnut Pods(Raw)", "groundnut pods (raw)", "Groundnut pods(raw)"):
        canonical = normalize_commodity_name(raw)
        assert compact_commodity_key(canonical) == compact_commodity_key("Groundnut Pods(Raw)")
        assert lookup_catalog_classification(canonical).review_status == REVIEW_STATUS_REQUIRED
        assert classify_commodity_storability(raw) == (UNCLASSIFIED_CATEGORY, False)
    assert classify_commodity_storability("Groundnut") == ("oilseed", True)
