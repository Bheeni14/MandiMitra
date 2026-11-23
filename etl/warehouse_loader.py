"""Idempotent warehouse loader for staging, operational reject logs, dimensions, and facts."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from typing import Any, Iterator, Sequence

import pandas as pd
from psycopg2.extras import execute_values
from sqlalchemy import bindparam, text

from database.connection import DatabaseManager, get_db_manager
from etl.cleaning import (
    classify_commodity,
    clean_apy_records,
    clean_arrival_records,
    clean_price_records,
)
from etl.transformation import build_date_dim_rows, build_reconciliation_summary
from etl.validation import (
    ValidationResult,
    validate_apy_records,
    validate_arrival_records,
    validate_price_records,
)
from ingestion.scheduler import finish_pipeline_run, start_pipeline_run

logger = logging.getLogger(__name__)

_PG_PAGE_SIZE = 1000
_SQLITE_CHUNK_SIZE = 1000

# Valid rows are written in transactions of this many rows. Smaller transactions bound memory and
# lock time for multi-year backfills; every write is idempotent, so a failed run can simply be re-run.
DEFAULT_LOAD_CHUNK_SIZE = 5000
SOURCE_CONFLICT_STAGE = "warehouse_source_conflict"


def _json_safe(value: Any) -> Any:
    """PostgreSQL JSONB rejects NaN/Infinity tokens and NUL characters; map them to valid JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _dump_raw_record(raw_record: Any) -> str:
    # sort_keys makes the text canonical, so identical rejected records compare equal on every backend.
    return json.dumps(_json_safe(raw_record), default=str, allow_nan=False, sort_keys=True)


