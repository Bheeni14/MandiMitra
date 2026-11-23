"""Idempotent SQL migration discovery, checksum verification, and version tracking.

PostgreSQL migrations are only ever executed through ``apply_migrations`` /
``rollback_migration`` (driven by ``scripts/migrate.py``); application start-up only verifies.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from config import DATABASE_DIR
from database.models import build_metadata

if TYPE_CHECKING:
    from database.connection import DatabaseManager

logger = logging.getLogger(__name__)

DEFAULT_MIGRATIONS_DIR = DATABASE_DIR / "migrations"
DEFAULT_ROLLBACKS_DIR = DATABASE_DIR / "rollbacks"
BASELINE_VERSION = "001_initial_schema"
_SCHEMA_PREFIX_RE = re.compile(r"\b(?:staging|warehouse|operational|analytics)\.")

# Transaction-scoped advisory lock serialising every migration/rollback across processes.
MIGRATION_ADVISORY_LOCK_KEY = 4_729_301_118_003
DEFAULT_LOCK_TIMEOUT = "5s"
DEFAULT_STATEMENT_TIMEOUT = "15min"
_TIMEOUT_RE = re.compile(r"^\d+(ms|s|min|h)?$")

# Objects created by 001_initial_schema that must exist before a pre-tracking database is baselined.
REQUIRED_BASELINE_TABLES: tuple[tuple[str, str], ...] = (
    ("staging", "stg_agmarknet_prices"),
    ("staging", "stg_ceda_arrivals"),
    ("staging", "stg_apy_production"),
    ("operational", "pipeline_run_log"),
    ("operational", "dq_rejects"),
    ("warehouse", "dim_date"),
    ("warehouse", "dim_commodity"),
    ("warehouse", "dim_market"),
    ("warehouse", "dim_crop"),
    ("warehouse", "map_commodity_crop"),
    ("warehouse", "fact_price_daily"),
    ("warehouse", "fact_arrivals_daily"),
    ("warehouse", "fact_crop_production"),
    ("analytics", "market_signals"),
    ("analytics", "briefings"),
)
REQUIRED_BASELINE_VIEWS: tuple[tuple[str, str], ...] = (
    ("analytics", "v_q1_price_volatility"),
    ("analytics", "v_q2_state_price_gaps"),
    ("analytics", "v_q3_mom_trends"),
    ("analytics", "v_q4_arrivals_vs_price"),
    ("analytics", "v_q5_seasonal_price_index"),
    ("analytics", "v_q6_best_markets"),
    ("analytics", "v_q7_production_vs_price"),
    ("analytics", "v_q7_production_price_corr"),
)
REQUIRED_BASELINE_COLUMNS: dict[tuple[str, str], tuple[str, ...]] = {
    ("warehouse", "dim_commodity"): ("commodity_id", "commodity_name", "category", "storable"),
    ("warehouse", "fact_price_daily"): (
        "date_id", "market_id", "commodity_id", "variety", "grade", "min_price", "max_price",
        "modal_price", "outlier_flag", "source_name", "ingested_at",
    ),
    ("warehouse", "fact_arrivals_daily"): (
        "date_id", "market_id", "commodity_id", "quantity_tonnes", "source_name", "ingested_at",
    ),
    ("analytics", "market_signals"): ("commodity_id", "market_id", "signal_date"),
}


class MigrationError(RuntimeError):
    """Raised when migrations cannot be planned, applied or rolled back safely."""


class PendingMigrationsError(MigrationError):
    """Raised at start-up when a PostgreSQL schema is not at the latest migration."""


@dataclass(frozen=True)
class MigrationFile:
    version: str
    path: Path
    checksum: str
    description: str
    sql_text: str


def _compute_checksum(sql_text: str) -> str:
    normalized = sql_text.replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _extract_description(version: str, sql_text: str) -> str:
    for raw_line in sql_text.splitlines()[:10]:
        line = raw_line.strip()
        if not line.startswith("--"):
            if line:
                break
            continue
        cleaned = line.lstrip("-").strip()
        if not cleaned or set(cleaned) <= {"=", "-", " "}:
            continue
        cleaned = re.sub(r"^Migration\s+\d+\s*:\s*", "", cleaned, flags=re.IGNORECASE)
        if cleaned:
            return cleaned
    parts = version.split("_", 1)
    slug = parts[1] if len(parts) > 1 else version
    return slug.replace("_", " ").strip().title()


def discover_migrations(migrations_dir: Path | None = None) -> list[MigrationFile]:
    target_dir = migrations_dir or DEFAULT_MIGRATIONS_DIR
    if not target_dir.exists():
        return []
    files = sorted(p for p in target_dir.glob("*.sql") if p.is_file())
    migrations: list[MigrationFile] = []
    for path in files:
        sql_text = path.read_text(encoding="utf-8")
        migrations.append(
            MigrationFile(
                version=path.stem,
                path=path,
                checksum=_compute_checksum(sql_text),
                description=_extract_description(path.stem, sql_text),
                sql_text=sql_text,
            )
        )
    return migrations


def _adapt_sql_for_sqlite(sql_text: str) -> str:
    adapted = _SCHEMA_PREFIX_RE.sub("", sql_text)
    adapted = re.sub(r"\bTIMESTAMPTZ\b", "TIMESTAMP", adapted, flags=re.IGNORECASE)
    adapted = re.sub(r"\bJSONB\b", "TEXT", adapted, flags=re.IGNORECASE)
    adapted = re.sub(r"\bBIGSERIAL\s+PRIMARY\s+KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT", adapted, flags=re.IGNORECASE)
    adapted = re.sub(r"\bSERIAL\s+PRIMARY\s+KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT", adapted, flags=re.IGNORECASE)
    return adapted


def _sqlite_begin_atomic(conn: Any) -> None:
    """Open the SQLite transaction explicitly before any DDL.

    Python's sqlite3 module (legacy transaction control) only issues an implicit BEGIN before DML,
    so leading CREATE TABLE statements would otherwise autocommit and survive a failed migration.
    """
    conn.exec_driver_sql("BEGIN")


def _split_sql_statements(sql_text: str) -> list[str]:
    """Split SQL text into executable statements, respecting quotes, comments, and $$ blocks."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql_text)
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    dollar_tag: str | None = None

    while i < n:
        ch = sql_text[i]
        nxt = sql_text[i + 1] if i + 1 < n else ""

        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue

        if dollar_tag is not None:
            if sql_text.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
            else:
                buf.append(ch)
                i += 1
            continue

        if in_single:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":
                    buf.append(nxt)
                    i += 2
                    continue
                in_single = False
            i += 1
            continue

        if in_double:
            buf.append(ch)
            if ch == '"':
                if nxt == '"':
                    buf.append(nxt)
                    i += 2
                    continue
                in_double = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            buf.append(ch)
            buf.append(nxt)
            in_line_comment = True
            i += 2
            continue

        if ch == "/" and nxt == "*":
            buf.append(ch)
            buf.append(nxt)
            in_block_comment = True
            i += 2
            continue

        if ch == "'":
            buf.append(ch)
            in_single = True
            i += 1
            continue

        if ch == '"':
            buf.append(ch)
            in_double = True
            i += 1
            continue

        if ch == "$":
            match = re.match(r"\$[A-Za-z_0-9]*\$", sql_text[i:])
            if match:
                dollar_tag = match.group(0)
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue

        if ch == ";":
            stmt = "".join(buf).strip()
            if _has_executable_sql(stmt):
                statements.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    trailing = "".join(buf).strip()
    if _has_executable_sql(trailing):
        statements.append(trailing)
    return statements


