# MandiMitra Data Dictionary

## 1. Warehouse Dimensions & Mapping

### `warehouse.dim_date`
| Column | Type | Description |
|---|---|---|
| `date_id` | `INT` (PK) | Integer surrogate date key formatted as `YYYYMMDD` |
| `full_date` | `DATE` | ISO calendar date |
| `year` | `SMALLINT` | Calendar year |
| `month` | `SMALLINT` | Calendar month (`1..12`) |
| `week` | `SMALLINT` | ISO week number (`1..53`) |
| `season` | `TEXT` | Indian agricultural season (`Kharif`, `Rabi`, `Zaid`) |

### `warehouse.dim_commodity`
| Column | Type | Description |
|---|---|---|
| `commodity_id` | `SERIAL` (PK) | Unique commodity identifier |
| `commodity_name` | `TEXT` | Canonical commodity name (e.g. `Tomato`, `Onion`, `Wheat`) |
| `category` | `TEXT` | Crop group (`vegetable`, `cereal`, `pulse`, `oilseed`, `spice`) |
| `storable` | `BOOLEAN` | Whether crop can be safely held (`false` caps `WAIT` to 1 week) |

### `warehouse.dim_market`
| Column | Type | Description |
|---|---|---|
| `market_id` | `SERIAL` (PK) | Unique APMC mandi identifier |
| `market_name` | `TEXT` | Standardized market name |
| `district` | `TEXT` | Standardized district name |
| `state` | `TEXT` | Standardized Indian state / UT name |

### `warehouse.dim_crop` & `warehouse.map_commodity_crop`
- Links Agmarknet market commodities (`dim_commodity`) to Directorate of Economics & Statistics APY crops (`dim_crop`) for Q7 production-vs-price analytics.

## 2. Fact Tables

### `warehouse.fact_price_daily`
- Natural PK: `(date_id, market_id, commodity_id, variety, grade)`
- Measures: `min_price`, `max_price`, `modal_price` (₹ per quintal)
- Quality flags: `outlier_flag` (`TRUE` when modal price lies outside `[Q1 - 3*IQR, Q3 + 3*IQR]`), `source_name` (`agmarknet_api`, `ceda_archive`, or `demo_agmarknet_sample`).

### `warehouse.fact_arrivals_daily`
- Natural PK: `(date_id, market_id, commodity_id)`
- Measure: `quantity_tonnes` (metric tonnes from CEDA historical archive / demo arrivals).

### `warehouse.fact_crop_production`
- Natural PK: `(crop_year, season, state, district, crop_id)`
- Measures: `area_ha` (hectares), `production_t` (tonnes), `yield_t_ha` (tonnes per hectare).

## 3. Analytics & Operational Tables

- `operational.pipeline_run_log`: Run status, row counts (`rows_fetched`, `rows_loaded`, `rows_rejected`), errors, and metadata.
- `operational.dq_rejects`: Rejected raw JSON payloads with explicit `reject_reason`.
- `analytics.market_signals`: Deterministic verdict (`SELL NOW`, `WAIT`, `NEUTRAL`), verdict label, momentum, seasonal position, arrivals trend, 12m CV, evidence completeness, and threshold configuration.
- `analytics.briefings`: Stored bilingual (`reason_en`, `caution_en`, `reason_hi`, `caution_hi`) briefings with exact input `signal_json`, `validation_status`, and `used_fallback`.
