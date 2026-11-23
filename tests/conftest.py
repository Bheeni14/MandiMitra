"""Shared pytest fixtures using isolated temporary SQLite warehouses (never mutating production DB).

Isolation layers applied to every test (no opt-in needed):
  * the process never sees a real DATABASE_URL and DEMO_MODE is pinned to true, so the developer's
    ``.env`` production database can never be selected implicitly;
  * SQLAlchemy engines and psycopg2 connections may only target SQLite or loopback PostgreSQL
    (the disposable test cluster);
  * Python sockets may only connect to loopback, so no HTTP call can leave the machine;
  * Supabase client creation and the Gemini SDK are blocked;
  * the raw zone (data/raw and its cache) is redirected to a per-test temporary directory, and a
    session guard fails the run if the real data/raw directory changed.
"""

from __future__ import annotations

import os

# Must run before any settings object is built: real env vars outrank ``.env``.
os.environ["DATABASE_URL"] = ""
os.environ["DEMO_MODE"] = "true"
for _flag in ("RAW_STORAGE_UPLOAD_ENABLED", "GEMINI_ENABLED", "ADVISOR_PERSIST"):
    os.environ.pop(_flag, None)

import socket  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any  # noqa: E402
from unittest import mock  # noqa: E402

import pytest  # noqa: E402

import config  # noqa: E402
from config import get_settings  # noqa: E402
from database.connection import DatabaseManager  # noqa: E402
from database.seed import seed_reference_dimensions  # noqa: E402
from etl.warehouse_loader import WarehouseLoader  # noqa: E402
from genai.gemini_client import GeminiBriefingClient  # noqa: E402
from ingestion.storage import RawZoneStorage  # noqa: E402

get_settings(refresh=True)

_SUPABASE_ENV_VARS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_STORAGE_BUCKET")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", ""}
_REAL_RAW_DIR = config.DATA_RAW_DIR


class NetworkAccessBlocked(BaseException):
    """Raised when test code tries to reach a non-loopback host (BaseException so it cannot be swallowed)."""


class ProductionDatabaseBlocked(BaseException):
    """Raised when test code tries to open a non-SQLite, non-loopback database connection."""


def _assert_loopback(host: Any, what: str) -> None:
    if host is None:
        return
    name = host.decode() if isinstance(host, bytes) else str(host)
    if name.strip("[]") not in _LOOPBACK_HOSTS:
        raise NetworkAccessBlocked(f"Tests may not open network connections ({what} -> {name!r}).")


@pytest.fixture(scope="session", autouse=True)
def block_external_network() -> Iterator[None]:
    """Only loopback sockets are allowed: no HTTP call (data.gov.in, CEDA, Supabase, Gemini) can leave the machine."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def _connect(self: socket.socket, address: Any) -> Any:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _assert_loopback(address[0], "socket.connect")
        return real_connect(self, address)

    def _connect_ex(self: socket.socket, address: Any) -> Any:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _assert_loopback(address[0], "socket.connect_ex")
        return real_connect_ex(self, address)

    def _getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        _assert_loopback(host, "getaddrinfo")
        return real_getaddrinfo(host, *args, **kwargs)

    with mock.patch.object(socket.socket, "connect", _connect), mock.patch.object(
        socket.socket, "connect_ex", _connect_ex
    ), mock.patch.object(socket, "getaddrinfo", _getaddrinfo):
        yield


@pytest.fixture(scope="session", autouse=True)
def block_non_local_databases() -> Iterator[None]:
    """Engines/connections may only target SQLite or the loopback disposable PostgreSQL cluster.

    psycopg2 connects through libpq (C sockets), which the socket guard cannot see, so database
    targets are checked here explicitly.
    """
    import database.connection as connection_module
    from sqlalchemy.engine import make_url

    real_create_engine = connection_module.create_engine

    def _guarded_create_engine(url: Any, *args: Any, **kwargs: Any) -> Any:
        parsed = make_url(url)
        if not parsed.drivername.startswith("sqlite") and (parsed.host or "") not in _LOOPBACK_HOSTS:
            raise ProductionDatabaseBlocked(f"Tests may not connect to database host {parsed.host!r}.")
        return real_create_engine(url, *args, **kwargs)

    patchers = [mock.patch.object(connection_module, "create_engine", _guarded_create_engine)]
    try:
        import psycopg2
        from psycopg2.extensions import parse_dsn

        real_pg_connect = psycopg2.connect

        def _guarded_pg_connect(dsn: Any = None, *args: Any, **kwargs: Any) -> Any:
            host = kwargs.get("host")
            if dsn:
                host = parse_dsn(dsn).get("host", host)
            if (host or "") not in _LOOPBACK_HOSTS:
                raise ProductionDatabaseBlocked(f"Tests may not connect to PostgreSQL host {host!r}.")
            return real_pg_connect(dsn, *args, **kwargs)

        patchers.append(mock.patch.object(psycopg2, "connect", _guarded_pg_connect))
    except ImportError:
        pass
    for p in patchers:
        p.start()
    try:
        yield
    finally:
        for p in reversed(patchers):
            p.stop()


def _raw_dir_fingerprint(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture(scope="session", autouse=True)
def real_raw_zone_untouched() -> Iterator[None]:
    """Fail the run if any test created, modified or deleted a file under the real data/raw/."""
    before = _raw_dir_fingerprint(_REAL_RAW_DIR)
    yield
    after = _raw_dir_fingerprint(_REAL_RAW_DIR)
    if before != after:
        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
        raise AssertionError(
            f"Tests modified the real raw zone {_REAL_RAW_DIR}: added={added[:10]} removed={removed[:10]} changed={changed[:10]}"
        )


@pytest.fixture(autouse=True)
def isolated_raw_zone(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Default raw/cache locations point at a per-test temp dir (covers ``RawZoneStorage()`` defaults)."""
    raw_dir = tmp_path_factory.mktemp("raw_zone")
    cache_dir = raw_dir / "_cache"
    for module in ("config", "ingestion.storage", "ingestion.ceda_importer", "ingestion.apy_importer"):
        monkeypatch.setattr(f"{module}.DATA_RAW_DIR", raw_dir, raising=False)
    for module in ("config", "ingestion.storage"):
        monkeypatch.setattr(f"{module}.DATA_CACHE_DIR", cache_dir, raising=False)
    return raw_dir


