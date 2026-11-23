-- =============================================================================
-- Migration 003: Merge Agmarknet Pulse Aliases & Apply Reviewed Commodity Classification
--
-- Runs inside one transaction (scripts/migrate.py --apply). Before changing anything it
-- snapshots dim_commodity and every fact row it moves into operational.migration_003_*
-- tables, which database/rollbacks/003_commodity_aliases_and_storability.down.sql uses to
-- restore the original commodity IDs, classifications and fact-row mappings exactly.
-- The backup tables are created without IF NOT EXISTS on purpose: leftover backups from an
-- earlier attempt abort the migration instead of being silently mixed with new data.
--
-- Only these merges are performed: Pigeon Pea (Tur), Green Gram (Moong) and Chickpea aliases.
-- Paddy, Onion Green, ambiguous "Chillies" and Groundnut Pods(Raw) stay separate commodities.
-- The classification list, including each name's review status, must match
-- etl/commodity_catalog.py (enforced by tests).
-- =============================================================================

-- 1. Backups (taken before any change)
CREATE TABLE operational.migration_003_commodity_backup (
    commodity_id    INT PRIMARY KEY,
    commodity_name  TEXT NOT NULL,
    category        TEXT NOT NULL,
    storable        BOOLEAN NOT NULL
);

INSERT INTO operational.migration_003_commodity_backup (commodity_id, commodity_name, category, storable)
SELECT commodity_id, commodity_name, category, storable
FROM warehouse.dim_commodity;

CREATE TABLE operational.migration_003_alias_map (
    alias_key       TEXT PRIMARY KEY,
    canonical_name  TEXT NOT NULL
);

INSERT INTO operational.migration_003_alias_map (alias_key, canonical_name)
VALUES
    ('arhar', 'Pigeon Pea (Tur)'),
    ('arhar (tur/red gram)(whole)', 'Pigeon Pea (Tur)'),
    ('arhar(tur/red gram)', 'Pigeon Pea (Tur)'),
    ('arhar(tur/red gram)(whole)', 'Pigeon Pea (Tur)'),
    ('arhar(whole)', 'Pigeon Pea (Tur)'),
    ('arhar/tur', 'Pigeon Pea (Tur)'),
    ('arhar/tur(whole)', 'Pigeon Pea (Tur)'),
    ('pigeon pea', 'Pigeon Pea (Tur)'),
    ('pigeon pea(tur)', 'Pigeon Pea (Tur)'),
    ('red gram', 'Pigeon Pea (Tur)'),
    ('red gram(arhar/tur)(whole)', 'Pigeon Pea (Tur)'),
    ('red gram(whole)', 'Pigeon Pea (Tur)'),
    ('red gram/arhar/tur', 'Pigeon Pea (Tur)'),
    ('red gram/arhar/tur(whole)', 'Pigeon Pea (Tur)'),
    ('tur', 'Pigeon Pea (Tur)'),
    ('tur(whole)', 'Pigeon Pea (Tur)'),
    ('tur/arhar', 'Pigeon Pea (Tur)'),
    ('green gram', 'Green Gram (Moong)'),
    ('green gram (moong)(whole)', 'Green Gram (Moong)'),
    ('green gram(moong)', 'Green Gram (Moong)'),
    ('green gram(moong)(whole)', 'Green Gram (Moong)'),
    ('green gram(whole)', 'Green Gram (Moong)'),
    ('green gram/moong', 'Green Gram (Moong)'),
    ('green gram/moong(whole)', 'Green Gram (Moong)'),
    ('moong', 'Green Gram (Moong)'),
    ('moong(green gram)', 'Green Gram (Moong)'),
    ('moong(green gram)(whole)', 'Green Gram (Moong)'),
    ('moong(whole)', 'Green Gram (Moong)'),
    ('bengal gram', 'Chickpea'),
    ('bengal gram(gram)(whole)', 'Chickpea'),
    ('bengal gram(whole)', 'Chickpea'),
    ('chana', 'Chickpea'),
    ('chana(whole)', 'Chickpea'),
    ('desi chana', 'Chickpea'),
    ('gram', 'Chickpea'),
    ('gram(whole)', 'Chickpea');