def _has_executable_sql(stmt: str) -> bool:
    for line in stmt.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return True
    return False


def _table_exists(conn: Any, backend: str, schema: str, table: str) -> bool:
    if backend == "sqlite":
        row = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name LIMIT 1"),
            {"name": table},
        ).first()
        return row is not None
    full_name = f"{schema}.{table}"
    return bool(conn.execute(text("SELECT to_regclass(:full_name) IS NOT NULL"), {"full_name": full_name}).scalar())


def _column_names(conn: Any, backend: str, schema: str, table: str) -> set[str]:
    if backend == "sqlite":
        return {str(r[1]) for r in conn.execute(text(f'PRAGMA table_info("{table}")')).all()}
    rows = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table"
        ),
        {"schema": schema, "table": table},
    ).all()
    return {str(r[0]) for r in rows}


def find_missing_baseline_objects(conn: Any, backend: str) -> list[str]:
    """List 001 objects (tables, views, key columns) absent from the connected database."""
    missing: list[str] = []
    for schema, table in REQUIRED_BASELINE_TABLES:
        if not _table_exists(conn, backend, schema, table):
            missing.append(f"table {schema}.{table}")
    if backend != "sqlite":
        # Demo SQLite builds tables from SQLAlchemy metadata and emulates the analytics views in Python.
        for schema, view in REQUIRED_BASELINE_VIEWS:
            if not _table_exists(conn, backend, schema, view):
                missing.append(f"view {schema}.{view}")
    for (schema, table), columns in REQUIRED_BASELINE_COLUMNS.items():
        if f"table {schema}.{table}" in missing:
            continue
        present = _column_names(conn, backend, schema, table)
        for col in columns:
            if col not in present:
                missing.append(f"column {schema}.{table}.{col}")
    return missing