class SupabaseCallBlocked(BaseException):
    """Raised when test code tries to build a real Supabase client.

    Deliberately a BaseException so the broad ``except Exception`` in the storage layer
    cannot swallow it: an accidental remote call fails the test loudly instead of
    degrading to a logged warning.
    """


@pytest.fixture(autouse=True)
def supabase_guard() -> Iterator[SimpleNamespace]:
    """Guarantee no test can reach real Supabase Storage, even if a developer .env holds valid keys.

    Two independent layers:
      1. Supabase credentials are blanked in the process environment (real env vars outrank
         ``.env``) and the cached settings are refreshed, so ``has_supabase_storage`` is False.
      2. ``supabase.create_client`` is replaced by a guard that records the attempt and raises
         ``SupabaseCallBlocked``. Tests that need to exercise the upload code install their own
         fake ``supabase`` module via ``sys.modules`` (which bypasses this guard by design).

    This fixture deliberately does not use ``monkeypatch`` so env/settings are restored only
    after the test's own monkeypatch has been undone.
    """
    saved_env = {name: os.environ.get(name) for name in _SUPABASE_ENV_VARS}
    for name in _SUPABASE_ENV_VARS:
        os.environ[name] = ""
    get_settings(refresh=True)

    guard = SimpleNamespace(calls=[], blocked_exc=SupabaseCallBlocked)

    def _blocked_create_client(*args: Any, **kwargs: Any) -> Any:
        guard.calls.append("create_client")
        raise SupabaseCallBlocked("Real Supabase client creation is disabled in tests.")

    patcher = None
    try:
        import supabase as _real_supabase
    except ImportError:  # SDK not installed: layer 1 alone still applies.
        _real_supabase = None
    if _real_supabase is not None:
        patcher = mock.patch.object(_real_supabase, "create_client", _blocked_create_client)
        patcher.start()
    try:
        yield guard
    finally:
        if patcher is not None:
            patcher.stop()
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings(refresh=True)


@pytest.fixture()
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RawZoneStorage:
    """RawZoneStorage confined to ``tmp_path`` whose remote upload hook can never do I/O.

    Passing temp dirs only isolates *local* paths; the Supabase upload path reads credentials
    from the global settings. This fixture additionally replaces the instance's upload hook with
    a recorder that returns False, so no Supabase operation is possible through this object.
    ``storage.blocked_upload_attempts`` lists the remote keys whose upload dispatch was blocked.
    """
    storage = RawZoneStorage(raw_root=tmp_path / "raw", cache_dir=tmp_path / "cache")
    attempts: list[str] = []

    def _no_remote_upload(local_path: Path, remote_key: str, content_type: str) -> bool:
        attempts.append(remote_key)
        return False

    monkeypatch.setattr(storage, "_maybe_upload_supabase", _no_remote_upload)
    storage.blocked_upload_attempts = attempts  # type: ignore[attr-defined]
    return storage