-- 2. Ensure merge targets exist (classification is applied in step 6)
INSERT INTO warehouse.dim_commodity (commodity_name, category, storable)
VALUES
    ('Pigeon Pea (Tur)', 'pulse', TRUE),
    ('Green Gram (Moong)', 'pulse', TRUE),
    ('Chickpea', 'pulse', TRUE)
ON CONFLICT (commodity_name) DO NOTHING;

-- 3. Record every alias fact row with its original key and values
CREATE TABLE operational.migration_003_price_moves (
    date_id                   INT NOT NULL,
    market_id                 INT NOT NULL,
    original_commodity_id     INT NOT NULL,
    canonical_commodity_id    INT NOT NULL,
    variety                   TEXT NOT NULL,
    grade                     TEXT NOT NULL,
    min_price                 NUMERIC(10, 2) NOT NULL,
    max_price                 NUMERIC(10, 2) NOT NULL,
    modal_price               NUMERIC(10, 2) NOT NULL,
    outlier_flag              BOOLEAN NOT NULL,
    source_name               TEXT NOT NULL,
    ingested_at               TIMESTAMPTZ NOT NULL,
    canonical_row_preexisted  BOOLEAN NOT NULL,
    PRIMARY KEY (date_id, market_id, original_commodity_id, variety, grade)
);

INSERT INTO operational.migration_003_price_moves (
    date_id, market_id, original_commodity_id, canonical_commodity_id, variety, grade,
    min_price, max_price, modal_price, outlier_flag, source_name, ingested_at, canonical_row_preexisted
)
SELECT
    p.date_id, p.market_id, p.commodity_id, dst.commodity_id, p.variety, p.grade,
    p.min_price, p.max_price, p.modal_price, p.outlier_flag, p.source_name, p.ingested_at,
    EXISTS (
        SELECT 1 FROM warehouse.fact_price_daily x
        WHERE x.date_id = p.date_id
          AND x.market_id = p.market_id
          AND x.commodity_id = dst.commodity_id
          AND x.variety = p.variety
          AND x.grade = p.grade
    )
FROM warehouse.fact_price_daily p
JOIN warehouse.dim_commodity src ON src.commodity_id = p.commodity_id
JOIN operational.migration_003_alias_map am ON am.alias_key = LOWER(src.commodity_name)
JOIN warehouse.dim_commodity dst ON dst.commodity_name = am.canonical_name
WHERE src.commodity_id <> dst.commodity_id;

CREATE TABLE operational.migration_003_arrival_moves (
    date_id                   INT NOT NULL,
    market_id                 INT NOT NULL,
    original_commodity_id     INT NOT NULL,
    canonical_commodity_id    INT NOT NULL,
    quantity_tonnes           NUMERIC(12, 3) NOT NULL,
    source_name               TEXT NOT NULL,
    ingested_at               TIMESTAMPTZ NOT NULL,
    canonical_row_preexisted  BOOLEAN NOT NULL,
    PRIMARY KEY (date_id, market_id, original_commodity_id)
);

INSERT INTO operational.migration_003_arrival_moves (
    date_id, market_id, original_commodity_id, canonical_commodity_id,
    quantity_tonnes, source_name, ingested_at, canonical_row_preexisted
)
SELECT
    a.date_id, a.market_id, a.commodity_id, dst.commodity_id,
    a.quantity_tonnes, a.source_name, a.ingested_at,
    EXISTS (
        SELECT 1 FROM warehouse.fact_arrivals_daily x
        WHERE x.date_id = a.date_id
          AND x.market_id = a.market_id
          AND x.commodity_id = dst.commodity_id
    )
FROM warehouse.fact_arrivals_daily a
JOIN warehouse.dim_commodity src ON src.commodity_id = a.commodity_id
JOIN operational.migration_003_alias_map am ON am.alias_key = LOWER(src.commodity_name)
JOIN warehouse.dim_commodity dst ON dst.commodity_name = am.canonical_name
WHERE src.commodity_id <> dst.commodity_id;