def resolve_version(version: str, discovered: list[MigrationFile]) -> str:
    """Accept a full version (``003_commodity_...``) or its numeric prefix (``003``)."""
    by_version = {m.version for m in discovered}
    if version in by_version:
        return version
    matches = [m.version for m in discovered if m.version.split("_", 1)[0] == version]
    if len(matches) == 1:
        return matches[0]
    raise MigrationError(f"Unknown migration version {version!r}. Known versions: {sorted(by_version)}")


def _validate_timeout(value: str, name: str) -> str:
    cleaned = str(value).strip().replace(" ", "")
    if not _TIMEOUT_RE.match(cleaned):
        raise MigrationError(f"Invalid {name} {value!r}; use e.g. '500ms', '5s', '10min' or '1h'.")
    return cleaned


def get_migration_status(
    db: DatabaseManager,
    migrations_dir: Path | None = None,
) -> dict[str, Any]:
    """Read-only inspection of applied vs pending migrations and checksum integrity."""
    discovered = discover_migrations(migrations_dir)
    t_mig = db.table_ref("operational", "schema_migrations")

    with db.readonly_connection() as conn:
        tracking_exists = _table_exists(conn, db.backend, "operational", "schema_migrations")
        base_schema_exists = _table_exists(conn, db.backend, "warehouse", "fact_price_daily")
        baseline_missing: list[str] = []
        applied_rows: list[dict[str, Any]] = []
        if tracking_exists:
            rows = conn.execute(
                text(f"SELECT version, checksum, description, applied_at FROM {t_mig} ORDER BY version")
            ).mappings().all()
            applied_rows = [
                {
                    "version": str(r["version"]),
                    "checksum": str(r["checksum"]),
                    "description": str(r["description"] or ""),
                    "applied_at": str(r["applied_at"]),
                }
                for r in rows
            ]
        legacy_untracked = base_schema_exists and all(r["version"] != BASELINE_VERSION for r in applied_rows)
        if legacy_untracked:
            baseline_missing = find_missing_baseline_objects(conn, db.backend)

    applied_by_ver = {r["version"]: r for r in applied_rows}
    discovered_versions = {m.version for m in discovered}
    missing_files = [r["version"] for r in applied_rows if r["version"] not in discovered_versions]
    mismatched: list[dict[str, str]] = []
    pending: list[dict[str, str]] = []

    for mig in discovered:
        existing = applied_by_ver.get(mig.version)
        if existing is not None:
            if existing["checksum"] != mig.checksum:
                mismatched.append(
                    {
                        "version": mig.version,
                        "recorded_checksum": existing["checksum"],
                        "file_checksum": mig.checksum,
                    }
                )
        else:
            action = "baseline" if (legacy_untracked and mig.version == BASELINE_VERSION) else "apply"
            pending.append(
                {
                    "version": mig.version,
                    "description": mig.description,
                    "checksum": mig.checksum,
                    "action": action,
                    "path": str(mig.path),
                }
            )

    return {
        "backend": db.backend,
        "tracking_table_exists": tracking_exists,
        "base_schema_exists": base_schema_exists,
        "legacy_untracked_baseline": legacy_untracked,
        "baseline_missing_objects": baseline_missing,
        "discovered_count": len(discovered),
        "applied": applied_rows,
        "pending": pending,
        "mismatched": mismatched,
        "missing_files": missing_files,
    }


