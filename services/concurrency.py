"""Run independent read queries concurrently against a high-latency remote PostgreSQL.

Against Supabase each query costs at least one ~200ms network round trip while the SQL itself is cheap, so
endpoints that issue several independent reads are bound by how many round trips they make *in sequence*.
These helpers overlap them. Demo SQLite is local and fast, so it stays sequential (and deterministic for tests).

Two pools keep this deadlock-free: leaf queries only ever run on ``QUERY_POOL``; loaders that themselves wait on
leaf queries only ever run on ``BUNDLE_POOL``. A task never waits on work queued behind it in its own pool.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from database.connection import DatabaseManager

QUERY_POOL = ThreadPoolExecutor(max_workers=12, thread_name_prefix="db-query")
BUNDLE_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="db-bundle")


def use_concurrency(db: DatabaseManager) -> bool:
    return db.backend == "postgresql"


def _run(db: DatabaseManager, pool: ThreadPoolExecutor, tasks: dict[str, Callable[[], Any]]) -> dict[str, Any]:
    if not use_concurrency(db) or len(tasks) <= 1:
        return {name: fn() for name, fn in tasks.items()}
    futures = {name: pool.submit(fn) for name, fn in tasks.items()}
    return {name: fut.result() for name, fut in futures.items()}


def run_queries(db: DatabaseManager, tasks: dict[str, Callable[[], Any]]) -> dict[str, Any]:
    """Run leaf queries (functions that do NOT call run_queries themselves) concurrently."""
    return _run(db, QUERY_POOL, tasks)


def run_bundles(db: DatabaseManager, tasks: dict[str, Callable[[], Any]]) -> dict[str, Any]:
    """Run loaders that may internally call run_queries concurrently."""
    return _run(db, BUNDLE_POOL, tasks)
