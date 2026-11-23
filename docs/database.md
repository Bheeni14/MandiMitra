# MandiMitra Database Design & Supabase Setup

## 1. Schemas

The PostgreSQL warehouse (`database/schema.sql`) is organized into four schemas:

- **`staging`**: Typed batch landing tables (`stg_agmarknet_prices`, `stg_ceda_arrivals`, `stg_apy_production`).
- **`warehouse`**: Relational star schema (`dim_date`, `dim_commodity`, `dim_market`, `dim_crop`, `map_commodity_crop`, `fact_price_daily`, `fact_arrivals_daily`, `fact_crop_production`).
- **`operational`**: Pipeline telemetry and audit trails (`pipeline_run_log`, `dq_rejects`).
- **`analytics`**: Seven analytical SQL views (`v_q1_*` through `v_q7_*`), `market_signals`, and `briefings`.

## 2. Core Constraints & Indexes

- **Natural Key Idempotency (`fact_price_daily`)**: `PRIMARY KEY (date_id, market_id, commodity_id, variety, grade)` ensures re-running a day updates prices via `ON CONFLICT DO UPDATE` without creating duplicates.
- **Data Quality Checks**: `CHECK (min_price > 0)`, `CHECK (max_price >= min_price)`, `CHECK (modal_price > 0 AND modal_price BETWEEN min_price AND max_price)`.
- **Performance Indexes**: Composite B-tree indexes on `(commodity_id, date_id)` and `(market_id, commodity_id, date_id)` keep dashboard & signal queries fast.

## 3. Connecting Supabase Cloud PostgreSQL + Storage

1. Create a Supabase project and copy your Postgres connection string (Session or Transaction Pooler with `sslmode=require`) into `.env`:
   ```env
   DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
   SUPABASE_URL=https://<project-ref>.supabase.co
   SUPABASE_SERVICE_ROLE_KEY=<your-service-role-key>
   SUPABASE_STORAGE_BUCKET=mandimitra-raw
   DEMO_MODE=false
   ```
2. Create a private bucket named `mandimitra-raw` in **Supabase Storage**.
3. Initialize schemas, tables, views, and seed reference dimensions:
   ```bash
   python scripts/init_db.py
   ```
   *(Alternatively, you can run `database/schema.sql` directly inside the Supabase SQL Editor, followed by `python database/seed.py`.)*