@pytest.fixture(autouse=True)
def block_real_gemini_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee no test reaches the real Gemini API, even if a developer .env holds a key."""

    def _blocked(self: GeminiBriefingClient) -> None:
        raise RuntimeError("Real Gemini SDK client construction is disabled in tests.")

    monkeypatch.setattr(GeminiBriefingClient, "_build_sdk_client", _blocked)


@pytest.fixture(autouse=True)
def isolate_backtest_cache() -> Iterator[None]:
    """Each test starts and ends with an empty in-process backtest results cache."""
    from backend.routers.backtest import clear_backtest_cache

    clear_backtest_cache()
    yield
    clear_backtest_cache()


@pytest.fixture()
def temp_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "test_mandimitra.db"
    db = DatabaseManager(database_url=f"sqlite:///{db_file.as_posix()}")
    db.initialize_schema()
    seed_reference_dimensions(db)
    return db


@pytest.fixture()
def populated_db(temp_db: DatabaseManager) -> DatabaseManager:
    """Populate a deterministic mini-warehouse with exact hand-verifiable numbers across 2+ years."""
    loader = WarehouseLoader(temp_db)
    start = date(2024, 1, 1)
    end = date(2025, 6, 30)

    price_records = []
    arrival_records = []
    cur = start
    idx = 0
    while cur <= end:
        # 3 trading days/week
        if cur.weekday() in (0, 2, 4):
            # Tomato: seasonal pattern (higher in Jul-Sep, lower in Jan-Mar) + rising recent momentum in Jun 2025
            base_t = 1500.0 if cur.month in (1, 2, 3) else (3000.0 if cur.month in (7, 8, 9) else 2000.0)
            if cur >= date(2025, 6, 1):
                base_t = 1800.0 + (cur - date(2025, 6, 1)).days * 15.0  # Rising 4w momentum, still below Jul peak!
            price_records.append(
                {
                    "state": "Maharashtra",
                    "district": "Nashik",
                    "market": "Lasalgaon",
                    "commodity": "Tomato",
                    "variety": "Hybrid",
                    "grade": "FAQ",
                    "arrival_date": cur.isoformat(),
                    "min_price": round(base_t * 0.85, 2),
                    "max_price": round(base_t * 1.15, 2),
                    "modal_price": round(base_t, 2),
                }
            )
            price_records.append(
                {
                    "state": "Karnataka",
                    "district": "Bengaluru Urban",
                    "market": "Bengaluru",
                    "commodity": "Tomato",
                    "variety": "Hybrid",
                    "grade": "FAQ",
                    "arrival_date": cur.isoformat(),
                    "min_price": round(base_t * 1.10, 2),
                    "max_price": round(base_t * 1.35, 2),
                    "modal_price": round(base_t * 1.25, 2),
                }
            )
            # Wheat: stable storable crop
            price_records.append(
                {
                    "state": "Madhya Pradesh",
                    "district": "Indore",
                    "market": "Indore",
                    "commodity": "Wheat",
                    "variety": "Lokwan",
                    "grade": "FAQ",
                    "arrival_date": cur.isoformat(),
                    "min_price": 2250.0,
                    "max_price": 2550.0,
                    "modal_price": 2400.0 + (idx % 5) * 10.0,
                }
            )
            # Arrivals inversely related to Tomato price
            arr_qty = max(30.0, 350.0 - (base_t - 1500.0) * 0.12)
            if cur >= date(2025, 6, 15):
                arr_qty = 110.0  # Falling arrivals late June
            elif cur >= date(2025, 6, 1):
                arr_qty = 180.0
            arrival_records.append(
                {
                    "state": "Maharashtra",
                    "district": "Nashik",
                    "market": "Lasalgaon",
                    "commodity": "Tomato",
                    "arrival_date": cur.isoformat(),
                    "quantity_tonnes": round(arr_qty, 2),
                }
            )
            idx += 1
        cur += timedelta(days=1)

    apy_records = [
        {
            "crop_year": 2024,
            "season": "Kharif",
            "state": "Maharashtra",
            "district": "Nashik",
            "crop_name": "Tomato",
            "crop_type": "Horticulture",
            "area_ha": 12000.0,
            "production_t": 240000.0,
            "yield_t_ha": 20.0,
        },
        {
            "crop_year": 2024,
            "season": "Kharif",
            "state": "Karnataka",
            "district": "Bengaluru Urban",
                    "crop_name": "Tomato",
            "crop_type": "Horticulture",
            "area_ha": 8000.0,
            "production_t": 144000.0,
            "yield_t_ha": 18.0,
        },
        {
            "crop_year": 2025,
            "season": "Kharif",
            "state": "Maharashtra",
            "district": "Nashik",
            "crop_name": "Tomato",
            "crop_type": "Horticulture",
            "area_ha": 12500.0,
            "production_t": 250000.0,
            "yield_t_ha": 20.0,
        },
        {
            "crop_year": 2025,
            "season": "Kharif",
            "state": "Karnataka",
            "district": "Bengaluru Urban",
            "crop_name": "Tomato",
            "crop_type": "Horticulture",
            "area_ha": 8200.0,
            "production_t": 150000.0,
            "yield_t_ha": 18.29,
        },
    ]

    loader.load_prices(price_records, source_name="test_fixture")
    loader.load_arrivals(arrival_records, source_name="test_fixture")
    loader.load_apy_production(apy_records, source_name="test_fixture")
    return temp_db
