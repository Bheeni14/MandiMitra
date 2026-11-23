-- =============================================================================
-- Rollback for migration 004 (run only via
--   python scripts/migrate.py --rollback 004_split_paddy_onion_green_normalize_kerala)
--
-- Restores from the operational.migration_004_* audit tables written by migration 004
--   * every moved fact row goes back to its original commodity_id (Rice or Onion), keeping its
--     key, prices, variety, grade, flags and timestamps
--   * every market renamed from Keralam gets its original state value back
--   * Paddy / Onion Green dimension rows that 004 created are deleted when nothing references them
--   * The Paddy -> Rice crop mapping (and Rice crop, if 004 created it) are removed when 004 added them
-- Plain UPDATEs are used, so any key conflict created after 004 aborts the whole rollback.
-- When the fact totals still equal the post-004 totals (no later ingestion), the rollback also
-- asserts that fact count and min/max/modal sums are back to the exact pre-004 values and that the
-- created dimension rows are gone. Every check aborts the transaction on failure.
-- Intended to run before new ingestion writes Paddy, Onion Green or Kerala rows.
-- =============================================================================

CREATE TABLE operational.migration_004_rollback_checks (
    check_name  TEXT PRIMARY KEY,
    passed      BOOLEAN NOT NULL CHECK (passed IS TRUE)
);

CREATE TABLE operational.migration_004_rollback_state (
    state_name  TEXT PRIMARY KEY,
    flag        BOOLEAN NOT NULL
);

-- 0. Was anything written to fact_price_daily after migration 004
INSERT INTO operational.migration_004_rollback_state (state_name, flag)
SELECT 'totals_unchanged_since_004',
       (SELECT COUNT(*) FROM warehouse.fact_price_daily)
     = (SELECT after_value FROM operational.migration_004_stats WHERE stat_name = 'fact_price_count')
   AND ABS((SELECT COALESCE(SUM(modal_price), 0) FROM warehouse.fact_price_daily)
     - (SELECT after_value FROM operational.migration_004_stats WHERE stat_name = 'fact_price_modal_sum')) < 0.005;

INSERT INTO operational.migration_004_rollback_checks (check_name, passed)
SELECT 'every_moved_row_is_still_present_on_its_target',
       (SELECT COUNT(*)
        FROM operational.migration_004_price_moves m
        JOIN warehouse.fact_price_daily p
          ON p.date_id = m.date_id
         AND p.market_id = m.market_id
         AND p.commodity_id = m.target_commodity_id
         AND p.variety = m.variety
         AND p.grade = m.grade)
     = (SELECT COUNT(*) FROM operational.migration_004_price_moves);

-- 1. Moved fact rows back to their original commodity
UPDATE warehouse.fact_price_daily
SET commodity_id = (
    SELECT m.original_commodity_id
    FROM operational.migration_004_price_moves m
    WHERE m.date_id = fact_price_daily.date_id
      AND m.market_id = fact_price_daily.market_id
      AND m.target_commodity_id = fact_price_daily.commodity_id
      AND m.variety = fact_price_daily.variety
      AND m.grade = fact_price_daily.grade
)
WHERE EXISTS (
    SELECT 1
    FROM operational.migration_004_price_moves m
    WHERE m.date_id = fact_price_daily.date_id
      AND m.market_id = fact_price_daily.market_id
      AND m.target_commodity_id = fact_price_daily.commodity_id
      AND m.variety = fact_price_daily.variety
      AND m.grade = fact_price_daily.grade
);

-- 2. Market states back to their original value
UPDATE warehouse.dim_market
SET state = (
    SELECT b.original_state
    FROM operational.migration_004_market_state_backup b
    WHERE b.market_id = dim_market.market_id
)
WHERE market_id IN (SELECT market_id FROM operational.migration_004_market_state_backup);

-- 3. Remove the Paddy -> Rice mapping (and Rice crop) only when 004 created them
DELETE FROM warehouse.map_commodity_crop
WHERE (commodity_id, crop_id) IN (
    SELECT c.commodity_id, cr.crop_id
    FROM operational.migration_004_crop_mapping_backup b
    JOIN warehouse.dim_commodity c ON c.commodity_name = b.commodity_name
    JOIN warehouse.dim_crop cr ON cr.crop_name = b.crop_name
    WHERE NOT b.mapping_existed_before
);

DELETE FROM warehouse.dim_crop
WHERE crop_name IN (
        SELECT crop_name FROM operational.migration_004_crop_mapping_backup WHERE NOT crop_existed_before
    )
  AND NOT EXISTS (SELECT 1 FROM warehouse.map_commodity_crop m WHERE m.crop_id = dim_crop.crop_id)
  AND NOT EXISTS (SELECT 1 FROM warehouse.fact_crop_production f WHERE f.crop_id = dim_crop.crop_id);

-- 4. Dimension rows created by 004 that nothing references
DELETE FROM warehouse.dim_commodity
WHERE commodity_name IN (
        SELECT commodity_name FROM operational.migration_004_commodity_targets WHERE NOT existed_before
    )
  AND NOT EXISTS (SELECT 1 FROM warehouse.fact_price_daily p WHERE p.commodity_id = dim_commodity.commodity_id)
  AND NOT EXISTS (SELECT 1 FROM warehouse.fact_arrivals_daily a WHERE a.commodity_id = dim_commodity.commodity_id)
  AND NOT EXISTS (SELECT 1 FROM warehouse.map_commodity_crop c WHERE c.commodity_id = dim_commodity.commodity_id)
  AND NOT EXISTS (SELECT 1 FROM analytics.market_signals s WHERE s.commodity_id = dim_commodity.commodity_id)
  AND NOT EXISTS (SELECT 1 FROM analytics.briefings b WHERE b.commodity_id = dim_commodity.commodity_id);

