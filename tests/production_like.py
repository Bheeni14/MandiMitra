"""Production-like pre-migration warehouse fixtures shared by SQLite and disposable PostgreSQL tests.

Seeds the 190 commodity names (with their pre-migration classification and per-name price row
counts) captured in tests/fixtures/production_commodities_2026_11_20.json.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from database.connection import DatabaseManager
from etl.cleaning import derive_indian_season

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "production_commodities_2026_11_20.json"
RED_GRAM = "Red Gram/Arhar/Tur(Whole)"
PIGEON_PEA = "Pigeon Pea (Tur)"
FIRST_COMMODITY_ID = 101
N_DAYS = 50
N_MARKETS = 40
INGESTED_AT = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)
CONFLICT_PIGEON_PEA_MODAL = 9999.0
FACT_TABLES = ("fact_price_daily", "fact_arrivals_daily")


def load_production_fixture() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["commodities"]


def _dates() -> list[date]:
    start = date(2026, 10, 1)
    return [start + timedelta(days=i) for i in range(N_DAYS)]


def _date_id(d: date) -> int:
    return int(d.strftime("%Y%m%d"))


def _ts(db: DatabaseManager) -> Any:
    return INGESTED_AT if db.backend == "postgresql" else INGESTED_AT.strftime("%Y-%m-%d %H:%M:%S")


def seed_production_like(db: DatabaseManager) -> dict[str, int]:
    """Insert production-shaped rows into an existing (pre-003) warehouse schema.

    Adds one deliberate key conflict: Pigeon Pea (Tur) already owns the same price/arrival key as
    the first Red Gram alias row, exercising the "existing canonical row wins" path of migration 003.
    """
    commodities = load_production_fixture()
    t = db.table_ref
    ids = {c["commodity_name"]: FIRST_COMMODITY_ID + i for i, c in enumerate(commodities)}
    dates = _dates()
    ts = _ts(db)

    with db.transaction() as conn:
        conn.execute(
            text(f"INSERT INTO {t('warehouse', 'dim_date')} (date_id, full_date, year, month, week, season) "
                 "VALUES (:date_id, :full_date, :year, :month, :week, :season)"),
            [
                {
                    "date_id": _date_id(d),
                    "full_date": d if db.backend == "postgresql" else d.isoformat(),
                    "year": d.year,
                    "month": d.month,
                    "week": d.isocalendar()[1],
                    "season": derive_indian_season(d),
                }
                for d in dates
            ],
        )
        conn.execute(
            text(f"INSERT INTO {t('warehouse', 'dim_market')} (market_id, market_name, district, state) "
                 "VALUES (:market_id, :market_name, :district, :state)"),
            [
                {"market_id": m + 1, "market_name": f"Mandi {m + 1}", "district": f"District {m % 7}", "state": f"State {m % 5}"}
                for m in range(N_MARKETS)
            ],
        )
        conn.execute(
            text(f"INSERT INTO {t('warehouse', 'dim_commodity')} (commodity_id, commodity_name, category, storable) "
                 "VALUES (:commodity_id, :commodity_name, :category, :storable)"),
            [
                {
                    "commodity_id": ids[c["commodity_name"]],
                    "commodity_name": c["commodity_name"],
                    "category": c["pre_migration_category"],
                    "storable": c["pre_migration_storable"],
                }
                for c in commodities
            ],
        )
        if db.backend == "postgresql":
            conn.execute(
                text("SELECT setval(pg_get_serial_sequence('warehouse.dim_commodity', 'commodity_id'), 342)")
            )

        price_rows: list[dict[str, Any]] = []
        for c in commodities:
            for i in range(c["price_rows"]):
                price_rows.append(
                    {
                        "date_id": _date_id(dates[i % N_DAYS]),
                        "market_id": (i // N_DAYS) % N_MARKETS + 1,
                        "commodity_id": ids[c["commodity_name"]],
                        "variety": "FAQ Variety",
                        "grade": "FAQ",
                        "min_price": 1000.0 + i,
                        "max_price": 1400.0 + i,
                        "modal_price": 1200.0 + i,
                        "outlier_flag": i % 97 == 0,
                        "source_name": "agmarknet_api",
                        "ingested_at": ts,
                    }
                )
        # Same key as Red Gram's first row (i=0 -> first date, market 1).
        price_rows.append(
            {
                "date_id": _date_id(dates[0]),
                "market_id": 1,
                "commodity_id": ids[PIGEON_PEA],
                "variety": "FAQ Variety",
                "grade": "FAQ",
                "min_price": 9000.0,
                "max_price": 11000.0,
                "modal_price": CONFLICT_PIGEON_PEA_MODAL,
                "outlier_flag": False,
                "source_name": "agmarknet_api",
                "ingested_at": ts,
            }
        )
        conn.execute(
            text(
                f"INSERT INTO {t('warehouse', 'fact_price_daily')} "
                "(date_id, market_id, commodity_id, variety, grade, min_price, max_price, modal_price, outlier_flag, source_name, ingested_at) "
                "VALUES (:date_id, :market_id, :commodity_id, :variety, :grade, :min_price, :max_price, :modal_price, :outlier_flag, :source_name, :ingested_at)"
            ),
            price_rows,
        )

        arrival_rows = [
            {
                "date_id": _date_id(dates[i]),
                "market_id": 1,
                "commodity_id": ids[name],
                "quantity_tonnes": 10.0 + i,
                "source_name": "ceda_archive",
                "ingested_at": ts,
            }
            for name in (RED_GRAM, "Onion", "Rice")
            for i in range(6)
        ]
        arrival_rows.append(
            {
                "date_id": _date_id(dates[0]),
                "market_id": 1,
                "commodity_id": ids[PIGEON_PEA],
                "quantity_tonnes": 77.0,
                "source_name": "ceda_archive",
                "ingested_at": ts,
            }
        )
        conn.execute(
            text(
                f"INSERT INTO {t('warehouse', 'fact_arrivals_daily')} "
                "(date_id, market_id, commodity_id, quantity_tonnes, source_name, ingested_at) "
                "VALUES (:date_id, :market_id, :commodity_id, :quantity_tonnes, :source_name, :ingested_at)"
            ),
            arrival_rows,
        )
    return ids


def _norm(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(value, "is_finite") or isinstance(value, float):  # Decimal / float
        return round(float(value), 3)
    return value


def snapshot(db: DatabaseManager) -> dict[str, Any]:
    """Exact, order-independent snapshot of commodity dimension and fact tables."""
    t = db.table_ref
    with db.readonly_connection() as conn:
        dim = sorted(
            (int(r[0]), str(r[1]), str(r[2]), bool(r[3]))
            for r in conn.execute(
                text(f"SELECT commodity_id, commodity_name, category, storable FROM {t('warehouse', 'dim_commodity')}")
            ).all()
        )
        prices = sorted(
            (int(r[0]), int(r[1]), int(r[2]), str(r[3]), str(r[4]), _norm(r[5]), _norm(r[6]), _norm(r[7]), bool(r[8]), str(r[9]), _norm(r[10]))
            for r in conn.execute(
                text(
                    "SELECT date_id, market_id, commodity_id, variety, grade, min_price, max_price, modal_price, "
                    f"outlier_flag, source_name, ingested_at FROM {t('warehouse', 'fact_price_daily')}"
                )
            ).all()
        )
        arrivals = sorted(
            (int(r[0]), int(r[1]), int(r[2]), _norm(r[3]), str(r[4]), _norm(r[5]))
            for r in conn.execute(
                text(
                    "SELECT date_id, market_id, commodity_id, quantity_tonnes, source_name, ingested_at "
                    f"FROM {t('warehouse', 'fact_arrivals_daily')}"
                )
            ).all()
        )
    return {"dim_commodity": dim, "fact_price_daily": prices, "fact_arrivals_daily": arrivals}


def classification_by_name(snap: dict[str, Any]) -> dict[str, tuple[str, bool]]:
    return {name: (cat, sto) for _id, name, cat, sto in snap["dim_commodity"]}


def orphan_counts(db: DatabaseManager) -> dict[str, int]:
    """Count fact/signal rows whose foreign keys point at missing dimension rows."""
    t = db.table_ref
    checks = {
        "price_commodity": (t("warehouse", "fact_price_daily"), "commodity_id", t("warehouse", "dim_commodity"), "commodity_id"),
        "price_market": (t("warehouse", "fact_price_daily"), "market_id", t("warehouse", "dim_market"), "market_id"),
        "price_date": (t("warehouse", "fact_price_daily"), "date_id", t("warehouse", "dim_date"), "date_id"),
        "arrival_commodity": (t("warehouse", "fact_arrivals_daily"), "commodity_id", t("warehouse", "dim_commodity"), "commodity_id"),
        "arrival_market": (t("warehouse", "fact_arrivals_daily"), "market_id", t("warehouse", "dim_market"), "market_id"),
        "map_commodity": (t("warehouse", "map_commodity_crop"), "commodity_id", t("warehouse", "dim_commodity"), "commodity_id"),
        "signal_commodity": (t("analytics", "market_signals"), "commodity_id", t("warehouse", "dim_commodity"), "commodity_id"),
        "briefing_commodity": (t("analytics", "briefings"), "commodity_id", t("warehouse", "dim_commodity"), "commodity_id"),
    }
    out: dict[str, int] = {}
    with db.readonly_connection() as conn:
        for label, (child, col, parent, pcol) in checks.items():
            out[label] = int(
                conn.execute(
                    text(
                        f"SELECT COUNT(*) FROM {child} c LEFT JOIN {parent} p ON p.{pcol} = c.{col} "
                        f"WHERE p.{pcol} IS NULL"
                    )
                ).scalar_one()
            )
    return out
