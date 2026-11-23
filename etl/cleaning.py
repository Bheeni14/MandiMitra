"""Data cleaning & standardization utilities for MandiMitra."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from config import TARGET_COMMODITIES
from etl.commodity_catalog import (
    UNCLASSIFIED_CATEGORY,
    compact_commodity_key,
    heuristic_commodity_classification,
    lookup_catalog_classification,
)

STATE_ALIASES: dict[str, str] = {
    "andaman & nicobar": "Andaman & Nicobar Islands",
    "andaman and nicobar": "Andaman & Nicobar Islands",
    "andaman and nicobar islands": "Andaman & Nicobar Islands",
    "andhra pradesh": "Andhra Pradesh",
    "arunachal pradesh": "Arunachal Pradesh",
    "assam": "Assam",
    "bihar": "Bihar",
    "chandigarh": "Chandigarh",
    "chattisgarh": "Chhattisgarh",
    "chhattisgarh": "Chhattisgarh",
    "dadra and nagar haveli": "Dadra & Nagar Haveli and Daman & Diu",
    "daman and diu": "Dadra & Nagar Haveli and Daman & Diu",
    "delhi": "NCT of Delhi",
    "nct of delhi": "NCT of Delhi",
    "new delhi": "NCT of Delhi",
    "goa": "Goa",
    "gujarat": "Gujarat",
    "haryana": "Haryana",
    "himachal pradesh": "Himachal Pradesh",
    "jammu & kashmir": "Jammu & Kashmir",
    "jammu and kashmir": "Jammu & Kashmir",
    "jharkhand": "Jharkhand",
    "karnataka": "Karnataka",
    "kerala": "Kerala",
    # Agmarknet now reports Kerala as "Keralam"; CEDA and DES/APY still use "Kerala".
    # The canonical name is Kerala (approved 2026-11-20); migration 004 normalises stored markets.
    "keralam": "Kerala",
    "ladakh": "Ladakh",
    "madhya pradesh": "Madhya Pradesh",
    "maharashtra": "Maharashtra",
    "manipur": "Manipur",
    "meghalaya": "Meghalaya",
    "mizoram": "Mizoram",
    "nagaland": "Nagaland",
    "odisha": "Odisha",
    "orissa": "Odisha",
    "pondicherry": "Puducherry",
    "puducherry": "Puducherry",
    "punjab": "Punjab",
    "rajasthan": "Rajasthan",
    "sikkim": "Sikkim",
    "tamil nadu": "Tamil Nadu",
    "tamilnadu": "Tamil Nadu",
    "telangana": "Telangana",
    "tripura": "Tripura",
    "uttar pradesh": "Uttar Pradesh",
    "uttaranchal": "Uttarakhand",
    "uttarakhand": "Uttarakhand",
    "uttrakhand": "Uttarakhand",
    "west bengal": "West Bengal",
}

DISTRICT_ALIASES: dict[str, str] = {
    "ahmadnagar": "Ahilyanagar",
    "ahmednagar": "Ahilyanagar",
    "allahabad": "Prayagraj",
    "bangalore": "Bengaluru",
    "bangalore rural": "Bengaluru Rural",
    "bangalore urban": "Bengaluru Urban",
    "belgaum": "Belagavi",
    "burdwan": "Purba Bardhaman",
    "bardhaman": "Purba Bardhaman",
    "gulbarga": "Kalaburagi",
    "gurgaon": "Gurugram",
    "hooghly": "Hooghly",
    "hubli": "Hubballi",
    "mysore": "Mysuru",
    "nasik": "Nashik",
    "shimoga": "Shivamogga",
    "tumkur": "Tumakuru",
}

COMMODITY_ALIASES: dict[str, str] = {
    "tomato": "Tomato",
    "tomato hybrid": "Tomato",
    "tomato local": "Tomato",
    "tomato desi": "Tomato",
    "onion": "Onion",
    "onion big": "Onion",
    "cabbage": "Cabbage",
    "cauliflower": "Cauliflower",
    "brinjal": "Brinjal",
    "eggplant": "Brinjal",
    "green chilli": "Green Chilli",
    "chilli green": "Green Chilli",
    "chilli(green)": "Green Chilli",
    "chillies green": "Green Chilli",
    "chillies(green)": "Green Chilli",
    "green chillies": "Green Chilli",
    "potato": "Potato",
    "garlic": "Garlic",
    "turmeric": "Turmeric",
    "turmeric(raw)": "Turmeric",
    "turmeric(bulb)": "Turmeric",
    "turmeric(finger)": "Turmeric",
    "wheat": "Wheat",
    "rice": "Rice",
    "maize": "Maize",
    "soybean": "Soybean",
    "soyabean": "Soybean",
    "soya bean": "Soybean",
    "soyabean(whole)": "Soybean",
    "soyabean yellow": "Soybean",
    "soybean yellow": "Soybean",
    "groundnut": "Groundnut",
    "ground nut": "Groundnut",
    "groundnut(with shell)": "Groundnut",
    "mustard": "Mustard",
    "mustard seed": "Mustard",
    "rapeseed & mustard": "Mustard",
    "rapeseed and mustard": "Mustard",
    "chickpea": "Chickpea",
    "bengal gram": "Chickpea",
    "bengal gram(whole)": "Chickpea",
    "bengal gram(gram)(whole)": "Chickpea",
    "chana": "Chickpea",
    "chana(whole)": "Chickpea",
    "desi chana": "Chickpea",
    "gram": "Chickpea",
    "gram(whole)": "Chickpea",
    "pigeon pea": "Pigeon Pea (Tur)",
    "pigeon pea(tur)": "Pigeon Pea (Tur)",
    "pigeon pea (tur)": "Pigeon Pea (Tur)",
    "arhar": "Pigeon Pea (Tur)",
    "arhar(whole)": "Pigeon Pea (Tur)",
    "arhar/tur": "Pigeon Pea (Tur)",
    "arhar/tur(whole)": "Pigeon Pea (Tur)",
    "arhar(tur/red gram)(whole)": "Pigeon Pea (Tur)",
    "arhar (tur/red gram)(whole)": "Pigeon Pea (Tur)",
    "arhar(tur/red gram)": "Pigeon Pea (Tur)",
    "red gram": "Pigeon Pea (Tur)",
    "red gram(whole)": "Pigeon Pea (Tur)",
    "red gram/arhar/tur": "Pigeon Pea (Tur)",
    "red gram/arhar/tur(whole)": "Pigeon Pea (Tur)",
    "red gram(arhar/tur)(whole)": "Pigeon Pea (Tur)",
    "tur": "Pigeon Pea (Tur)",
    "tur(whole)": "Pigeon Pea (Tur)",
    "tur/arhar": "Pigeon Pea (Tur)",
    "green gram": "Green Gram (Moong)",
    "green gram(whole)": "Green Gram (Moong)",
    "green gram (moong)": "Green Gram (Moong)",
    "green gram(moong)": "Green Gram (Moong)",
    "green gram (moong)(whole)": "Green Gram (Moong)",
    "green gram(moong)(whole)": "Green Gram (Moong)",
    "green gram/moong": "Green Gram (Moong)",
    "green gram/moong(whole)": "Green Gram (Moong)",
    "moong": "Green Gram (Moong)",
    "moong(whole)": "Green Gram (Moong)",
    "moong(green gram)": "Green Gram (Moong)",
    "moong(green gram)(whole)": "Green Gram (Moong)",
    # Paddy is one canonical commodity, separate from Rice (approved 2026-11-20). The raw sub-type
    # (Common / Basmati / Fine...) is preserved in the migration evidence, and the raw variety is
    # kept unchanged in the variety field. Genuine Rice records are never merged into Paddy.
    "paddy": "Paddy",
    "paddy dhan": "Paddy",
    "paddy(dhan)": "Paddy",
    "paddy(dhan)(common)": "Paddy",
    "paddy(dhan)(basmati)": "Paddy",
    "paddy (dhan)(basmati)": "Paddy",
    "paddy(common)": "Paddy",
    "paddy(basmati)": "Paddy",
    "paddy(fine/superfine/grade a)": "Paddy",
}

_TARGET_STORABILITY: dict[str, tuple[str, bool]] = {
    spec.name.lower(): (spec.category, spec.storable) for spec in TARGET_COMMODITIES
}

# Agmarknet names that denote a different commodity (or an ambiguous one) from the target their
# prefix suggests. They are never merged; a future mapping needs explicit source evidence.
DISTINCT_COMMODITY_KEYS: frozenset[str] = frozenset(
    {
        "onion green",
        "chillies",
        "groundnut pods",
        "groundnut pods(raw)",
    }
)

CROP_TO_CANONICAL_MAP: dict[str, str] = {
    spec.crop_name.lower(): spec.crop_name for spec in TARGET_COMMODITIES
}
CROP_TO_CANONICAL_MAP.update(
    {
        "soybean": "Soyabean",
        "soyabean": "Soyabean",
        "gram": "Gram",
        "chickpea": "Gram",
        "bengal gram": "Gram",
        "bengal gram(gram)": "Gram",
        "arhar": "Arhar/Tur",
        "tur": "Arhar/Tur",
        "arhar/tur": "Arhar/Tur",
        "pigeon pea": "Arhar/Tur",
        "pigeon pea (tur)": "Arhar/Tur",
        "red gram/arhar/tur": "Arhar/Tur",
        "moong": "Moong(Green Gram)",
        "moong(green gram)": "Moong(Green Gram)",
        "green gram": "Moong(Green Gram)",
        "green gram (moong)": "Moong(Green Gram)",
        "rapeseed & mustard": "Rapeseed & Mustard",
        "rapeseed and mustard": "Rapeseed & Mustard",
        "mustard": "Rapeseed & Mustard",
        "paddy": "Rice",
        "rice": "Rice",
        "chillies": "Chillies",
        "dry chillies": "Chillies",
        "green chilli": "Chillies",
    }
)

_EMPTY_TOKENS = {"", "na", "n/a", "nan", "none", "null", "-", "--", "nr"}

AGGREGATE_STATE_TOKENS = {
    "all india",
    "india",
    "total",
    "grand total",
    "national total",
    "all states",
}

AGGREGATE_DISTRICT_TOKENS = {
    "state total",
    "all india",
    "all districts",
    "district total",
    "grand total",
    "total",
    "all",
    "india",
}


def is_aggregate_location(value: Any, level: str = "district") -> bool:
    """Detect summary/total rows such as 'State Total' or 'All India' in APY reports."""
    cleaned = clean_text(value).lower()
    if not cleaned:
        return False
    tokens = AGGREGATE_STATE_TOKENS if level == "state" else AGGREGATE_DISTRICT_TOKENS
    if cleaned in tokens:
        return True
    if level == "district" and any(phrase in cleaned for phrase in ("state total", "all india", "grand total", "all districts")):
        return True
    if level == "state" and any(phrase in cleaned for phrase in ("all india", "grand total")):
        return True
    return False


def _extract_combined_unit_clauses(unit_text: str) -> tuple[str, str, str]:
    """Parse combined unit text like 'area in Hectares, production in Tonnes, crop_yield in kg per Hectare'."""
    area_u = ""
    prod_u = ""
    yield_u = ""
    for part in unit_text.split(","):
        p = part.strip().lower()
        if "area" in p:
            area_u = p.split(" in ", 1)[-1].strip() if " in " in p else p
        elif "production" in p:
            prod_u = p.split(" in ", 1)[-1].strip() if " in " in p else p
        elif "yield" in p:
            yield_u = p.split(" in ", 1)[-1].strip() if " in " in p else p
    return area_u, prod_u, yield_u


def convert_apy_area_to_ha(area: float | None, area_unit: str) -> tuple[float | None, str | None]:
    if area is None:
        return None, None
    u = clean_text(area_unit).lower()
    if not u or u in {"ha", "hectare", "hectares"}:
        return round(area, 2), None
    if u in {"000 hectares", "thousand hectares", "'000 hectares"}:
        return round(area * 1000.0, 2), None
    if u in {"acre", "acres"}:
        return round(area * 0.404686, 2), None
    return None, f"unsupported_area_unit:{area_unit}"


def convert_apy_production_to_tonnes(
    production: float | None,
    production_unit: str,
    crop_name: str,
) -> tuple[float | None, str | None]:
    """Convert APY production units strictly; never silently coerce Bales/Nuts/unknown units to tonnes."""
    if production is None:
        return None, None
    u = clean_text(production_unit).lower()
    if not u or u in {"t", "tonne", "tonnes", "metric tonne", "metric tonnes", "mt"}:
        return round(production, 2), None
    if any(tok in u for tok in ("000 tonnes", "thousand tonnes", "'000 tonnes")):
        return round(production * 1000.0, 2), None
    if u in {"kg", "kgs", "kilogram", "kilograms"}:
        return round(production * 0.001, 2), None
    if u in {"qtl", "quintal", "quintals"}:
        return round(production * 0.1, 2), None
    if "nut" in u or "nos" in u or "number" in u:
        return None, f"non_weight_production_unit:{production_unit}"
    if "bale" in u:
        # Check explicit bale weight in unit string first (e.g., "Bales of 170 Kgs" / "Bales (180 kg)")
        wt_match = re.search(r"(\d+(?:\.\d+)?)\s*kg", u)
        if wt_match:
            kg_per_bale = float(wt_match.group(1))
            return round(production * (kg_per_bale / 1000.0), 2), None
        crop_low = crop_name.lower()
        if "cotton" in crop_low:
            return round(production * 0.17, 2), None
        if "jute" in crop_low or "mesta" in crop_low:
            return round(production * 0.18, 2), None
        return None, f"unsupported_bale_unit_for_crop:{crop_name}:{production_unit}"
    return None, f"unsupported_production_unit:{production_unit}"


def convert_apy_yield_to_t_ha(
    yld: float | None,
    yield_unit: str,
    area_ha: float | None,
    production_t: float | None,
) -> tuple[float | None, str | None]:
    if yld is None:
        if area_ha is not None and production_t is not None and area_ha > 0:
            return round(production_t / area_ha, 4), None
        return None, None

    u = clean_text(yield_unit).lower()
    if u in {"", "t/ha", "tonne/ha", "tonnes/ha", "tonne per hectare", "tonnes per hectare", "mt/ha"}:
        if not u and area_ha and production_t is not None and area_ha > 0 and production_t > 0:
            expected = production_t / area_ha
            if yld > expected * 100:
                return round(yld / 1000.0, 4), None
        return round(yld, 4), None
    if any(tok in u for tok in ("kg/ha", "kg per hectare", "kilogram/hectare", "kgs/ha")):
        return round(yld / 1000.0, 4), None
    if any(tok in u for tok in ("qtl/ha", "quintal/ha", "quintals/ha", "quintal per hectare")):
        return round(yld * 0.1, 4), None
    # If yield is in count/bales per ha, derive consistent t/ha from converted production & area if available
    if area_ha is not None and production_t is not None and area_ha > 0:
        return round(production_t / area_ha, 4), None
    return None, f"unsupported_yield_unit:{yield_unit}"


def clean_text(value: Any) -> str:
    """Trim whitespace, collapse internal spacing, and treat sentinel tokens as empty string."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    if cleaned.lower() in _EMPTY_TOKENS:
        return ""
    return cleaned


