"""Unit tests for ETL standardization, date parsing, and unit conversions."""

from __future__ import annotations

from datetime import date

from etl.cleaning import (
    classify_commodity_storability,
    clean_price_records,
    convert_arrivals_to_tonnes,
    derive_indian_season,
    normalize_commodity_name,
    normalize_district_name,
    normalize_state_name,
    parse_date,
    to_numeric,
)


def test_name_standardization() -> None:
    assert normalize_state_name("  orissa ") == "Odisha"
    assert normalize_state_name("madhya   pradesh") == "Madhya Pradesh"
    assert normalize_state_name("delhi") == "NCT of Delhi"
    assert normalize_district_name("bangalore (urban)") == "Bengaluru"
    assert normalize_commodity_name("Bengal Gram(Gram)(Whole)") == "Chickpea"
    assert normalize_commodity_name("  soyabean ") == "Soybean"


def test_agmarknet_commodity_aliases_and_distinct_forms() -> None:
    # Target Pigeon Pea (Tur) aliases observed on data.gov.in
    assert normalize_commodity_name("Red Gram/Arhar/Tur(Whole)") == "Pigeon Pea (Tur)"
    assert normalize_commodity_name("Red Gram/Arhar/Tur") == "Pigeon Pea (Tur)"
    assert normalize_commodity_name("Arhar/Tur(Whole)") == "Pigeon Pea (Tur)"
    assert normalize_commodity_name("Arhar (Tur/Red Gram)(Whole)") == "Pigeon Pea (Tur)"
    assert normalize_commodity_name("Arhar/Tur") == "Pigeon Pea (Tur)"
    assert normalize_commodity_name("Tur/Arhar") == "Pigeon Pea (Tur)"

    # Other target aliases
    assert normalize_commodity_name("Green Gram (Moong)(Whole)") == "Green Gram (Moong)"
    assert normalize_commodity_name("Green Gram/Moong(Whole)") == "Green Gram (Moong)"
    assert normalize_commodity_name("Moong(Green Gram)(Whole)") == "Green Gram (Moong)"
    assert normalize_commodity_name("Bengal Gram(Whole)") == "Chickpea"
    assert normalize_commodity_name("Soyabean(Whole)") == "Soybean"
    assert normalize_commodity_name("Chilli(Green)") == "Green Chilli"

    # Processed / distinct forms must remain distinct from whole raw target averages
    assert normalize_commodity_name("Pegeon Pea(Arhar Fali)") == "Pegeon Pea(Arhar Fali)"
    assert normalize_commodity_name("Dry Chillies") == "Dry Chillies"
    assert normalize_commodity_name("Bengal Gram Dal(Chana Dal)") == "Bengal Gram Dal(Chana Dal)"
    assert normalize_commodity_name("Green Gram Dal(Moong Dal)") == "Green Gram Dal(Moong Dal)"
    assert normalize_commodity_name("Broken Rice") == "Broken Rice"
    assert normalize_commodity_name("Mustard Oil") == "Mustard Oil"

    # Merge policy: every paddy spelling is one canonical Paddy commodity (the variety keeps the
    # original detail); Rice is never merged into Paddy. Spring onion, ambiguous chillies and raw
    # groundnut pods stay distinct.
    for raw in ("Paddy(Dhan)", "Paddy(Dhan)(Common)", "Paddy(Common)", "Paddy(Basmati)", "Paddy(Fine/Superfine/Grade A)", "Paddy"):
        assert normalize_commodity_name(raw) == "Paddy"
    assert normalize_commodity_name("Rice") == "Rice"
    assert normalize_commodity_name("Rice(Fine/Superfine/Grade A)") != "Paddy"
    assert normalize_commodity_name("Onion Green") == "Onion Green"
    assert normalize_commodity_name("Chillies") == "Chillies"
    assert normalize_commodity_name("Chillies(Green)") == "Green Chilli"
    assert normalize_commodity_name("Groundnut pods(raw)") == "Groundnut Pods(Raw)"
    assert normalize_commodity_name("Groundnut Pods") == "Groundnut Pods"


def test_commodity_storability_classification() -> None:
    # Target crops (aligned with config.TARGET_COMMODITIES)
    assert classify_commodity_storability("Wheat") == ("cereal", True)
    assert classify_commodity_storability("Pigeon Pea (Tur)") == ("pulse", True)
    assert classify_commodity_storability("Potato") == ("vegetable", True)
    assert classify_commodity_storability("Onion") == ("vegetable", False)
    assert classify_commodity_storability("Tomato") == ("vegetable", False)
    assert classify_commodity_storability("Green Chilli") == ("vegetable", False)

    # Non-target perishables (previously defaulted erroneously to storable=True)
    for perishable in (
        "Bhindi(Ladies Finger)",
        "Bitter Gourd",
        "Bottle Gourd",
        "Carrot",
        "Raddish",
        "Cucumbar(Kheera)",
        "Beetroot",
        "Turnip",
        "Banana",
        "Papaya",
        "Pegeon Pea(Arhar Fali)",
    ):
        _cat, storable = classify_commodity_storability(perishable)
        assert storable is False, f"{perishable} must be classified as non-storable"

    # Non-target storable commodities
    assert classify_commodity_storability("Dry Chillies") == ("spice", True)
    assert classify_commodity_storability("Bengal Gram Dal(Chana Dal)") == ("pulse", True)
    assert classify_commodity_storability("Broken Rice") == ("cereal", True)
    assert classify_commodity_storability("Mustard Oil") == ("oilseed", True)

    # Unknown commodities are flagged as unclassified (non-storable) instead of guessed
    assert classify_commodity_storability("Unknown Exotic Crop") == ("unclassified", False)


def test_date_and_numeric_parsing() -> None:
    assert parse_date("19/11/2026") == date(2026, 11, 19)
    assert parse_date("2026-11-19") == date(2026, 11, 19)
    assert parse_date("19-Nov-2026") == date(2026, 11, 19)
    assert parse_date("NR") is None
    assert to_numeric("₹ 2,450.50") == 2450.5
    assert to_numeric("-") is None
    assert convert_arrivals_to_tonnes("150", unit_hint="quintals") == 15.0
    assert derive_indian_season(date(2026, 7, 15)) == "Kharif"
    assert derive_indian_season(date(2026, 1, 15)) == "Rabi"
    assert derive_indian_season(date(2026, 4, 20)) == "Zaid"


def test_clean_price_records_fills_blank_variety_grade() -> None:
    df = clean_price_records(
        [
            {
                "state": "  uttar   pradesh ",
                "district": " agra ",
                "market": " agra ",
                "commodity": "potato",
                "variety": "",
                "grade": None,
                "arrival_date": "18/11/2026",
                "min_price": "1200",
                "max_price": "1600",
                "modal_price": "1400",
            }
        ]
    )
    assert df.loc[0, "state"] == "Uttar Pradesh"
    assert df.loc[0, "variety"] == "NA"
    assert df.loc[0, "grade"] == "NA"
    assert df.loc[0, "modal_price"] == 1400.0
