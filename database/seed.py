"""Idempotent reference dimension seeding for MandiMitra."""

from __future__ import annotations

import logging

from sqlalchemy import text

from config import TARGET_COMMODITIES
from database.connection import DatabaseManager, get_db_manager

logger = logging.getLogger(__name__)


class ReferenceDataMissingError(RuntimeError):
    """Raised when required reference dimensions are absent and seeding was not explicitly requested."""


def reference_dimensions_status(db: DatabaseManager | None = None) -> dict[str, object]:
    """Read-only check that the target commodities, crops and mappings already exist.

    Existing rows are never compared for equality: reviewed classifications may legitimately differ
    from the seed defaults, so only *absence* counts as missing.
    """
    db = db or get_db_manager()
    t_comm = db.table_ref("warehouse", "dim_commodity")
    t_crop = db.table_ref("warehouse", "dim_crop")
    t_map = db.table_ref("warehouse", "map_commodity_crop")
    with db.readonly_connection() as conn:
        commodities = {r[0] for r in conn.execute(text(f"SELECT commodity_name FROM {t_comm}")).all()}
        crops = {r[0] for r in conn.execute(text(f"SELECT crop_name FROM {t_crop}")).all()}
        mapped = {
            (r[0], r[1])
            for r in conn.execute(
                text(
                    f"""
                    SELECT c.commodity_name, k.crop_name
                    FROM {t_map} m
                    JOIN {t_comm} c ON c.commodity_id = m.commodity_id
                    JOIN {t_crop} k ON k.crop_id = m.crop_id
                    """
                )
            ).all()
        }
    missing_commodities = sorted({s.name for s in TARGET_COMMODITIES} - commodities)
    missing_crops = sorted({s.crop_name for s in TARGET_COMMODITIES} - crops)
    missing_mappings = sorted(f"{s.name} -> {s.crop_name}" for s in TARGET_COMMODITIES if (s.name, s.crop_name) not in mapped)
    return {
        "complete": not (missing_commodities or missing_crops or missing_mappings),
        "missing_commodities": missing_commodities,
        "missing_crops": missing_crops,
        "missing_mappings": missing_mappings,
    }


def ensure_reference_dimensions(db: DatabaseManager | None = None, force: bool = False) -> dict[str, object]:
    """Seed reference dimensions only when needed; recurring ingestion performs no seed writes.

    * Complete reference data and ``force`` unset: nothing is written.
    * Missing data on the disposable Demo SQLite warehouse: seeded automatically.
    * Missing data on PostgreSQL: raises unless ``force`` is set (CLI ``--seed-reference``), so an
      unexpected gap in production is surfaced instead of being silently rewritten.
    """
    db = db or get_db_manager()
    if not force:
        status = reference_dimensions_status(db)
        if status["complete"]:
            return {"written": False, "reason": "reference dimensions already complete", **status}
        if not db.is_demo:
            raise ReferenceDataMissingError(
                "Reference dimensions are incomplete "
                f"(commodities={status['missing_commodities']}, crops={status['missing_crops']}, "
                f"mappings={status['missing_mappings']}). Re-run with --seed-reference to seed them explicitly."
            )
    seeded = seed_reference_dimensions(db)
    return {"written": True, "reason": "forced" if force else "seeded missing demo data", **seeded}


def seed_reference_dimensions(db: DatabaseManager | None = None) -> dict[str, int]:
    """Upsert canonical commodities, APY crops, and commodity-to-crop mappings."""
    db = db or get_db_manager()
    t_comm = db.table_ref("warehouse", "dim_commodity")
    t_crop = db.table_ref("warehouse", "dim_crop")
    t_map = db.table_ref("warehouse", "map_commodity_crop")

    commodities_seeded = 0
    crops_seeded = 0
    mappings_seeded = 0

    with db.transaction() as conn:
        for spec in TARGET_COMMODITIES:
            if db.backend == "postgresql":
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {t_comm} (commodity_name, category, storable)
                        VALUES (:name, :cat, :storable)
                        ON CONFLICT (commodity_name) DO UPDATE
                        SET category = EXCLUDED.category,
                            storable = EXCLUDED.storable
                        """
                    ),
                    {"name": spec.name, "cat": spec.category, "storable": spec.storable},
                )
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {t_crop} (crop_name, crop_type)
                        VALUES (:crop_name, :crop_type)
                        ON CONFLICT (crop_name) DO UPDATE
                        SET crop_type = EXCLUDED.crop_type
                        """
                    ),
                    {"crop_name": spec.crop_name, "crop_type": spec.crop_type},
                )
            else:
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {t_comm} (commodity_name, category, storable)
                        VALUES (:name, :cat, :storable)
                        ON CONFLICT (commodity_name) DO UPDATE
                        SET category = excluded.category,
                            storable = excluded.storable
                        """
                    ),
                    {"name": spec.name, "cat": spec.category, "storable": int(spec.storable)},
                )
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {t_crop} (crop_name, crop_type)
                        VALUES (:crop_name, :crop_type)
                        ON CONFLICT (crop_name) DO UPDATE
                        SET crop_type = excluded.crop_type
                        """
                    ),
                    {"crop_name": spec.crop_name, "crop_type": spec.crop_type},
                )

            commodities_seeded += 1
            crops_seeded += 1

            comm_id = conn.execute(
                text(f"SELECT commodity_id FROM {t_comm} WHERE commodity_name = :name"),
                {"name": spec.name},
            ).scalar_one()
            crop_id = conn.execute(
                text(f"SELECT crop_id FROM {t_crop} WHERE crop_name = :crop_name"),
                {"crop_name": spec.crop_name},
            ).scalar_one()

            conn.execute(
                text(
                    f"""
                    INSERT INTO {t_map} (commodity_id, crop_id)
                    VALUES (:cid, :crid)
                    ON CONFLICT (commodity_id, crop_id) DO NOTHING
                    """
                ),
                {"cid": comm_id, "crid": crop_id},
            )
            mappings_seeded += 1

    logger.info(
        "Seeded %d commodities, %d crops, %d commodity-crop mappings.",
        commodities_seeded,
        crops_seeded,
        mappings_seeded,
    )
    return {
        "commodities": commodities_seeded,
        "crops": crops_seeded,
        "mappings": mappings_seeded,
    }


if __name__ == "__main__":
    mgr = get_db_manager()
    mgr.initialize_schema()
    print(seed_reference_dimensions(mgr))