def normalize_state_name(value: Any) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    key = re.sub(r"\s+", " ", raw.lower())
    return STATE_ALIASES.get(key, raw.title())


def normalize_district_name(value: Any) -> str:
    raw = clean_text(value)
    if not raw:
        return "Unknown"
    raw = re.sub(r"\([^)]*\)", "", raw).strip()
    if not raw:
        return "Unknown"
    key = raw.lower()
    return DISTRICT_ALIASES.get(key, raw.title())


def normalize_market_name(value: Any) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    raw = re.sub(r"[,;]+$", "", raw).strip()
    parts = []
    for token in raw.split(" "):
        if token.upper() in {"APMC", "AMC", "RMC", "KRISHI"}:
            parts.append(token.upper() if token.upper() != "KRISHI" else "Krishi")
        else:
            parts.append(token.capitalize())
    return " ".join(parts)


_compact_alias_key = compact_commodity_key


def normalize_commodity_name(value: Any) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    key = raw.lower()
    compacted = _compact_alias_key(raw)
    if key in DISTINCT_COMMODITY_KEYS or compacted in DISTINCT_COMMODITY_KEYS:
        return raw.title()
    if key in COMMODITY_ALIASES:
        return COMMODITY_ALIASES[key]
    if compacted in COMMODITY_ALIASES:
        return COMMODITY_ALIASES[compacted]
    for stripped in (
        re.sub(r"\s*\([^)]*\)", "", key).strip(),
        re.sub(r"\s*\([^)]*\)", "", compacted).strip(),
    ):
        if stripped in DISTINCT_COMMODITY_KEYS:
            return raw.title()
        if stripped in COMMODITY_ALIASES:
            return COMMODITY_ALIASES[stripped]
    return raw.title()