def assert_schema_current(db: DatabaseManager, migrations_dir: Path | None = None) -> dict[str, Any]:
    """Verify a PostgreSQL schema is fully migrated without changing it; raise otherwise."""
    status = get_migration_status(db, migrations_dir)
    problems: list[str] = []
    if status["mismatched"]:
        problems.append("checksum mismatch for " + ", ".join(m["version"] for m in status["mismatched"]))
    if status["pending"]:
        problems.append(
            "pending migrations " + ", ".join(f"{p['version']} ({p['action']})" for p in status["pending"])
        )
    if status["missing_files"]:
        logger.warning(
            "Applied migrations without a migration file on disk: %s", ", ".join(status["missing_files"])
        )
    if problems:
        raise PendingMigrationsError(
            f"{db.backend} schema is not at the latest migration: {'; '.join(problems)}. "
            "Migrations are never applied automatically in PostgreSQL mode. Review the plan with "
            "`python scripts/migrate.py --status` (or --dry-run) and apply it explicitly with "
            "`python scripts/migrate.py --apply` once approved."
        )
    return status


def _ensure_tracking_table_sqlite(conn: Any) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version      TEXT PRIMARY KEY,
                checksum     TEXT NOT NULL,
                description  TEXT NOT NULL DEFAULT '',
                applied_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )


def _record_migration_conn(conn: Any, table_ref: str, mig: MigrationFile, description_override: str | None = None) -> None:
    conn.execute(
        text(
            f"""
            INSERT INTO {table_ref} (version, checksum, description)
            VALUES (:version, :checksum, :description)
            ON CONFLICT (version) DO NOTHING
            """
        ),
        {
            "version": mig.version,
            "checksum": mig.checksum,
            "description": description_override or mig.description,
        },
    )