-- 5. Verification
INSERT INTO operational.migration_004_rollback_checks (check_name, passed)
SELECT 'every_moved_row_is_back_on_its_original_commodity_with_identical_values',
       (SELECT COUNT(*)
        FROM operational.migration_004_price_moves m
        JOIN warehouse.fact_price_daily p
          ON p.date_id = m.date_id
         AND p.market_id = m.market_id
         AND p.commodity_id = m.original_commodity_id
         AND p.variety = m.variety
         AND p.grade = m.grade
         AND p.min_price = m.min_price
         AND p.max_price = m.max_price
         AND p.modal_price = m.modal_price
         AND p.source_name = m.source_name)
     = (SELECT COUNT(*) FROM operational.migration_004_price_moves);

INSERT INTO operational.migration_004_rollback_checks (check_name, passed)
SELECT 'every_market_state_restored', NOT EXISTS (
    SELECT 1
    FROM operational.migration_004_market_state_backup b
    JOIN warehouse.dim_market m ON m.market_id = b.market_id
    WHERE m.state <> b.original_state
       OR m.market_name <> b.market_name
       OR m.district <> b.district
);

INSERT INTO operational.migration_004_rollback_checks (check_name, passed)
SELECT 'fact_price_count_restored',
       NOT (SELECT flag FROM operational.migration_004_rollback_state WHERE state_name = 'totals_unchanged_since_004')
    OR (SELECT COUNT(*) FROM warehouse.fact_price_daily)
     = (SELECT before_value FROM operational.migration_004_stats WHERE stat_name = 'fact_price_count');

INSERT INTO operational.migration_004_rollback_checks (check_name, passed)
SELECT 'fact_price_sums_restored',
       NOT (SELECT flag FROM operational.migration_004_rollback_state WHERE state_name = 'totals_unchanged_since_004')
    OR (
           ABS((SELECT COALESCE(SUM(min_price), 0) FROM warehouse.fact_price_daily)
             - (SELECT before_value FROM operational.migration_004_stats WHERE stat_name = 'fact_price_min_sum')) < 0.005
       AND ABS((SELECT COALESCE(SUM(max_price), 0) FROM warehouse.fact_price_daily)
             - (SELECT before_value FROM operational.migration_004_stats WHERE stat_name = 'fact_price_max_sum')) < 0.005
       AND ABS((SELECT COALESCE(SUM(modal_price), 0) FROM warehouse.fact_price_daily)
             - (SELECT before_value FROM operational.migration_004_stats WHERE stat_name = 'fact_price_modal_sum')) < 0.005
    );

INSERT INTO operational.migration_004_rollback_checks (check_name, passed)
SELECT 'rice_and_onion_counts_restored',
       NOT (SELECT flag FROM operational.migration_004_rollback_state WHERE state_name = 'totals_unchanged_since_004')
    OR (
           (SELECT COUNT(*) FROM warehouse.fact_price_daily p
            JOIN warehouse.dim_commodity c ON c.commodity_id = p.commodity_id WHERE c.commodity_name = 'Rice')
         = (SELECT before_value FROM operational.migration_004_stats WHERE stat_name = 'rice_fact_count')
       AND (SELECT COUNT(*) FROM warehouse.fact_price_daily p
            JOIN warehouse.dim_commodity c ON c.commodity_id = p.commodity_id WHERE c.commodity_name = 'Onion')
         = (SELECT before_value FROM operational.migration_004_stats WHERE stat_name = 'onion_fact_count')
    );

INSERT INTO operational.migration_004_rollback_checks (check_name, passed)
SELECT 'created_dimension_rows_removed',
       NOT (SELECT flag FROM operational.migration_004_rollback_state WHERE state_name = 'totals_unchanged_since_004')
    OR NOT EXISTS (
           SELECT 1 FROM warehouse.dim_commodity d
           JOIN operational.migration_004_commodity_targets t ON t.commodity_name = d.commodity_name
           WHERE NOT t.existed_before
       );

INSERT INTO operational.migration_004_rollback_checks (check_name, passed)
SELECT 'paddy_rice_mapping_created_by_004_removed',
       NOT EXISTS (
           SELECT 1
           FROM operational.migration_004_crop_mapping_backup b
           JOIN warehouse.dim_commodity c ON c.commodity_name = b.commodity_name
           JOIN warehouse.dim_crop cr ON cr.crop_name = b.crop_name
           JOIN warehouse.map_commodity_crop m
             ON m.commodity_id = c.commodity_id
            AND m.crop_id = cr.crop_id
           WHERE NOT b.mapping_existed_before
       );

INSERT INTO operational.migration_004_rollback_checks (check_name, passed)
SELECT 'rice_crop_created_by_004_removed_when_unused',
       NOT EXISTS (
           SELECT 1
           FROM operational.migration_004_crop_mapping_backup b
           JOIN warehouse.dim_crop cr ON cr.crop_name = b.crop_name
           WHERE NOT b.crop_existed_before
       );

-- 6. Remove the 004 audit tables
DROP TABLE operational.migration_004_rollback_checks;
DROP TABLE operational.migration_004_rollback_state;
DROP TABLE operational.migration_004_price_moves;
DROP TABLE operational.migration_004_split_evidence;
DROP TABLE operational.migration_004_crop_mapping_backup;
DROP TABLE operational.migration_004_commodity_targets;
DROP TABLE operational.migration_004_market_state_backup;
DROP TABLE operational.migration_004_stats;
DROP TABLE operational.migration_004_assertions;