@dataclass(frozen=True)
class CommodityClassification:
    category: str
    storable: bool
    # "target", "reviewed", "provisional", "review_required", "heuristic" or "unclassified"
    source: str

    @property
    def is_reviewed(self) -> bool:
        return self.source in {"target", "reviewed"}

    @property
    def requires_review(self) -> bool:
        return not self.is_reviewed


def classify_commodity(commodity_name: Any) -> CommodityClassification:
    """Classify a commodity, preferring config targets, then the reviewed catalog, then keywords.

    Catalog entries keep their review status as the source, so provisional and review-required
    names are never reported as reviewed. Names matched by neither the catalog nor any keyword
    are returned as ``unclassified`` (non-storable) so callers can flag them instead of guessing.
    """
    canonical = normalize_commodity_name(commodity_name)
    if not canonical:
        return CommodityClassification(UNCLASSIFIED_CATEGORY, False, "unclassified")

    target = _TARGET_STORABILITY.get(canonical.lower())
    if target is not None:
        return CommodityClassification(target[0], target[1], "target")

    entry = lookup_catalog_classification(canonical)
    if entry is not None:
        return CommodityClassification(entry.category, entry.storable, entry.review_status)

    category, storable = heuristic_commodity_classification(canonical)
    source = "unclassified" if category == UNCLASSIFIED_CATEGORY else "heuristic"
    return CommodityClassification(category, storable, source)


