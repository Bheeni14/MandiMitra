-- =============================================================================
-- Rollback for migration 003 (run only via: python scripts/migrate.py --rollback 003_commodity_aliases_and_storability)
--
-- Restores from the operational.migration_003_* backups written by migration 003:
--   * alias dim_commodity rows deleted by the merge, with their original commodity_id;
--   * the original category/storable of every commodity that existed before 003;
--   * every moved price/arrival row under its original commodity_id, removing the canonical
--     copies that 003 created (canonical rows that already existed are left untouched).
-- Plain INSERTs are used so any unexpected key conflict aborts the whole rollback.
-- Intended to run before new ingestion touches the merged commodities.
-- =============================================================================

INSERT INTO warehouse.dim_commodity (commodity_id, commodity_name, category, storable)
SELECT b.commodity_id, b.commodity_name, b.category, b.storable
FROM operational.migration_003_commodity_backup b
WHERE NOT EXISTS (
    SELECT 1 FROM warehouse.dim_commodity d WHERE d.commodity_id = b.commodity_id
);

UPDATE warehouse.dim_commodity
SET category = (
        SELECT b.category FROM operational.migration_003_commodity_backup b
        WHERE b.commodity_id = dim_commodity.commodity_id
    ),
    storable = (
        SELECT b.storable FROM operational.migration_003_commodity_backup b
        WHERE b.commodity_id = dim_commodity.commodity_id
    )
WHERE commodity_id IN (SELECT commodity_id FROM operational.migration_003_commodity_backup);

DELETE FROM warehouse.fact_price_daily
WHERE EXISTS (
    SELECT 1 FROM operational.migration_003_price_moves m
    WHERE NOT m.canonical_row_preexisted
      AND m.date_id = fact_price_daily.date_id
      AND m.market_id = fact_price_daily.market_id
      AND m.canonical_commodity_id = fact_price_daily.commodity_id
      AND m.variety = fact_price_daily.variety
      AND m.grade = fact_price_daily.grade
);

INSERT INTO warehouse.fact_price_daily (
    date_id, market_id, commodity_id, variety, grade,
    min_price, max_price, modal_price, outlier_flag, source_name, ingested_at
)
SELECT
    date_id, market_id, original_commodity_id, variety, grade,
    min_price, max_price, modal_price, outlier_flag, source_name, ingested_at
FROM operational.migration_003_price_moves;

DELETE FROM warehouse.fact_arrivals_daily
WHERE EXISTS (
    SELECT 1 FROM operational.migration_003_arrival_moves m
    WHERE NOT m.canonical_row_preexisted
      AND m.date_id = fact_arrivals_daily.date_id
      AND m.market_id = fact_arrivals_daily.market_id
      AND m.canonical_commodity_id = fact_arrivals_daily.commodity_id
);

INSERT INTO warehouse.fact_arrivals_daily (
    date_id, market_id, commodity_id, quantity_tonnes, source_name, ingested_at
)
SELECT
    date_id, market_id, original_commodity_id, quantity_tonnes, source_name, ingested_at
FROM operational.migration_003_arrival_moves;

-- Merge targets that 003 had to create (absent from the backup) and that nothing references.
DELETE FROM warehouse.dim_commodity
WHERE commodity_id NOT IN (SELECT commodity_id FROM operational.migration_003_commodity_backup)
  AND commodity_name IN (SELECT canonical_name FROM operational.migration_003_alias_map)
  AND NOT EXISTS (SELECT 1 FROM warehouse.fact_price_daily p WHERE p.commodity_id = dim_commodity.commodity_id)
  AND NOT EXISTS (SELECT 1 FROM warehouse.fact_arrivals_daily a WHERE a.commodity_id = dim_commodity.commodity_id)
  AND NOT EXISTS (SELECT 1 FROM warehouse.map_commodity_crop m WHERE m.commodity_id = dim_commodity.commodity_id)
  AND NOT EXISTS (SELECT 1 FROM analytics.market_signals s WHERE s.commodity_id = dim_commodity.commodity_id)
  AND NOT EXISTS (SELECT 1 FROM analytics.briefings b WHERE b.commodity_id = dim_commodity.commodity_id);

DROP TABLE operational.migration_003_reviewed_classification;
DROP TABLE operational.migration_003_arrival_moves;
DROP TABLE operational.migration_003_price_moves;
DROP TABLE operational.migration_003_alias_map;
DROP TABLE operational.migration_003_commodity_backup;
