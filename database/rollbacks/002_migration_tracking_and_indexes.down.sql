-- =============================================================================
-- Rollback for migration 002 (run only via: python scripts/migrate.py --rollback 002_migration_tracking_and_indexes)
-- operational.schema_migrations is kept: it also records the 001 baseline.
-- =============================================================================

DROP INDEX IF EXISTS analytics.idx_market_signals_lookup;
