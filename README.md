# MandiMitra — Crop Price and Market Analytics for India

> **"Smarter Mandi Decisions. Better Agricultural Insights."**  
> HCL Hackathon Capstone Project — **Bheeni Agarwal** & **Anurag Tiwari** (B.Tech CSE - Data Science, Ajay Kumar Garg Engineering College, Ghaziabad)

---

## 1. Problem Statement & Objectives

Thousands of Indian agricultural markets (mandis) publish daily wholesale prices via Agmarknet, yet farmers and small traders face volatile prices, wide cross-state price spreads, and raw tables that fail to answer a practical question: **Should I sell today or wait?**

**MandiMitra** implements a complete end-to-end data engineering, SQL analytics, grounded GenAI, and backtesting platform:

1. **Automated Multi-Source Ingestion**: Daily prices from `data.gov.in` Agmarknet REST API, historical prices & arrival tonnage from CEDA Ashoka Agmarknet archive, and district-level crop Area, Production, Yield (APY) from DES.
2. **Data Quality & Star-Schema Warehouse**: Reusable Python cleaning/validation (natural-key deduplication, 3-IQR outlier flagging, zero silent drops into `operational.dq_rejects`) loaded idempotently into PostgreSQL/Supabase (`staging`, `warehouse`, `operational`, `analytics`).
3. **Seven SQL Analytical Views (Q1–Q7)**: Volatility (CV), state-wise price gaps, month-over-month trends & 3M moving average, arrivals vs price correlation, seasonal price index, best 7-day markets, and production vs price correlation.
4. **Deterministic Signal Engine ("SQL Decides, GenAI Explains")**: Rule-based SQL/metric verdicts (`SELL NOW`, `WAIT 1 WEEK`, `WAIT 1-2 WEEKS`, `NEUTRAL`) with storability caps and coverage guards.
5. **Grounded Bilingual Google Gemini Briefings**: Server-side explanations & cautions in **English and Hindi** via the Gemini API (`google-genai` SDK, free tier, structured JSON output), verified by an automated number-grounding and verdict-consistency validator with deterministic template fallback.
6. **Zero-Leakage Rolling-Origin Backtesting**: Evaluates verdict accuracy and `WAIT` precision strictly using past data (`<= decision_date`) against Always-SELL and Momentum-Only baselines.
7. **Modern Web Architecture**: Typed **FastAPI** REST backend exposing existing Python analytics & warehouse services to a **Next.js (App Router, TypeScript, Tailwind CSS, shadcn/ui, Recharts)** frontend.

---

## 2. System Architecture

```
Agmarknet API (data.gov.in) ──┐
CEDA Historical CSVs / API  ──┼──► Python Ingestion + local Raw Zone (data/raw/)
DES APY Production CSVs     ──┘     (optional Supabase Storage upload; OFF by default)
                                              │
                                              ▼
                               Pandas Cleaning & Validation (3-IQR, DQ Rejects)
                                              │
                                              ▼
                            PostgreSQL / Supabase Star Schema (Idempotent UPSERTs)
                                              │
                                              ▼
                            7 SQL Views (Q1–Q7) + Deterministic Signal Engine
                                              │
                                              ▼
                       Gemini AI Briefing (EN/HI; OFF by default) ──► Validator ──► Fallback
                                              │
                                              ▼
                          FastAPI REST API (/api/*, Pydantic v2, CORS)
                                              │
                                              ▼
                    Next.js Web Dashboard (7 App Router Pages, TypeScript, Recharts)
```

See [docs/architecture.md](docs/architecture.md), [docs/database.md](docs/database.md), and [docs/data_dictionary.md](docs/data_dictionary.md) for detailed design specs.

---

## 3. Technology Stack

