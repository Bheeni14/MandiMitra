"""Database connection & engine manager for PostgreSQL (Supabase) and Demo SQLite."""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DisconnectionError
from sqlalchemy.pool import ConnectionPoolEntry

from config import DEMO_DB_PATH, get_settings

logger = logging.getLogger(__name__)


class _StddevAggregate:
    """Sample standard deviation SQLite aggregate matching PostgreSQL STDDEV_SAMP."""

    def __init__(self) -> None:
        self.values: list[float] = []

    def step(self, value: float | None) -> None:
        if value is not None:
            self.values.append(float(value))

    def finalize(self) -> float | None:
        n = len(self.values)
        if n < 2:
            return None
        mean = sum(self.values) / n
        var = sum((x - mean) ** 2 for x in self.values) / (n - 1)
        return math.sqrt(var)


class _CorrAggregate:
    """Pearson correlation SQLite aggregate matching PostgreSQL CORR(y, x)."""

    def __init__(self) -> None:
        self.pairs: list[tuple[float, float]] = []

    def step(self, y: float | None, x: float | None) -> None:
        if y is not None and x is not None:
            self.pairs.append((float(y), float(x)))

    def finalize(self) -> float | None:
        n = len(self.pairs)
        if n < 2:
            return None
        ys = [p[0] for p in self.pairs]
        xs = [p[1] for p in self.pairs]
        mean_y = sum(ys) / n
        mean_x = sum(xs) / n
        cov = sum((y - mean_y) * (x - mean_x) for y, x in self.pairs)
        var_y = sum((y - mean_y) ** 2 for y in ys)
        var_x = sum((x - mean_x) ** 2 for x in xs)
        denom = math.sqrt(var_y * var_x)
        if denom == 0:
            return None
        return cov / denom


def _register_sqlite_functions(dbapi_conn: sqlite3.Connection, _connection_record: object) -> None:
    dbapi_conn.execute("PRAGMA foreign_keys=ON;")
    dbapi_conn.execute("PRAGMA journal_mode=WAL;")
    dbapi_conn.create_aggregate("STDDEV_SAMP", 1, _StddevAggregate)
    dbapi_conn.create_aggregate("STDDEV", 1, _StddevAggregate)
    dbapi_conn.create_aggregate("CORR", 2, _CorrAggregate)


# Pooled read connections idle for longer than this are pinged before reuse (dropped ones are replaced).
READ_POOL_IDLE_PING_SECONDS = 30.0


def _make_session_read_only(dbapi_conn: object, _connection_record: object) -> None:
    """Runs once per new physical connection: the database rejects writes for the whole session."""
    cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    finally:
        cursor.close()
    dbapi_conn.commit()  # type: ignore[attr-defined]  # no-op under autocommit; ends the implicit txn otherwise


def _remember_checkin_time(_dbapi_conn: object, connection_record: ConnectionPoolEntry) -> None:
    connection_record.info["last_checkin"] = time.monotonic()


def _ping_if_idle(dbapi_conn: object, connection_record: ConnectionPoolEntry, _proxy: object) -> None:
    last = connection_record.info.get("last_checkin")
    if last is None or time.monotonic() - last < READ_POOL_IDLE_PING_SECONDS:
        return
    try:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("SELECT 1")
        finally:
            cursor.close()
    except Exception as exc:  # noqa: BLE001 - any failure means the connection is unusable
        # DisconnectionError tells the pool to discard this connection and retry with a fresh one.
        raise DisconnectionError("Stale pooled read connection") from exc


class DatabaseManager:
    """Manages SQLAlchemy connections for both Supabase PostgreSQL and Demo SQLite."""

    def __init__(self, database_url: str | None = None, demo_db_path: Path | None = None) -> None:
        settings = get_settings()
        if database_url is not None:
            self.database_url = database_url
            self.is_demo = database_url.startswith("sqlite")
        elif settings.is_demo_mode:
            target = demo_db_path or DEMO_DB_PATH
            target.parent.mkdir(parents=True, exist_ok=True)
            self.database_url = f"sqlite:///{target.as_posix()}"
            self.is_demo = True
        else:
            self.database_url = settings.database_url
            self.is_demo = False

        self.backend = "sqlite" if self.is_demo else "postgresql"
        self.engine = self._create_engine()
        self._read_engine: Engine | None = None
        self._read_engine_lock = threading.Lock()

    def _create_engine(self) -> Engine:
        if self.is_demo:
            engine = create_engine(
                self.database_url,
                future=True,
                connect_args={"check_same_thread": False},
            )
            event.listen(engine, "connect", _register_sqlite_functions)
            return engine
        return create_engine(
            self.database_url,
            future=True,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_recycle=1800,
            executemany_mode="values_plus_batch",
            executemany_batch_page_size=1000,
            insertmanyvalues_page_size=1000,
        )

    @property
    def read_engine(self) -> Engine:
        """PostgreSQL engine dedicated to read-only queries, tuned for a high-latency remote database.

        The general engine spends ~4-5 network round trips per read (pre-ping, BEGIN, SET TRANSACTION
        READ ONLY, the query, ROLLBACK); against Supabase each round trip is ~200ms. This engine needs one:
        - every session is made read-only once, when the physical connection is opened, so the database
          itself still rejects writes;
        - AUTOCOMMIT removes the BEGIN/ROLLBACK round trips (READ COMMITTED already gave each statement
          its own snapshot, so results are unchanged);
        - the liveness ping only runs for connections that sat idle, instead of on every checkout.
        """
        if self._read_engine is None:
            with self._read_engine_lock:
                if self._read_engine is None:
                    self._read_engine = self._create_read_engine()
        return self._read_engine

    def _create_read_engine(self) -> Engine:
        engine = create_engine(
            self.database_url,
            future=True,
            isolation_level="AUTOCOMMIT",
            pool_pre_ping=False,  # replaced by the idle-only ping below
            pool_size=10,
            max_overflow=10,
            pool_recycle=1800,
        )
        event.listen(engine, "connect", _make_session_read_only)
        event.listen(engine, "checkin", _remember_checkin_time)
        event.listen(engine, "checkout", _ping_if_idle)
        return engine

    def table_ref(self, schema: str, table: str) -> str:
        """Return schema-qualified table name for PostgreSQL, or flat name for Demo SQLite."""
        return table if self.is_demo else f"{schema}.{table}"

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self.engine.begin() as conn:
            yield conn

    @contextmanager
    def readonly_connection(self) -> Iterator[Connection]:
        if self.backend == "postgresql":
            # Sessions on the read engine are read-only at the database level (see read_engine).
            with self.read_engine.connect() as conn:
                yield conn
            return
        with self.engine.connect() as conn:
            yield conn

    def initialize_schema(self) -> None:
        """Demo SQLite: create/upgrade the schema. PostgreSQL: verify only, never migrate.

        PostgreSQL migrations run exclusively via ``scripts/migrate.py --apply``; a pending or
        mismatched migration raises ``PendingMigrationsError`` pointing the operator there.
        """
        from database.migrator import apply_migrations, assert_schema_current

        if self.backend == "sqlite":
            apply_migrations(self)
            return
        assert_schema_current(self)

    def health_check(self) -> tuple[bool, str]:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True, f"Connected ({self.backend})"
        except Exception as exc:
            return False, f"Connection error ({self.backend}): {exc}"


_default_db_manager: DatabaseManager | None = None


def get_db_manager(refresh: bool = False) -> DatabaseManager:
    global _default_db_manager
    if _default_db_manager is None or refresh:
        _default_db_manager = DatabaseManager()
    return _default_db_manager
