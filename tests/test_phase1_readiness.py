"""Isolated Phase 1 readiness tests: bulk inserts, migrations, storability, and PG mode safeguards."""

from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, text

from backend.dependencies import get_app_settings, get_db
from backend.main import create_app
from config import DATABASE_DIR, Settings
from database.connection import DatabaseManager
from database.migrator import apply_migrations, discover_migrations, get_migration_status
from database.models import build_metadata
from etl.warehouse_loader import WarehouseLoader


class _FakePsycopg2Cursor:
    """Simulates a psycopg2 cursor with mogrify/execute without opening any network connection."""

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state
        self.connection = type("_ConnEnc", (), {"encoding": "UTF8"})()

    def __enter__(self) -> "_FakePsycopg2Cursor":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def mogrify(self, query: str, vars_tuple: tuple[Any, ...]) -> bytes:
        placeholders = query.count("%s")
        assert placeholders == len(vars_tuple)
        rendered = []
        for val in vars_tuple:
            if val is None:
                rendered.append("NULL")
            elif isinstance(val, bool):
                rendered.append("TRUE" if val else "FALSE")
            elif isinstance(val, (int, float)):
                rendered.append(str(val))
            else:
                escaped = str(val).replace("'", "''")
                rendered.append(f"'{escaped}'")
        out = query
        for rep in rendered:
            out = out.replace("%s", rep, 1)
        return out.encode("utf-8")

    def execute(self, sql: Any, params: Any = None) -> None:
        sql_str = sql.decode("utf-8") if isinstance(sql, (bytes, bytearray)) else str(sql)
        self._state["executed"].append(sql_str)


class _FakeDriverConn:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    def cursor(self) -> _FakePsycopg2Cursor:
        return _FakePsycopg2Cursor(self._state)


class _FakeSAConnection:
    """Fake SQLAlchemy Connection mimicking a PostgreSQL transaction for execute_values testing."""

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state
        self.connection = type("_DBAPIWrap", (), {"driver_connection": _FakeDriverConn(state)})()

    def execute(self, stmt: Any, params: Any = None) -> Any:
        sql_str = str(stmt)
        self._state["sa_queries"].append(sql_str)
        if "SELECT commodity_id, commodity_name" in sql_str:
            rows = [(idx + 1, name) for idx, name in enumerate(sorted(self._state["commodities"]))]
            return type("_Res", (), {"all": lambda _s: rows})()
        if "SELECT market_id, market_name, district, state" in sql_str:
            rows = [(idx + 1, *mkt) for idx, mkt in enumerate(sorted(self._state["markets"]))]
            return type("_Res", (), {"all": lambda _s: rows})()
        if "SELECT crop_id, crop_name" in sql_str:
            rows = [(idx + 1, cname) for idx, cname in enumerate(sorted(self._state["crops"]))]
            return type("_Res", (), {"all": lambda _s: rows})()
        return type("_Res", (), {"all": lambda _s: []})()


