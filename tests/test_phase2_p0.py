"""Phase 2 P0 regression tests.

A. Complete Agmarknet capture (pagination, scoped cache, retry, reconciliation)
B. ETL safety (idempotent staging/rejects, chunking, cross-source protection, no recurring seed writes)
C. Migration 004 (evidence, catalog consistency, SQLite apply / abort / rollback)
D. Configuration switches (Storage, Gemini, advisor no-persist) and CEDA aggregate exclusion

Everything runs offline against temporary SQLite warehouses and temporary raw zones.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest
import requests
from sqlalchemy import event, text

from config import DATABASE_DIR, TARGET_STATES, Settings
from database.connection import DatabaseManager
from database.migrator import apply_migrations, get_migration_status, rollback_migration
from etl.cleaning import normalize_commodity_name, normalize_state_name
from etl.commodity_catalog import POST_MIGRATION_004_CLASSIFICATION
from etl.transformation import build_ingestion_reconciliation
from etl.warehouse_loader import SOURCE_CONFLICT_STAGE, WarehouseLoader
from ingestion.agmarknet_api import AgmarknetClient, build_fetch_reconciliation
from ingestion.ceda_importer import CedaImporter
from ingestion.daily_capture import run_daily_capture
from ingestion.storage import RawZoneStorage

ROOT = Path(__file__).resolve().parent.parent
V004 = "004_split_paddy_onion_green_normalize_kerala"
MIGRATION_004 = DATABASE_DIR / "migrations" / f"{V004}.sql"
ROLLBACK_004 = DATABASE_DIR / "rollbacks" / f"{V004}.down.sql"
EVIDENCE_FIXTURE = ROOT / "tests" / "fixtures" / "migration_004_split_evidence.json"
FETCH_DAY = date(2026, 11, 20)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resp(status: int, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> SimpleNamespace:
    def _raise() -> None:
        if status >= 400:
            err = requests.HTTPError(f"HTTP {status}")
            err.response = SimpleNamespace(status_code=status)  # type: ignore[attr-defined]
            raise err

    return SimpleNamespace(status_code=status, headers=headers or {}, json=lambda: body, raise_for_status=_raise)


class FeedSession:
    """In-memory data.gov.in feed honouring offset/limit; counts every HTTP call."""

    def __init__(
        self,
        records: list[dict[str, Any]],
        total: int | None = None,
        updated: str = "2026-11-20T10:00:00Z",
        fail_offsets: set[int] | None = None,
    ) -> None:
        self.records = records
        self.total = len(records) if total is None else total
        self.updated = updated
        self.fail_offsets = fail_offsets or set()
        self.calls: list[int] = []

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: Any = None, headers: Any = None) -> Any:
        assert params is not None
        offset, limit = int(params["offset"]), int(params["limit"])
        self.calls.append(offset)
        if offset in self.fail_offsets:
            return _resp(503)
        return _resp(
            200,
            {"total": self.total, "updated_date": self.updated, "records": self.records[offset : offset + limit]},
        )


_STATES = ["Maharashtra", "Karnataka", "Keralam", "Punjab"]


def _feed_records(n: int, arrival: str = "20/11/2026", commodity: str = "Tomato") -> list[dict[str, Any]]:
    return [
        {
            "state": _STATES[i % len(_STATES)],
            "district": f"District {i % 7}",
            "market": f"Market {i}",
            "commodity": commodity,
            "variety": "Local",
            "grade": "FAQ",
            "arrival_date": arrival,
            "min_price": 1000 + i,
            "max_price": 1600 + i,
            "modal_price": 1300 + i,
        }
        for i in range(n)
    ]


def _client(storage: RawZoneStorage, session: Any, **kwargs: Any) -> AgmarknetClient:
    return AgmarknetClient(api_key="test-key", storage=storage, session=session, backoff_base=0.01, **kwargs)


def _count(db: DatabaseManager, table: str, where: str = "", params: dict[str, Any] | None = None) -> int:
    with db.readonly_connection() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {table} {where}"), params or {}).scalar() or 0)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr("ingestion.agmarknet_api.time.sleep", lambda s: sleeps.append(float(s)))
    return sleeps


# ---------------------------------------------------------------------------
# A. Agmarknet complete capture
# ---------------------------------------------------------------------------
def test_full_feed_is_fetched_by_default_with_no_5000_cap(isolated_storage: RawZoneStorage) -> None:
    session = FeedSession(_feed_records(5_437))
    res = _client(isolated_storage, session).fetch_daily_prices(use_cache=False, fetch_date=FETCH_DAY)

    assert res.status == "success" and res.error_message is None
    assert res.rows_fetched == 5_437
    assert session.calls == list(range(0, 5_437, 500))  # 11 pages, nothing skipped
    rec = res.metadata["fetch_reconciliation"]
    assert rec["complete"] is True and rec["shortfall_vs_reported"] == 0 and rec["truncated"] is False
    assert len(res.raw_files) == 11 and all(p.exists() for p in res.raw_files)
    # Defaults: no record cap anywhere on the daily path
    assert inspect.signature(AgmarknetClient.fetch_daily_prices).parameters["max_records"].default is None
    from ingestion.scheduler import run_daily_price_ingestion

    assert inspect.signature(run_daily_price_ingestion).parameters["max_records"].default is None
    assert inspect.signature(run_daily_capture).parameters["max_records"].default is None


def test_explicit_cap_is_reported_as_truncated_partial(isolated_storage: RawZoneStorage) -> None:
    res = _client(isolated_storage, FeedSession(_feed_records(1_200))).fetch_daily_prices(
        max_records=700, use_cache=False, fetch_date=FETCH_DAY
    )
    assert res.status == "partial_success"
    assert res.rows_fetched == 700
    assert res.metadata["fetch_reconciliation"]["truncated"] is True
    assert "truncated" in (res.error_message or "")


def test_feed_shortfall_against_reported_total_is_partial(isolated_storage: RawZoneStorage) -> None:
    res = _client(isolated_storage, FeedSession(_feed_records(1_234), total=1_300)).fetch_daily_prices(
        use_cache=False, fetch_date=FETCH_DAY
    )
    assert res.status == "partial_success"
    assert res.metadata["fetch_reconciliation"]["shortfall_vs_reported"] == 66
    assert "Incomplete fetch" in (res.error_message or "")


def test_mid_feed_failure_keeps_fetched_pages_and_reports_partial(isolated_storage: RawZoneStorage) -> None:
    session = FeedSession(_feed_records(1_400), fail_offsets={1_000})
    res = _client(isolated_storage, session, max_retries=1).fetch_daily_prices(use_cache=False, fetch_date=FETCH_DAY)
    assert res.status == "partial_success"
    assert res.rows_fetched == 1_000
    assert res.metadata["fetch_reconciliation"]["complete"] is False
    assert session.calls.count(1_000) == 2  # one retry, then give up


def test_retry_after_header_is_honoured(isolated_storage: RawZoneStorage, _no_sleep: list[float]) -> None:
    good = FeedSession(_feed_records(3))
    responses = iter([_resp(429, headers={"Retry-After": "7"})])

    class _Session:
        calls = 0

        def get(self, url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> Any:
            _Session.calls += 1
            return next(responses, None) or good.get(url, params=params)

    res = _client(isolated_storage, _Session()).fetch_daily_prices(use_cache=False, fetch_date=FETCH_DAY)
    assert res.status == "success" and res.rows_fetched == 3
    assert _no_sleep == [7.0]
    assert _Session.calls == 2


def test_cache_is_scoped_to_day_and_feed_state(isolated_storage: RawZoneStorage) -> None:
    records = _feed_records(1_200)

    first = FeedSession(records)
    r1 = _client(isolated_storage, first).fetch_daily_prices(fetch_date=FETCH_DAY)
    assert first.calls == [0, 500, 1_000] and r1.metadata["cache_hits"] == 0

    # Same day, same feed state: only the first page is re-fetched live
    second = FeedSession(records)
    r2 = _client(isolated_storage, second).fetch_daily_prices(fetch_date=FETCH_DAY)
    assert second.calls == [0] and r2.metadata["cache_hits"] == 2 and r2.rows_fetched == 1_200

    # Feed republished (new update stamp): no stale page may be served
    third = FeedSession(records, updated="2026-11-20T15:00:00Z")
    r3 = _client(isolated_storage, third).fetch_daily_prices(fetch_date=FETCH_DAY)
    assert third.calls == [0, 500, 1_000] and r3.metadata["cache_hits"] == 0

    # Next day: never reuses yesterday's pages
    fourth = FeedSession(records, updated="2026-11-20T15:00:00Z")
    r4 = _client(isolated_storage, fourth).fetch_daily_prices(fetch_date=date(2026, 11, 21))
    assert fourth.calls == [0, 500, 1_000] and r4.metadata["cache_hits"] == 0


def test_fetch_reconciliation_states_duplicates_and_required_coverage() -> None:
    recs = _feed_records(4)
    recs.append(dict(recs[0]))  # exact duplicate
    rec = build_fetch_reconciliation(recs, total_reported=5, pages_fetched=1, required_states=["Maharashtra", "Bihar"])
    assert rec["complete"] is True
    assert rec["exact_duplicate_records"] == 1
    assert rec["records_by_state"]["Kerala"] == 1  # Keralam normalised to canonical Kerala
    assert "Keralam" not in rec["records_by_state"]
    assert rec["missing_required_states"] == ["Bihar"]


def test_ingestion_reconciliation_issues_and_warnings() -> None:
    ok = build_ingestion_reconciliation(
        reported_total=10, fetched_count=10, valid_count=9, rejected_count=1, loaded_count=7,
        source_conflict_count=2, fetched_states=["A", "B"], loaded_states=["A", "B"], required_states=["A", "C"],
    )
    assert ok["reconciled"] is True and ok["issues"] == []
    assert any("C" in w for w in ok["warnings"])  # coverage gap is a warning, not a count failure

    short = build_ingestion_reconciliation(
        reported_total=10, fetched_count=8, valid_count=8, rejected_count=0, loaded_count=8, truncated=True,
    )
    assert short["reconciled"] is False and short["shortfall_vs_reported"] == 2
    assert len(short["issues"]) == 2

    lost = build_ingestion_reconciliation(
        reported_total=5, fetched_count=5, valid_count=5, rejected_count=0, loaded_count=4,
        fetched_states=["A", "B"], loaded_states=["A"],
    )
    assert lost["reconciled"] is False
    assert lost["states_fetched_not_loaded"] == ["B"]


def test_daily_capture_end_to_end_reconciles_and_is_idempotent(
    temp_db: DatabaseManager, isolated_storage: RawZoneStorage
) -> None:
    records = _feed_records(1_100)
    records[5]["modal_price"] = 999_999  # outside min/max -> rejected
    t_fact = temp_db.table_ref("warehouse", "fact_price_daily")
    t_stg = temp_db.table_ref("staging", "stg_agmarknet_prices")
    t_rej = temp_db.table_ref("operational", "dq_rejects")
    t_run = temp_db.table_ref("operational", "pipeline_run_log")

    out = run_daily_capture(
        db=temp_db, client=_client(isolated_storage, FeedSession(records)), use_cache=False, chunk_size=400
    )
    rec = out["reconciliation"]
    assert out["status"] == "success", rec
    assert (rec["reported_total"], rec["fetched_count"], rec["valid_count"], rec["rejected_count"], rec["loaded_count"]) == (
        1_100, 1_100, 1_099, 1, 1_099,
    )
    assert rec["reconciled"] is True and rec["loaded_state_count"] == 4
    assert out["load"]["chunks"] == 3
    with temp_db.readonly_connection() as conn:
        status, meta = conn.execute(
            text(f"SELECT status, metadata_json FROM {t_run} WHERE run_id = :r"), {"r": out["run_id"]}
        ).one()
    meta = json.loads(meta) if isinstance(meta, str) else meta
    assert status == "success"
    assert meta["reconciliation"]["reconciled"] is True
    assert meta["fetch"]["fetch_reconciliation"]["complete"] is True

    counts = (_count(temp_db, t_fact), _count(temp_db, t_stg), _count(temp_db, t_rej))
    assert counts == (1_099, 1_099, 1)

    # Retry of the same day: nothing is duplicated anywhere
    again = run_daily_capture(
        db=temp_db, client=_client(isolated_storage, FeedSession(records)), use_cache=False, chunk_size=400
    )
    assert again["status"] == "success"
    assert (_count(temp_db, t_fact), _count(temp_db, t_stg), _count(temp_db, t_rej)) == counts


def test_daily_capture_with_cap_is_never_reported_as_success(
    temp_db: DatabaseManager, isolated_storage: RawZoneStorage
) -> None:
    out = run_daily_capture(
        db=temp_db, client=_client(isolated_storage, FeedSession(_feed_records(900))), max_records=500, use_cache=False
    )
    assert out["status"] == "partial_success"
    assert out["reconciliation"]["reconciled"] is False
    assert any("truncated" in i for i in out["reconciliation"]["issues"])


# ---------------------------------------------------------------------------
# B. ETL safety
# ---------------------------------------------------------------------------
def _price_rows(n: int, source_price: float = 0.0) -> list[dict[str, Any]]:
    return [
        {
            "state": "Maharashtra", "district": "Nashik", "market": f"Mandi {i}", "commodity": "Onion",
            "variety": "Red", "grade": "FAQ", "arrival_date": "2026-11-18",
            "min_price": 1000 + source_price, "max_price": 2000 + source_price, "modal_price": 1500 + source_price,
        }
        for i in range(n)
    ]


def test_staging_and_rejects_are_idempotent_across_retries_and_chunks(temp_db: DatabaseManager) -> None:
    rows = _price_rows(7)
    rows.append(dict(rows[0]))  # duplicate natural key -> reject
    rows.append({**rows[1], "market": "Bad Mandi", "min_price": -5})  # invalid -> reject
    t_fact = temp_db.table_ref("warehouse", "fact_price_daily")
    t_stg = temp_db.table_ref("staging", "stg_agmarknet_prices")
    t_rej = temp_db.table_ref("operational", "dq_rejects")
    loader = WarehouseLoader(temp_db)

    first = loader.load_prices(rows, source_name="agmarknet_api", chunk_size=3)
    second = loader.load_prices(rows, source_name="agmarknet_api", chunk_size=3)
    third = loader.load_prices(rows, source_name="agmarknet_api", chunk_size=50)

    assert first["chunks"] == 3 and third["chunks"] == 1
    assert first["input_digest"] == second["input_digest"] == third["input_digest"]
    assert first["valid_count"] == 7 and first["rejected_count"] == 2 and first["reconciled"] is True
    assert _count(temp_db, t_fact) == 7
    assert _count(temp_db, t_stg) == 7  # one staging row per natural key, never appended twice
    assert _count(temp_db, t_rej) == 2  # each distinct rejected record recorded once


def test_failed_chunk_keeps_committed_chunks_and_retry_completes(
    temp_db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _price_rows(6)
    t_fact = temp_db.table_ref("warehouse", "fact_price_daily")
    t_stg = temp_db.table_ref("staging", "stg_agmarknet_prices")
    loader = WarehouseLoader(temp_db)
    real_chunk = loader._load_price_chunk
    calls = {"n": 0}

    def _flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated connection drop")
        return real_chunk(*args, **kwargs)

    monkeypatch.setattr(loader, "_load_price_chunk", _flaky)
    with pytest.raises(RuntimeError, match="simulated"):
        loader.load_prices(rows, source_name="agmarknet_api", chunk_size=2)
    assert _count(temp_db, t_fact) == 2 and _count(temp_db, t_stg) == 2  # chunk 1 committed, chunk 2 rolled back

    monkeypatch.setattr(loader, "_load_price_chunk", real_chunk)
    res = loader.load_prices(rows, source_name="agmarknet_api", chunk_size=2)
    assert res["status"] == "success"
    assert _count(temp_db, t_fact) == 6 and _count(temp_db, t_stg) == 6


def test_other_sources_facts_are_never_silently_overwritten(temp_db: DatabaseManager) -> None:
    t_fact = temp_db.table_ref("warehouse", "fact_price_daily")
    t_rej = temp_db.table_ref("operational", "dq_rejects")
    loader = WarehouseLoader(temp_db)
    loader.load_prices(_price_rows(3), source_name="agmarknet_api")

    other = loader.load_prices(_price_rows(3, source_price=50), source_name="ceda_archive")
    assert other["status"] == "partial_success"
    assert other["source_conflict_count"] == 3 and other["loaded_count"] == 0 and other["reconciled"] is True
    with temp_db.readonly_connection() as conn:
        owners = conn.execute(text(f"SELECT DISTINCT source_name, modal_price FROM {t_fact}")).all()
    assert {r[0] for r in owners} == {"agmarknet_api"}
    assert {float(r[1]) for r in owners} == {1500.0}
    conflict_where = "WHERE stage = :s"
    assert _count(temp_db, t_rej, conflict_where, {"s": SOURCE_CONFLICT_STAGE}) == 3

    loader.load_prices(_price_rows(3, source_price=50), source_name="ceda_archive")  # retry: no duplicate rejects
    assert _count(temp_db, t_rej, conflict_where, {"s": SOURCE_CONFLICT_STAGE}) == 3

    # Same-source updates keep normal upsert semantics
    loader.load_prices(_price_rows(3, source_price=10), source_name="agmarknet_api")
    with temp_db.readonly_connection() as conn:
        assert {float(r[0]) for r in conn.execute(text(f"SELECT modal_price FROM {t_fact}")).all()} == {1510.0}

    # Explicit override is the only way another source can replace the fact
    forced = loader.load_prices(_price_rows(3, source_price=50), source_name="ceda_archive", allow_source_override=True)
    assert forced["source_conflict_count"] == 0 and forced["loaded_count"] == 3
    with temp_db.readonly_connection() as conn:
        assert {r[0] for r in conn.execute(text(f"SELECT DISTINCT source_name FROM {t_fact}")).all()} == {"ceda_archive"}


def test_arrival_facts_are_protected_from_other_sources(temp_db: DatabaseManager) -> None:
    t_arr = temp_db.table_ref("warehouse", "fact_arrivals_daily")
    rec = [{"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion",
            "arrival_date": "2026-11-18", "quantity_tonnes": 120.0}]
    loader = WarehouseLoader(temp_db)
    loader.load_arrivals(rec, source_name="ceda_archive")
    res = loader.load_arrivals([{**rec[0], "quantity_tonnes": 999.0}], source_name="other_feed")
    assert res["source_conflict_count"] == 1
    with temp_db.readonly_connection() as conn:
        assert conn.execute(text(f"SELECT source_name, quantity_tonnes FROM {t_arr}")).one() == ("ceda_archive", 120.0)


def test_alias_collisions_are_rejected_with_explicit_reason(temp_db: DatabaseManager) -> None:
    base = {"state": "Punjab", "district": "Ludhiana", "market": "Khanna", "variety": "Common", "grade": "FAQ",
            "arrival_date": "2026-11-18", "min_price": 2000, "max_price": 2400, "modal_price": 2200}
    rows = [{**base, "commodity": "Paddy(Common)"}, {**base, "commodity": "Paddy(Dhan)(Common)"},
            {**base, "commodity": "Paddy(Common)"}]
    res = WarehouseLoader(temp_db).load_prices(rows, source_name="agmarknet_api")
    t_rej = temp_db.table_ref("operational", "dq_rejects")
    with temp_db.readonly_connection() as conn:
        reasons = sorted(r[0] for r in conn.execute(text(f"SELECT reject_reason FROM {t_rej}")).all())
    assert res["loaded_count"] == 1
    assert reasons == ["alias_collision_unresolved", "duplicate_natural_key"]


def test_paddy_rice_and_kerala_identity_rules(temp_db: DatabaseManager) -> None:
    for raw in ("Paddy(Common)", "Paddy(Basmati)", "Paddy(Fine/Superfine/Grade A)", "Paddy(Dhan)(Common)"):
        assert normalize_commodity_name(raw) == "Paddy"
    assert normalize_commodity_name("Rice") == "Rice"
    assert normalize_commodity_name("Rice(Fine/Superfine/Grade A)") == "Rice"
    assert normalize_commodity_name("Onion Green") == "Onion Green"
    assert normalize_state_name("Keralam") == "Kerala" == normalize_state_name("kerala")

    rows = [
        {"state": "Keralam", "district": "Palakkad", "market": "Palakkad", "commodity": "Paddy(Basmati)",
         "variety": "Basmati 1509", "grade": "FAQ", "arrival_date": "2026-11-18",
         "min_price": 3000, "max_price": 3400, "modal_price": 3200},
        {"state": "Kerala", "district": "Palakkad", "market": "Palakkad", "commodity": "Rice",
         "variety": "Matta", "grade": "FAQ", "arrival_date": "2026-11-18",
         "min_price": 4000, "max_price": 4400, "modal_price": 4200},
    ]
    WarehouseLoader(temp_db).load_prices(rows, source_name="agmarknet_api")
    t_fact = temp_db.table_ref("warehouse", "fact_price_daily")
    t_mkt = temp_db.table_ref("warehouse", "dim_market")
    t_comm = temp_db.table_ref("warehouse", "dim_commodity")
    with temp_db.readonly_connection() as conn:
        got = conn.execute(
            text(
                f"SELECT c.commodity_name, p.variety, m.state FROM {t_fact} p "
                f"JOIN {t_comm} c ON c.commodity_id = p.commodity_id JOIN {t_mkt} m ON m.market_id = p.market_id "
                "ORDER BY 1"
            )
        ).all()
    assert [tuple(r) for r in got] == [("Paddy", "Basmati 1509", "Kerala"), ("Rice", "Matta", "Kerala")]
    assert _count(temp_db, t_mkt, "WHERE state = 'Keralam'") == 0


def _write_statements(db: DatabaseManager) -> tuple[list[str], Any]:
    seen: list[str] = []

    def _capture(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(statement.strip().split()[0].upper())

    event.listen(db.engine, "before_cursor_execute", _capture)
    return seen, _capture


def test_recurring_runs_perform_no_seed_writes_when_reference_data_complete(temp_db: DatabaseManager) -> None:
    from database.seed import ensure_reference_dimensions, reference_dimensions_status

    assert reference_dimensions_status(temp_db)["complete"] is True
    seen, hook = _write_statements(temp_db)
    try:
        out = ensure_reference_dimensions(temp_db)
    finally:
        event.remove(temp_db.engine, "before_cursor_execute", hook)
    assert out["written"] is False
    assert not {"INSERT", "UPDATE", "DELETE"} & set(seen)


def test_missing_reference_data_on_postgres_requires_explicit_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    import database.seed as seed

    monkeypatch.setattr(
        seed, "reference_dimensions_status",
        lambda db: {"complete": False, "missing_commodities": ["Wheat"], "missing_crops": [], "missing_mappings": []},
    )
    seeded = MagicMock(return_value={})
    monkeypatch.setattr(seed, "seed_reference_dimensions", seeded)
    with pytest.raises(seed.ReferenceDataMissingError, match="--seed-reference"):
        seed.ensure_reference_dimensions(SimpleNamespace(is_demo=False))
    seeded.assert_not_called()

    assert seed.ensure_reference_dimensions(SimpleNamespace(is_demo=False), force=True)["written"] is True
    seeded.assert_called_once()


def test_demo_flow_refuses_a_non_demo_database() -> None:
    from scripts.ingest_all import run_demo_end_to_end

    fake = MagicMock(is_demo=False)
    with pytest.raises(SystemExit, match="non-demo database"):
        run_demo_end_to_end(fake)
    fake.initialize_schema.assert_not_called()


# ---------------------------------------------------------------------------
# C. Migration 004
# ---------------------------------------------------------------------------
def test_migration_004_sql_is_portable() -> None:
    for path in (MIGRATION_004, ROLLBACK_004):
        body = path.read_text(encoding="utf-8")
        assert ":" not in body, f"{path.name} must not contain ':' (SQLite bind-parameter syntax)"
        for line in body.splitlines():
            if line.lstrip().startswith("--"):
                assert ";" not in line, f"semicolon inside a comment in {path.name}"


def test_migration_004_classification_matches_catalog() -> None:
    sql = MIGRATION_004.read_text(encoding="utf-8")
    block = sql[sql.index("-- BEGIN 004 CLASSIFICATION") : sql.index("-- END 004 CLASSIFICATION")]
    rows = re.findall(r"\('([^']+)', '([^']+)', (TRUE|FALSE), '([^']+)'\)", block)
    parsed = {name: (cat, flag == "TRUE", status) for name, cat, flag, status in rows}
    assert parsed == POST_MIGRATION_004_CLASSIFICATION


def test_migration_004_evidence_fixture_equals_sql_block() -> None:
    from scripts.build_migration_004_mapping import BEGIN_MARKER, END_MARKER, render_sql_block

    fixture = json.loads(EVIDENCE_FIXTURE.read_text(encoding="utf-8"))
    sql = MIGRATION_004.read_text(encoding="utf-8")
    block = sql[sql.index(BEGIN_MARKER) : sql.index(END_MARKER) + len(END_MARKER)]
    assert render_sql_block(fixture["rows"]) == block

    rows = fixture["rows"]
    assert fixture["manifest_aggregate_sha256"] == "13b8fe7d7de3f984f652df5b3ee4aabe37d17348fafc84a244c3cf089d4fa4e3"
    assert len(rows) == 215
    assert sum(r["target_commodity"] == "Paddy" for r in rows) == 153
    assert sum(r["target_commodity"] == "Onion Green" for r in rows) == 62
    # Exact raw evidence only: every row's raw commodity is a Paddy / Onion Green spelling, never Rice
    assert {r["raw_commodity"] for r in rows} == {
        "Paddy(Common)", "Paddy(Basmati)", "Paddy(Fine/Superfine/Grade A)", "Onion Green",
    }
    assert all(normalize_commodity_name(r["raw_commodity"]) == r["target_commodity"] for r in rows)
    assert len({(r["raw_file"], r["raw_index"]) for r in rows}) == 215


def _sqlite_db(tmp_path: Path, name: str) -> DatabaseManager:
    return DatabaseManager(database_url=f"sqlite:///{(tmp_path / name).as_posix()}")


def _seed_pre_004_warehouse(db: DatabaseManager) -> dict[str, int]:
    """Build a pre-004 warehouse holding the 215 evidence rows exactly as production stored them.

    Backend-neutral (SQLite and PostgreSQL) so the same evidence drives both rehearsals.
    """
    from decimal import Decimal

    rows = json.loads(EVIDENCE_FIXTURE.read_text(encoding="utf-8"))["rows"]
    num = Decimal if db.backend == "postgresql" else float
    t_comm = db.table_ref("warehouse", "dim_commodity")
    t_date = db.table_ref("warehouse", "dim_date")
    t_mkt = db.table_ref("warehouse", "dim_market")
    t_fact = db.table_ref("warehouse", "fact_price_daily")
    with db.transaction() as conn:
        for name, cat, storable in (("Rice", "cereal", True), ("Onion", "vegetable", False), ("Tomato", "vegetable", False)):
            conn.execute(
                text(f"INSERT INTO {t_comm} (commodity_name, category, storable) VALUES (:n, :c, :s) "
                     "ON CONFLICT (commodity_name) DO NOTHING"),
                {"n": name, "c": cat, "s": storable},
            )
        comm = {r[1]: r[0] for r in conn.execute(text(f"SELECT commodity_id, commodity_name FROM {t_comm}")).all()}
        for d in sorted({r["arrival_date"] for r in rows} | {"2026-11-19"}):
            y, m, dd = (int(x) for x in d.split("-"))
            conn.execute(
                text(f"INSERT INTO {t_date} (date_id, full_date, year, month, week, season) "
                     "VALUES (:i, :f, :y, :m, 47, 'Rabi') ON CONFLICT (date_id) DO NOTHING"),
                {"i": y * 10000 + m * 100 + dd, "f": date(y, m, dd) if db.backend == "postgresql" else d, "y": y, "m": m},
            )

        def market(name: str, district: str, state: str) -> int:
            conn.execute(
                text(f"INSERT INTO {t_mkt} (market_name, district, state) VALUES (:m, :d, :s) "
                     "ON CONFLICT (market_name, district, state) DO NOTHING"),
                {"m": name, "d": district, "s": state},
            )
            return int(conn.execute(
                text(f"SELECT market_id FROM {t_mkt} WHERE market_name = :m AND district = :d AND state = :s"),
                {"m": name, "d": district, "s": state},
            ).scalar())

        def fact(date_s: str, mkt: int, comm_id: int, variety: str, grade: str, lo: str, hi: str, modal: str) -> None:
            conn.execute(
                text(f"INSERT INTO {t_fact} (date_id, market_id, commodity_id, variety, grade, min_price, "
                     "max_price, modal_price, outlier_flag, source_name) VALUES (:d, :m, :c, :v, :g, :lo, :hi, :mo, "
                     ":of, 'agmarknet_api')"),
                {"d": int(date_s.replace("-", "")), "m": mkt, "c": comm_id, "v": variety, "g": grade,
                 "lo": num(lo), "hi": num(hi), "mo": num(modal), "of": False},
            )

        for r in rows:
            state = "Keralam" if r["state"] == "Kerala" else r["state"]  # production stored Keralam
            mkt = market(r["market_name"], r["district"], state)
            fact(r["arrival_date"], mkt, comm[r["source_commodity"]], r["variety"], r["grade"],
                 r["min_price"], r["max_price"], r["modal_price"])
        # Genuine Rice / Onion rows and a Keralam market without the split commodities: must not move
        extra = market("Kottayam", "Kottayam", "Keralam")
        fact("2026-11-19", extra, comm["Rice"], "Matta", "FAQ", "3800", "4200", "4000")
        fact("2026-11-19", extra, comm["Onion"], "Big", "FAQ", "2500", "3100", "2800")
        fact("2026-11-19", extra, comm["Tomato"], "Local", "FAQ", "900", "1300", "1100")
    return comm


def _warehouse_snapshot(db: DatabaseManager) -> dict[str, Any]:
    t_fact = db.table_ref("warehouse", "fact_price_daily")
    t_mkt = db.table_ref("warehouse", "dim_market")
    t_comm = db.table_ref("warehouse", "dim_commodity")
    with db.readonly_connection() as conn:
        return {
            "facts": sorted(tuple(r) for r in conn.execute(text(
                "SELECT date_id, market_id, commodity_id, variety, grade, min_price, max_price, modal_price, "
                f"outlier_flag, source_name, ingested_at FROM {t_fact}")).all()),
            "markets": sorted(tuple(r) for r in conn.execute(text(f"SELECT * FROM {t_mkt}")).all()),
            "commodities": sorted(tuple(r) for r in conn.execute(text(f"SELECT * FROM {t_comm}")).all()),
        }


def _commodity_counts(db: DatabaseManager) -> dict[str, int]:
    t_fact = db.table_ref("warehouse", "fact_price_daily")
    t_comm = db.table_ref("warehouse", "dim_commodity")
    with db.readonly_connection() as conn:
        return {r[0]: int(r[1]) for r in conn.execute(text(
            f"SELECT c.commodity_name, COUNT(*) FROM {t_fact} p "
            f"JOIN {t_comm} c ON c.commodity_id = p.commodity_id GROUP BY c.commodity_name")).all()}


def _paddy_maps_to_rice(db: DatabaseManager) -> bool:
    t_map = db.table_ref("warehouse", "map_commodity_crop")
    t_comm = db.table_ref("warehouse", "dim_commodity")
    t_crop = db.table_ref("warehouse", "dim_crop")
    with db.readonly_connection() as conn:
        return bool(conn.execute(text(
            f"SELECT COUNT(*) FROM {t_map} m "
            f"JOIN {t_comm} c ON c.commodity_id = m.commodity_id "
            f"JOIN {t_crop} cr ON cr.crop_id = m.crop_id "
            "WHERE c.commodity_name = 'Paddy' AND cr.crop_name = 'Rice'"
        )).scalar())


def test_migration_004_is_a_noop_on_a_fresh_demo_warehouse(temp_db: DatabaseManager) -> None:
    status = get_migration_status(temp_db)
    assert V004 in [a["version"] for a in status["applied"]] and status["pending"] == []
    with temp_db.readonly_connection() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM migration_004_price_moves")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM migration_004_split_evidence")).scalar() == 215
        names = {r[0] for r in conn.execute(text("SELECT commodity_name FROM dim_commodity")).all()}
    assert {"Paddy", "Onion Green"} <= names
    assert _paddy_maps_to_rice(temp_db)


def test_migration_004_apply_rollback_reapply_on_sqlite(tmp_path: Path) -> None:
    db = _sqlite_db(tmp_path, "pre004.db")
    apply_migrations(db, target_version="003")
    _seed_pre_004_warehouse(db)
    before = _warehouse_snapshot(db)
    counts_before = _commodity_counts(db)
    assert counts_before == {"Rice": 154, "Onion": 63, "Tomato": 1}
    with db.readonly_connection() as conn:
        modal_before = conn.execute(text("SELECT SUM(modal_price) FROM fact_price_daily")).scalar()

    apply_migrations(db)
    assert _commodity_counts(db) == {"Rice": 1, "Paddy": 153, "Onion": 1, "Onion Green": 62, "Tomato": 1}
    assert _paddy_maps_to_rice(db)
    with db.readonly_connection() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM fact_price_daily")).scalar() == len(before["facts"])
        assert conn.execute(text("SELECT SUM(modal_price) FROM fact_price_daily")).scalar() == pytest.approx(modal_before)
        assert conn.execute(text("SELECT COUNT(*) FROM dim_market WHERE state = 'Keralam'")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM migration_004_price_moves")).scalar() == 215
        assert conn.execute(text("SELECT COUNT(*) FROM migration_004_assertions")).scalar() == 18
        # Varieties, grades and prices of moved rows are untouched
        moved = conn.execute(text(
            "SELECT COUNT(*) FROM migration_004_split_evidence e JOIN dim_market m ON m.market_name = e.market_name "
            "AND m.district = e.district AND m.state = e.state JOIN dim_commodity c ON c.commodity_name = e.target_commodity "
            "JOIN fact_price_daily p ON p.market_id = m.market_id AND p.commodity_id = c.commodity_id "
            "AND p.variety = e.variety AND p.grade = e.grade AND p.modal_price = e.modal_price")).scalar()
        assert moved == 215
        # Paddy and Rice commodities remain separate rows
        assert conn.execute(text(
            "SELECT COUNT(*) FROM dim_commodity WHERE commodity_name IN ('Paddy', 'Rice')")).scalar() == 2

    rollback_migration(db, V004)
    assert _warehouse_snapshot(db) == before
    assert not _paddy_maps_to_rice(db)
    with db.readonly_connection() as conn:
        assert conn.execute(text(
            "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'migration_004%'")).scalar() == 0

    apply_migrations(db)
    assert _commodity_counts(db) == {"Rice": 1, "Paddy": 153, "Onion": 1, "Onion Green": 62, "Tomato": 1}
    assert _paddy_maps_to_rice(db)


def test_migration_004_aborts_atomically_on_partial_evidence(tmp_path: Path) -> None:
    db = _sqlite_db(tmp_path, "tampered.db")
    apply_migrations(db, target_version="003")
    _seed_pre_004_warehouse(db)
    with db.transaction() as conn:  # one evidence-backed row no longer matches its raw prices
        conn.execute(text(
            "UPDATE fact_price_daily SET modal_price = min_price WHERE rowid = ("
            "SELECT p.rowid FROM fact_price_daily p JOIN dim_commodity c ON c.commodity_id = p.commodity_id "
            "WHERE c.commodity_name = 'Rice' AND p.modal_price <> p.min_price LIMIT 1)"))
    before = _warehouse_snapshot(db)

    with pytest.raises(Exception, match="CHECK constraint failed|passed"):
        apply_migrations(db)
    assert V004 in [p["version"] for p in get_migration_status(db)["pending"]]
    assert _warehouse_snapshot(db) == before
    with db.readonly_connection() as conn:
        assert conn.execute(text(
            "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'migration_004%'")).scalar() == 0


def test_query_crop_production_recognizes_paddy_as_rice_crop(temp_db: DatabaseManager) -> None:
    """After 004, filtering crop production by commodity Paddy resolves through the Rice crop map."""
    from services.data_service import query_crop_production

    assert _paddy_maps_to_rice(temp_db)
    t_crop = temp_db.table_ref("warehouse", "dim_crop")
    t_fact = temp_db.table_ref("warehouse", "fact_crop_production")
    with temp_db.transaction() as conn:
        rice_id = conn.execute(text(f"SELECT crop_id FROM {t_crop} WHERE crop_name = 'Rice'")).scalar()
        assert rice_id is not None
        conn.execute(
            text(
                f"INSERT INTO {t_fact} (crop_year, season, state, district, crop_id, area_ha, "
                "production_t, yield_t_ha, source_name) VALUES "
                "(2024, 'Kharif', 'Telangana', 'Nalgonda', :cid, 100.0, 250.0, 2.5, 'test_fixture')"
            ),
            {"cid": int(rice_id)},
        )

    by_paddy = query_crop_production(temp_db, commodity_name="Paddy")
    by_rice = query_crop_production(temp_db, commodity_name="Rice")
    assert not by_paddy.empty
    assert set(by_paddy["crop_name"]) == {"Rice"}
    assert set(by_paddy["commodity_name"]) == {"Paddy"}
    assert float(by_paddy.iloc[0]["production_t"]) == 250.0
    # Rice commodity still maps to the same crop and remains a separate filter key
    assert not by_rice.empty
    assert set(by_rice["crop_name"]) == {"Rice"}
    assert set(by_rice["commodity_name"]) == {"Rice"}


# ---------------------------------------------------------------------------
# D. Switches, advisor no-persist, CEDA aggregates
# ---------------------------------------------------------------------------
def test_switches_default_off_and_credentials_alone_never_enable() -> None:
    s = Settings(
        DATABASE_URL="", DEMO_MODE="true", SUPABASE_URL="https://x.supabase.co", SUPABASE_SERVICE_ROLE_KEY="k",
        SUPABASE_STORAGE_BUCKET="b", GEMINI_API_KEY="g",
    )
    assert s.has_supabase_storage and s.has_gemini_key
    assert s.storage_upload_active is False and s.gemini_active is False
    assert s.advisor_persistence_allowed(is_demo_db=False) is False
    assert s.advisor_persistence_allowed(is_demo_db=True) is True
    on = Settings(DATABASE_URL="", DEMO_MODE="true", ADVISOR_PERSIST="true")
    assert on.advisor_persistence_allowed(is_demo_db=False) is True


def test_advisor_no_persist_mode_writes_no_signals_or_briefings(
    populated_db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services import dashboard_service

    monkeypatch.setattr(
        dashboard_service, "get_settings",
        lambda: SimpleNamespace(advisor_persistence_allowed=lambda is_demo: False),
    )
    monkeypatch.setattr("genai.briefing.get_settings", lambda: SimpleNamespace(gemini_enabled=False))
    t_sig = populated_db.table_ref("analytics", "market_signals")
    t_br = populated_db.table_ref("analytics", "briefings")
    before = (_count(populated_db, t_sig), _count(populated_db, t_br))

    signal, briefing = dashboard_service.get_advisor_recommendation(
        populated_db, commodity="Tomato", market="Lasalgaon", state="Maharashtra", as_of_date=date(2025, 6, 30)
    )
    assert signal is not None and briefing is not None
    assert signal.signal_id is None and briefing.briefing_id is None
    assert briefing.used_fallback is True and briefing.validation_status == "fallback_template"
    assert any("Gemini disabled" in e for e in briefing.validation_errors)
    assert (_count(populated_db, t_sig), _count(populated_db, t_br)) == before


def test_ceda_portal_series_is_disabled_without_network() -> None:
    session = MagicMock()
    res = CedaImporter(storage=MagicMock(), session=session).fetch_portal_series(
        commodity_id=78, state_id=27, from_date="2025-01-01", to_date="2025-01-31"
    )
    assert res.status == "unavailable" and res.records == []
    assert "state-level aggregates" in (res.error_message or "")
    session.post.assert_not_called()
    session.get.assert_not_called()


def test_state_level_aggregate_rows_never_reach_market_facts(
    tmp_path: Path, temp_db: DatabaseManager, isolated_storage: RawZoneStorage
) -> None:
    """A portal-shaped export (state totals, no market) must not create market-level facts."""
    csv_path = tmp_path / "ceda_state_aggregate.csv"
    pd.DataFrame(
        [
            {"Date": "2025-01-02", "State": "Maharashtra", "Commodity": "Onion",
             "Min Price": 1500, "Max Price": 2500, "Modal Price": 2000},
            {"Date": "2025-01-03", "State": "Maharashtra", "Commodity": "Onion",
             "Min Price": 1550, "Max Price": 2550, "Modal Price": 2050},
        ]
    ).to_csv(csv_path, index=False)
    t_fact = temp_db.table_ref("warehouse", "fact_price_daily")
    facts_before = _count(temp_db, t_fact)

    res = CedaImporter(storage=isolated_storage).import_csv_files([csv_path], record_kind="price", preserve_raw=False)
    price_records = [r for r in res.records if r.get("record_type", "price") == "price"]
    if price_records:
        WarehouseLoader(temp_db).load_prices(price_records, source_name="ceda_archive")
    assert _count(temp_db, t_fact) == facts_before
    assert all(not str(r.get("market") or "").strip() for r in price_records)


def test_target_states_include_kerala_canonically() -> None:
    assert "Keralam" not in {normalize_state_name(s) for s in TARGET_STATES}