-- 4. Move recorded rows to the canonical commodity; existing canonical rows win on conflict
INSERT INTO warehouse.fact_price_daily (
    date_id, market_id, commodity_id, variety, grade,
    min_price, max_price, modal_price, outlier_flag, source_name, ingested_at
)
SELECT
    date_id, market_id, canonical_commodity_id, variety, grade,
    min_price, max_price, modal_price, outlier_flag, source_name, ingested_at
FROM operational.migration_003_price_moves
WHERE NOT canonical_row_preexisted
ON CONFLICT (date_id, market_id, commodity_id, variety, grade) DO NOTHING;

INSERT INTO warehouse.fact_arrivals_daily (
    date_id, market_id, commodity_id, quantity_tonnes, source_name, ingested_at
)
SELECT
    date_id, market_id, canonical_commodity_id, quantity_tonnes, source_name, ingested_at
FROM operational.migration_003_arrival_moves
WHERE NOT canonical_row_preexisted
ON CONFLICT (date_id, market_id, commodity_id) DO NOTHING;

DELETE FROM warehouse.fact_price_daily
WHERE EXISTS (
    SELECT 1 FROM operational.migration_003_price_moves m
    WHERE m.date_id = fact_price_daily.date_id
      AND m.market_id = fact_price_daily.market_id
      AND m.original_commodity_id = fact_price_daily.commodity_id
      AND m.variety = fact_price_daily.variety
      AND m.grade = fact_price_daily.grade
);

DELETE FROM warehouse.fact_arrivals_daily
WHERE EXISTS (
    SELECT 1 FROM operational.migration_003_arrival_moves m
    WHERE m.date_id = fact_arrivals_daily.date_id
      AND m.market_id = fact_arrivals_daily.market_id
      AND m.original_commodity_id = fact_arrivals_daily.commodity_id
);

-- 5. Remove alias commodity rows that are no longer referenced (originals kept in the backup)
DELETE FROM warehouse.dim_commodity
WHERE LOWER(commodity_name) IN (SELECT alias_key FROM operational.migration_003_alias_map)
  AND commodity_name NOT IN (SELECT canonical_name FROM operational.migration_003_alias_map)
  AND NOT EXISTS (SELECT 1 FROM warehouse.fact_price_daily p WHERE p.commodity_id = dim_commodity.commodity_id)
  AND NOT EXISTS (SELECT 1 FROM warehouse.fact_arrivals_daily a WHERE a.commodity_id = dim_commodity.commodity_id)
  AND NOT EXISTS (SELECT 1 FROM warehouse.map_commodity_crop m WHERE m.commodity_id = dim_commodity.commodity_id)
  AND NOT EXISTS (SELECT 1 FROM analytics.market_signals s WHERE s.commodity_id = dim_commodity.commodity_id)
  AND NOT EXISTS (SELECT 1 FROM analytics.briefings b WHERE b.commodity_id = dim_commodity.commodity_id);

-- 6. Reviewed classification for the 190 production commodity names (exact stored spelling).
--    Names not listed here are left unchanged. review_status records the decision state and
--    is kept in this table as the audit record:
--      reviewed         approved classification
--      provisional      approved for use, flagged for confirmation (not treated as reviewed)
--      review_required  identity not established; stored as unclassified / non-storable
CREATE TABLE operational.migration_003_reviewed_classification (
    commodity_name  TEXT PRIMARY KEY,
    category        TEXT NOT NULL,
    storable        BOOLEAN NOT NULL,
    review_status   TEXT NOT NULL CHECK (review_status IN ('reviewed', 'provisional', 'review_required')),
    CHECK (review_status <> 'review_required' OR (category = 'unclassified' AND NOT storable))
);

