"""Migration safety on isolated SQLite: 190-name cycle, rollback, target version and start-up guard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import MetaData, text

from database.connection import DatabaseManager
from database.migrator import (
    MigrationError,
    PendingMigrationsError,
    apply_migrations,
    get_migration_status,
    rollback_migration,
)
from database.models import build_metadata
from etl.cleaning import classify_commodity_storability
from etl.commodity_catalog import PRODUCTION_COMMODITY_CLASSIFICATION, PRODUCTION_COMMODITY_REVIEW_STATUS
from etl.warehouse_loader import WarehouseLoader, _dump_raw_record
from tests.production_like import (
    CONFLICT_PIGEON_PEA_MODAL,
    PIGEON_PEA,
    RED_GRAM,
    classification_by_name,
    load_production_fixture,
    orphan_counts,
    seed_production_like,
    snapshot,
)

V002 = "002_migration_tracking_and_indexes"
V003 = "003_commodity_aliases_and_storability"
V004 = "004_split_paddy_onion_green_normalize_kerala"


def _legacy_sqlite(tmp_path: Path, skip_tables: frozenset[str] = frozenset()) -> DatabaseManager:
    db = DatabaseManager(database_url=f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    pre_meta = MetaData()
    for tbl in build_metadata(use_schemas=False).sorted_tables:
        if tbl.name != "schema_migrations" and tbl.name not in skip_tables:
            tbl.to_metadata(pre_meta)
    pre_meta.create_all(db.engine)
    return db


def _sqlite_tables(db: DatabaseManager) -> set[str]:
    with db.readonly_connection() as conn:
        return {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")).all()}


def test_migration_003_full_cycle_on_190_production_names(tmp_path: Path) -> None:
    db = _legacy_sqlite(tmp_path)
    ids = seed_production_like(db)
    before = snapshot(db)
    before_cls = classification_by_name(before)
    assert len(before_cls) == 190

    result = apply_migrations(db, target_version=V003)
    assert result["baselined"] == ["001_initial_schema"]
    assert result["applied_now"] == [V002, V003]
    after = snapshot(db)
    after_cls = classification_by_name(after)

    # Every surviving name has the reviewed classification, identical to the Python classifier.
    assert set(after_cls) == set(before_cls) - {RED_GRAM}
    for name, cls in after_cls.items():
        assert cls == PRODUCTION_COMMODITY_CLASSIFICATION[name] == classify_commodity_storability(name), name
    changed = {n for n in after_cls if after_cls[n] != before_cls[n]}
    assert "Potato" not in changed and "Tomato" not in changed

    # Count reconciliation: 27 Red Gram rows move except the one whose key Pigeon Pea already owned.
    red_rows = next(c["price_rows"] for c in load_production_fixture() if c["commodity_name"] == RED_GRAM)
    assert red_rows == 27
    assert len(after["fact_price_daily"]) == len(before["fact_price_daily"]) - 1
    assert len(after["fact_arrivals_daily"]) == len(before["fact_arrivals_daily"]) - 1
    pigeon_prices = [r for r in after["fact_price_daily"] if r[2] == ids[PIGEON_PEA]]
    assert len(pigeon_prices) == red_rows
    assert CONFLICT_PIGEON_PEA_MODAL in {r[7] for r in pigeon_prices}
    assert not [r for r in after["fact_price_daily"] if r[2] == ids[RED_GRAM]]
    untouched_ids = {ids[n] for n in ids if n not in (RED_GRAM, PIGEON_PEA)}
    assert [r for r in before["fact_price_daily"] if r[2] in untouched_ids] == [
        r for r in after["fact_price_daily"] if r[2] in untouched_ids
    ]
    assert set(orphan_counts(db).values()) == {0}
    with db.readonly_connection() as conn:
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
        moves = conn.execute(
            text("SELECT COUNT(*), SUM(canonical_row_preexisted) FROM migration_003_price_moves")
        ).one()
        arrival_moves = conn.execute(
            text("SELECT COUNT(*), SUM(canonical_row_preexisted) FROM migration_003_arrival_moves")
        ).one()
        backup_rows = conn.execute(text("SELECT COUNT(*) FROM migration_003_commodity_backup")).scalar_one()
        recorded_status = dict(
            conn.execute(text("SELECT commodity_name, review_status FROM migration_003_reviewed_classification")).all()
        )
    assert (moves[0], moves[1]) == (27, 1)
    assert (arrival_moves[0], arrival_moves[1]) == (6, 1)
    assert backup_rows == 190
    assert recorded_status == PRODUCTION_COMMODITY_REVIEW_STATUS
    assert after_cls["Basil"] == ("spice", True)
    assert after_cls["Groundnut Pods(Raw)"] == ("unclassified", False)
    assert after_cls["Field Pea"] == ("pulse", True)

    # Rollback restores original IDs, classifications and fact-row mappings exactly.
    rb = rollback_migration(db, "003")
    assert rb["rolled_back"] is True
    assert snapshot(db) == before
    assert not {t for t in _sqlite_tables(db) if t.startswith("migration_003_")}
    assert [p["version"] for p in get_migration_status(db)["pending"]] == [V003, V004]

    # Re-applying after rollback reproduces the same result (idempotent cycle).
    again = apply_migrations(db, target_version=V003)
    assert again["applied_now"] == [V003]
    assert snapshot(db) == after
    assert apply_migrations(db, target_version=V003)["applied_now"] == []


def test_migration_003_leaves_paddy_onion_green_and_chillies_unmerged(tmp_path: Path) -> None:
    db = _legacy_sqlite(tmp_path)
    seed_production_like(db)
    with db.transaction() as conn:
        conn.execute(
            text(
                "INSERT INTO dim_commodity (commodity_id, commodity_name, category, storable) VALUES "
                "(901, 'Paddy(Common)', 'other', 1), (902, 'Onion Green', 'other', 1), (903, 'Chillies', 'other', 1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO fact_price_daily (date_id, market_id, commodity_id, variety, grade, min_price, max_price, modal_price, outlier_flag, source_name) "
                "VALUES (20261001, 1, 901, 'Common', 'FAQ', 2000, 2400, 2200, 0, 'agmarknet_api'), "
                "(20261001, 1, 902, 'NA', 'FAQ', 1000, 1400, 1200, 0, 'agmarknet_api'), "
                "(20261001, 1, 903, 'NA', 'FAQ', 5000, 6000, 5500, 0, 'agmarknet_api')"
            )
        )
    apply_migrations(db)
    after = snapshot(db)
    cls = classification_by_name(after)
    # Names outside the reviewed list are not reclassified by 003 (no catch-all rule).
    assert cls["Paddy(Common)"] == ("other", True)
    assert cls["Onion Green"] == ("other", True)
    assert cls["Chillies"] == ("other", True)
    for cid in (901, 902, 903):
        assert len([r for r in after["fact_price_daily"] if r[2] == cid]) == 1


def test_target_version_applies_only_up_to_requested_migration(tmp_path: Path) -> None:
    db = DatabaseManager(database_url=f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    with pytest.raises(MigrationError, match="Unknown migration version"):
        apply_migrations(db, target_version="999")
    partial = apply_migrations(db, target_version="002")
    assert partial["applied_now"] == ["001_initial_schema", V002]
    assert partial["remaining_pending"] == [V003, V004]
    assert [p["version"] for p in get_migration_status(db)["pending"]] == [V003, V004]
    rest = apply_migrations(db, target_version=V003)
    assert rest["applied_now"] == [V003]
    assert rest["remaining_pending"] == [V004]


def test_missing_migration_files_are_reported(tmp_path: Path) -> None:
    db = DatabaseManager(database_url=f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    apply_migrations(db)
    with db.transaction() as conn:
        conn.execute(
            text("INSERT INTO schema_migrations (version, checksum, description) VALUES ('004_removed', 'x', 'gone')")
        )
    assert get_migration_status(db)["missing_files"] == ["004_removed"]
    assert apply_migrations(db)["missing_files"] == ["004_removed"]


def test_baseline_refused_when_required_objects_are_missing(tmp_path: Path) -> None:
    db = _legacy_sqlite(tmp_path, skip_tables=frozenset({"briefings"}))
    status = get_migration_status(db)
    assert status["legacy_untracked_baseline"] is True
    assert "table analytics.briefings" in status["baseline_missing_objects"]
    with pytest.raises(MigrationError, match="Refusing to baseline"):
        apply_migrations(db)
    assert "schema_migrations" not in _sqlite_tables(db)


def test_rollback_guards(tmp_path: Path) -> None:
    db = DatabaseManager(database_url=f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    with pytest.raises(MigrationError, match="not applied"):
        rollback_migration(db, "003")
    apply_migrations(db)
    with pytest.raises(MigrationError, match="latest"):
        rollback_migration(db, "002")
    with pytest.raises(MigrationError, match="latest"):
        rollback_migration(db, "003")
    with pytest.raises(MigrationError, match="cannot be rolled back"):
        rollback_migration(db, "001")
    assert rollback_migration(db, "004", dry_run=True)["rolled_back"] is False


def test_invalid_timeouts_are_rejected(tmp_path: Path) -> None:
    db = DatabaseManager(database_url=f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    with pytest.raises(MigrationError, match="lock_timeout"):
        apply_migrations(db, lock_timeout="5s'; DROP TABLE dim_commodity; --")
    with pytest.raises(MigrationError, match="statement_timeout"):
        apply_migrations(db, statement_timeout="forever")


def _fake_pg_manager(monkeypatch: pytest.MonkeyPatch) -> DatabaseManager:
    monkeypatch.setattr("database.connection.create_engine", lambda url, **kw: "FAKE_ENGINE")
    db = DatabaseManager(database_url="postgresql://user:pass@localhost:5432/synthetic_test")
    assert db.backend == "postgresql"
    return db


def _status(pending: list[dict[str, Any]], mismatched: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"pending": pending, "mismatched": mismatched or [], "missing_files": [], "applied": []}


def test_initialize_schema_never_migrates_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _fake_pg_manager(monkeypatch)

    def _no_apply(*_a: Any, **_k: Any) -> None:
        raise AssertionError("initialize_schema must not apply PostgreSQL migrations")

    monkeypatch.setattr("database.migrator.apply_migrations", _no_apply)
    monkeypatch.setattr(
        "database.migrator.get_migration_status",
        lambda _db, _dir=None: _status([{"version": V003, "action": "apply"}]),
    )
    with pytest.raises(PendingMigrationsError, match=r"scripts/migrate\.py --apply"):
        db.initialize_schema()

    monkeypatch.setattr(
        "database.migrator.get_migration_status",
        lambda _db, _dir=None: _status([], [{"version": V002, "recorded_checksum": "a", "file_checksum": "b"}]),
    )
    with pytest.raises(PendingMigrationsError, match="checksum mismatch"):
        db.initialize_schema()

    monkeypatch.setattr("database.migrator.get_migration_status", lambda _db, _dir=None: _status([]))
    db.initialize_schema()


def test_initialize_schema_still_builds_demo_sqlite(temp_db: DatabaseManager) -> None:
    status = get_migration_status(temp_db)
    assert status["pending"] == [] and status["mismatched"] == []
    assert len(status["applied"]) == 4


def test_loader_never_overwrites_existing_rows_with_unreviewed_classification(temp_db: DatabaseManager) -> None:
    with temp_db.transaction() as conn:
        conn.execute(
            text(
                "INSERT INTO dim_commodity (commodity_name, category, storable) VALUES "
                "('Mystery Root', 'spice', 1), ('Carrot', 'other', 1)"
            )
        )
    rows = [
        {
            "state": "Kerala",
            "district": "Ernakulam",
            "market": "Aluva",
            "commodity": name,
            "variety": "NA",
            "grade": "FAQ",
            "arrival_date": "2026-11-01",
            "min_price": 1000.0,
            "max_price": 1500.0,
            "modal_price": 1200.0,
        }
        for name in ("Mystery Root", "Carrot", "Brand New Tuber")
    ]
    res = WarehouseLoader(temp_db).load_prices(rows, source_name="test_fixture")
    assert res["commodities_requiring_review"] == ["Brand New Tuber", "Mystery Root"]
    with temp_db.readonly_connection() as conn:
        got = {
            r[0]: (r[1], bool(r[2]))
            for r in conn.execute(text("SELECT commodity_name, category, storable FROM dim_commodity")).all()
        }
    assert got["Mystery Root"] == ("spice", True)
    assert got["Carrot"] == ("vegetable", False)
    assert got["Brand New Tuber"] == ("unclassified", False)


def test_loader_treats_provisional_and_review_required_names_as_unreviewed(temp_db: DatabaseManager) -> None:
    with temp_db.transaction() as conn:
        conn.execute(
            text(
                "INSERT INTO dim_commodity (commodity_name, category, storable) VALUES "
                "('Field Pea', 'other', 1), ('Groundnut Pods(Raw)', 'oilseed', 0), ('Basil', 'vegetable', 0)"
            )
        )
    rows = [
        {
            "state": "Gujarat",
            "district": "Rajkot",
            "market": "Rajkot",
            "commodity": name,
            "variety": "Other",
            "grade": "FAQ",
            "arrival_date": "2026-11-01",
            "min_price": 1000.0,
            "max_price": 1500.0,
            "modal_price": 1200.0,
        }
        for name in ("Field Pea", "Groundnut Pods(Raw)", "Basil", "Bop")
    ]
    res = WarehouseLoader(temp_db).load_prices(rows, source_name="test_fixture")
    assert res["commodities_requiring_review"] == ["Bop", "Field Pea", "Groundnut Pods(Raw)"]
    with temp_db.readonly_connection() as conn:
        got = {
            r[0]: (r[1], bool(r[2]))
            for r in conn.execute(text("SELECT commodity_name, category, storable FROM dim_commodity")).all()
        }
    assert got["Field Pea"] == ("other", True)
    assert got["Groundnut Pods(Raw)"] == ("oilseed", False)
    assert got["Basil"] == ("spice", True)
    assert got["Bop"] == ("unclassified", False)


def test_loader_inserts_new_provisional_name_with_its_provisional_classification(temp_db: DatabaseManager) -> None:
    with temp_db.transaction() as conn:
        conn.execute(text("DELETE FROM dim_commodity WHERE commodity_name = 'Field Pea'"))
    row = {
        "state": "Madhya Pradesh",
        "district": "Sagar",
        "market": "Sagar",
        "commodity": "Field Pea",
        "variety": "Field Pea",
        "grade": "FAQ",
        "arrival_date": "2026-11-01",
        "min_price": 4000.0,
        "max_price": 4600.0,
        "modal_price": 4500.0,
    }
    res = WarehouseLoader(temp_db).load_prices([row], source_name="test_fixture")
    assert res["commodities_requiring_review"] == ["Field Pea"]
    with temp_db.readonly_connection() as conn:
        got = conn.execute(
            text("SELECT category, storable FROM dim_commodity WHERE commodity_name = 'Field Pea'")
        ).one()
    assert (got[0], bool(got[1])) == ("pulse", True)


def test_reject_payloads_are_strict_json() -> None:
    payload = json.loads(_dump_raw_record({"price": float("nan"), "x": [float("inf"), "a\x00b"], "hi": "हरी मटर"}))
    assert payload == {"price": None, "x": [None, "ab"], "hi": "हरी मटर"}
