-- =============================================================================
-- Migration 002: Schema Migration Version Tracking & Market Signal Lookup Index
-- =============================================================================

CREATE TABLE IF NOT EXISTS operational.schema_migrations (
    version      TEXT PRIMARY KEY,
    checksum     TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_market_signals_lookup ON analytics.market_signals (commodity_id, market_id, signal_date DESC);