-- BEGIN REVIEWED CLASSIFICATION
INSERT INTO operational.migration_003_reviewed_classification (commodity_name, category, storable, review_status)
VALUES
    ('Ajwan', 'spice', TRUE, 'reviewed'),
    ('Almond(Badam)', 'dry_fruit_nut', TRUE, 'reviewed'),
    ('Amaranthus', 'vegetable', FALSE, 'reviewed'),
    ('Ambady/Mesta/Patson', 'cash_crop', TRUE, 'reviewed'),
    ('Amla(Nelli Kai)', 'fruit', FALSE, 'reviewed'),
    ('Amranthas Red', 'vegetable', FALSE, 'reviewed'),
    ('Apple', 'fruit', FALSE, 'reviewed'),
    ('Arecanut(Betelnut/Supari)', 'cash_crop', TRUE, 'reviewed'),
    ('Ashgourd', 'vegetable', FALSE, 'reviewed'),
    ('Astera', 'flower', FALSE, 'reviewed'),
    ('Avocado', 'fruit', FALSE, 'reviewed'),
    ('Baby Corn', 'vegetable', FALSE, 'reviewed'),
    ('Bajra(Pearl Millet/Cumbu)', 'cereal', TRUE, 'reviewed'),
    ('Banana', 'fruit', FALSE, 'reviewed'),
    ('Banana - Green', 'vegetable', FALSE, 'reviewed'),
    ('Barley(Jau)', 'cereal', TRUE, 'reviewed'),
    ('Basil', 'spice', TRUE, 'reviewed'),
    ('Bay Leaf(Tejpatta)', 'spice', TRUE, 'reviewed'),
    ('Beans', 'vegetable', FALSE, 'reviewed'),
    ('Beetroot', 'vegetable', FALSE, 'reviewed'),
    ('Bengal Gram Dal(Chana Dal)', 'pulse', TRUE, 'reviewed'),
    ('Ber(Zizyphus/Borehannu)', 'fruit', FALSE, 'reviewed'),
    ('Betal Leaves', 'other', FALSE, 'reviewed'),
    ('Bhindi(Ladies Finger)', 'vegetable', FALSE, 'reviewed'),
    ('Bitter Gourd', 'vegetable', FALSE, 'reviewed'),
    ('Black Gram Dal(Urd Dal)', 'pulse', TRUE, 'reviewed'),
    ('Black Gram(Urd Beans)(Whole)', 'pulse', TRUE, 'reviewed'),
    ('Black Pepper', 'spice', TRUE, 'reviewed'),
    ('Bop', 'unclassified', FALSE, 'review_required'),
    ('Bottle Gourd', 'vegetable', FALSE, 'reviewed'),
    ('Brinjal', 'vegetable', FALSE, 'reviewed'),
    ('Broken Rice', 'cereal', TRUE, 'reviewed'),
    ('Broomstick(Flower Broom)', 'other', TRUE, 'reviewed'),
    ('Buttery', 'unclassified', FALSE, 'review_required'),
    ('Cabbage', 'vegetable', FALSE, 'reviewed'),
    ('Capsicum', 'vegetable', FALSE, 'reviewed'),
    ('Cardamom', 'spice', TRUE, 'reviewed'),
    ('Carrot', 'vegetable', FALSE, 'reviewed'),
    ('Castor Seed', 'oilseed', TRUE, 'reviewed'),
    ('Cauliflower', 'vegetable', FALSE, 'reviewed'),
    ('Chiaseeds', 'oilseed', TRUE, 'reviewed'),
    ('Chickpea', 'pulse', TRUE, 'reviewed'),
    ('Chikoos(Sapota)', 'fruit', FALSE, 'reviewed'),
    ('Chili Red', 'spice', TRUE, 'reviewed'),
    ('Chilly Capsicum', 'vegetable', FALSE, 'reviewed'),
    ('Chow Chow', 'vegetable', FALSE, 'reviewed'),
    ('Cinamon(Dalchini)', 'spice', TRUE, 'reviewed'),
    ('Cluster Beans', 'vegetable', FALSE, 'reviewed'),
    ('Coconut', 'cash_crop', TRUE, 'reviewed'),
    ('Coconut Oil', 'oilseed', TRUE, 'reviewed'),
    ('Coffee', 'cash_crop', TRUE, 'reviewed'),
    ('Colacasia', 'vegetable', FALSE, 'reviewed'),
    ('Copra', 'oilseed', TRUE, 'reviewed'),
    ('Coriander(Leaves)', 'vegetable', FALSE, 'reviewed'),
    ('Corriander Seed', 'spice', TRUE, 'reviewed'),
    ('Cotton', 'cash_crop', TRUE, 'reviewed'),
    ('Cowpea(Lobia/Karamani)', 'pulse', TRUE, 'reviewed'),
    ('Cowpea(Veg)', 'vegetable', FALSE, 'reviewed'),
    ('Cucumbar(Kheera)', 'vegetable', FALSE, 'reviewed'),
    ('Cummin Seed(Jeera)', 'spice', TRUE, 'reviewed'),
    ('Custard Apple(Sharifa)', 'fruit', FALSE, 'reviewed'),
    ('Dates', 'dry_fruit_nut', TRUE, 'reviewed'),
    ('Dried Mango', 'dry_fruit_nut', TRUE, 'reviewed'),
    ('Drumstick', 'vegetable', FALSE, 'reviewed'),
    ('Dry Chillies', 'spice', TRUE, 'reviewed'),
    ('Elephant Yam(Suran)/Amorphophallus', 'vegetable', FALSE, 'reviewed'),
    ('Field Bean(Anumulu)', 'pulse', TRUE, 'reviewed'),
    ('Field Pea', 'pulse', TRUE, 'provisional'),
    ('Firewood', 'other', TRUE, 'reviewed'),
    ('Fish', 'other', FALSE, 'reviewed'),
    ('French Beans(Frasbean)', 'vegetable', FALSE, 'reviewed'),
    ('Galgal(Lemon)', 'fruit', FALSE, 'reviewed'),
    ('Garlic', 'spice', TRUE, 'reviewed'),
    ('Ginger(Dry)', 'spice', TRUE, 'reviewed'),
    ('Ginger(Green)', 'vegetable', FALSE, 'reviewed'),
    ('Grapes', 'fruit', FALSE, 'reviewed'),
    ('Green Avare(W)', 'vegetable', FALSE, 'reviewed'),
    ('Green Chilli', 'vegetable', FALSE, 'reviewed'),
    ('Green Gram (Moong)', 'pulse', TRUE, 'reviewed'),
    ('Green Gram Dal(Moong Dal)', 'pulse', TRUE, 'reviewed'),
    ('Green Peas', 'vegetable', FALSE, 'reviewed'),
    ('Ground Nut Seed', 'oilseed', TRUE, 'reviewed'),
    ('Groundnut', 'oilseed', TRUE, 'reviewed'),
    ('Groundnut Pods(Raw)', 'unclassified', FALSE, 'review_required'),
    ('Guar', 'unclassified', FALSE, 'review_required'),
    ('Guar Seed(Cluster Beans Seed)', 'cash_crop', TRUE, 'reviewed'),
    ('Guava', 'fruit', FALSE, 'reviewed'),
    ('Gur(Jaggery)', 'other', TRUE, 'reviewed'),
    ('Indian Beans(Seam)', 'vegetable', FALSE, 'reviewed'),
    ('Indian Colza(Sarson)', 'oilseed', TRUE, 'reviewed'),
    ('Jack Fruit(Ripe)', 'fruit', FALSE, 'reviewed'),
    ('Jarbara', 'flower', FALSE, 'reviewed'),
    ('Jasmine', 'flower', FALSE, 'reviewed'),
    ('Jowar(Sorghum)', 'cereal', TRUE, 'reviewed'),
    ('Kabuli Chana(Chickpeas-White)', 'pulse', TRUE, 'reviewed'),
    ('Karbuja(Musk Melon)', 'fruit', FALSE, 'reviewed'),
    ('Kinnow', 'fruit', FALSE, 'reviewed'),
    ('Knool Khol', 'vegetable', FALSE, 'reviewed'),
    ('Kodo Millet(Varagu)', 'cereal', TRUE, 'reviewed'),
    ('Kutki', 'cereal', TRUE, 'reviewed'),
    ('Ladies Finger', 'vegetable', FALSE, 'reviewed'),
    ('Lak(Teora)', 'pulse', TRUE, 'reviewed'),
    ('Leafy Vegetable', 'vegetable', FALSE, 'reviewed'),
    ('Lemon', 'fruit', FALSE, 'reviewed'),
    ('Lentil(Masur)(Whole)', 'pulse', TRUE, 'reviewed'),
    ('Lime', 'fruit', FALSE, 'reviewed'),
    ('Linseed', 'oilseed', TRUE, 'reviewed'),
    ('Little Gourd(Kundru)', 'vegetable', FALSE, 'reviewed'),
    ('Long Melon(Kakri)', 'vegetable', FALSE, 'reviewed'),
    ('Mahedi', 'other', TRUE, 'reviewed'),
    ('Maize', 'cereal', TRUE, 'reviewed'),
    ('Mango', 'fruit', FALSE, 'reviewed'),
    ('Mango(Raw-Ripe)', 'fruit', FALSE, 'reviewed'),
    ('Marigold(Calcutta)', 'flower', FALSE, 'reviewed'),
    ('Masur Dal', 'pulse', TRUE, 'reviewed'),
    ('Mataki', 'pulse', TRUE, 'reviewed'),
    ('Mentha Oil', 'cash_crop', TRUE, 'reviewed'),
    ('Methi Seeds', 'spice', TRUE, 'reviewed'),
    ('Methi(Leaves)', 'vegetable', FALSE, 'reviewed'),
    ('Mint(Pudina)', 'vegetable', FALSE, 'reviewed'),
    ('Moath Dal', 'pulse', TRUE, 'reviewed'),
    ('Mousambi(Sweet Lime)', 'fruit', FALSE, 'reviewed'),
    ('Mushroom', 'vegetable', FALSE, 'reviewed'),
    ('Mustard', 'oilseed', TRUE, 'reviewed'),
    ('Mustard Oil', 'oilseed', TRUE, 'reviewed'),
    ('Nigella', 'spice', TRUE, 'reviewed'),
    ('Onion', 'vegetable', FALSE, 'reviewed'),
    ('Orange', 'fruit', FALSE, 'reviewed'),
    ('Other Green And Fresh Vegetables', 'vegetable', FALSE, 'reviewed'),
    ('Papaya', 'fruit', FALSE, 'reviewed'),
    ('Papaya(Raw)', 'vegetable', FALSE, 'reviewed'),
    ('Pea Pod/Pea Cod/हरी मटर', 'vegetable', FALSE, 'reviewed'),
    ('Pear(Marasebu)', 'fruit', FALSE, 'reviewed'),
    ('Peas Wet', 'vegetable', FALSE, 'reviewed'),
    ('Peas(Dry)', 'pulse', TRUE, 'reviewed'),
    ('Pegeon Pea(Arhar Fali)', 'vegetable', FALSE, 'reviewed'),
    ('Persimon(Japani Fal)', 'fruit', FALSE, 'reviewed'),
    ('Pigeon Pea (Tur)', 'pulse', TRUE, 'reviewed'),
    ('Pineapple', 'fruit', FALSE, 'reviewed'),
    ('Pointed Gourd(Parval)', 'vegetable', FALSE, 'reviewed'),
    ('Pomegranate', 'fruit', FALSE, 'reviewed'),
    ('Potato', 'vegetable', TRUE, 'reviewed'),
    ('Pumpkin', 'vegetable', FALSE, 'reviewed'),
    ('Rab/Liquid Jaggery/Molasses', 'other', TRUE, 'reviewed'),
    ('Raddish', 'vegetable', FALSE, 'reviewed'),
    ('Ragi(Finger Millet)', 'cereal', TRUE, 'reviewed'),
    ('Rajgir', 'cereal', TRUE, 'reviewed'),
    ('Red Gram/Arhar/Tur(Whole)', 'pulse', TRUE, 'reviewed'),
    ('Rice', 'cereal', TRUE, 'reviewed'),
    ('Ridgeguard(Tori)', 'vegetable', FALSE, 'reviewed'),
    ('Rose(Local)', 'flower', FALSE, 'reviewed'),
    ('Round Gourd', 'vegetable', FALSE, 'reviewed'),
    ('Rubber', 'cash_crop', TRUE, 'reviewed'),
    ('Same/Savi', 'cereal', TRUE, 'reviewed'),
    ('Season Leaves', 'unclassified', FALSE, 'review_required'),
    ('Seetapal', 'fruit', FALSE, 'reviewed'),
    ('Sem', 'vegetable', FALSE, 'reviewed'),
    ('Sesamum(Sesame,Gingelly,Til)', 'oilseed', TRUE, 'reviewed'),
    ('Snakeguard', 'vegetable', FALSE, 'reviewed'),
    ('Soanf', 'spice', TRUE, 'reviewed'),
    ('Soybean', 'oilseed', TRUE, 'reviewed'),
    ('Spinach', 'vegetable', FALSE, 'reviewed'),
    ('Spiny Gourd / Kartali(Kantola)', 'vegetable', FALSE, 'reviewed'),
    ('Sponge Gourd', 'vegetable', FALSE, 'reviewed'),
    ('Squash(Chappal Kadoo)', 'vegetable', FALSE, 'reviewed'),
    ('Sugar', 'other', TRUE, 'reviewed'),
    ('Surat Beans(Papadi)', 'vegetable', FALSE, 'reviewed'),
    ('Sweet Corn', 'vegetable', FALSE, 'reviewed'),
    ('Sweet Potato', 'vegetable', FALSE, 'reviewed'),
    ('Sweet Pumpkin', 'vegetable', FALSE, 'reviewed'),
    ('T.V. Cumbu', 'cereal', TRUE, 'reviewed'),
    ('Tamarind Fruit', 'spice', TRUE, 'reviewed'),
    ('Tapioca', 'vegetable', FALSE, 'reviewed'),
    ('Taramira', 'oilseed', TRUE, 'reviewed'),
    ('Tender Coconut', 'fruit', FALSE, 'reviewed'),
    ('Thondekai', 'vegetable', FALSE, 'reviewed'),
    ('Tinda', 'vegetable', FALSE, 'reviewed'),
    ('Tobacco', 'cash_crop', TRUE, 'reviewed'),
    ('Tomato', 'vegetable', FALSE, 'reviewed'),
    ('Tube Rose(Loose)', 'flower', FALSE, 'reviewed'),
    ('Turmeric', 'spice', TRUE, 'reviewed'),
    ('Turnip', 'vegetable', FALSE, 'reviewed'),
    ('Walnut', 'dry_fruit_nut', TRUE, 'reviewed'),
    ('Water Melon', 'fruit', FALSE, 'reviewed'),
    ('Wheat', 'cereal', TRUE, 'reviewed'),
    ('Wheat Atta', 'cereal', TRUE, 'reviewed'),
    ('White Muesli', 'other', TRUE, 'reviewed'),
    ('Wild Cucumber', 'vegetable', FALSE, 'reviewed'),
    ('Wood', 'other', TRUE, 'reviewed'),
    ('Yam(Ratalu)', 'vegetable', FALSE, 'reviewed');
-- END REVIEWED CLASSIFICATION

UPDATE warehouse.dim_commodity
SET category = (
        SELECT r.category FROM operational.migration_003_reviewed_classification r
        WHERE r.commodity_name = dim_commodity.commodity_name
    ),
    storable = (
        SELECT r.storable FROM operational.migration_003_reviewed_classification r
        WHERE r.commodity_name = dim_commodity.commodity_name
    )
WHERE commodity_name IN (SELECT commodity_name FROM operational.migration_003_reviewed_classification);