def _make_price_rows(count: int, duplicate_first: bool = False) -> list[dict[str, Any]]:
    commodities = [
        "Tomato",
        "Onion",
        "Wheat",
        "Red Gram/Arhar/Tur(Whole)",
        "Bhindi(Ladies Finger)",
        "Dry Chillies",
        "Carrot",
        "Paddy",
        "Bengal Gram(Whole)",
        "Green Gram (Moong)(Whole)",
    ]
    base_day = date(2026, 1, 1)
    rows: list[dict[str, Any]] = []
    for idx in range(count):
        day = (base_day + timedelta(days=idx % 25)).isoformat()
        mkt_idx = (idx // 25) % 10
        comm_bucket = idx // 250
        comm = commodities[comm_bucket % len(commodities)]
        rows.append(
            {
                "state": f"State{mkt_idx % 4}",
                "district": f"District{mkt_idx % 5}",
                "market": f"Mandi{mkt_idx}",
                "commodity": comm,
                "variety": f"Var{comm_bucket}",
                "grade": "FAQ",
                "arrival_date": day,
                "min_price": 1800.0 + (idx % 10),
                "max_price": 2400.0 + (idx % 10),
                "modal_price": 2100.0 + (idx % 10),
            }
        )
    if duplicate_first and rows:
        dup = dict(rows[0])
        dup["modal_price"] = 2155.0
        rows.append(dup)
    return rows


def test_pg_engine_configures_batch_executemany_without_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_create_engine(url: str, **kwargs: Any) -> str:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return "FAKE_ENGINE"

    monkeypatch.setattr("database.connection.create_engine", _fake_create_engine)
    db = DatabaseManager(database_url="postgresql://user:pass@localhost:5432/synthetic_test")
    assert db.backend == "postgresql"
    assert db.engine == "FAKE_ENGINE"
    assert captured["kwargs"]["executemany_mode"] == "values_plus_batch"
    assert captured["kwargs"]["executemany_batch_page_size"] == 1000
    assert captured["kwargs"]["insertmanyvalues_page_size"] == 1000
    assert captured["kwargs"]["pool_recycle"] == 1800


def test_pg_loader_uses_batched_execute_values_and_dedups_pks() -> None:
    state: dict[str, Any] = {
        "executed": [],
        "sa_queries": [],
        "commodities": {"Tomato", "Onion", "Wheat", "Pigeon Pea (Tur)", "Bhindi(Ladies Finger)", "Dry Chillies", "Carrot", "Paddy", "Chickpea", "Green Gram (Moong)"},
        "markets": {(f"Mandi{i}", f"District{i % 5}", f"State{i % 4}") for i in range(20)},
        "crops": {"Tomato", "Wheat", "Arhar/Tur"},
    }
    fake_db = type("_FakePGDb", (), {})()
    fake_db.backend = "postgresql"
    fake_db.is_demo = False
    fake_db.table_ref = lambda schema, table: f"{schema}.{table}"

    @contextmanager
    def _fake_tx():
        yield _FakeSAConnection(state)

    fake_db.transaction = _fake_tx
    loader = WarehouseLoader(db=fake_db)

    # 2,500 distinct rows + 1 duplicate PK + 1 invalid row -> page_size=1000 => 3 staging batches + 3 fact batches
    price_rows = _make_price_rows(2500, duplicate_first=True)
    price_rows.append(
        {
            "state": "State0",
            "district": "District0",
            "market": "Mandi0",
            "commodity": "Tomato",
            "arrival_date": "2026-01-02",
            "min_price": 2000.0,
            "max_price": 1500.0,  # invalid max < min -> reject logged via execute_values
            "modal_price": 1700.0,
        }
    )
    res_p = loader.load_prices(price_rows, source_name="agmarknet_api", run_id=1)
    assert res_p["loaded_count"] == 2500
    assert res_p["rejected_count"] == 2  # 1 duplicate_natural_key + 1 max_price_less_than_min_price

    executed = state["executed"]
    stg_batches = [s for s in executed if "INSERT INTO staging.stg_agmarknet_prices" in s]
    fact_batches = [s for s in executed if "INSERT INTO warehouse.fact_price_daily" in s]
    rej_batches = [s for s in executed if "INSERT INTO operational.dq_rejects" in s]
    comm_batches = [s for s in executed if "INSERT INTO warehouse.dim_commodity" in s]

    assert len(stg_batches) == 3, "2,500 rows with page_size=1000 must execute in 3 batched statements"
    assert len(fact_batches) == 3, "2,500 deduplicated rows with page_size=1000 must execute in 3 batched statements"
    assert len(rej_batches) == 1 and "::jsonb" in rej_batches[0]
    # Reviewed classifications (including Paddy) upsert; no provisional insert-only batch here
    assert len(comm_batches) == 1
    assert "ON CONFLICT (commodity_name) DO UPDATE" in comm_batches[0]
    assert "('Paddy', 'cereal', TRUE)" in comm_batches[0]
    assert res_p["commodities_requiring_review"] == []
    assert "ON CONFLICT (date_id, market_id, commodity_id, variety, grade) DO UPDATE" in fact_batches[0]
    # Verify non-target perishables are marked FALSE in the SQL emitted to PostgreSQL
    assert "('Bhindi(Ladies Finger)', 'vegetable', FALSE)" in comm_batches[0]
    assert "('Pigeon Pea (Tur)', 'pulse', TRUE)" in comm_batches[0]
    assert "('Dry Chillies', 'spice', TRUE)" in comm_batches[0]

    # Also verify arrivals & APY bulk psycopg2 execute_values paths
    arr_rows = [
        {
            "state": "State0",
            "district": "District0",
            "market": "Mandi0",
            "commodity": "Red Gram/Arhar/Tur(Whole)",
            "arrival_date": (date(2026, 2, 1) + timedelta(days=i)).isoformat(),
            "quantity_tonnes": 100.0 + i,
        }
        for i in range(1200)
    ]
    res_a = loader.load_arrivals(arr_rows, source_name="ceda_archive", run_id=2)
    assert res_a["loaded_count"] == 1200
    assert len([s for s in state["executed"] if "INSERT INTO warehouse.fact_arrivals_daily" in s]) == 2

    apy_rows = [
        {
            "crop_year": 2000 + (i // 20),
            "season": "Kharif",
            "state": f"State{i % 4}",
            "district": f"Dist{i % 5}",
            "crop_name": "Arhar/Tur",
            "crop_type": "Pulses",
            "area_ha": 1000.0,
            "production_t": 1500.0,
            "yield_t_ha": 1.5,
        }
        for i in range(1100)
    ]
    res_y = loader.load_apy_production(apy_rows, source_name="des_apy", run_id=3)
    assert res_y["loaded_count"] == 1100
    assert len([s for s in state["executed"] if "INSERT INTO warehouse.fact_crop_production" in s]) == 2


def test_representative_5000_row_bulk_load_throughput_and_storability(temp_db: DatabaseManager) -> None:
    loader = WarehouseLoader(temp_db)
    rows = _make_price_rows(5000)

    t0 = time.perf_counter()
    res = loader.load_prices(rows, source_name="bulk_perf_test")
    elapsed = time.perf_counter() - t0

    assert res["loaded_count"] == 5000
    assert res["reconciled"] is True
    assert elapsed < 10.0, f"5,000-row representative load took unexpectedly long: {elapsed:.2f}s"

    with temp_db.readonly_connection() as conn:
        fact_count = conn.execute(text("SELECT COUNT(*) FROM fact_price_daily")).scalar_one()
        comm_rows = conn.execute(text("SELECT commodity_name, category, storable FROM dim_commodity")).all()

    assert fact_count == 5000
    comm_map = {name: (cat, bool(storable)) for name, cat, storable in comm_rows}
    assert "Red Gram/Arhar/Tur(Whole)" not in comm_map
    assert comm_map["Pigeon Pea (Tur)"] == ("pulse", True)
    assert comm_map["Bhindi(Ladies Finger)"] == ("vegetable", False)
    assert comm_map["Carrot"] == ("vegetable", False)
    assert comm_map["Dry Chillies"] == ("spice", True)


def test_migration_001_unchanged_and_fresh_db_tracking(tmp_path: Path) -> None:
    mig_001 = DATABASE_DIR / "migrations" / "001_initial_schema.sql"
    checksum_001 = hashlib.sha256(mig_001.read_bytes()).hexdigest()
    assert checksum_001.startswith("f3723074bd10"), "001_initial_schema.sql baseline must remain unmodified"

    discovered = discover_migrations()
    assert [m.version for m in discovered] == [
        "001_initial_schema",
        "002_migration_tracking_and_indexes",
        "003_commodity_aliases_and_storability",
        "004_split_paddy_onion_green_normalize_kerala",
    ]

    fresh_db = DatabaseManager(database_url=f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    status_before = get_migration_status(fresh_db)
    assert status_before["tracking_table_exists"] is False
    assert status_before["base_schema_exists"] is False
    assert status_before["legacy_untracked_baseline"] is False
    assert len(status_before["pending"]) == 4

    dry = apply_migrations(fresh_db, dry_run=True)
    assert dry["dry_run"] is True
    assert get_migration_status(fresh_db)["tracking_table_exists"] is False

    applied = apply_migrations(fresh_db, dry_run=False)
    assert applied["applied_now"] == [
        "001_initial_schema",
        "002_migration_tracking_and_indexes",
        "003_commodity_aliases_and_storability",
        "004_split_paddy_onion_green_normalize_kerala",
    ]

    # Idempotent second run applies nothing
    second = apply_migrations(fresh_db, dry_run=False)
    assert second["applied_now"] == []
    assert second["baselined"] == []

    status_after = get_migration_status(fresh_db)
    assert status_after["pending"] == []
    assert status_after["mismatched"] == []
    assert len(status_after["applied"]) == 4


def test_legacy_pretracking_baseline_migration_merges_aliases_and_fixes_storability(tmp_path: Path) -> None:
    """Simulates existing Supabase DB state: 001 schema present without schema_migrations, plus legacy alias/storable rows."""
    legacy_path = tmp_path / "legacy_pretracking.db"
    legacy_db = DatabaseManager(database_url=f"sqlite:///{legacy_path.as_posix()}")

    # Create pre-tracking metadata WITHOUT schema_migrations
    full_meta = build_metadata(use_schemas=False)
    pre_meta = MetaData()
    for tbl in full_meta.sorted_tables:
        if tbl.name != "schema_migrations":
            tbl.to_metadata(pre_meta)
    pre_meta.create_all(legacy_db.engine)

    with legacy_db.transaction() as conn:
        conn.execute(
            text(
                "INSERT INTO dim_date (date_id, full_date, year, month, week, season) "
                "VALUES (20261119, '2026-11-19', 2026, 11, 47, 'Kharif')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dim_market (market_id, market_name, district, state) "
                "VALUES (1, 'Latur', 'Latur', 'Maharashtra')"
            )
        )
        # Seed misclassified legacy commodities + unnormalized alias row
        conn.execute(
            text(
                "INSERT INTO dim_commodity (commodity_id, commodity_name, category, storable) VALUES "
                "(101, 'Red Gram/Arhar/Tur(Whole)', 'other', 1), "
                "(102, 'Bhindi(Ladies Finger)', 'other', 1), "
                "(103, 'Dry Chillies', 'other', 1), "
                "(104, 'Pegeon Pea(Arhar Fali)', 'other', 1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO fact_price_daily "
                "(date_id, market_id, commodity_id, variety, grade, min_price, max_price, modal_price, outlier_flag, source_name) "
                "VALUES "
                "(20261119, 1, 101, 'Red', 'FAQ', 6800, 7400, 7100, 0, 'agmarknet_api'), "
                "(20261119, 1, 102, 'NA', 'NA', 1800, 2400, 2100, 0, 'agmarknet_api'), "
                "(20261119, 1, 103, 'NA', 'NA', 12000, 15000, 13500, 0, 'agmarknet_api'), "
                "(20261119, 1, 104, 'NA', 'NA', 3200, 4100, 3600, 0, 'agmarknet_api')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO fact_arrivals_daily (date_id, market_id, commodity_id, quantity_tonnes, source_name) "
                "VALUES (20261119, 1, 101, 42.5, 'ceda_archive')"
            )
        )

    pre_status = get_migration_status(legacy_db)
    assert pre_status["tracking_table_exists"] is False
    assert pre_status["base_schema_exists"] is True
    assert pre_status["legacy_untracked_baseline"] is True
    assert pre_status["pending"][0]["action"] == "baseline"

    res = apply_migrations(legacy_db)
    assert res["baselined"] == ["001_initial_schema"]
    assert res["applied_now"] == [
        "002_migration_tracking_and_indexes",
        "003_commodity_aliases_and_storability",
        "004_split_paddy_onion_green_normalize_kerala",
    ]

    with legacy_db.readonly_connection() as conn:
        commodities = {
            r[0]: (r[1], bool(r[2]))
            for r in conn.execute(text("SELECT commodity_name, category, storable FROM dim_commodity")).all()
        }
        tur_prices = conn.execute(
            text(
                "SELECT p.modal_price FROM fact_price_daily p "
                "JOIN dim_commodity c ON c.commodity_id = p.commodity_id "
                "WHERE c.commodity_name = 'Pigeon Pea (Tur)'"
            )
        ).scalars().all()
        tur_arrivals = conn.execute(
            text(
                "SELECT a.quantity_tonnes FROM fact_arrivals_daily a "
                "JOIN dim_commodity c ON c.commodity_id = a.commodity_id "
                "WHERE c.commodity_name = 'Pigeon Pea (Tur)'"
            )
        ).scalars().all()
        idx_names = {
            r[0]
            for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'index'")).all()
        }

    assert "Red Gram/Arhar/Tur(Whole)" not in commodities
    assert commodities["Pigeon Pea (Tur)"] == ("pulse", True)
    assert commodities["Bhindi(Ladies Finger)"] == ("vegetable", False)
    assert commodities["Dry Chillies"] == ("spice", True)
    assert commodities["Pegeon Pea(Arhar Fali)"] == ("vegetable", False)
    assert [float(v) for v in tur_prices] == [7100.0]
    assert [float(v) for v in tur_arrivals] == [42.5]
    assert "idx_market_signals_lookup" in idx_names
    assert not {"idx_dim_date_full_date", "idx_fact_price_date_id"} & idx_names

    # Tampered checksum guard blocks applying further migrations
    with legacy_db.transaction() as conn:
        conn.execute(text("UPDATE schema_migrations SET checksum = 'tampered' WHERE version = '002_migration_tracking_and_indexes'"))
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        apply_migrations(legacy_db)


def test_state_price_gaps_handles_over_8000_rows_without_truncation(populated_db: DatabaseManager) -> None:
    """Regression test: /api/analytics/state-price-gaps must aggregate all rows in SQL (>8,000 rows)."""
    start = date(2024, 1, 1)
    extra_rows = []
    for v in range(10):
        for d in range(425):
            day = (start + timedelta(days=d)).isoformat()
            extra_rows.append(
                {
                    "state": "Maharashtra",
                    "district": "Nashik",
                    "market": "Lasalgaon",
                    "commodity": "Tomato",
                    "variety": f"GapBulk{v}",
                    "grade": "FAQ",
                    "arrival_date": day,
                    "min_price": 4500.0,
                    "max_price": 5500.0,
                    "modal_price": 5000.0,
                }
            )
            extra_rows.append(
                {
                    "state": "Karnataka",
                    "district": "Bengaluru Urban",
                    "market": "Bengaluru",
                    "commodity": "Tomato",
                    "variety": f"GapBulk{v}",
                    "grade": "FAQ",
                    "arrival_date": day,
                    "min_price": 1000.0,
                    "max_price": 1400.0,
                    "modal_price": 1200.0,
                }
            )
    WarehouseLoader(populated_db).load_prices(extra_rows, source_name="test_fixture")

    with populated_db.readonly_connection() as conn:
        total = int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM fact_price_daily p "
                    "JOIN dim_commodity c ON c.commodity_id = p.commodity_id "
                    "WHERE c.commodity_name = 'Tomato'"
                )
            ).scalar_one()
        )
    assert total > 8500

    settings = Settings(DATABASE_URL="", DEMO_MODE="true", DATA_GOV_IN_API_KEY=" ", GEMINI_API_KEY=" ")
    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db
    app.dependency_overrides[get_app_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            resp = client.get("/api/analytics/state-price-gaps", params={"commodity": "Tomato"})
        assert resp.status_code == 200
        body = resp.json()
        assert sum(r["obs"] for r in body["state_rankings"]) == total
        assert len(body["monthly_state_trends"]) > 0
    finally:
        app.dependency_overrides.clear()


def test_warehouse_db_status_label_distinguishes_demo_override(populated_db: DatabaseManager) -> None:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db

    # Case 1: DEMO_MODE=true while DATABASE_URL is configured
    override_settings = Settings(
        DATABASE_URL="postgresql://user:pass@localhost:5432/synthetic_db",
        DEMO_MODE="true",
        DATA_GOV_IN_API_KEY=" ",
        GEMINI_API_KEY=" ",
    )
    app.dependency_overrides[get_app_settings] = lambda: override_settings
    try:
        with TestClient(app) as client:
            body = client.get("/api/data-sources/status").json()
        db_comp = next(c for c in body["components"] if c["component"] == "Warehouse Database")
        assert db_comp["status_label"] == "Demo SQLite Override (DEMO_MODE=true)"
        assert "pass" not in db_comp["details"]
    finally:
        app.dependency_overrides.clear()