_PG_TRACKING_DDL = """
CREATE SCHEMA IF NOT EXISTS operational;
CREATE TABLE IF NOT EXISTS operational.schema_migrations (
    version      TEXT PRIMARY KEY,
    checksum     TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _pg_begin_locked(cur: Any, lock_timeout: str, statement_timeout: str) -> None:
    """Start the migration transaction: bounded waits first, then the cross-process lock."""
    cur.execute(f"SET LOCAL lock_timeout = '{lock_timeout}'")
    cur.execute(f"SET LOCAL statement_timeout = '{statement_timeout}'")
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_ADVISORY_LOCK_KEY,))


def _pg_recorded_checksum(cur: Any, version: str) -> str | None:
    cur.execute("SELECT checksum FROM operational.schema_migrations WHERE version = %s", (version,))
    row = cur.fetchone()
    return None if row is None else str(row[0])


def _pg_run_in_transaction(db: DatabaseManager, work: Any) -> Any:
    raw_conn = db.engine.raw_connection()
    try:
        with raw_conn.cursor() as cur:
            result = work(cur)
        raw_conn.commit()
        return result
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        raw_conn.close()


def _plan_pending(
    status: dict[str, Any],
    discovered: list[MigrationFile],
    target_version: str | None,
) -> tuple[list[dict[str, str]], str | None]:
    if target_version is None:
        return list(status["pending"]), None
    resolved = resolve_version(target_version, discovered)
    order = [m.version for m in discovered]
    limit = order.index(resolved)
    return [p for p in status["pending"] if order.index(p["version"]) <= limit], resolved


def apply_migrations(
    db: DatabaseManager,
    dry_run: bool = False,
    migrations_dir: Path | None = None,
    target_version: str | None = None,
    lock_timeout: str = DEFAULT_LOCK_TIMEOUT,
    statement_timeout: str = DEFAULT_STATEMENT_TIMEOUT,
) -> dict[str, Any]:
    """Baseline a verified pre-tracking schema and apply pending migrations in order.

    Each migration and its tracking row commit atomically in one transaction. On PostgreSQL every
    transaction takes ``pg_advisory_xact_lock`` and re-checks the tracking table under the lock, so
    concurrent runners cannot apply the same version twice.
    """
    lock_timeout = _validate_timeout(lock_timeout, "lock_timeout")
    statement_timeout = _validate_timeout(statement_timeout, "statement_timeout")
    discovered = discover_migrations(migrations_dir)
    mig_by_version = {m.version: m for m in discovered}
    status = get_migration_status(db, migrations_dir)

    if status["mismatched"]:
        mismatch_str = ", ".join(m["version"] for m in status["mismatched"])
        raise MigrationError(f"Migration checksum mismatch detected for applied versions: {mismatch_str}")
    if status["missing_files"]:
        logger.warning(
            "Applied migrations without a migration file on disk: %s", ", ".join(status["missing_files"])
        )

    plan, resolved_target = _plan_pending(status, discovered, target_version)
    base_result = {
        "backend": db.backend,
        "target_version": resolved_target,
        "already_applied": [a["version"] for a in status["applied"]],
        "missing_files": status["missing_files"],
    }
    if any(p["action"] == "baseline" for p in plan) and status["baseline_missing_objects"]:
        raise MigrationError(
            "Refusing to baseline 001_initial_schema: the existing database is missing "
            + ", ".join(status["baseline_missing_objects"])
        )
    if dry_run or not plan:
        return {**base_result, "dry_run": dry_run, "baselined": [], "applied_now": [], "pending_plan": plan}

    baselined: list[str] = []
    applied_now: list[str] = []
    if db.backend == "sqlite":
        _apply_sqlite(db, status, plan, mig_by_version, baselined, applied_now)
    else:
        _apply_postgresql(db, plan, mig_by_version, baselined, applied_now, lock_timeout, statement_timeout)

    remaining = [p for p in status["pending"] if p["version"] not in set(baselined) | set(applied_now)]
    return {
        **base_result,
        "dry_run": False,
        "baselined": baselined,
        "applied_now": applied_now,
        "pending_plan": [],
        "remaining_pending": [p["version"] for p in remaining],
    }


def _apply_sqlite(
    db: DatabaseManager,
    status: dict[str, Any],
    plan: list[dict[str, str]],
    mig_by_version: dict[str, MigrationFile],
    baselined: list[str],
    applied_now: list[str],
) -> None:
    t_mig = db.table_ref("operational", "schema_migrations")
    for item in plan:
        ver = item["version"]
        mig = mig_by_version[ver]
        if ver == BASELINE_VERSION:
            if not status["base_schema_exists"]:
                build_metadata(use_schemas=False).create_all(db.engine)
                logger.info("Initialized Demo SQLite tables from SQLAlchemy metadata")
                with db.transaction() as conn:
                    _ensure_tracking_table_sqlite(conn)
                    _record_migration_conn(conn, t_mig, mig)
                applied_now.append(ver)
            else:
                with db.transaction() as conn:
                    missing = find_missing_baseline_objects(conn, db.backend)
                    if missing:
                        raise MigrationError(
                            "Refusing to baseline 001_initial_schema: missing " + ", ".join(missing)
                        )
                    _ensure_tracking_table_sqlite(conn)
                    _record_migration_conn(
                        conn, t_mig, mig, description_override="Baselined existing schema (pre-tracking)"
                    )
                baselined.append(ver)
            continue

        stmts = _split_sql_statements(_adapt_sql_for_sqlite(mig.sql_text))
        with db.transaction() as conn:
            _sqlite_begin_atomic(conn)
            _ensure_tracking_table_sqlite(conn)
            for stmt in stmts:
                conn.execute(text(stmt))
            _record_migration_conn(conn, t_mig, mig)
        applied_now.append(ver)
        logger.info("Applied SQLite migration %s", ver)


def _apply_postgresql(
    db: DatabaseManager,
    plan: list[dict[str, str]],
    mig_by_version: dict[str, MigrationFile],
    baselined: list[str],
    applied_now: list[str],
    lock_timeout: str,
    statement_timeout: str,
) -> None:
    for item in plan:
        mig = mig_by_version[item["version"]]
        is_baseline = item["action"] == "baseline"

        def _work(cur: Any, mig: MigrationFile = mig, is_baseline: bool = is_baseline) -> str:
            _pg_begin_locked(cur, lock_timeout, statement_timeout)
            cur.execute(_PG_TRACKING_DDL)
            recorded = _pg_recorded_checksum(cur, mig.version)
            if recorded is not None:
                if recorded != mig.checksum:
                    raise MigrationError(f"Migration {mig.version} was recorded concurrently with a different checksum")
                return "skipped"
            description = mig.description
            if is_baseline:
                missing = _pg_missing_baseline_objects(cur)
                if missing:
                    raise MigrationError("Refusing to baseline 001_initial_schema: missing " + ", ".join(missing))
                description = "Baselined existing schema (pre-tracking)"
            else:
                cur.execute(mig.sql_text)
            cur.execute(
                "INSERT INTO operational.schema_migrations (version, checksum, description) VALUES (%s, %s, %s)",
                (mig.version, mig.checksum, description),
            )
            return "baselined" if is_baseline else "applied"

        outcome = _pg_run_in_transaction(db, _work)
        if outcome == "baselined":
            baselined.append(mig.version)
            logger.info("Baselined pre-existing PostgreSQL schema at %s", mig.version)
        elif outcome == "applied":
            applied_now.append(mig.version)
            logger.info("Applied PostgreSQL migration %s", mig.version)
        else:
            logger.info("Skipped %s: already recorded by another runner", mig.version)


def _pg_missing_baseline_objects(cur: Any) -> list[str]:
    missing: list[str] = []
    for kind, objects in (("table", REQUIRED_BASELINE_TABLES), ("view", REQUIRED_BASELINE_VIEWS)):
        for schema, name in objects:
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.{name}",))
            if not cur.fetchone()[0]:
                missing.append(f"{kind} {schema}.{name}")
    for (schema, table), columns in REQUIRED_BASELINE_COLUMNS.items():
        if f"table {schema}.{table}" in missing:
            continue
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
            (schema, table),
        )
        present = {str(r[0]) for r in cur.fetchall()}
        missing.extend(f"column {schema}.{table}.{c}" for c in columns if c not in present)
    return missing


def _rollback_path(version: str, rollbacks_dir: Path | None) -> Path:
    return (rollbacks_dir or DEFAULT_ROLLBACKS_DIR) / f"{version}.down.sql"


def rollback_migration(
    db: DatabaseManager,
    version: str,
    migrations_dir: Path | None = None,
    rollbacks_dir: Path | None = None,
    dry_run: bool = False,
    lock_timeout: str = DEFAULT_LOCK_TIMEOUT,
    statement_timeout: str = DEFAULT_STATEMENT_TIMEOUT,
) -> dict[str, Any]:
    """Undo the most recently applied migration with its reviewed ``.down.sql`` script, atomically."""
    lock_timeout = _validate_timeout(lock_timeout, "lock_timeout")
    statement_timeout = _validate_timeout(statement_timeout, "statement_timeout")
    discovered = discover_migrations(migrations_dir)
    resolved = resolve_version(version, discovered)
    if resolved == BASELINE_VERSION:
        raise MigrationError("001_initial_schema cannot be rolled back by the migration tool.")
    mig = next(m for m in discovered if m.version == resolved)

    status = get_migration_status(db, migrations_dir)
    applied_versions = [a["version"] for a in status["applied"]]
    if resolved not in applied_versions:
        raise MigrationError(f"Migration {resolved} is not applied; nothing to roll back.")
    latest = max(applied_versions)
    if resolved != latest:
        raise MigrationError(f"Only the latest applied migration can be rolled back (latest is {latest}).")
    if any(m["version"] == resolved for m in status["mismatched"]):
        raise MigrationError(f"Recorded checksum for {resolved} differs from the file; refusing to roll back.")

    down_path = _rollback_path(resolved, rollbacks_dir)
    if not down_path.exists():
        raise MigrationError(f"No rollback script found for {resolved} at {down_path}")
    down_sql = down_path.read_text(encoding="utf-8")
    result = {"backend": db.backend, "version": resolved, "rollback_script": str(down_path), "dry_run": dry_run}
    if dry_run:
        return {**result, "rolled_back": False}

    if db.backend == "sqlite":
        t_mig = db.table_ref("operational", "schema_migrations")
        with db.transaction() as conn:
            _sqlite_begin_atomic(conn)
            for stmt in _split_sql_statements(_adapt_sql_for_sqlite(down_sql)):
                conn.execute(text(stmt))
            conn.execute(text(f"DELETE FROM {t_mig} WHERE version = :v"), {"v": resolved})
    else:

        def _work(cur: Any) -> None:
            _pg_begin_locked(cur, lock_timeout, statement_timeout)
            recorded = _pg_recorded_checksum(cur, resolved)
            if recorded != mig.checksum:
                raise MigrationError(f"Tracking row for {resolved} changed concurrently; aborting rollback.")
            cur.execute(down_sql)
            cur.execute("DELETE FROM operational.schema_migrations WHERE version = %s", (resolved,))

        _pg_run_in_transaction(db, _work)
    logger.info("Rolled back migration %s", resolved)
    return {**result, "rolled_back": True}
