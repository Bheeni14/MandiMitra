"""Initialize database schemas, star-schema tables, and SQL views.

PostgreSQL: verifies the schema is current and never applies migrations or seeds
reference dimensions unless ``--seed-reference`` is passed explicitly.

SQLite / Demo Mode: builds the schema and seeds reference dimensions so demo
startup remains self-contained.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DEMO_DB_PATH, configure_logging, get_settings
from database.connection import DatabaseManager
from database.seed import seed_reference_dimensions


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize MandiMitra schema (Demo SQLite) or verify PostgreSQL schema."
    )
    parser.add_argument("--demo", action="store_true", help="Force initialization of Demo SQLite database.")
    parser.add_argument(
        "--seed-reference",
        action="store_true",
        help=(
            "Explicitly seed target commodities, crops, and commodity-to-crop mappings. "
            "Required for PostgreSQL; always performed for Demo SQLite."
        ),
    )
    args = parser.parse_args()

    configure_logging()
    settings = get_settings(refresh=True)
    startup = settings.validate_startup()

    if args.demo:
        db = DatabaseManager(database_url=f"sqlite:///{DEMO_DB_PATH.as_posix()}")
    else:
        db = DatabaseManager()

    ok, msg = db.health_check()
    if not ok:
        raise RuntimeError(f"Database connection failed: {msg}")

    # Demo SQLite may apply migrations; PostgreSQL only asserts currency (never migrates).
    db.initialize_schema()

    seeded: dict[str, int] | None = None
    if db.backend == "sqlite":
        seeded = seed_reference_dimensions(db)
        print(f"Database initialized ({db.backend}): {startup['database']}")
        print(f"Seeded reference dimensions: {seeded}")
        return

    if args.seed_reference:
        seeded = seed_reference_dimensions(db)
        print(f"PostgreSQL schema verified ({db.backend}): {startup['database']}")
        print(f"Seeded reference dimensions (explicit --seed-reference): {seeded}")
        return

    print(f"PostgreSQL schema verified ({db.backend}): {startup['database']}")
    print("Reference dimensions not seeded (pass --seed-reference to seed deliberately).")


if __name__ == "__main__":
    main()