def _input_digest(raw_records: Sequence[Any]) -> str:
    """Deterministic content id of a load's raw input; a retry of the same input yields the same id."""
    payload = json.dumps(_json_safe(list(raw_records)), default=str, sort_keys=True, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _iter_chunks(df: pd.DataFrame, size: int) -> Iterator[pd.DataFrame]:
    if size < 1:
        raise ValueError("chunk_size must be at least 1")
    for start in range(0, len(df), size):
        yield df.iloc[start : start + size]


class WarehouseLoader:
    """Loads staging tables, logs DQ rejects, and performs idempotent transactional UPSERTs into warehouse tables."""

    def __init__(self, db: DatabaseManager | None = None) -> None:
        self.db = db or get_db_manager()
        self.commodities_requiring_review: set[str] = set()

    def _pg_execute_values(
        self,
        conn: Any,
        sql_with_values_placeholder: str,
        rows: Sequence[tuple[Any, ...]],
        template: str,
        page_size: int = _PG_PAGE_SIZE,
    ) -> None:
        """Execute high-throughput multi-row VALUES batches via psycopg2.extras.execute_values."""
        if not rows:
            return
        dbapi_conn = getattr(conn, "connection", None)
        driver_conn = getattr(dbapi_conn, "driver_connection", dbapi_conn)
        if driver_conn is None or not hasattr(driver_conn, "cursor"):
            raise RuntimeError("PostgreSQL bulk insert requires an active DBAPI driver connection.")
        with driver_conn.cursor() as cur:
            execute_values(
                cur,
                sql_with_values_placeholder,
                rows,
                template=template,
                page_size=page_size,
            )

    def _sqlite_execute_chunks(
        self,
        conn: Any,
        sql_named: str,
        rows: Sequence[dict[str, Any]],
        chunk_size: int = _SQLITE_CHUNK_SIZE,
    ) -> None:
        if not rows:
            return
        stmt = text(sql_named)
        for i in range(0, len(rows), chunk_size):
            conn.execute(stmt, list(rows[i : i + chunk_size]))

    def _log_rejects(self, conn: Any, run_id: int, rejects: list[dict[str, Any]]) -> int:
        """Insert DQ rejects idempotently and return how many were newly recorded.

        A reject is identified by (source_name, stage, reject_reason, raw_record). Re-running the same
        input therefore never adds a second copy; the first occurrence keeps its original run_id.
        """
        if not rejects:
            return 0
        t_rej = self.db.table_ref("operational", "dq_rejects")
        unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for r in rejects:
            dumped = _dump_raw_record(r["raw_record"])
            unique.setdefault((r["source_name"], r["stage"], r["reject_reason"], dumped), r)

        if self.db.backend == "postgresql":
            rows = [
                (run_id, src_name, stage, reason, dumped)
                for (src_name, stage, reason, dumped) in unique
            ]
            before = self._count_rows(conn, t_rej)
            self._pg_execute_values(
                conn,
                f"""
                INSERT INTO {t_rej} (run_id, source_name, stage, reject_reason, raw_record)
                SELECT v.run_id, v.source_name, v.stage, v.reject_reason, v.raw_record
                FROM (VALUES %s) AS v(run_id, source_name, stage, reject_reason, raw_record)
                WHERE NOT EXISTS (
                    SELECT 1 FROM {t_rej} d
                    WHERE d.source_name = v.source_name
                      AND d.stage = v.stage
                      AND d.reject_reason = v.reject_reason
                      AND d.raw_record = v.raw_record
                )
                """,
                rows,
                template="(%s::bigint, %s::text, %s::text, %s::text, %s::jsonb)",
            )
            return max(0, self._count_rows(conn, t_rej) - before)

        payload = [
            {
                "run_id": run_id,
                "source_name": src_name,
                "stage": stage,
                "reject_reason": reason,
                "raw_record": dumped,
            }
            for (src_name, stage, reason, dumped) in unique
        ]
        before = self._count_rows(conn, t_rej)
        self._sqlite_execute_chunks(
            conn,
            f"""
            INSERT INTO {t_rej} (run_id, source_name, stage, reject_reason, raw_record)
            SELECT :run_id, :source_name, :stage, :reject_reason, :raw_record
            WHERE NOT EXISTS (
                SELECT 1 FROM {t_rej}
                WHERE source_name = :source_name
                  AND stage = :stage
                  AND reject_reason = :reject_reason
                  AND raw_record = :raw_record
            )
            """,
            payload,
        )
        return max(0, self._count_rows(conn, t_rej) - before)

    def _count_rows(self, conn: Any, table: str) -> int:
        row = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).all()
        return int(row[0][0]) if row else 0

    def _delete_staging_by_keys(
        self,
        conn: Any,
        t_stg: str,
        columns: Sequence[str],
        pg_types: Sequence[str],
        rows: list[tuple[Any, ...]],
    ) -> None:
        """Remove earlier staging copies of these natural keys so staging holds one row per key.

        Staging is a latest-snapshot area. Replacing by natural key (inside the same transaction as the
        new insert) is what makes a retried or repeated load idempotent without a schema change.
        """
        if not rows:
            return
        if self.db.backend == "postgresql":
            cols = ", ".join(columns)
            cond = " AND ".join(f"s.{c} = v.{c}" for c in columns)
            template = "(" + ", ".join(f"%s::{t}" for t in pg_types) + ")"
            self._pg_execute_values(
                conn,
                f"DELETE FROM {t_stg} AS s USING (VALUES %s) AS v({cols}) WHERE {cond}",
                rows,
                template=template,
            )
            return
        cond = " AND ".join(f"{c} = :{c}" for c in columns)
        dict_rows = [
            {c: (v.isoformat() if hasattr(v, "isoformat") else v) for c, v in zip(columns, row)}
            for row in rows
        ]
        self._sqlite_execute_chunks(conn, f"DELETE FROM {t_stg} WHERE {cond}", dict_rows)

    def _existing_fact_sources(self, conn: Any, select_sql: str, ids: list[Any]) -> dict[tuple[Any, ...], str]:
        """Map fact keys to the source that currently owns them (only for the ids in this chunk)."""
        owners: dict[tuple[Any, ...], str] = {}
        unique_ids = sorted(set(ids))
        stmt = text(select_sql).bindparams(bindparam("ids", expanding=True))
        for i in range(0, len(unique_ids), 500):
            for row in conn.execute(stmt, {"ids": unique_ids[i : i + 500]}).all():
                owners[tuple(row[:-1])] = str(row[-1])
        return owners

    @staticmethod
    def _split_source_conflicts(
        facts: dict[tuple[Any, ...], dict[str, Any]],
        owners: dict[tuple[Any, ...], str],
        allow_override: bool,
    ) -> tuple[dict[tuple[Any, ...], dict[str, Any]], list[dict[str, Any]], int]:
        """Separate facts another source already owns; they are rejected visibly, never overwritten."""
        if allow_override:
            return facts, [], 0
        kept: dict[tuple[Any, ...], dict[str, Any]] = {}
        conflicts: list[dict[str, Any]] = []
        conflict_rows = 0
        for pk, fact in facts.items():
            owner = owners.get(pk)
            if owner is not None and owner != fact["source_name"]:
                conflict_rows += int(fact.get("_n", 1))
                conflicts.append(
                    {
                        "source_name": fact["source_name"],
                        "stage": SOURCE_CONFLICT_STAGE,
                        "reject_reason": f"cross_source_conflict:existing_source={owner}",
                        "raw_record": {
                            "incoming": fact.get("_record"),
                            "incoming_source": fact["source_name"],
                            "existing_source": owner,
                        },
                    }
                )
            else:
                kept[pk] = fact
        return kept, conflicts, conflict_rows

    def _upsert_dim_dates(self, conn: Any, date_rows: list[dict[str, Any]]) -> None:
        if not date_rows:
            return
        t_date = self.db.table_ref("warehouse", "dim_date")
        if self.db.backend == "postgresql":
            rows = [
                (r["date_id"], r["full_date"], r["year"], r["month"], r["week"], r["season"])
                for r in date_rows
            ]
            self._pg_execute_values(
                conn,
                f"""
                INSERT INTO {t_date} (date_id, full_date, year, month, week, season)
                VALUES %s
                ON CONFLICT (date_id) DO UPDATE
                SET full_date = excluded.full_date,
                    year = excluded.year,
                    month = excluded.month,
                    week = excluded.week,
                    season = excluded.season
                """,
                rows,
                template="(%s, %s, %s, %s, %s, %s)",
            )
            return

        params = [
            {
                **r,
                "full_date": r["full_date"].isoformat(),
            }
            for r in date_rows
        ]
        self._sqlite_execute_chunks(
            conn,
            f"""
            INSERT INTO {t_date} (date_id, full_date, year, month, week, season)
            VALUES (:date_id, :full_date, :year, :month, :week, :season)
            ON CONFLICT (date_id) DO UPDATE
            SET full_date = excluded.full_date,
                year = excluded.year,
                month = excluded.month,
                week = excluded.week,
                season = excluded.season
            """,
            params,
        )

    def _upsert_commodities(self, conn: Any, commodities: list[str]) -> dict[str, int]:
        """Insert new commodities; only reviewed classifications may overwrite existing rows."""
        t_comm = self.db.table_ref("warehouse", "dim_commodity")
        unique_names = sorted({c for c in commodities if c})
        reviewed: list[tuple[str, str, bool]] = []
        unreviewed: list[tuple[str, str, bool]] = []
        unreviewed_sources: dict[str, str] = {}
        for name in unique_names:
            result = classify_commodity(name)
            (reviewed if result.is_reviewed else unreviewed).append((name, result.category, result.storable))
            if result.requires_review:
                unreviewed_sources[name] = result.source
        if unreviewed:
            # Warn once per loader instance and name, not once per chunk.
            newly_seen = [r for r in unreviewed if r[0] not in self.commodities_requiring_review]
            self.commodities_requiring_review.update(r[0] for r in unreviewed)
            if newly_seen:
                logger.warning(
                    "Commodities without a reviewed classification (inserted if new, existing rows untouched): %s",
                    ", ".join(
                        f"{n} -> {c}/{'storable' if s else 'perishable'} [{unreviewed_sources[n]}]"
                        for n, c, s in newly_seen
                    ),
                )

        on_conflict = {
            "update": "ON CONFLICT (commodity_name) DO UPDATE SET category = excluded.category, storable = excluded.storable",
            "keep": "ON CONFLICT (commodity_name) DO NOTHING",
        }
        for rows, conflict_key in ((reviewed, "update"), (unreviewed, "keep")):
            if not rows:
                continue
            if self.db.backend == "postgresql":
                self._pg_execute_values(
                    conn,
                    f"INSERT INTO {t_comm} (commodity_name, category, storable) VALUES %s {on_conflict[conflict_key]}",
                    rows,
                    template="(%s, %s, %s)",
                )
            else:
                self._sqlite_execute_chunks(
                    conn,
                    f"""
                    INSERT INTO {t_comm} (commodity_name, category, storable)
                    VALUES (:commodity_name, :category, :storable)
                    {on_conflict[conflict_key]}
                    """,
                    [{"commodity_name": n, "category": c, "storable": int(s)} for n, c, s in rows],
                )
        existing = conn.execute(text(f"SELECT commodity_id, commodity_name FROM {t_comm}")).all()
        return {row[1]: int(row[0]) for row in existing}

    def _upsert_markets(self, conn: Any, markets: list[tuple[str, str, str]]) -> dict[tuple[str, str, str], int]:
        t_mkt = self.db.table_ref("warehouse", "dim_market")
        unique_mkts = sorted({(m, d or "Unknown", s) for m, d, s in markets if m and s})
        if unique_mkts:
            if self.db.backend == "postgresql":
                self._pg_execute_values(
                    conn,
                    f"""
                    INSERT INTO {t_mkt} (market_name, district, state)
                    VALUES %s
                    ON CONFLICT (market_name, district, state) DO NOTHING
                    """,
                    unique_mkts,
                    template="(%s, %s, %s)",
                )
            else:
                rows = [{"market_name": m, "district": d, "state": s} for m, d, s in unique_mkts]
                self._sqlite_execute_chunks(
                    conn,
                    f"""
                    INSERT INTO {t_mkt} (market_name, district, state)
                    VALUES (:market_name, :district, :state)
                    ON CONFLICT (market_name, district, state) DO NOTHING
                    """,
                    rows,
                )
        existing = conn.execute(text(f"SELECT market_id, market_name, district, state FROM {t_mkt}")).all()
        return {(row[1], row[2], row[3]): int(row[0]) for row in existing}

    def _upsert_crops(self, conn: Any, crops: list[tuple[str, str]]) -> dict[str, int]:
        t_crop = self.db.table_ref("warehouse", "dim_crop")
        unique_crops: dict[str, str] = {}
        for cname, ctype in crops:
            if cname:
                unique_crops[cname] = ctype or "Other"
        if unique_crops:
            if self.db.backend == "postgresql":
                rows = sorted(unique_crops.items())
                self._pg_execute_values(
                    conn,
                    f"""
                    INSERT INTO {t_crop} (crop_name, crop_type)
                    VALUES %s
                    ON CONFLICT (crop_name) DO UPDATE
                    SET crop_type = COALESCE(excluded.crop_type, {t_crop}.crop_type)
                    """,
                    rows,
                    template="(%s, %s)",
                )
            else:
                rows_dict = [{"crop_name": k, "crop_type": v} for k, v in sorted(unique_crops.items())]
                self._sqlite_execute_chunks(
                    conn,
                    f"""
                    INSERT INTO {t_crop} (crop_name, crop_type)
                    VALUES (:crop_name, :crop_type)
                    ON CONFLICT (crop_name) DO UPDATE
                    SET crop_type = COALESCE(excluded.crop_type, {t_crop}.crop_type)
                    """,
                    rows_dict,
                )
        existing = conn.execute(text(f"SELECT crop_id, crop_name FROM {t_crop}")).all()
        return {row[1]: int(row[0]) for row in existing}

    # ------------------------------------------------------------------
    # Shared load helpers
    # ------------------------------------------------------------------
    def _begin_run(self, run_id: int | None, stage: str, source_name: str, digest: str) -> tuple[int, bool]:
        owns_run = run_id is None
        if owns_run:
            run_id = start_pipeline_run(
                self.db,
                stage=stage,
                source_name=source_name,
                metadata={"batch_id": digest, "input_digest": digest},
            )
        assert run_id is not None
        return run_id, owns_run

    def _commit_rejects(self, run_id: int, rejects: list[dict[str, Any]]) -> None:
        """Rejects are written in their own transaction so they survive a later chunk failure."""
        if rejects:
            with self.db.transaction() as conn:
                self._log_rejects(conn, run_id, rejects)

    # ------------------------------------------------------------------
    # Price Pipeline Load
    # ------------------------------------------------------------------
    def load_prices(
        self,
        raw_records: list[dict[str, Any]],
        source_name: str = "agmarknet_api",
        raw_file_path: str | None = None,
        run_id: int | None = None,
        chunk_size: int = DEFAULT_LOAD_CHUNK_SIZE,
        allow_source_override: bool = False,
    ) -> dict[str, Any]:
        """Clean, validate and load price records in idempotent chunked transactions.

        * Staging holds one row per natural key (replaced, not appended, on a re-run).
        * DQ rejects are recorded once per distinct rejected record.
        * Facts keep their upsert semantics, except that a fact owned by a *different* source is
          never overwritten: it is reported in ``cross_source_conflicts`` and logged as a reject
          unless ``allow_source_override`` is set.
        """
        digest = _input_digest(raw_records)
        run_id, owns_run = self._begin_run(run_id, "etl_load_prices", source_name, digest)
        chunks_committed = 0
        try:
            cleaned = clean_price_records(raw_records, source_name=source_name)
            validation: ValidationResult = validate_price_records(cleaned, source_name=source_name)
            valid_df = validation.valid_df
            self._commit_rejects(run_id, validation.rejects)

            conflict_rejects_total = 0
            conflict_rows_total = 0
            for idx, chunk in enumerate(_iter_chunks(valid_df, chunk_size)):
                with self.db.transaction() as conn:
                    conflict_rows, conflict_rejects = self._load_price_chunk(
                        conn, chunk, f"{digest}-{idx:04d}", raw_file_path, allow_source_override
                    )
                    self._log_rejects(conn, run_id, conflict_rejects)
                conflict_rows_total += conflict_rows
                conflict_rejects_total += len(conflict_rejects)
                chunks_committed += 1

            loaded_count = len(valid_df) - conflict_rows_total
            recon = build_reconciliation_summary(
                raw_count=len(raw_records),
                valid_count=len(valid_df),
                rejected_count=len(validation.rejects),
                loaded_count=loaded_count,
                source_conflict_count=conflict_rows_total,
            )
            status = "success" if (loaded_count > 0 or len(raw_records) == 0) and conflict_rows_total == 0 else "partial_success"
            extra = {
                "outliers_flagged": validation.stats.get("outliers_flagged", 0),
                "chunks": chunks_committed,
                "chunk_size": chunk_size,
                "input_digest": digest,
                "cross_source_conflicts": conflict_rows_total,
                "loaded_states": sorted({s for s in valid_df["state"] if s}) if not valid_df.empty else [],
            }
            if owns_run:
                finish_pipeline_run(
                    self.db,
                    run_id=run_id,
                    status=status,
                    rows_fetched=len(raw_records),
                    rows_loaded=loaded_count,
                    rows_rejected=len(validation.rejects),
                    metadata={"reconciliation": recon, **{k: v for k, v in extra.items() if k != "loaded_states"}},
                )
            return {
                "run_id": run_id,
                "status": status,
                **recon,
                **extra,
                "commodities_requiring_review": sorted(self.commodities_requiring_review),
            }
        except Exception as exc:
            logger.exception("Failed loading price batch %s after %d committed chunk(s): %s", digest, chunks_committed, exc)
            if owns_run:
                finish_pipeline_run(
                    self.db,
                    run_id=run_id,
                    status="failed",
                    rows_fetched=len(raw_records),
                    error_message=str(exc),
                    metadata={"chunks_committed": chunks_committed, "input_digest": digest},
                )
            raise

    def _load_price_chunk(
        self,
        conn: Any,
        chunk: pd.DataFrame,
        batch_id: str,
        raw_file_path: str | None,
        allow_source_override: bool,
    ) -> tuple[int, list[dict[str, Any]]]:
        t_stg = self.db.table_ref("staging", "stg_agmarknet_prices")
        t_fact = self.db.table_ref("warehouse", "fact_price_daily")
        records = chunk.to_dict(orient="records")
        pg = self.db.backend == "postgresql"

        # --- staging: replace by natural key, then insert this chunk ---
        stg_key_cols = ("source_name", "arrival_date", "state", "district", "market", "commodity", "variety", "grade")
        self._delete_staging_by_keys(
            conn, t_stg, stg_key_cols,
            ("text", "date", "text", "text", "text", "text", "text", "text"),
            [tuple(row[c] for c in stg_key_cols) for row in records],
        )
        if pg:
            stg_tuples = [
                (
                    batch_id, row["source_name"], raw_file_path, row["arrival_date"],
                    row["state"], row["district"], row["market"], row["commodity"],
                    row["variety"], row["grade"],
                    float(row["min_price"]), float(row["max_price"]), float(row["modal_price"]),
                    bool(row["outlier_flag"]),
                )
                for row in records
            ]
            self._pg_execute_values(
                conn,
                f"""
                INSERT INTO {t_stg} (
                    batch_id, source_name, raw_file_path, arrival_date,
                    state, district, market, commodity, variety, grade,
                    min_price, max_price, modal_price, outlier_flag
                ) VALUES %s
                """,
                stg_tuples,
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            )
        else:
            stg_rows = [
                {
                    "batch_id": batch_id,
                    "source_name": row["source_name"],
                    "raw_file_path": raw_file_path,
                    "arrival_date": row["arrival_date"].isoformat(),
                    "state": row["state"],
                    "district": row["district"],
                    "market": row["market"],
                    "commodity": row["commodity"],
                    "variety": row["variety"],
                    "grade": row["grade"],
                    "min_price": float(row["min_price"]),
                    "max_price": float(row["max_price"]),
                    "modal_price": float(row["modal_price"]),
                    "outlier_flag": int(bool(row["outlier_flag"])),
                }
                for row in records
            ]
            self._sqlite_execute_chunks(
                conn,
                f"""
                INSERT INTO {t_stg} (
                    batch_id, source_name, raw_file_path, arrival_date,
                    state, district, market, commodity, variety, grade,
                    min_price, max_price, modal_price, outlier_flag
                ) VALUES (
                    :batch_id, :source_name, :raw_file_path, :arrival_date,
                    :state, :district, :market, :commodity, :variety, :grade,
                    :min_price, :max_price, :modal_price, :outlier_flag
                )
                """,
                stg_rows,
            )

        # --- dimensions ---
        self._upsert_dim_dates(conn, build_date_dim_rows(list(chunk["arrival_date"])))
        comm_map = self._upsert_commodities(conn, list(chunk["commodity"]))
        mkt_map = self._upsert_markets(
            conn, list(zip(chunk["market"], chunk["district"], chunk["state"]))
        )

        # --- facts ---
        dedup_fact: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in records:
            date_id = int(row["arrival_date"].strftime("%Y%m%d"))
            mkt_id = mkt_map[(row["market"], row["district"] or "Unknown", row["state"])]
            comm_id = comm_map[row["commodity"]]
            pk = (date_id, mkt_id, comm_id, row["variety"], row["grade"])
            previous = dedup_fact.get(pk)
            dedup_fact[pk] = {
                "date_id": date_id,
                "market_id": mkt_id,
                "commodity_id": comm_id,
                "variety": row["variety"],
                "grade": row["grade"],
                "min_price": float(row["min_price"]),
                "max_price": float(row["max_price"]),
                "modal_price": float(row["modal_price"]),
                "outlier_flag": bool(row["outlier_flag"]) if pg else int(bool(row["outlier_flag"])),
                "source_name": row["source_name"],
                "_n": (previous["_n"] if previous else 0) + 1,
                "_record": {
                    k: row[k] for k in (
                        "arrival_date", "state", "district", "market", "commodity",
                        "variety", "grade", "min_price", "max_price", "modal_price",
                    )
                },
            }

        owners = self._existing_fact_sources(
            conn,
            f"SELECT date_id, market_id, commodity_id, variety, grade, source_name FROM {t_fact} WHERE date_id IN :ids",
            [pk[0] for pk in dedup_fact],
        )
        kept, conflicts, conflict_rows = self._split_source_conflicts(dedup_fact, owners, allow_source_override)
        if conflicts:
            logger.warning(
                "%d fact key(s) already owned by another source were NOT overwritten (logged as %s).",
                len(conflicts), SOURCE_CONFLICT_STAGE,
            )

        guard = "" if allow_source_override else f"WHERE {t_fact}.source_name = excluded.source_name"
        if pg:
            fact_tuples = [
                (
                    r["date_id"], r["market_id"], r["commodity_id"], r["variety"], r["grade"],
                    r["min_price"], r["max_price"], r["modal_price"], r["outlier_flag"], r["source_name"],
                )
                for r in kept.values()
            ]
            self._pg_execute_values(
                conn,
                f"""
                INSERT INTO {t_fact} (
                    date_id, market_id, commodity_id, variety, grade,
                    min_price, max_price, modal_price, outlier_flag, source_name
                ) VALUES %s
                ON CONFLICT (date_id, market_id, commodity_id, variety, grade) DO UPDATE
                SET min_price = excluded.min_price,
                    max_price = excluded.max_price,
                    modal_price = excluded.modal_price,
                    outlier_flag = excluded.outlier_flag,
                    source_name = excluded.source_name
                {guard}
                """,
                fact_tuples,
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            )
        else:
            fact_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in kept.values()]
            self._sqlite_execute_chunks(
                conn,
                f"""
                INSERT INTO {t_fact} (
                    date_id, market_id, commodity_id, variety, grade,
                    min_price, max_price, modal_price, outlier_flag, source_name
                ) VALUES (
                    :date_id, :market_id, :commodity_id, :variety, :grade,
                    :min_price, :max_price, :modal_price, :outlier_flag, :source_name
                )
                ON CONFLICT (date_id, market_id, commodity_id, variety, grade) DO UPDATE
                SET min_price = excluded.min_price,
                    max_price = excluded.max_price,
                    modal_price = excluded.modal_price,
                    outlier_flag = excluded.outlier_flag,
                    source_name = excluded.source_name
                {guard}
                """,
                fact_rows,
            )
        return conflict_rows, conflicts

    # ------------------------------------------------------------------
    # Arrivals Pipeline Load (CEDA actual arrival tonnage ONLY)
    # ------------------------------------------------------------------
    def load_arrivals(
        self,
        raw_records: list[dict[str, Any]],
        source_name: str = "ceda_archive",
        raw_file_path: str | None = None,
        run_id: int | None = None,
        chunk_size: int = DEFAULT_LOAD_CHUNK_SIZE,
        allow_source_override: bool = False,
    ) -> dict[str, Any]:
        digest = _input_digest(raw_records)
        run_id, owns_run = self._begin_run(run_id, "etl_load_arrivals", source_name, digest)
        chunks_committed = 0
        try:
            cleaned = clean_arrival_records(raw_records, source_name=source_name)
            validation = validate_arrival_records(cleaned, source_name=source_name)
            valid_df = validation.valid_df
            self._commit_rejects(run_id, validation.rejects)

            conflict_rows_total = 0
            for idx, chunk in enumerate(_iter_chunks(valid_df, chunk_size)):
                with self.db.transaction() as conn:
                    conflict_rows, conflict_rejects = self._load_arrival_chunk(
                        conn, chunk, f"{digest}-{idx:04d}", raw_file_path, allow_source_override
                    )
                    self._log_rejects(conn, run_id, conflict_rejects)
                conflict_rows_total += conflict_rows
                chunks_committed += 1

            loaded_count = len(valid_df) - conflict_rows_total
            recon = build_reconciliation_summary(
                raw_count=len(raw_records),
                valid_count=len(valid_df),
                rejected_count=len(validation.rejects),
                loaded_count=loaded_count,
                aggregated_count=validation.stats.get("duplicates_summed", 0),
                source_conflict_count=conflict_rows_total,
            )
            status = "partial_success" if conflict_rows_total else "success"
            extra = {
                "chunks": chunks_committed,
                "chunk_size": chunk_size,
                "input_digest": digest,
                "cross_source_conflicts": conflict_rows_total,
            }
            if owns_run:
                finish_pipeline_run(
                    self.db,
                    run_id=run_id,
                    status=status,
                    rows_fetched=len(raw_records),
                    rows_loaded=loaded_count,
                    rows_rejected=len(validation.rejects),
                    metadata={"reconciliation": recon, **extra},
                )
            return {
                "run_id": run_id,
                "status": status,
                **recon,
                **extra,
                "commodities_requiring_review": sorted(self.commodities_requiring_review),
            }
        except Exception as exc:
            logger.exception("Failed loading arrivals batch %s after %d committed chunk(s): %s", digest, chunks_committed, exc)
            if owns_run:
                finish_pipeline_run(
                    self.db, run_id=run_id, status="failed", rows_fetched=len(raw_records),
                    error_message=str(exc),
                    metadata={"chunks_committed": chunks_committed, "input_digest": digest},
                )
            raise

    def _load_arrival_chunk(
        self,
        conn: Any,
        chunk: pd.DataFrame,
        batch_id: str,
        raw_file_path: str | None,
        allow_source_override: bool,
    ) -> tuple[int, list[dict[str, Any]]]:
        t_stg = self.db.table_ref("staging", "stg_ceda_arrivals")
        t_fact = self.db.table_ref("warehouse", "fact_arrivals_daily")
        records = chunk.to_dict(orient="records")
        pg = self.db.backend == "postgresql"

        stg_key_cols = ("source_name", "arrival_date", "state", "district", "market", "commodity")
        self._delete_staging_by_keys(
            conn, t_stg, stg_key_cols,
            ("text", "date", "text", "text", "text", "text"),
            [tuple(row[c] for c in stg_key_cols) for row in records],
        )
        if pg:
            stg_tuples = [
                (
                    batch_id, row["source_name"], raw_file_path, row["arrival_date"],
                    row["state"], row["district"], row["market"], row["commodity"],
                    float(row["quantity_tonnes"]),
                )
                for row in records
            ]
            self._pg_execute_values(
                conn,
                f"""
                INSERT INTO {t_stg} (
                    batch_id, source_name, raw_file_path, arrival_date,
                    state, district, market, commodity, quantity_tonnes
                ) VALUES %s
                """,
                stg_tuples,
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            )
        else:
            stg_rows = [
                {
                    "batch_id": batch_id,
                    "source_name": row["source_name"],
                    "raw_file_path": raw_file_path,
                    "arrival_date": row["arrival_date"].isoformat(),
                    "state": row["state"],
                    "district": row["district"],
                    "market": row["market"],
                    "commodity": row["commodity"],
                    "quantity_tonnes": float(row["quantity_tonnes"]),
                }
                for row in records
            ]
            self._sqlite_execute_chunks(
                conn,
                f"""
                INSERT INTO {t_stg} (
                    batch_id, source_name, raw_file_path, arrival_date,
                    state, district, market, commodity, quantity_tonnes
                ) VALUES (
                    :batch_id, :source_name, :raw_file_path, :arrival_date,
                    :state, :district, :market, :commodity, :quantity_tonnes
                )
                """,
                stg_rows,
            )

        self._upsert_dim_dates(conn, build_date_dim_rows(list(chunk["arrival_date"])))
        comm_map = self._upsert_commodities(conn, list(chunk["commodity"]))
        mkt_map = self._upsert_markets(
            conn, list(zip(chunk["market"], chunk["district"], chunk["state"]))
        )

        dedup_fact: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in records:
            date_id = int(row["arrival_date"].strftime("%Y%m%d"))
            mkt_id = mkt_map[(row["market"], row["district"] or "Unknown", row["state"])]
            comm_id = comm_map[row["commodity"]]
            pk = (date_id, mkt_id, comm_id)
            previous = dedup_fact.get(pk)
            dedup_fact[pk] = {
                "date_id": date_id,
                "market_id": mkt_id,
                "commodity_id": comm_id,
                "quantity_tonnes": float(row["quantity_tonnes"]),
                "source_name": row["source_name"],
                "_n": (previous["_n"] if previous else 0) + 1,
                "_record": {
                    k: row[k] for k in ("arrival_date", "state", "district", "market", "commodity", "quantity_tonnes")
                },
            }

        owners = self._existing_fact_sources(
            conn,
            f"SELECT date_id, market_id, commodity_id, source_name FROM {t_fact} WHERE date_id IN :ids",
            [pk[0] for pk in dedup_fact],
        )
        kept, conflicts, conflict_rows = self._split_source_conflicts(dedup_fact, owners, allow_source_override)
        guard = "" if allow_source_override else f"WHERE {t_fact}.source_name = excluded.source_name"
        if pg:
            fact_tuples = [
                (r["date_id"], r["market_id"], r["commodity_id"], r["quantity_tonnes"], r["source_name"])
                for r in kept.values()
            ]
            self._pg_execute_values(
                conn,
                f"""
                INSERT INTO {t_fact} (date_id, market_id, commodity_id, quantity_tonnes, source_name)
                VALUES %s
                ON CONFLICT (date_id, market_id, commodity_id) DO UPDATE
                SET quantity_tonnes = excluded.quantity_tonnes,
                    source_name = excluded.source_name
                {guard}
                """,
                fact_tuples,
                template="(%s, %s, %s, %s, %s)",
            )
        else:
            fact_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in kept.values()]
            self._sqlite_execute_chunks(
                conn,
                f"""
                INSERT INTO {t_fact} (date_id, market_id, commodity_id, quantity_tonnes, source_name)
                VALUES (:date_id, :market_id, :commodity_id, :quantity_tonnes, :source_name)
                ON CONFLICT (date_id, market_id, commodity_id) DO UPDATE
                SET quantity_tonnes = excluded.quantity_tonnes,
                    source_name = excluded.source_name
                {guard}
                """,
                fact_rows,
            )
        return conflict_rows, conflicts

    # ------------------------------------------------------------------
    # APY Crop Production Pipeline Load
    # ------------------------------------------------------------------
    def load_apy_production(
        self,
        raw_records: list[dict[str, Any]],
        source_name: str = "des_apy",
        raw_file_path: str | None = None,
        run_id: int | None = None,
        chunk_size: int = DEFAULT_LOAD_CHUNK_SIZE,
        allow_source_override: bool = False,
    ) -> dict[str, Any]:
        digest = _input_digest(raw_records)
        run_id, owns_run = self._begin_run(run_id, "etl_load_apy", source_name, digest)
        chunks_committed = 0
        try:
            cleaned = clean_apy_records(raw_records, source_name=source_name)
            validation = validate_apy_records(cleaned, source_name=source_name)
            valid_df = validation.valid_df
            self._commit_rejects(run_id, validation.rejects)

            conflict_rows_total = 0
            for idx, chunk in enumerate(_iter_chunks(valid_df, chunk_size)):
                with self.db.transaction() as conn:
                    conflict_rows, conflict_rejects = self._load_apy_chunk(
                        conn, chunk, f"{digest}-{idx:04d}", raw_file_path, allow_source_override
                    )
                    self._log_rejects(conn, run_id, conflict_rejects)
                conflict_rows_total += conflict_rows
                chunks_committed += 1

            loaded_count = len(valid_df) - conflict_rows_total
            recon = build_reconciliation_summary(
                raw_count=len(raw_records),
                valid_count=len(valid_df),
                rejected_count=len(validation.rejects),
                loaded_count=loaded_count,
                source_conflict_count=conflict_rows_total,
            )
            status = "partial_success" if conflict_rows_total else "success"
            extra = {
                "chunks": chunks_committed,
                "chunk_size": chunk_size,
                "input_digest": digest,
                "cross_source_conflicts": conflict_rows_total,
            }
            if owns_run:
                finish_pipeline_run(
                    self.db,
                    run_id=run_id,
                    status=status,
                    rows_fetched=len(raw_records),
                    rows_loaded=loaded_count,
                    rows_rejected=len(validation.rejects),
                    metadata={"reconciliation": recon, **extra},
                )
            return {"run_id": run_id, "status": status, **recon, **extra}
        except Exception as exc:
            logger.exception("Failed loading APY batch %s after %d committed chunk(s): %s", digest, chunks_committed, exc)
            if owns_run:
                finish_pipeline_run(
                    self.db, run_id=run_id, status="failed", rows_fetched=len(raw_records),
                    error_message=str(exc),
                    metadata={"chunks_committed": chunks_committed, "input_digest": digest},
                )
            raise

    def _load_apy_chunk(
        self,
        conn: Any,
        chunk: pd.DataFrame,
        batch_id: str,
        raw_file_path: str | None,
        allow_source_override: bool,
    ) -> tuple[int, list[dict[str, Any]]]:
        t_stg = self.db.table_ref("staging", "stg_apy_production")
        t_fact = self.db.table_ref("warehouse", "fact_crop_production")
        records = chunk.to_dict(orient="records")
        pg = self.db.backend == "postgresql"

        def _num(v: Any) -> float | None:
            return None if pd.isna(v) else float(v)

        stg_key_cols = ("source_name", "crop_year", "season", "state", "district", "crop_name")
        self._delete_staging_by_keys(
            conn, t_stg, stg_key_cols,
            ("text", "int", "text", "text", "text", "text"),
            [
                (row["source_name"], int(row["crop_year"]), row["season"], row["state"], row["district"], row["crop_name"])
                for row in records
            ],
        )
        if pg:
            stg_tuples = [
                (
                    batch_id, row["source_name"], raw_file_path, int(row["crop_year"]),
                    row["season"], row["state"], row["district"], row["crop_name"], row["crop_type"],
                    _num(row["area_ha"]), _num(row["production_t"]), _num(row["yield_t_ha"]),
                )
                for row in records
            ]
            self._pg_execute_values(
                conn,
                f"""
                INSERT INTO {t_stg} (
                    batch_id, source_name, raw_file_path, crop_year,
                    season, state, district, crop_name, crop_type,
                    area_ha, production_t, yield_t_ha
                ) VALUES %s
                """,
                stg_tuples,
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            )
        else:
            stg_rows = [
                {
                    "batch_id": batch_id,
                    "source_name": row["source_name"],
                    "raw_file_path": raw_file_path,
                    "crop_year": int(row["crop_year"]),
                    "season": row["season"],
                    "state": row["state"],
                    "district": row["district"],
                    "crop_name": row["crop_name"],
                    "crop_type": row["crop_type"],
                    "area_ha": _num(row["area_ha"]),
                    "production_t": _num(row["production_t"]),
                    "yield_t_ha": _num(row["yield_t_ha"]),
                }
                for row in records
            ]
            self._sqlite_execute_chunks(
                conn,
                f"""
                INSERT INTO {t_stg} (
                    batch_id, source_name, raw_file_path, crop_year,
                    season, state, district, crop_name, crop_type,
                    area_ha, production_t, yield_t_ha
                ) VALUES (
                    :batch_id, :source_name, :raw_file_path, :crop_year,
                    :season, :state, :district, :crop_name, :crop_type,
                    :area_ha, :production_t, :yield_t_ha
                )
                """,
                stg_rows,
            )

        crop_map = self._upsert_crops(conn, list(zip(chunk["crop_name"], chunk["crop_type"])))
        dedup_fact: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in records:
            crop_id = crop_map[row["crop_name"]]
            pk = (int(row["crop_year"]), row["season"], row["state"], row["district"], crop_id)
            previous = dedup_fact.get(pk)
            dedup_fact[pk] = {
                "crop_year": int(row["crop_year"]),
                "season": row["season"],
                "state": row["state"],
                "district": row["district"],
                "crop_id": crop_id,
                "area_ha": _num(row["area_ha"]),
                "production_t": _num(row["production_t"]),
                "yield_t_ha": _num(row["yield_t_ha"]),
                "source_name": row["source_name"],
                "_n": (previous["_n"] if previous else 0) + 1,
                "_record": {
                    k: (None if (isinstance(row[k], float) and pd.isna(row[k])) else row[k])
                    for k in ("crop_year", "season", "state", "district", "crop_name", "area_ha", "production_t", "yield_t_ha")
                },
            }

        owners = self._existing_fact_sources(
            conn,
            f"SELECT crop_year, season, state, district, crop_id, source_name FROM {t_fact} WHERE crop_year IN :ids",
            [pk[0] for pk in dedup_fact],
        )
        kept, conflicts, conflict_rows = self._split_source_conflicts(dedup_fact, owners, allow_source_override)
        guard = "" if allow_source_override else f"WHERE {t_fact}.source_name = excluded.source_name"
        if pg:
            fact_tuples = [
                (
                    r["crop_year"], r["season"], r["state"], r["district"], r["crop_id"],
                    r["area_ha"], r["production_t"], r["yield_t_ha"], r["source_name"],
                )
                for r in kept.values()
            ]
            self._pg_execute_values(
                conn,
                f"""
                INSERT INTO {t_fact} (
                    crop_year, season, state, district, crop_id,
                    area_ha, production_t, yield_t_ha, source_name
                ) VALUES %s
                ON CONFLICT (crop_year, season, state, district, crop_id) DO UPDATE
                SET area_ha = excluded.area_ha,
                    production_t = excluded.production_t,
                    yield_t_ha = excluded.yield_t_ha,
                    source_name = excluded.source_name
                {guard}
                """,
                fact_tuples,
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            )
        else:
            fact_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in kept.values()]
            self._sqlite_execute_chunks(
                conn,
                f"""
                INSERT INTO {t_fact} (
                    crop_year, season, state, district, crop_id,
                    area_ha, production_t, yield_t_ha, source_name
                ) VALUES (
                    :crop_year, :season, :state, :district, :crop_id,
                    :area_ha, :production_t, :yield_t_ha, :source_name
                )
                ON CONFLICT (crop_year, season, state, district, crop_id) DO UPDATE
                SET area_ha = excluded.area_ha,
                    production_t = excluded.production_t,
                    yield_t_ha = excluded.yield_t_ha,
                    source_name = excluded.source_name
                {guard}
                """,
                fact_rows,
            )
        return conflict_rows, conflicts
