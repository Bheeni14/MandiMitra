"""Real PostgreSQL validation against a local, throwaway server only.

Runs only when MANDIMITRA_TEST_PG_ADMIN_URL points at a loopback PostgreSQL server, e.g.
``postgresql://mm_admin@127.0.0.1:55432/postgres``. Every test creates its own database owned by a
non-superuser role (closer to the Supabase ``postgres`` role) and force-drops it afterwards.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.errors
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from config import DATABASE_DIR, get_settings
from database.connection import DatabaseManager
from database.migrator import (
    MIGRATION_ADVISORY_LOCK_KEY,
    MigrationError,
    PendingMigrationsError,
    apply_migrations,
    discover_migrations,
    get_migration_status,
    rollback_migration,
)
from database.seed import seed_reference_dimensions
from etl.cleaning import classify_commodity_storability
from etl.commodity_catalog import PRODUCTION_COMMODITY_CLASSIFICATION, PRODUCTION_COMMODITY_REVIEW_STATUS
from etl.warehouse_loader import WarehouseLoader
from tests.production_like import (
    CONFLICT_PIGEON_PEA_MODAL,
    PIGEON_PEA,
    RED_GRAM,
    classification_by_name,
    orphan_counts,
    seed_production_like,
    snapshot,
)
from tests.test_phase1_readiness import _make_price_rows

pytestmark = pytest.mark.postgres_disposable

ADMIN_URL_ENV = "MANDIMITRA_TEST_PG_ADMIN_URL"
MIGRATOR_ROLE = "mm_migrator"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
V002 = "002_migration_tracking_and_indexes"
V003 = "003_commodity_aliases_and_storability"
V004 = "004_split_paddy_onion_green_normalize_kerala"


def _admin_url() -> Any:
    raw = os.environ.get(ADMIN_URL_ENV, "").strip()
    if not raw:
        pytest.skip(f"{ADMIN_URL_ENV} not set; disposable PostgreSQL tests skipped")
    url = make_url(raw)
    if url.host not in _LOOPBACK:
        pytest.fail(f"{ADMIN_URL_ENV} must point at a loopback host, got {url.host!r}")
    configured = get_settings().database_url
    if configured:
        prod = make_url(configured)
        if prod.host == url.host and (prod.port or 5432) == (url.port or 5432):
            pytest.fail("Disposable PostgreSQL URL matches the configured DATABASE_URL server; refusing.")
    return url


def _psycopg2_dsn(url: Any, database: str, user: str | None = None) -> str:
    return f"host={url.host} port={url.port or 5432} dbname={database} user={user or url.username}"


@pytest.fixture()
def pg_db() -> Iterator[DatabaseManager]:
    url = _admin_url()
    name = f"mm_disposable_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(_psycopg2_dsn(url, url.database or "postgres"))
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (MIGRATOR_ROLE,))
        if cur.fetchone() is None:
            cur.execute(f"CREATE ROLE {MIGRATOR_ROLE} LOGIN NOSUPERUSER NOCREATEROLE")
        cur.execute(f'CREATE DATABASE "{name}" OWNER {MIGRATOR_ROLE} ENCODING \'UTF8\' TEMPLATE template0')
    db = DatabaseManager(
        database_url=f"postgresql+psycopg2://{MIGRATOR_ROLE}@{url.host}:{url.port or 5432}/{name}"
    )
    db.dsn = _psycopg2_dsn(url, name, MIGRATOR_ROLE)  # type: ignore[attr-defined]
    try:
        yield db
    finally:
        db.engine.dispose()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def _run_raw(db: DatabaseManager, sql: str) -> None:
    conn = psycopg2.connect(db.dsn)  # type: ignore[attr-defined]
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


def _scalar(db: DatabaseManager, sql: str, params: dict[str, Any] | None = None) -> Any:
    with db.readonly_connection() as conn:
        return conn.execute(text(sql), params or {}).scalar()


def _create_legacy_schema(db: DatabaseManager) -> None:
    """Production state before tracking: 001 applied by hand, no operational.schema_migrations."""
    _run_raw(db, (DATABASE_DIR / "migrations" / "001_initial_schema.sql").read_text(encoding="utf-8"))


def test_pg_fresh_database_target_version_startup_guard_and_idempotency(pg_db: DatabaseManager) -> None:
    with pytest.raises(PendingMigrationsError, match=r"scripts/migrate\.py"):
        pg_db.initialize_schema()
    assert _scalar(pg_db, "SELECT to_regclass('operational.schema_migrations') IS NULL") is True
    assert _scalar(pg_db, "SELECT to_regclass('warehouse.dim_commodity') IS NULL") is True

    first = apply_migrations(pg_db, target_version="002")
    assert first["applied_now"] == ["001_initial_schema", V002]
    with pytest.raises(PendingMigrationsError, match=V003):
        pg_db.initialize_schema()

    second = apply_migrations(pg_db, target_version=V003)
    assert second["applied_now"] == [V003]
    with pytest.raises(PendingMigrationsError, match=V004):
        pg_db.initialize_schema()

    with pg_db.readonly_connection() as conn:
        indexes = {r[0] for r in conn.execute(text("SELECT indexname FROM pg_indexes")).all()}
        targets = conn.execute(
            text("SELECT commodity_name, category, storable FROM warehouse.dim_commodity ORDER BY commodity_name")
        ).all()
    assert "idx_market_signals_lookup" in indexes
    assert not {"idx_dim_date_full_date", "idx_fact_price_date_id"} & indexes
    assert [tuple(r) for r in targets] == [
        ("Chickpea", "pulse", True),
        ("Green Gram (Moong)", "pulse", True),
        ("Pigeon Pea (Tur)", "pulse", True),
    ]

    rollback_migration(pg_db, "003")
    assert _scalar(pg_db, "SELECT COUNT(*) FROM warehouse.dim_commodity") == 0
    assert _scalar(pg_db, "SELECT to_regclass('operational.migration_003_commodity_backup') IS NULL") is True
    assert apply_migrations(pg_db, target_version=V003)["applied_now"] == [V003]

    # 004 on an empty warehouse only adds the two canonical dimension rows
    assert apply_migrations(pg_db)["applied_now"] == [V004]
    pg_db.initialize_schema()
    assert apply_migrations(pg_db)["applied_now"] == []
    with pg_db.readonly_connection() as conn:
        recorded = dict(conn.execute(text("SELECT version, checksum FROM operational.schema_migrations")).all())
        names = {r[0] for r in conn.execute(text("SELECT commodity_name FROM warehouse.dim_commodity")).all()}
    assert recorded == {m.version: m.checksum for m in discover_migrations()}
    assert {"Paddy", "Onion Green"} <= names
    assert _scalar(pg_db, "SELECT COUNT(*) FROM operational.migration_004_price_moves") == 0


def test_pg_legacy_baseline_190_names_rollback_and_reapply(pg_db: DatabaseManager, tmp_path: Path) -> None:
    _create_legacy_schema(pg_db)
    ids = seed_production_like(pg_db)
    before = snapshot(pg_db)
    before_cls = classification_by_name(before)
    assert len(before_cls) == 190

    status = get_migration_status(pg_db)
    assert status["legacy_untracked_baseline"] is True
    assert status["baseline_missing_objects"] == []
    assert [p["action"] for p in status["pending"]] == ["baseline", "apply", "apply", "apply"]

    result = apply_migrations(pg_db, target_version=V003)
    assert result["baselined"] == ["001_initial_schema"]
    assert result["applied_now"] == [V002, V003]
    after = snapshot(pg_db)
    after_cls = classification_by_name(after)

    assert set(after_cls) == set(before_cls) - {RED_GRAM}
    mismatches = {
        n: c for n, c in after_cls.items()
        if not (c == PRODUCTION_COMMODITY_CLASSIFICATION[n] == classify_commodity_storability(n))
    }
    assert mismatches == {}
    transitions = Counter(
        f"{before_cls[n][0]}/{before_cls[n][1]} -> {after_cls[n][0]}/{after_cls[n][1]}" for n in after_cls
    )
    (tmp_path / "classification_transitions.json").write_text(json.dumps(transitions, indent=1))
    print("\n190-name classification transitions (before -> after):")
    for key, count in sorted(transitions.items()):
        print(f"  {count:4d}  {key}")

    assert len(after["fact_price_daily"]) == len(before["fact_price_daily"]) - 1
    assert len(after["fact_arrivals_daily"]) == len(before["fact_arrivals_daily"]) - 1
    pigeon = [r for r in after["fact_price_daily"] if r[2] == ids[PIGEON_PEA]]
    assert len(pigeon) == 27 and CONFLICT_PIGEON_PEA_MODAL in {r[7] for r in pigeon}
    assert set(orphan_counts(pg_db).values()) == {0}
    assert _scalar(pg_db, "SELECT COUNT(*) FROM operational.migration_003_commodity_backup") == 190
    assert _scalar(pg_db, "SELECT COUNT(*) FROM operational.migration_003_price_moves") == 27
    assert _scalar(
        pg_db, "SELECT COUNT(*) FROM operational.migration_003_price_moves WHERE canonical_row_preexisted"
    ) == 1
    assert _scalar(pg_db, "SELECT COUNT(*) FROM operational.migration_003_arrival_moves") == 6
    with pg_db.readonly_connection() as conn:
        recorded_status = dict(
            conn.execute(
                text("SELECT commodity_name, review_status FROM operational.migration_003_reviewed_classification")
            ).all()
        )
    assert recorded_status == PRODUCTION_COMMODITY_REVIEW_STATUS
    assert after_cls["Basil"] == ("spice", True)
    assert after_cls["Groundnut Pods(Raw)"] == ("unclassified", False)
    assert after_cls["Field Pea"] == ("pulse", True)
    with pytest.raises(Exception, match="check"):
        _run_raw(
            pg_db,
            "INSERT INTO operational.migration_003_reviewed_classification "
            "VALUES ('Probe', 'spice', TRUE, 'review_required')",
        )
    # 004 finds none of its evidence rows here: a no-op on facts whose rollback restores the 003 state exactly
    assert apply_migrations(pg_db)["applied_now"] == [V004]
    pg_db.initialize_schema()
    assert _scalar(pg_db, "SELECT COUNT(*) FROM operational.migration_004_price_moves") == 0
    rollback_migration(pg_db, V004)
    assert snapshot(pg_db) == after

    rollback_migration(pg_db, "003")
    assert snapshot(pg_db) == before
    assert set(orphan_counts(pg_db).values()) == {0}
    assert _scalar(pg_db, "SELECT COUNT(*) FROM pg_tables WHERE tablename LIKE 'migration_003_%'") == 0
    assert [p["version"] for p in get_migration_status(pg_db)["pending"]] == [V003, V004]

    assert apply_migrations(pg_db, target_version=V003)["applied_now"] == [V003]
    assert snapshot(pg_db) == after


def test_pg_baseline_refused_when_required_view_is_missing(pg_db: DatabaseManager) -> None:
    _create_legacy_schema(pg_db)
    _run_raw(pg_db, "DROP VIEW analytics.v_q7_production_price_corr")
    status = get_migration_status(pg_db)
    assert "view analytics.v_q7_production_price_corr" in status["baseline_missing_objects"]
    with pytest.raises(MigrationError, match="Refusing to baseline"):
        apply_migrations(pg_db)
    assert _scalar(pg_db, "SELECT to_regclass('operational.schema_migrations') IS NULL") is True


def test_pg_advisory_lock_timeouts_and_atomicity(pg_db: DatabaseManager, tmp_path: Path) -> None:
    holder = psycopg2.connect(pg_db.dsn)  # type: ignore[attr-defined]
    holder.autocommit = True
    try:
        with holder.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_ADVISORY_LOCK_KEY,))
        t0 = time.perf_counter()
        with pytest.raises(psycopg2.errors.LockNotAvailable):
            apply_migrations(pg_db, lock_timeout="300ms")
        assert time.perf_counter() - t0 < 5
        assert _scalar(pg_db, "SELECT to_regclass('warehouse.dim_commodity') IS NULL") is True
    finally:
        holder.close()

    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    for mig in discover_migrations():
        shutil.copy(mig.path, mig_dir / mig.path.name)
    (mig_dir / "999_slow_probe.sql").write_text(
        "CREATE TABLE operational.atomic_probe (x INT);\nSELECT pg_sleep(3);\n", encoding="utf-8"
    )
    applied = apply_migrations(pg_db, migrations_dir=mig_dir, target_version=V004)
    assert applied["applied_now"][-1] == V004
    with pytest.raises(psycopg2.errors.QueryCanceled):
        apply_migrations(pg_db, migrations_dir=mig_dir, statement_timeout="500ms")
    assert _scalar(pg_db, "SELECT to_regclass('operational.atomic_probe') IS NULL") is True
    assert [p["version"] for p in get_migration_status(pg_db, mig_dir)["pending"]] == ["999_slow_probe"]

    (mig_dir / "999_slow_probe.sql").unlink()
    _run_raw(
        pg_db,
        "INSERT INTO operational.schema_migrations (version, checksum, description) VALUES ('999_slow_probe', 'x', 'gone')",
    )
    assert get_migration_status(pg_db, mig_dir)["missing_files"] == ["999_slow_probe"]


def _loader_ready(pg_db: DatabaseManager) -> WarehouseLoader:
    apply_migrations(pg_db)
    seed_reference_dimensions(pg_db)
    return WarehouseLoader(pg_db)


def test_pg_bulk_loader_jsonb_conflicts_and_transaction_rollback(pg_db: DatabaseManager, monkeypatch: pytest.MonkeyPatch) -> None:
    loader = _loader_ready(pg_db)
    rows = _make_price_rows(5000)
    bad = [
        {**rows[0], "market": "Mandi 99", "modal_price": float("nan"), "variety": "हरी मटर"},
        {**rows[1], "market": "Mandi 98", "max_price": 1.0, "nested": {"note": "max<min", "vals": [1, float("inf")]}},
    ]
    t0 = time.perf_counter()
    res = loader.load_prices(rows + bad, source_name="pg_disposable_test")
    elapsed = time.perf_counter() - t0
    print(f"\n5,000-row PostgreSQL price load: {elapsed:.2f}s")
    assert res["loaded_count"] == 5000 and res["rejected_count"] == 2 and res["reconciled"] is True
    assert res["commodities_requiring_review"] == []
    assert _scalar(
        pg_db,
        "SELECT COUNT(*) FROM warehouse.map_commodity_crop m "
        "JOIN warehouse.dim_commodity c ON c.commodity_id = m.commodity_id "
        "JOIN warehouse.dim_crop cr ON cr.crop_id = m.crop_id "
        "WHERE c.commodity_name = 'Paddy' AND cr.crop_name = 'Rice'",
    ) == 1

    with pg_db.readonly_connection() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM warehouse.fact_price_daily")).scalar() == 5000
        rejects = conn.execute(
            text(
                "SELECT jsonb_typeof(raw_record), raw_record->>'variety', raw_record->'modal_price', "
                "raw_record #> '{nested,vals}' FROM operational.dq_rejects ORDER BY reject_id"
            )
        ).all()
        stg = conn.execute(text("SELECT COUNT(*) FROM staging.stg_agmarknet_prices")).scalar()
    assert stg == 5000  # staging holds validated rows; rejects go to dq_rejects
    assert len(rejects) == 2
    assert {r[0] for r in rejects} == {"object"}
    assert any(r[1] == "हरी मटर" and r[2] is None for r in rejects)
    assert any(r[3] == [1, None] for r in rejects)

    # Conflict handling: re-loading the same keys updates in place without duplicating rows.
    updated = [{**r, "modal_price": r["modal_price"] + 1} for r in rows]
    assert loader.load_prices(updated, source_name="pg_disposable_test")["loaded_count"] == 5000
    with pg_db.readonly_connection() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM warehouse.fact_price_daily")).scalar() == 5000
        assert conn.execute(
            text("SELECT COUNT(*) FROM warehouse.fact_price_daily WHERE modal_price = 2101")
        ).scalar() > 0

    arrivals = [
        {"state": "State0", "district": "District0", "market": "Mandi0", "commodity": "Red Gram/Arhar/Tur(Whole)",
         "arrival_date": f"2026-02-{(i % 28) + 1:02d}", "quantity_tonnes": 100.0 + i}
        for i in range(28)
    ]
    assert loader.load_arrivals(arrivals, source_name="pg_disposable_test")["loaded_count"] == 28
    apy = [
        {"crop_year": 2000 + (i // 20), "season": "Kharif", "state": f"State{i % 4}", "district": f"Dist{i % 5}",
         "crop_name": "Arhar/Tur", "crop_type": "Pulses", "area_ha": 1000.0, "production_t": 1500.0, "yield_t_ha": 1.5}
        for i in range(1100)
    ]
    assert loader.load_apy_production(apy, source_name="pg_disposable_test")["loaded_count"] == 1100

    # Reviewed rows (including Paddy) are upserted back to the catalog classification.
    _run_raw(pg_db, "UPDATE warehouse.dim_commodity SET category = 'spice', storable = TRUE WHERE commodity_name = 'Paddy'")
    paddy_rows = [r for r in rows if r["commodity"] == "Paddy"][:10]
    assert paddy_rows, "fixture rows must include Paddy so the reviewed upsert is exercised"
    loader.load_prices(paddy_rows, source_name="pg_disposable_test")
    assert _scalar(pg_db, "SELECT category FROM warehouse.dim_commodity WHERE commodity_name = 'Paddy'") == "cereal"

    # A failure while inserting facts rolls back staging, dimensions and facts of that batch.
    counts_before = {
        t: _scalar(pg_db, f"SELECT COUNT(*) FROM {t}")
        for t in ("staging.stg_agmarknet_prices", "warehouse.fact_price_daily", "warehouse.dim_market", "operational.dq_rejects")
    }
    original = WarehouseLoader._pg_execute_values

    def _fail_on_facts(self: WarehouseLoader, conn: Any, sql: str, *args: Any, **kwargs: Any) -> None:
        if "fact_price_daily" in sql:
            raise RuntimeError("simulated failure during fact insert")
        original(self, conn, sql, *args, **kwargs)

    monkeypatch.setattr(WarehouseLoader, "_pg_execute_values", _fail_on_facts)
    new_rows = [{**r, "market": "Brand New Mandi", "district": "New District"} for r in rows[:50]]
    with pytest.raises(RuntimeError, match="simulated failure"):
        loader.load_prices(new_rows + bad[:1], source_name="pg_disposable_test")
    for table, count in counts_before.items():
        assert _scalar(pg_db, f"SELECT COUNT(*) FROM {table}") == count, table
    assert _scalar(
        pg_db, "SELECT status FROM operational.pipeline_run_log ORDER BY run_id DESC LIMIT 1"
    ) == "failed"



def test_pg_migration_004_apply_rollback_reapply_and_abort(pg_db: DatabaseManager) -> None:
    """Exact Paddy / Onion Green split and Keralam normalisation on real PostgreSQL, then exact rollback."""
    from tests.test_phase2_p0 import _commodity_counts, _seed_pre_004_warehouse, _warehouse_snapshot

    apply_migrations(pg_db, target_version=V003)
    _seed_pre_004_warehouse(pg_db)
    before = _warehouse_snapshot(pg_db)
    assert _commodity_counts(pg_db) == {"Rice": 154, "Onion": 63, "Tomato": 1}
    modal_before = _scalar(pg_db, "SELECT SUM(modal_price) FROM warehouse.fact_price_daily")

    apply_migrations(pg_db)
    assert _commodity_counts(pg_db) == {"Rice": 1, "Paddy": 153, "Onion": 1, "Onion Green": 62, "Tomato": 1}
    assert _scalar(pg_db, "SELECT SUM(modal_price) FROM warehouse.fact_price_daily") == modal_before
    assert _scalar(pg_db, "SELECT COUNT(*) FROM warehouse.fact_price_daily") == len(before["facts"])
    assert _scalar(pg_db, "SELECT COUNT(*) FROM warehouse.dim_market WHERE state = 'Keralam'") == 0
    assert _scalar(pg_db, "SELECT COUNT(*) FROM operational.migration_004_price_moves") == 215
    assert _scalar(pg_db, "SELECT COUNT(*) FROM operational.migration_004_assertions") == 18
    assert _scalar(
        pg_db,
        "SELECT COUNT(*) FROM warehouse.map_commodity_crop m "
        "JOIN warehouse.dim_commodity c ON c.commodity_id = m.commodity_id "
        "JOIN warehouse.dim_crop cr ON cr.crop_id = m.crop_id "
        "WHERE c.commodity_name = 'Paddy' AND cr.crop_name = 'Rice'",
    ) == 1
    assert _scalar(pg_db, "SELECT COUNT(*) FROM warehouse.dim_commodity WHERE commodity_name IN ('Paddy', 'Rice')") == 2

    rollback_migration(pg_db, V004)
    assert _warehouse_snapshot(pg_db) == before
    assert _scalar(pg_db, "SELECT COUNT(*) FROM pg_tables WHERE tablename LIKE 'migration_004%'") == 0
    assert _scalar(
        pg_db,
        "SELECT COUNT(*) FROM warehouse.map_commodity_crop m "
        "JOIN warehouse.dim_commodity c ON c.commodity_id = m.commodity_id "
        "JOIN warehouse.dim_crop cr ON cr.crop_id = m.crop_id "
        "WHERE c.commodity_name = 'Paddy' AND cr.crop_name = 'Rice'",
    ) == 0

    apply_migrations(pg_db)
    assert _commodity_counts(pg_db) == {"Rice": 1, "Paddy": 153, "Onion": 1, "Onion Green": 62, "Tomato": 1}
    assert _scalar(
        pg_db,
        "SELECT COUNT(*) FROM warehouse.map_commodity_crop m "
        "JOIN warehouse.dim_commodity c ON c.commodity_id = m.commodity_id "
        "JOIN warehouse.dim_crop cr ON cr.crop_id = m.crop_id "
        "WHERE c.commodity_name = 'Paddy' AND cr.crop_name = 'Rice'",
    ) == 1

    # Tampered evidence: roll back, alter one evidence-backed price, and the re-apply must abort atomically.
    rollback_migration(pg_db, V004)
    _run_raw(
        pg_db,
        "UPDATE warehouse.fact_price_daily p SET modal_price = p.min_price FROM ("
        " SELECT f.date_id, f.market_id, f.commodity_id, f.variety, f.grade FROM warehouse.fact_price_daily f"
        " JOIN warehouse.dim_commodity c USING (commodity_id) WHERE c.commodity_name = 'Rice'"
        " AND f.variety <> 'Matta' AND f.modal_price <> f.min_price LIMIT 1) x"
        " WHERE p.date_id = x.date_id AND p.market_id = x.market_id AND p.commodity_id = x.commodity_id"
        " AND p.variety = x.variety AND p.grade = x.grade",
    )
    tampered = _warehouse_snapshot(pg_db)
    with pytest.raises(psycopg2.errors.CheckViolation, match="evidence_matches_all_or_nothing"):
        apply_migrations(pg_db)
    assert _warehouse_snapshot(pg_db) == tampered
    assert _scalar(pg_db, "SELECT COUNT(*) FROM pg_tables WHERE tablename LIKE 'migration_004%'") == 0
    assert V004 in [p["version"] for p in get_migration_status(pg_db)["pending"]]
