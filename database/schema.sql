-- =============================================================================
-- MandiMitra PostgreSQL / Supabase Star-Schema Warehouse DDL
-- Schemas: staging, warehouse, operational, analytics
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS warehouse;
CREATE SCHEMA IF NOT EXISTS operational;
CREATE SCHEMA IF NOT EXISTS analytics;

-- -----------------------------------------------------------------------------
-- 1. STAGING LAYER
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS staging.stg_agmarknet_prices (
    stg_id          BIGSERIAL PRIMARY KEY,
    batch_id        TEXT NOT NULL,
    source_name     TEXT NOT NULL,
    raw_file_path   TEXT,
    arrival_date    DATE,
    state           TEXT,
    district        TEXT,
    market          TEXT,
    commodity       TEXT,
    variety         TEXT,
    grade           TEXT,
    min_price       NUMERIC(12, 2),
    max_price       NUMERIC(12, 2),
    modal_price     NUMERIC(12, 2),
    outlier_flag    BOOLEAN DEFAULT FALSE,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stg_prices_batch ON staging.stg_agmarknet_prices (batch_id);

CREATE TABLE IF NOT EXISTS staging.stg_ceda_arrivals (
    stg_id          BIGSERIAL PRIMARY KEY,
    batch_id        TEXT NOT NULL,
    source_name     TEXT NOT NULL,
    raw_file_path   TEXT,
    arrival_date    DATE,
    state           TEXT,
    district        TEXT,
    market          TEXT,
    commodity       TEXT,
    quantity_tonnes NUMERIC(14, 3),
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stg_arrivals_batch ON staging.stg_ceda_arrivals (batch_id);

CREATE TABLE IF NOT EXISTS staging.stg_apy_production (
    stg_id          BIGSERIAL PRIMARY KEY,
    batch_id        TEXT NOT NULL,
    source_name     TEXT NOT NULL,
    raw_file_path   TEXT,
    crop_year       INT,
    season          TEXT,
    state           TEXT,
    district        TEXT,
    crop_name       TEXT,
    crop_type       TEXT,
    area_ha         NUMERIC(14, 2),
    production_t    NUMERIC(14, 2),
    yield_t_ha      NUMERIC(10, 4),
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stg_apy_batch ON staging.stg_apy_production (batch_id);

-- -----------------------------------------------------------------------------
-- 2. OPERATIONAL LAYER (Run Logs & Data Quality Rejects)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS operational.pipeline_run_log (
    run_id          BIGSERIAL PRIMARY KEY,
    stage           TEXT NOT NULL,
    source_name     TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    rows_fetched    INT NOT NULL DEFAULT 0 CHECK (rows_fetched >= 0),
    rows_loaded     INT NOT NULL DEFAULT 0 CHECK (rows_loaded >= 0),
    rows_rejected   INT NOT NULL DEFAULT 0 CHECK (rows_rejected >= 0),
    status          TEXT NOT NULL CHECK (status IN ('running', 'success', 'partial_success', 'failed', 'unavailable')),
    error_message   TEXT,
    metadata_json   JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_run_log_started ON operational.pipeline_run_log (started_at DESC);

CREATE TABLE IF NOT EXISTS operational.dq_rejects (
    reject_id       BIGSERIAL PRIMARY KEY,
    run_id          BIGINT REFERENCES operational.pipeline_run_log (run_id) ON DELETE SET NULL,
    source_name     TEXT NOT NULL,
    stage           TEXT NOT NULL,
    reject_reason   TEXT NOT NULL,
    raw_record      JSONB NOT NULL,
    rejected_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dq_rejects_run ON operational.dq_rejects (run_id);

CREATE TABLE IF NOT EXISTS operational.schema_migrations (
    version         TEXT PRIMARY KEY,
    checksum        TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- -----------------------------------------------------------------------------
-- 3. WAREHOUSE STAR SCHEMA
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS warehouse.dim_date (
    date_id         INT PRIMARY KEY,
    full_date       DATE UNIQUE NOT NULL,
    year            SMALLINT NOT NULL,
    month           SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    week            SMALLINT NOT NULL CHECK (week BETWEEN 1 AND 53),
    season          TEXT NOT NULL CHECK (season IN ('Kharif', 'Rabi', 'Zaid'))
);

CREATE INDEX IF NOT EXISTS idx_dim_date_ym ON warehouse.dim_date (year, month);

CREATE TABLE IF NOT EXISTS warehouse.dim_commodity (
    commodity_id    SERIAL PRIMARY KEY,
    commodity_name  TEXT UNIQUE NOT NULL,
    category        TEXT NOT NULL,
    storable        BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS warehouse.dim_market (
    market_id       SERIAL PRIMARY KEY,
    market_name     TEXT NOT NULL,
    district        TEXT NOT NULL DEFAULT 'Unknown',
    state           TEXT NOT NULL,
    UNIQUE (market_name, district, state)
);

CREATE INDEX IF NOT EXISTS idx_dim_market_state ON warehouse.dim_market (state, district);

CREATE TABLE IF NOT EXISTS warehouse.dim_crop (
    crop_id         SERIAL PRIMARY KEY,
    crop_name       TEXT UNIQUE NOT NULL,
    crop_type       TEXT
);

CREATE TABLE IF NOT EXISTS warehouse.map_commodity_crop (
    commodity_id    INT NOT NULL REFERENCES warehouse.dim_commodity (commodity_id) ON DELETE CASCADE,
    crop_id         INT NOT NULL REFERENCES warehouse.dim_crop (crop_id) ON DELETE CASCADE,
    PRIMARY KEY (commodity_id, crop_id)
);

CREATE TABLE IF NOT EXISTS warehouse.fact_price_daily (
    date_id         INT NOT NULL REFERENCES warehouse.dim_date (date_id),
    market_id       INT NOT NULL REFERENCES warehouse.dim_market (market_id),
    commodity_id    INT NOT NULL REFERENCES warehouse.dim_commodity (commodity_id),
    variety         TEXT NOT NULL DEFAULT 'NA',
    grade           TEXT NOT NULL DEFAULT 'NA',
    min_price       NUMERIC(10, 2) NOT NULL CHECK (min_price > 0),
    max_price       NUMERIC(10, 2) NOT NULL CHECK (max_price >= min_price),
    modal_price     NUMERIC(10, 2) NOT NULL CHECK (modal_price > 0 AND modal_price BETWEEN min_price AND max_price),
    outlier_flag    BOOLEAN NOT NULL DEFAULT FALSE,
    source_name     TEXT NOT NULL DEFAULT 'agmarknet_api',
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (date_id, market_id, commodity_id, variety, grade)
);

CREATE INDEX IF NOT EXISTS idx_fact_price_comm_date ON warehouse.fact_price_daily (commodity_id, date_id);
CREATE INDEX IF NOT EXISTS idx_fact_price_mkt_comm_date ON warehouse.fact_price_daily (market_id, commodity_id, date_id);
CREATE INDEX IF NOT EXISTS idx_fact_price_source ON warehouse.fact_price_daily (source_name);

CREATE TABLE IF NOT EXISTS warehouse.fact_arrivals_daily (
    date_id         INT NOT NULL REFERENCES warehouse.dim_date (date_id),
    market_id       INT NOT NULL REFERENCES warehouse.dim_market (market_id),
    commodity_id    INT NOT NULL REFERENCES warehouse.dim_commodity (commodity_id),
    quantity_tonnes NUMERIC(12, 3) NOT NULL CHECK (quantity_tonnes >= 0),
    source_name     TEXT NOT NULL DEFAULT 'ceda_archive',
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (date_id, market_id, commodity_id)
);

CREATE INDEX IF NOT EXISTS idx_fact_arrivals_comm_date ON warehouse.fact_arrivals_daily (commodity_id, date_id);

CREATE TABLE IF NOT EXISTS warehouse.fact_crop_production (
    crop_year       INT NOT NULL CHECK (crop_year BETWEEN 1990 AND 2100),
    season          TEXT NOT NULL,
    state           TEXT NOT NULL,
    district        TEXT NOT NULL,
    crop_id         INT NOT NULL REFERENCES warehouse.dim_crop (crop_id),
    area_ha         NUMERIC(14, 2) CHECK (area_ha >= 0),
    production_t    NUMERIC(14, 2) CHECK (production_t >= 0),
    yield_t_ha      NUMERIC(10, 4) CHECK (yield_t_ha >= 0),
    source_name     TEXT NOT NULL DEFAULT 'des_apy',
    PRIMARY KEY (crop_year, season, state, district, crop_id)
);

CREATE INDEX IF NOT EXISTS idx_fact_prod_state_year ON warehouse.fact_crop_production (state, crop_year, crop_id);

-- -----------------------------------------------------------------------------
-- 4. ANALYTICS LAYER (7 SQL Analytical Views + Signals + Briefings)
-- -----------------------------------------------------------------------------

-- Q1: Price Volatility (Coefficient of Variation over trailing 12 months)
CREATE OR REPLACE VIEW analytics.v_q1_price_volatility AS
WITH anchor AS (
    SELECT MAX(d.full_date) AS as_of_date
    FROM warehouse.fact_price_daily p
    JOIN warehouse.dim_date d ON d.date_id = p.date_id
),
windowed AS (
    SELECT
        c.commodity_id,
        c.commodity_name,
        c.category,
        c.storable,
        p.modal_price,
        a.as_of_date
    FROM warehouse.fact_price_daily p
    JOIN warehouse.dim_commodity c ON c.commodity_id = p.commodity_id
    JOIN warehouse.dim_date d ON d.date_id = p.date_id
    CROSS JOIN anchor a
    WHERE d.full_date >= (a.as_of_date - INTERVAL '12 months')
)
SELECT
    commodity_id,
    commodity_name,
    category,
    storable,
    COUNT(*) AS observation_count,
    ROUND(AVG(modal_price), 2) AS avg_modal_price,
    ROUND(STDDEV_SAMP(modal_price), 2) AS stddev_modal_price,
    ROUND(STDDEV_SAMP(modal_price) / NULLIF(AVG(modal_price), 0), 4) AS cv,
    ROUND(MIN(modal_price), 2) AS min_modal_price,
    ROUND(MAX(modal_price), 2) AS max_modal_price,
    MAX(as_of_date) AS window_end,
    (MAX(as_of_date) - INTERVAL '12 months')::date AS window_start,
    RANK() OVER (ORDER BY (STDDEV_SAMP(modal_price) / NULLIF(AVG(modal_price), 0)) DESC NULLS LAST) AS volatility_rank
FROM windowed
GROUP BY commodity_id, commodity_name, category, storable
HAVING COUNT(*) >= 30
ORDER BY cv DESC;

-- Q2: State-wise Price Gaps per Commodity and Month
CREATE OR REPLACE VIEW analytics.v_q2_state_price_gaps AS
WITH state_monthly AS (
    SELECT
        c.commodity_id,
        c.commodity_name,
        d.year,
        d.month,
        m.state,
        ROUND(AVG(p.modal_price), 2) AS avg_modal_price,
        COUNT(*) AS observation_count
    FROM warehouse.fact_price_daily p
    JOIN warehouse.dim_commodity c ON c.commodity_id = p.commodity_id
    JOIN warehouse.dim_market m ON m.market_id = p.market_id
    JOIN warehouse.dim_date d ON d.date_id = p.date_id
    GROUP BY c.commodity_id, c.commodity_name, d.year, d.month, m.state
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY commodity_id, year, month ORDER BY avg_modal_price DESC) AS rn_high,
        ROW_NUMBER() OVER (PARTITION BY commodity_id, year, month ORDER BY avg_modal_price ASC) AS rn_low,
        COUNT(*) OVER (PARTITION BY commodity_id, year, month) AS state_count
    FROM state_monthly
)
SELECT
    hi.commodity_id,
    hi.commodity_name,
    hi.year,
    hi.month,
    hi.state AS highest_state,
    hi.avg_modal_price AS highest_avg_price,
    lo.state AS lowest_state,
    lo.avg_modal_price AS lowest_avg_price,
    ROUND(hi.avg_modal_price - lo.avg_modal_price, 2) AS price_spread,
    ROUND(((hi.avg_modal_price - lo.avg_modal_price) / NULLIF(lo.avg_modal_price, 0)) * 100.0, 2) AS pct_difference,
    hi.state_count
FROM ranked hi
JOIN ranked lo
    ON hi.commodity_id = lo.commodity_id
   AND hi.year = lo.year
   AND hi.month = lo.month
WHERE hi.rn_high = 1
  AND lo.rn_low = 1
  AND hi.state_count >= 2
ORDER BY hi.year DESC, hi.month DESC, pct_difference DESC;

-- Q3: Month-over-Month Trends & 3-Month Moving Average
CREATE OR REPLACE VIEW analytics.v_q3_mom_trends AS
WITH monthly AS (
    SELECT
        c.commodity_id,
        c.commodity_name,
        d.year,
        d.month,
        MAKE_DATE(d.year, d.month, 1) AS month_start,
        ROUND(AVG(p.modal_price), 2) AS avg_modal_price,
        COUNT(*) AS observation_count
    FROM warehouse.fact_price_daily p
    JOIN warehouse.dim_commodity c ON c.commodity_id = p.commodity_id
    JOIN warehouse.dim_date d ON d.date_id = p.date_id
    GROUP BY c.commodity_id, c.commodity_name, d.year, d.month
)
SELECT
    commodity_id,
    commodity_name,
    year,
    month,
    month_start,
    avg_modal_price,
    LAG(avg_modal_price) OVER w AS prev_month_price,
    ROUND(avg_modal_price - LAG(avg_modal_price) OVER w, 2) AS mom_abs_change,
    ROUND(((avg_modal_price - LAG(avg_modal_price) OVER w) / NULLIF(LAG(avg_modal_price) OVER w, 0)) * 100.0, 2) AS mom_pct_change,
    ROUND(AVG(avg_modal_price) OVER (PARTITION BY commodity_id ORDER BY year, month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS ma_3m,
    observation_count
FROM monthly
WINDOW w AS (PARTITION BY commodity_id ORDER BY year, month)
ORDER BY commodity_name, year, month;

-- Q4: Arrivals vs Price (Actual Arrival Records Only - Same-Month & 1-Month Lagged Correlation)
CREATE OR REPLACE VIEW analytics.v_q4_arrivals_vs_price AS
WITH monthly_price AS (
    SELECT
        p.commodity_id,
        d.year,
        d.month,
        MAKE_DATE(d.year, d.month, 1) AS mth,
        AVG(p.modal_price) AS avg_price
    FROM warehouse.fact_price_daily p
    JOIN warehouse.dim_date d ON d.date_id = p.date_id
    GROUP BY p.commodity_id, d.year, d.month
),
monthly_arrivals AS (
    SELECT
        a.commodity_id,
        d.year,
        d.month,
        MAKE_DATE(d.year, d.month, 1) AS mth,
        SUM(a.quantity_tonnes) AS total_arrivals_tonnes
    FROM warehouse.fact_arrivals_daily a
    JOIN warehouse.dim_date d ON d.date_id = a.date_id
    GROUP BY a.commodity_id, d.year, d.month
),
joined AS (
    SELECT
        mp.commodity_id,
        c.commodity_name,
        mp.mth,
        mp.avg_price,
        ma.total_arrivals_tonnes,
        LAG(ma.total_arrivals_tonnes) OVER (PARTITION BY mp.commodity_id ORDER BY mp.mth) AS arrivals_prev_month
    FROM monthly_price mp
    JOIN monthly_arrivals ma
        ON mp.commodity_id = ma.commodity_id
       AND mp.mth = ma.mth
    JOIN warehouse.dim_commodity c ON c.commodity_id = mp.commodity_id
)
SELECT
    commodity_id,
    commodity_name,
    COUNT(*) AS paired_months,
    ROUND(AVG(total_arrivals_tonnes), 2) AS avg_monthly_arrivals_tonnes,
    ROUND(AVG(avg_price), 2) AS avg_monthly_price,
    ROUND(CORR(avg_price, total_arrivals_tonnes)::numeric, 4) AS corr_same_month,
    ROUND(CORR(avg_price, arrivals_prev_month)::numeric, 4) AS corr_lag_1m
FROM joined
GROUP BY commodity_id, commodity_name
HAVING COUNT(*) >= 3
ORDER BY corr_same_month ASC NULLS LAST;

-- Q5: Seasonal Price Index by Calendar Month
CREATE OR REPLACE VIEW analytics.v_q5_seasonal_price_index AS
WITH monthly_hist AS (
    SELECT
        c.commodity_id,
        c.commodity_name,
        d.month,
        ROUND(AVG(p.modal_price), 2) AS month_avg_price,
        COUNT(*) AS observation_count
    FROM warehouse.fact_price_daily p
    JOIN warehouse.dim_commodity c ON c.commodity_id = p.commodity_id
    JOIN warehouse.dim_date d ON d.date_id = p.date_id
    GROUP BY c.commodity_id, c.commodity_name, d.month
),
with_annual AS (
    SELECT
        commodity_id,
        commodity_name,
        month,
        month_avg_price,
        ROUND(AVG(month_avg_price) OVER (PARTITION BY commodity_id), 2) AS annual_avg_price,
        observation_count
    FROM monthly_hist
)
SELECT
    commodity_id,
    commodity_name,
    month,
    month_avg_price,
    annual_avg_price,
    ROUND((month_avg_price / NULLIF(annual_avg_price, 0)) * 100.0, 2) AS seasonal_index,
    RANK() OVER (PARTITION BY commodity_id ORDER BY month_avg_price DESC) AS peak_month_rank,
    observation_count
FROM with_annual
ORDER BY commodity_name, month;

-- Q6: Best Markets in Last 7 Days (per State and Commodity)
CREATE OR REPLACE VIEW analytics.v_q6_best_markets AS
WITH anchor AS (
    SELECT MAX(d.full_date) AS as_of_date
    FROM warehouse.fact_price_daily p
    JOIN warehouse.dim_date d ON d.date_id = p.date_id
),
recent AS (
    SELECT
        m.state,
        m.district,
        m.market_id,
        m.market_name,
        c.commodity_id,
        c.commodity_name,
        p.modal_price,
        p.min_price,
        p.max_price,
        d.full_date,
        a.as_of_date
    FROM warehouse.fact_price_daily p
    JOIN warehouse.dim_market m ON m.market_id = p.market_id
    JOIN warehouse.dim_commodity c ON c.commodity_id = p.commodity_id
    JOIN warehouse.dim_date d ON d.date_id = p.date_id
    CROSS JOIN anchor a
    WHERE d.full_date >= (a.as_of_date - INTERVAL '6 days')
)
SELECT
    state,
    district,
    market_id,
    market_name,
    commodity_id,
    commodity_name,
    COUNT(*) AS report_count,
    ROUND(AVG(modal_price), 2) AS avg_modal_price,
    ROUND(MIN(min_price), 2) AS lowest_min_price,
    ROUND(MAX(max_price), 2) AS highest_max_price,
    MAX(full_date) AS latest_report_date,
    MAX(as_of_date) AS window_end,
    RANK() OVER (PARTITION BY state, commodity_id ORDER BY AVG(modal_price) DESC) AS market_rank
FROM recent
GROUP BY state, district, market_id, market_name, commodity_id, commodity_name
HAVING COUNT(*) >= 2
ORDER BY state, commodity_name, market_rank;

-- Q7: Production vs Price (Linked via map_commodity_crop)
CREATE OR REPLACE VIEW analytics.v_q7_production_vs_price AS
WITH state_prod AS (
    SELECT
        mcc.commodity_id,
        dc.commodity_name,
        cr.crop_name,
        fp.crop_year,
        fp.state,
        SUM(fp.area_ha) AS total_area_ha,
        SUM(fp.production_t) AS total_production_t,
        CASE WHEN SUM(fp.area_ha) > 0 THEN ROUND(SUM(fp.production_t) / SUM(fp.area_ha), 4) ELSE NULL END AS agg_yield_t_ha
    FROM warehouse.fact_crop_production fp
    JOIN warehouse.dim_crop cr ON cr.crop_id = fp.crop_id
    JOIN warehouse.map_commodity_crop mcc ON mcc.crop_id = cr.crop_id
    JOIN warehouse.dim_commodity dc ON dc.commodity_id = mcc.commodity_id
    GROUP BY mcc.commodity_id, dc.commodity_name, cr.crop_name, fp.crop_year, fp.state
),
state_price AS (
    SELECT
        p.commodity_id,
        d.year AS price_year,
        m.state,
        ROUND(AVG(p.modal_price), 2) AS avg_yearly_modal_price,
        COUNT(*) AS price_observations
    FROM warehouse.fact_price_daily p
    JOIN warehouse.dim_date d ON d.date_id = p.date_id
    JOIN warehouse.dim_market m ON m.market_id = p.market_id
    GROUP BY p.commodity_id, d.year, m.state
)
SELECT
    sp.commodity_id,
    sp.commodity_name,
    sp.crop_name,
    sp.crop_year,
    sp.state,
    sp.total_area_ha,
    sp.total_production_t,
    sp.agg_yield_t_ha,
    pr.avg_yearly_modal_price,
    pr.price_observations
FROM state_prod sp
JOIN state_price pr
    ON sp.commodity_id = pr.commodity_id
   AND sp.crop_year = pr.price_year
   AND sp.state = pr.state
ORDER BY sp.commodity_name, sp.crop_year DESC, sp.total_production_t DESC;

CREATE OR REPLACE VIEW analytics.v_q7_production_price_corr AS
SELECT
    commodity_id,
    commodity_name,
    crop_name,
    COUNT(*) AS state_year_pairs,
    ROUND(SUM(total_production_t), 2) AS cumulative_production_t,
    ROUND(AVG(avg_yearly_modal_price), 2) AS avg_modal_price,
    ROUND(CORR(total_production_t, avg_yearly_modal_price)::numeric, 4) AS corr_production_vs_price
FROM analytics.v_q7_production_vs_price
GROUP BY commodity_id, commodity_name, crop_name
HAVING COUNT(*) >= 3
ORDER BY corr_production_vs_price ASC NULLS LAST;

-- Signals & Briefings Output Tables
CREATE TABLE IF NOT EXISTS analytics.market_signals (
    signal_id               BIGSERIAL PRIMARY KEY,
    signal_date             DATE NOT NULL,
    commodity_id            INT NOT NULL REFERENCES warehouse.dim_commodity (commodity_id),
    market_id               INT NOT NULL REFERENCES warehouse.dim_market (market_id),
    verdict_class           TEXT NOT NULL CHECK (verdict_class IN ('SELL NOW', 'WAIT', 'NEUTRAL')),
    verdict_label           TEXT NOT NULL,
    current_modal_price     NUMERIC(10, 2),
    price_change_4w_pct     NUMERIC(8, 2),
    vs_seasonal_avg_pct     NUMERIC(8, 2),
    arrivals_trend          TEXT NOT NULL,
    arrivals_change_14d_pct NUMERIC(8, 2),
    cv_12m                  NUMERIC(8, 4),
    state_price_gap_pct     NUMERIC(8, 2),
    storable                BOOLEAN NOT NULL,
    evidence_completeness   TEXT NOT NULL CHECK (evidence_completeness IN ('HIGH', 'MEDIUM', 'LOW')),
    observation_count_90d   INT NOT NULL DEFAULT 0,
    analysis_window_start   DATE,
    analysis_window_end     DATE,
    data_sources            TEXT,
    rationale_metrics       JSONB NOT NULL DEFAULT '{}'::jsonb,
    threshold_config        JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (signal_date, commodity_id, market_id)
);

CREATE INDEX IF NOT EXISTS idx_market_signals_lookup ON analytics.market_signals (commodity_id, market_id, signal_date DESC);

CREATE TABLE IF NOT EXISTS analytics.briefings (
    briefing_id         BIGSERIAL PRIMARY KEY,
    commodity_id        INT NOT NULL REFERENCES warehouse.dim_commodity (commodity_id),
    market_id           INT NOT NULL REFERENCES warehouse.dim_market (market_id),
    signal_id           BIGINT REFERENCES analytics.market_signals (signal_id) ON DELETE SET NULL,
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    signal_json         JSONB NOT NULL,
    verdict             TEXT NOT NULL,
    reason_en           TEXT NOT NULL,
    caution_en          TEXT NOT NULL,
    reason_hi           TEXT NOT NULL,
    caution_hi          TEXT NOT NULL,
    validation_status   TEXT NOT NULL CHECK (validation_status IN ('validated_ai', 'validated_ai_retry', 'fallback_template', 'failed')),
    validation_errors   JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_used          TEXT NOT NULL,
    used_fallback       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_briefings_lookup ON analytics.briefings (commodity_id, market_id, generated_at DESC);
