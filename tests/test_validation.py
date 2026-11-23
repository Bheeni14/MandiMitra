"""Unit tests for data quality validation, natural-key deduplication, 3-IQR flagging, and reject logging."""

from __future__ import annotations

from etl.cleaning import clean_apy_records, clean_arrival_records, clean_price_records
from etl.validation import validate_apy_records, validate_arrival_records, validate_price_records


def test_price_validation_rejects_and_deduplicates() -> None:
    raw = [
        # Valid row 1
        {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "variety": "Red", "grade": "FAQ", "arrival_date": "2026-11-10", "min_price": 1500, "max_price": 2200, "modal_price": 1900},
        # Duplicate natural key of row 1
        {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "variety": "Red", "grade": "FAQ", "arrival_date": "2026-11-10", "min_price": 1500, "max_price": 2200, "modal_price": 1950},
        # Non-positive price
        {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "variety": "Red", "grade": "FAQ", "arrival_date": "2026-11-11", "min_price": -10, "max_price": 2200, "modal_price": 1900},
        # Modal outside min/max
        {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "variety": "Red", "grade": "FAQ", "arrival_date": "2026-11-12", "min_price": 1500, "max_price": 2000, "modal_price": 2500},
        # Missing arrival date
        {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "variety": "Red", "grade": "FAQ", "arrival_date": "", "min_price": 1500, "max_price": 2000, "modal_price": 1700},
    ]
    cleaned = clean_price_records(raw)
    res = validate_price_records(cleaned)

    assert len(res.valid_df) == 1
    assert len(res.rejects) == 4
    reasons = {r["reject_reason"] for r in res.rejects}
    assert reasons == {
        "duplicate_natural_key",
        "non_positive_price",
        "modal_price_outside_min_max_range",
        "missing_or_invalid_arrival_date",
    }


def test_outlier_beyond_3iqr_flagged_not_deleted() -> None:
    raw = []
    for day in range(1, 12):
        modal = 1000 + (day % 3) * 20
        raw.append(
            {
                "state": "Karnataka",
                "district": "Kolar",
                "market": "Kolar",
                "commodity": "Tomato",
                "variety": "Local",
                "grade": "FAQ",
                "arrival_date": f"2026-11-{day:02d}",
                "min_price": modal - 100,
                "max_price": modal + 100,
                "modal_price": modal,
            }
        )
    # Extreme valid spike (> Q3 + 3*IQR)
    raw.append(
        {
            "state": "Karnataka",
            "district": "Kolar",
            "market": "Kolar",
            "commodity": "Tomato",
            "variety": "Local",
            "grade": "FAQ",
            "arrival_date": "2026-11-15",
            "min_price": 4500,
            "max_price": 5500,
            "modal_price": 5000,
        }
    )

    res = validate_price_records(clean_price_records(raw))
    assert len(res.valid_df) == 12  # Spike NOT deleted
    flagged = res.valid_df[res.valid_df["outlier_flag"]]
    assert len(flagged) == 1
    assert float(flagged.iloc[0]["modal_price"]) == 5000.0


def test_arrival_duplicates_summed_and_negative_rejected() -> None:
    raw = [
        {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "arrival_date": "2026-11-10", "quantity_tonnes": 100.5},
        {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "arrival_date": "2026-11-10", "quantity_tonnes": 49.5},
        {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "arrival_date": "2026-11-11", "quantity_tonnes": -5},
    ]
    res = validate_arrival_records(clean_arrival_records(raw))
    assert len(res.valid_df) == 1
    assert float(res.valid_df.iloc[0]["quantity_tonnes"]) == 150.0
    assert len(res.rejects) == 1
    assert res.rejects[0]["reject_reason"] == "negative_arrival_quantity"


def test_apy_rejects_aggregate_summary_rows_and_handles_units() -> None:
    raw = [
        # Valid district row in tonnes + kg/ha yield (Dataful combined unit string)
        {
            "crop_year": "2024-25",
            "season": "Kharif",
            "state": "Maharashtra",
            "district": "Nashik",
            "crop_name": "Onion",
            "area": 1000.0,
            "production": 15000.0,
            "yield": 15000.0,
            "unit": "area in Hectares, production in Tonnes, crop_yield in kg per Hectare",
        },
        # State Total summary row -> MUST be rejected (no double-counting)
        {
            "crop_year": "2024-25",
            "season": "Kharif",
            "state": "Maharashtra",
            "district": "State Total",
            "crop_name": "Onion",
            "area": 50000.0,
            "production": 750000.0,
            "yield": 15.0,
        },
        # All India summary row -> MUST be rejected
        {
            "crop_year": "2024-25",
            "season": "Kharif",
            "state": "All India",
            "district": "All India",
            "crop_name": "Onion",
            "area": 200000.0,
            "production": 3000000.0,
            "yield": 15.0,
        },
        # Cotton in Bales -> valid conversion (100 bales * 0.17 = 17.0 tonnes)
        {
            "crop_year": "2024-25",
            "season": "Kharif",
            "state": "Gujarat",
            "district": "Rajkot",
            "crop_name": "Cotton(lint)",
            "area": 200.0,
            "area_unit": "Hectares",
            "production": 100.0,
            "production_unit": "Bales",
        },
        # Coconut in Nuts -> non-weight unit MUST be rejected, never silently treated as tonnes
        {
            "crop_year": "2024-25",
            "season": "Whole Year",
            "state": "Karnataka",
            "district": "Tumakuru",
            "crop_name": "Coconut",
            "area": 500.0,
            "area_unit": "Hectares",
            "production": 450000.0,
            "production_unit": "Nuts",
        },
        # Unknown unit -> MUST be rejected, never silently treated as tonnes
        {
            "crop_year": "2024-25",
            "season": "Rabi",
            "state": "Punjab",
            "district": "Ludhiana",
            "crop_name": "Wheat",
            "area": 300.0,
            "area_unit": "Hectares",
            "production": 900.0,
            "production_unit": "Crates",
        },
    ]

    cleaned = clean_apy_records(raw)
    res = validate_apy_records(cleaned)

    assert len(res.valid_df) == 2
    onion_row = res.valid_df[res.valid_df["crop_name"] == "Onion"].iloc[0]
    assert float(onion_row["production_t"]) == 15000.0
    assert float(onion_row["yield_t_ha"]) == 15.0

    cotton_row = res.valid_df[res.valid_df["crop_name"] == "Cotton(Lint)"].iloc[0]
    assert float(cotton_row["production_t"]) == 17.0
    assert float(cotton_row["yield_t_ha"]) == 0.085

    reasons = [r["reject_reason"] for r in res.rejects]
    assert "aggregate_district_summary_row_excluded" in reasons
    assert "aggregate_state_summary_row_excluded" in reasons
    assert any(r.startswith("non_weight_production_unit:Nuts") for r in reasons)
    assert any(r.startswith("unsupported_production_unit:Crates") for r in reasons)

