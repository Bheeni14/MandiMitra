"""Safe migration status, dry-run planning, execution and rollback CLI for MandiMitra.

This is the only entry point that changes a PostgreSQL schema. --apply and --rollback against
PostgreSQL ask for typed confirmation unless --yes is given.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.engine import make_url  # noqa: E402

from config import DEMO_DB_PATH, configure_logging  # noqa: E402
from database.connection import DatabaseManager  # noqa: E402
from database.migrator import (  # noqa: E402
    DEFAULT_LOCK_TIMEOUT,
    DEFAULT_STATEMENT_TIMEOUT,
    apply_migrations,
    get_migration_status,
    rollback_migration,
)


def _describe_target(db: DatabaseManager) -> str:
    if db.backend == "sqlite":
        return f"SQLite {db.database_url.removeprefix('sqlite:///')}"
    url = make_url(db.database_url)
    return f"PostgreSQL host={url.host} port={url.port or 5432} database={url.database}"


def _confirm(db: DatabaseManager, action: str, assume_yes: bool) -> None:
    if db.backend == "sqlite" or assume_yes:
        return
    print(f"About to {action} on {_describe_target(db)}.")
    answer = input("Type 'yes' to continue: ").strip().lower()
    if answer != "yes":
        raise SystemExit("Aborted: no changes were made.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect, apply or roll back MandiMitra database migrations.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--status", action="store_true", help="Show applied/pending migrations (read-only).")
    mode_group.add_argument("--apply", action="store_true", help="Apply pending migrations (baselines pre-tracking DBs).")
    mode_group.add_argument("--dry-run", action="store_true", help="Plan migrations without modifying the database.")
    mode_group.add_argument("--rollback", metavar="VERSION", help="Roll back the latest applied migration (e.g. 003).")
    parser.add_argument("--to-version", metavar="VERSION", help="Apply/plan only up to and including this version.")
    parser.add_argument("--lock-timeout", default=DEFAULT_LOCK_TIMEOUT, help="SET LOCAL lock_timeout (default %(default)s).")
    parser.add_argument(
        "--statement-timeout",
        default=DEFAULT_STATEMENT_TIMEOUT,
        help="SET LOCAL statement_timeout (default %(default)s).",
    )
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation for PostgreSQL writes.")
    parser.add_argument("--demo", action="store_true", help="Target Demo SQLite database.")
    parser.add_argument("--database-url", type=str, default=None, help="Explicit database URL override (e.g. disposable test DB).")
    args = parser.parse_args()

    configure_logging()
    if args.demo:
        db = DatabaseManager(database_url=f"sqlite:///{DEMO_DB_PATH.as_posix()}")
    elif args.database_url:
        db = DatabaseManager(database_url=args.database_url)
    else:
        db = DatabaseManager()

    ok, msg = db.health_check()
    if not ok:
        raise RuntimeError(f"Database connection failed: {msg}")

    timeouts = {"lock_timeout": args.lock_timeout, "statement_timeout": args.statement_timeout}
    if args.apply:
        _confirm(db, "APPLY pending migrations" + (f" up to {args.to_version}" if args.to_version else ""), args.yes)
        result = apply_migrations(db, dry_run=False, target_version=args.to_version, **timeouts)
    elif args.dry_run:
        result = apply_migrations(db, dry_run=True, target_version=args.to_version, **timeouts)
    elif args.rollback:
        _confirm(db, f"ROLL BACK migration {args.rollback}", args.yes)
        result = rollback_migration(db, args.rollback, **timeouts)
    else:
        result = get_migration_status(db)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