def classify_commodity_storability(commodity_name: Any) -> tuple[str, bool]:
    result = classify_commodity(commodity_name)
    return (result.category, result.storable)


def normalize_crop_name(value: Any) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    key = raw.lower()
    return CROP_TO_CANONICAL_MAP.get(key, raw.title())


def normalize_season(value: Any) -> str:
    raw = clean_text(value).lower()
    if not raw:
        return "Whole Year"
    if "kharif" in raw:
        return "Kharif"
    if "rabi" in raw:
        return "Rabi"
    if "zaid" in raw or "summer" in raw:
        return "Summer"
    if "autumn" in raw:
        return "Autumn"
    if "winter" in raw:
        return "Winter"
    if "whole" in raw or "annual" in raw or "total" in raw:
        return "Whole Year"
    return raw.title()


def derive_indian_season(dt: date) -> str:
    """Map calendar month to Kharif (Jun-Oct), Rabi (Nov-Mar), or Zaid (Apr-May)."""
    if 6 <= dt.month <= 10:
        return "Kharif"
    if dt.month in (4, 5):
        return "Zaid"
    return "Rabi"


def parse_date(value: Any) -> date | None:
    """Parse ISO or Indian DD/MM/YYYY / DD-MM-YYYY date strings cleanly."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, float) and pd.isna(value):
        return None

    s = clean_text(value)
    if not s:
        return None

    iso_match = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if iso_match:
        try:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            return None

    dmy_match = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
    if dmy_match:
        try:
            return date(int(dmy_match.group(3)), int(dmy_match.group(2)), int(dmy_match.group(1)))
        except ValueError:
            return None

    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y"):
        try:
            return datetime.strptime(s[:20], fmt).date()
        except ValueError:
            continue

    try:
        parsed = pd.to_datetime(s, dayfirst=True, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None


def to_numeric(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if pd.isna(value):
            return None
        return float(value)
    s = clean_text(value)
    if not s:
        return None
    s = re.sub(r"[₹Rs,\s]", "", s, flags=re.IGNORECASE)
    try:
        num = float(s)
        return None if pd.isna(num) else num
    except ValueError:
        return None


def convert_arrivals_to_tonnes(value: Any, unit_hint: str | None = None) -> float | None:
    num = to_numeric(value)
    if num is None:
        return None
    hint = (unit_hint or "").lower()
    if "quintal" in hint or "qtl" in hint:
        return round(num * 0.1, 3)
    if "kg" in hint or "kilogram" in hint:
        return round(num * 0.001, 3)
    return round(num, 3)


def clean_price_records(records: list[dict[str, Any]], source_name: str = "agmarknet_api") -> pd.DataFrame:
    """Standardize raw Agmarknet/CEDA price records into typed tabular form."""
    rows: list[dict[str, Any]] = []
    for raw in records:
        variety = clean_text(raw.get("variety")) or "NA"
        grade = clean_text(raw.get("grade")) or "NA"
        rows.append(
            {
                "raw_record": raw,
                "source_name": raw.get("source_name") or source_name,
                "arrival_date": parse_date(raw.get("arrival_date") or raw.get("date")),
                "state": normalize_state_name(raw.get("state")),
                "district": normalize_district_name(raw.get("district")),
                "market": normalize_market_name(raw.get("market")),
                "commodity": normalize_commodity_name(raw.get("commodity")),
                "variety": variety,
                "grade": grade,
                "min_price": to_numeric(raw.get("min_price")),
                "max_price": to_numeric(raw.get("max_price")),
                "modal_price": to_numeric(raw.get("modal_price")),
            }
        )
    return pd.DataFrame(rows)


def clean_arrival_records(records: list[dict[str, Any]], source_name: str = "ceda_archive") -> pd.DataFrame:
    """Standardize raw CEDA arrival records into typed tabular form (quantity in tonnes)."""
    rows: list[dict[str, Any]] = []
    for raw in records:
        qty_raw = raw.get("quantity_tonnes", raw.get("arrivals", raw.get("quantity")))
        rows.append(
            {
                "raw_record": raw,
                "source_name": raw.get("source_name") or source_name,
                "arrival_date": parse_date(raw.get("arrival_date") or raw.get("date")),
                "state": normalize_state_name(raw.get("state")),
                "district": normalize_district_name(raw.get("district")),
                "market": normalize_market_name(raw.get("market")),
                "commodity": normalize_commodity_name(raw.get("commodity")),
                "quantity_tonnes": convert_arrivals_to_tonnes(qty_raw, unit_hint=raw.get("unit")),
            }
        )
    return pd.DataFrame(rows)


def clean_apy_records(records: list[dict[str, Any]], source_name: str = "des_apy") -> pd.DataFrame:
    """Standardize raw DES APY production records into typed tabular form with strict unit handling."""
    from ingestion.apy_importer import ApyImporter

    rows: list[dict[str, Any]] = []
    for raw in records:
        raw_state = clean_text(raw.get("state"))
        raw_district = clean_text(raw.get("district"))
        crop_name = normalize_crop_name(raw.get("crop_name") or raw.get("crop"))

        area_raw = to_numeric(raw.get("area_ha", raw.get("area")))
        prod_raw = to_numeric(raw.get("production_t", raw.get("production")))
        yld_raw = to_numeric(raw.get("yield_t_ha", raw.get("yield")))

        combined_unit = clean_text(raw.get("unit", ""))
        c_area_u, c_prod_u, c_yield_u = _extract_combined_unit_clauses(combined_unit) if combined_unit else ("", "", "")
        area_unit = clean_text(raw.get("area_unit")) or c_area_u
        prod_unit = clean_text(raw.get("production_unit")) or c_prod_u
        yield_unit = clean_text(raw.get("yield_unit")) or c_yield_u or (combined_unit if not (c_area_u or c_prod_u or c_yield_u) else "")

        area_ha, area_err = convert_apy_area_to_ha(area_raw, area_unit)
        production_t, prod_err = convert_apy_production_to_tonnes(prod_raw, prod_unit, crop_name)
        yield_t_ha, yield_err = convert_apy_yield_to_t_ha(yld_raw, yield_unit, area_ha, production_t)

        unit_error = area_err or prod_err or yield_err

        rows.append(
            {
                "raw_record": raw,
                "source_name": raw.get("source_name") or source_name,
                "crop_year": ApyImporter.parse_crop_year(raw.get("crop_year") or raw.get("year")),
                "season": normalize_season(raw.get("season")),
                "state": normalize_state_name(raw_state),
                "district": normalize_district_name(raw_district),
                "is_aggregate_state": is_aggregate_location(raw_state, level="state"),
                "is_aggregate_district": is_aggregate_location(raw_district, level="district"),
                "crop_name": crop_name,
                "crop_type": clean_text(raw.get("crop_type")) or "Other",
                "area_ha": area_ha,
                "production_t": production_t,
                "yield_t_ha": yield_t_ha,
                "unit_error": unit_error,
            }
        )
    return pd.DataFrame(rows)
