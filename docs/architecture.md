# MandiMitra System Architecture

## 1. End-to-End Data Flow

```
  [data.gov.in Agmarknet API]    [CEDA Agmarknet Archive CSVs]    [DES APY Crop Production CSVs]
              |                                |                                |
              +--------------------------------+--------------------------------+
                                               |
                                               v
                      [1. Python Ingestion & Raw Zone Preservation]
                - AgmarknetClient: limit/offset pagination, exponential backoff,
                  429/5xx retry, disk TTL cache, raw JSON archiving
                - CedaImporter & ApyImporter: CSV header inspection + archive ingestion
                - RawZoneStorage: append-only data/raw/ + optional Supabase Storage
                                               |
                                               v
                         [2. Cleaning, Validation & Transformation]
                - Whitespace/casing/alias normalization (state, district, commodity)
                - Indian DD/MM/YYYY & ISO date parsing, numeric & unit conversion
                - Validation rules: missing keys, non-positive prices, modal in [min, max]
                - Natural key deduplication; 3-IQR outlier flagging (never dropped)
                - 100% of rejected records written to operational.dq_rejects
                                               |
                                               v
                      [3. PostgreSQL / Supabase Star-Schema Warehouse]
                - staging.* tables -> warehouse.dim_* + warehouse.fact_*
                - Idempotent transactional UPSERTs on natural keys
                - operational.pipeline_run_log tracks raw -> valid -> loaded counts
                                               |
                                               v
                        [4. SQL Analytics (Q1-Q7) & Signal Engine]
                - 7 reusable SQL views (v_q1_price_volatility .. v_q7_production_vs_price)
                - Deterministic rule engine decides SELL NOW / WAIT / NEUTRAL in SQL
                - Perishable crops cap WAIT at 1 week; sparse data forces NEUTRAL
                                               |
                                               v
                  [5. Gemini GenAI Grounded Briefing & Number Validator]
                - Passes structured JSON metrics to Google Gemini API (google-genai SDK,
                  JSON response schema, default model gemini-3.8-flash)
                - Validator enforces verdict match, Devanagari Hindi, no profit guarantees,
                  and strict number grounding (rejects ungrounded numbers, retries once,
                  falls back to verified bilingual template if retry/API fails, including
                  429 quota exhaustion, unavailable model, auth errors, and timeouts)
                                               |
                                               v
                         [6. FastAPI REST Backend (backend/)]
                - Typed Pydantic v2 schemas, query validation, CORS & error handling
                - Exposes health, overview, explorer, Q1-Q7 analytics, AI advisor,
                  rolling-origin backtester & CSV export, and read-only pipeline audit
                                               |
                                               v
                       [7. Next.js Web Dashboard (frontend/)]
                - App Router + TypeScript + Tailwind CSS + shadcn/ui + Recharts
                - 7 responsive pages communicating strictly via FastAPI REST API
                - Zero direct DB connections or client-side API keys
```

## 2. Design Principles

1. **SQL Decides, GenAI Explains**: The LLM never chooses `SELL NOW` vs `WAIT`. Deterministic SQL rules produce the verdict and numeric evidence; Gemini translates those exact numbers into farmer-readable English and Hindi.
2. **Source Honesty & Separation**: Live `data.gov.in` daily prices do not carry arrival tonnage (`quantity_tonnes`). CEDA historical arrival data lives separately in `warehouse.fact_arrivals_daily`, and reporting-market counts are never substituted for arrival volume.
3. **No Silent Drops**: Every rejected record is captured with its raw payload and rejection reason in `operational.dq_rejects`.
4. **Thin Presentation Tier**: All SQL analytics, rolling-origin backtesting, and server-side Gemini advisories stay in Python behind FastAPI; Next.js strictly handles visualization, filtering, and UI state.
5. **Reproducible Demo & Cloud Ready**: Works immediately out-of-the-box in clearly labelled Demo Mode (SQLite mirror with `STDDEV_SAMP` and `CORR` aggregates) when `DATABASE_URL` is unconfigured, and switches seamlessly to Supabase Cloud PostgreSQL + Supabase Storage once `.env` is populated.