- **Frontend:** Next.js (App Router), React, TypeScript, Tailwind CSS, shadcn/ui, Recharts, Lucide icons (`frontend/`)
- **Backend API:** FastAPI, Uvicorn, Pydantic v2 (`backend/`)
- **Core Python & Data:** Python 3.11+, Pandas, NumPy, Requests, SQLAlchemy
- **Storage & Warehouse:** PostgreSQL (Supabase Cloud) + Demo SQLite mirror; raw files kept locally under `data/raw/` (optional Supabase Storage upload is **disabled by default**)
- **GenAI:** Optional Google Gemini API via `google-genai` (`GEMINI_ENABLED=false` by default; deterministic bilingual template fallback)
- **Testing & Automation:** Pytest, Vitest + React Testing Library; GitHub Actions daily ingestion workflow is **disabled by default** (manual confirmation required)

---

## 4. Data Sources & Honesty Notes

| Source | Purpose | Link |
|---|---|---|
| **Agmarknet Daily Prices** | Live wholesale min/max/modal prices (Resource `9ef84268-d588-465a-a308-a864a43d0070`) | [data.gov.in](https://data.gov.in/) |
| **CEDA Agmarknet Archive** | Historical daily prices & arrival quantities (tonnes) | [agmarknet.ceda.ashoka.edu.in](https://agmarknet.ceda.ashoka.edu.in/) ([README](https://agmarknet.ceda.ashoka.edu.in/README.html)) |
| **DES Crop Production (APY)** | District/season-wise Area, Production & Yield | [data.desagri.gov.in](https://data.desagri.gov.in/website/crops-apy-report-web) / [aps.dac.gov.in](https://aps.dac.gov.in/APY/Public_Report1.aspx) |

**Important Data Integrity Rules:**
- Live `data.gov.in` daily prices do not carry arrival tonnage; reporting-market counts are never substituted as arrival tonnage.
- When `DATABASE_URL` / API keys are unconfigured or upstream portals are unreachable, MandiMitra runs in **clearly labelled Demo Mode** using deterministic simulated sample fixtures (`data/sample/`) and never misrepresents demo data as live government pulls.
- Database credentials and `GEMINI_API_KEY` remain strictly server-side in Python/FastAPI and are never exposed to Next.js.

---

## 5. Installation & Quickstart

### Step 1: Python Backend Setup
```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### Step 2: Frontend Setup
```bash
cd frontend
npm install
cd ..
```

### Step 3: Configure Environment Variables
```bash
cp .env.example .env
```
Edit `.env` to add credentials as needed. Safe defaults (already in `.env.example`):

| Switch | Default | Meaning |
|---|---|---|
| `RAW_STORAGE_UPLOAD_ENABLED` | `false` | Keep raw Agmarknet/CEDA files in local `data/raw/` only |
| `GEMINI_ENABLED` | `false` | Use deterministic bilingual template explanations |
| `ADVISOR_PERSIST` | `false` | Do not write `market_signals` / `briefings` on non-demo DBs |
| `DEMO_MODE` | `auto` | Demo SQLite when `DATABASE_URL` is empty |

Leave `DATABASE_URL` blank (or `DEMO_MODE=auto`) to run immediately in clearly labelled **Demo Mode**.

**Gemini briefings (optional):** set `GEMINI_API_KEY` and explicitly set `GEMINI_ENABLED=true`. `GEMINI_MODEL` defaults to `gemini-3.8-flash`. With Gemini disabled, missing key, quota exhaustion (`429`), timeout, or failed validation, MandiMitra serves the validated deterministic bilingual template instead.

**Supabase Storage (optional):** credentials alone never upload. Set `RAW_STORAGE_UPLOAD_ENABLED=true` only when remote raw-object upload is intentionally required.

---

## 6. Running Backend & Frontend Independently (Demo or Live)

Works out-of-the-box with **zero external credentials** in Demo Mode (auto-bootstraps `data/sample/mandimitra_demo.db` on startup when `DATABASE_URL` is unset):

### Terminal 1 — Start FastAPI Backend (port 8000)
```bash
# Optional explicit demo seed first:
python scripts/ingest_all.py --demo

# Start FastAPI server:
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```
API docs are available at `http://127.0.0.1:8000/docs` and health check at `http://127.0.0.1:8000/api/health`.

### Terminal 2 — Start Next.js Frontend (port 3000)
```bash
cd frontend
# Optional: set NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 (default)
npm run dev
```
Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## 7. Production / Live Pipeline Execution (Supabase + Live APIs)

Once `.env` contains your Supabase PostgreSQL `DATABASE_URL` and `DATA_GOV_IN_API_KEY`:

```bash
# 1. Apply pending migrations explicitly (never via app startup or init_db on PostgreSQL)
python scripts/migrate.py --status
python scripts/migrate.py --apply   # only after backup + explicit approval

# 2. Verify schema (PostgreSQL does not seed here). Seed only when deliberate:
python scripts/init_db.py
python scripts/init_db.py --seed-reference   # optional, explicit

# 3. Daily Agmarknet capture (complete pagination by default; raw JSON kept in data/raw/)
python scripts/ingest_all.py --daily
# Optional safety cap (marks the run partial_success when truncated):
# python scripts/ingest_all.py --daily --max-records 5000

# 4. Optional one-time / backfill CEDA & DES APY CSV imports
python scripts/ingest_all.py --ceda path/to/ceda_prices.csv path/to/ceda_arrivals.csv
python scripts/ingest_all.py --apy path/to/apy_production.csv

# 5. SQL Analytics Q1-Q7 (advisor persistence stays off unless ADVISOR_PERSIST=true)
python scripts/run_analytics.py --commodity Tomato --briefings 3

# 6. Walk-forward threshold tuning & rolling-origin backtesting
python scripts/run_backtest.py --tune --eval-days 365 --step-days 7

# 7. Start FastAPI + Next.js production build
uvicorn backend.main:app --host 0.0.0.0 --port 8000
cd frontend && npm run build && npm run start
```

The GitHub Actions workflow `.github/workflows/daily_ingestion.yml` does **not** run on push. It runs daily at 01:30 UTC (07:00 IST) once the workflow is on GitHub with repository secrets configured. A manual `workflow_dispatch` still requires typing `APPLY-PRODUCTION-INGESTION`.

---

## 8. Running Tests & Verification

```bash
# Backend unit + FastAPI API test suite (isolated temp SQLite warehouses & mocked Gemini)
pytest -v -m "not integration"

# Live PostgreSQL + data.gov.in integration check (requires DATABASE_URL & DATA_GOV_IN_API_KEY)
pytest -v -m integration

# Frontend tests, TypeScript check, ESLint & production build
cd frontend
npm run test
npm run typecheck
npm run lint
npm run build
```

---

## 9. Known Limitations & Responsible Use

1. **Wholesale vs. Farm-Gate Prices**: Mandi prices reflect APMC wholesale transactions; net farmer realization varies with transport, grading, and mandi fees.
2. **Intermittent Reporting**: Sparse mandis fall back to `NEUTRAL` whenever trailing 90-day observations fall below minimum coverage thresholds.
3. **Guidance, Not a Guarantee**: Briefings explain historical trends and supply dynamics; they are informational decision support and never promise guaranteed returns.
4. **Backtest CSV export vs. displayed results**: Backtest results are kept in an in-memory cache inside the API process (one entry per evaluation window and step). Pagination and `/api/backtest/export` reuse that entry, so after a normal refresh the file matches the page. The cache is lost on server restart, is not shared between workers, and can be replaced by a refresh from another browser tab; the export can then come from a different source (precomputed results file vs. live run) than the page. The export endpoint reports its source and row count in the `X-Backtest-Source` and `X-Backtest-Rows` headers, and the Backtesting page compares them with what is on screen and warns on a mismatch. A matching source and row count is evidence of consistency, not proof: row contents are not compared. For a guaranteed pair, run a single API process and use *Re-run Backtest* followed by *Export CSV*.
