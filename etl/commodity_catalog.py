"""Reviewed commodity classification catalog shared by the ETL loader and migration 003.

``PRODUCTION_COMMODITY_CLASSIFICATION`` is keyed by the exact ``dim_commodity.commodity_name``
values present in production on 2026-11-20 (190 names). Migration 003 carries the same mapping,
including each name's review status, as a literal VALUES list; ``tests/test_commodity_catalog.py``
enforces that both stay identical.

Every catalog entry has a review status:

* ``reviewed``: approved classification; the loader may overwrite existing rows with it.
* ``provisional``: approved for use but explicitly awaiting confirmation. It is applied by
  migration 003 and used for new rows, but never overwrites existing rows and is always reported
  in ``commodities_requiring_review``.
* ``review_required``: identity not established. Stored as ``unclassified`` / non-storable and
  always reported for human review.
"""

from __future__ import annotations

import re
from typing import NamedTuple

UNCLASSIFIED_CATEGORY = "unclassified"

REVIEW_STATUS_REVIEWED = "reviewed"
REVIEW_STATUS_PROVISIONAL = "provisional"
REVIEW_STATUS_REQUIRED = "review_required"
REVIEW_STATUSES: frozenset[str] = frozenset(
    {REVIEW_STATUS_REVIEWED, REVIEW_STATUS_PROVISIONAL, REVIEW_STATUS_REQUIRED}
)

# Names whose identity could not be established from source evidence (state, variety, price).
# They are stored as ``unclassified`` / non-storable and must be reviewed by a human.
COMMODITIES_REQUIRING_REVIEW: frozenset[str] = frozenset(
    {"Bop", "Buttery", "Guar", "Groundnut Pods(Raw)", "Season Leaves"}
)

# Approved provisional classifications: (category, storable). Not treated as fully reviewed.
PROVISIONAL_CLASSIFICATION: dict[str, tuple[str, bool]] = {
    "Field Pea": ("pulse", True),
}

COMMODITY_REVIEW_NOTES: dict[str, str] = {
    "Bop": "Single Delhi record, variety 'Other', modal Rs 50/q; commodity identity unknown.",
    "Buttery": "Madhya Pradesh only, variety 'Buttery', Rs 3,230-4,280/q; commodity identity unknown.",
    "Guar": (
        "Varieties Gwar/Guaar, Rs 1,900-9,500/q across Rajasthan, Gujarat, Haryana, MP and Maharashtra; "
        "prices overlap both Cluster Beans (vegetable) and Guar Seed (cash crop)."
    ),
    "Groundnut Pods(Raw)": (
        "Single Gujarat record at Rs 7,000/q, close to dry in-shell groundnut; kept separate from "
        "Groundnut and not given a storability decision until confirmed."
    ),
    "Season Leaves": "Single Himachal Pradesh record, variety 'Other', Rs 1,500/q; produce type unknown.",
    "Field Pea": (
        "Provisionally a storable dry pulse; some source records (modal up to Rs 9,300/q) may be "
        "fresh green peas."
    ),
}

_VEGETABLE_PERISHABLE = (
    "Amaranthus",
    "Amranthas Red",
    "Ashgourd",
    "Baby Corn",
    "Banana - Green",
    "Beans",
    "Beetroot",
    "Bhindi(Ladies Finger)",
    "Bitter Gourd",
    "Bottle Gourd",
    "Capsicum",
    "Carrot",
    "Chilly Capsicum",
    "Chow Chow",
    "Cluster Beans",
    "Colacasia",
    "Coriander(Leaves)",
    "Cowpea(Veg)",
    "Cucumbar(Kheera)",
    "Drumstick",
    "Elephant Yam(Suran)/Amorphophallus",
    "French Beans(Frasbean)",
    "Ginger(Green)",
    "Green Avare(W)",
    "Green Peas",
    "Indian Beans(Seam)",
    "Knool Khol",
    "Ladies Finger",
    "Leafy Vegetable",
    "Little Gourd(Kundru)",
    "Long Melon(Kakri)",
    "Methi(Leaves)",
    "Mint(Pudina)",
    "Mushroom",
    "Other Green And Fresh Vegetables",
    "Papaya(Raw)",
    "Pea Pod/Pea Cod/हरी मटर",
    "Peas Wet",
    "Pegeon Pea(Arhar Fali)",
    "Pointed Gourd(Parval)",
    "Pumpkin",
    "Raddish",
    "Ridgeguard(Tori)",
    "Round Gourd",
    "Sem",
    "Snakeguard",
    "Spinach",
    "Spiny Gourd / Kartali(Kantola)",
    "Sponge Gourd",
    "Squash(Chappal Kadoo)",
    "Surat Beans(Papadi)",
    "Sweet Corn",
    "Sweet Potato",
    "Sweet Pumpkin",
    "Tapioca",
    "Thondekai",
    "Tinda",
    "Turnip",
    "Wild Cucumber",
    "Yam(Ratalu)",
)

_FRUIT_PERISHABLE = (
    "Amla(Nelli Kai)",
    "Apple",
    "Avocado",
    "Banana",
    "Ber(Zizyphus/Borehannu)",
    "Chikoos(Sapota)",
    "Custard Apple(Sharifa)",
    "Galgal(Lemon)",
    "Grapes",
    "Guava",
    "Jack Fruit(Ripe)",
    "Karbuja(Musk Melon)",
    "Kinnow",
    "Lemon",
    "Lime",
    "Mango",
    "Mango(Raw-Ripe)",
    "Mousambi(Sweet Lime)",
    "Orange",
    "Papaya",
    "Pear(Marasebu)",
    "Persimon(Japani Fal)",
    "Pineapple",
    "Pomegranate",
    "Seetapal",
    "Tender Coconut",
    "Water Melon",
)

_PULSE_STORABLE = (
    "Bengal Gram Dal(Chana Dal)",
    "Black Gram Dal(Urd Dal)",
    "Black Gram(Urd Beans)(Whole)",
    "Cowpea(Lobia/Karamani)",
    "Field Bean(Anumulu)",
    "Green Gram Dal(Moong Dal)",
    "Kabuli Chana(Chickpeas-White)",
    "Lak(Teora)",
    "Lentil(Masur)(Whole)",
    "Masur Dal",
    "Mataki",
    "Moath Dal",
    "Peas(Dry)",
    "Red Gram/Arhar/Tur(Whole)",
)

_CEREAL_STORABLE = (
    "Bajra(Pearl Millet/Cumbu)",
    "Barley(Jau)",
    "Broken Rice",
    "Jowar(Sorghum)",
    "Kodo Millet(Varagu)",
    "Kutki",
    "Ragi(Finger Millet)",
    "Rajgir",
    "Same/Savi",
    "T.V. Cumbu",
    "Wheat Atta",
)

_SPICE_STORABLE = (
    "Ajwan",
    "Basil",  # production records are variety "Basil seed" (dried seed), not fresh basil
    "Bay Leaf(Tejpatta)",
    "Black Pepper",
    "Cardamom",
    "Chili Red",
    "Cinamon(Dalchini)",
    "Corriander Seed",
    "Cummin Seed(Jeera)",
    "Dry Chillies",
    "Ginger(Dry)",
    "Methi Seeds",
    "Nigella",
    "Soanf",
    "Tamarind Fruit",
)

_OILSEED_STORABLE = (
    "Castor Seed",
    "Chiaseeds",
    "Coconut Oil",
    "Copra",
    "Ground Nut Seed",
    "Indian Colza(Sarson)",
    "Linseed",
    "Mustard Oil",
    "Sesamum(Sesame,Gingelly,Til)",
    "Taramira",
)

_CASH_CROP_STORABLE = (
    "Ambady/Mesta/Patson",
    "Arecanut(Betelnut/Supari)",
    "Coconut",
    "Coffee",
    "Cotton",
    "Guar Seed(Cluster Beans Seed)",
    "Mentha Oil",
    "Rubber",
    "Tobacco",
)

_DRY_FRUIT_NUT_STORABLE = (
    "Almond(Badam)",
    "Dates",
    "Dried Mango",
    "Walnut",
)

_FLOWER_PERISHABLE = (
    "Astera",
    "Jarbara",
    "Jasmine",
    "Marigold(Calcutta)",
    "Rose(Local)",
    "Tube Rose(Loose)",
)

_OTHER_STORABLE = (
    "Broomstick(Flower Broom)",
    "Firewood",
    "Gur(Jaggery)",
    "Mahedi",
    "Rab/Liquid Jaggery/Molasses",
    "Sugar",
    "White Muesli",
    "Wood",
)

_OTHER_PERISHABLE = (
    "Betal Leaves",
    "Fish",
)

# Mirrors config.TARGET_COMMODITIES; a test asserts they stay in sync.
_TARGETS = {
    "Tomato": ("vegetable", False),
    "Onion": ("vegetable", False),
    "Cabbage": ("vegetable", False),
    "Cauliflower": ("vegetable", False),
    "Brinjal": ("vegetable", False),
    "Green Chilli": ("vegetable", False),
    "Potato": ("vegetable", True),
    "Garlic": ("spice", True),
    "Turmeric": ("spice", True),
    "Wheat": ("cereal", True),
    "Rice": ("cereal", True),
    "Maize": ("cereal", True),
    "Soybean": ("oilseed", True),
    "Groundnut": ("oilseed", True),
    "Mustard": ("oilseed", True),
    "Chickpea": ("pulse", True),
    "Pigeon Pea (Tur)": ("pulse", True),
    "Green Gram (Moong)": ("pulse", True),
}


class CatalogClassification(NamedTuple):
    category: str
    storable: bool
    review_status: str


def _build_production_catalog() -> dict[str, CatalogClassification]:
    groups: list[tuple[tuple[str, ...], str, bool]] = [
        (_VEGETABLE_PERISHABLE, "vegetable", False),
        (_FRUIT_PERISHABLE, "fruit", False),
        (_PULSE_STORABLE, "pulse", True),
        (_CEREAL_STORABLE, "cereal", True),
        (_SPICE_STORABLE, "spice", True),
        (_OILSEED_STORABLE, "oilseed", True),
        (_CASH_CROP_STORABLE, "cash_crop", True),
        (_DRY_FRUIT_NUT_STORABLE, "dry_fruit_nut", True),
        (_FLOWER_PERISHABLE, "flower", False),
        (_OTHER_STORABLE, "other", True),
        (_OTHER_PERISHABLE, "other", False),
    ]
    entries: list[tuple[str, CatalogClassification]] = [
        (name, CatalogClassification(category, storable, REVIEW_STATUS_REVIEWED))
        for name, (category, storable) in _TARGETS.items()
    ]
    for names, category, storable in groups:
        entries += [(name, CatalogClassification(category, storable, REVIEW_STATUS_REVIEWED)) for name in names]
    entries += [
        (name, CatalogClassification(category, storable, REVIEW_STATUS_PROVISIONAL))
        for name, (category, storable) in PROVISIONAL_CLASSIFICATION.items()
    ]
    entries += [
        (name, CatalogClassification(UNCLASSIFIED_CATEGORY, False, REVIEW_STATUS_REQUIRED))
        for name in sorted(COMMODITIES_REQUIRING_REVIEW)
    ]
    catalog: dict[str, CatalogClassification] = {}
    for name, entry in entries:
        if name in catalog:
            raise ValueError(f"Duplicate commodity in reviewed catalog: {name}")
        catalog[name] = entry
    return catalog


PRODUCTION_COMMODITY_CATALOG: dict[str, CatalogClassification] = _build_production_catalog()

PRODUCTION_COMMODITY_CLASSIFICATION: dict[str, tuple[str, bool]] = {
    name: (entry.category, entry.storable) for name, entry in PRODUCTION_COMMODITY_CATALOG.items()
}

PRODUCTION_COMMODITY_REVIEW_STATUS: dict[str, str] = {
    name: entry.review_status for name, entry in PRODUCTION_COMMODITY_CATALOG.items()
}

# Reviewed names not yet seen in production; used for future Agmarknet spellings.
ADDITIONAL_REVIEWED_CLASSIFICATION: dict[str, tuple[str, bool]] = {
    "Ash Gourd": ("vegetable", False),
    "Broad Beans": ("vegetable", False),
    "Colocasia": ("vegetable", False),
    "Cucumber": ("vegetable", False),
    "Curry Leaves": ("vegetable", False),
    "Knol Khol": ("vegetable", False),
    "Radish": ("vegetable", False),
    "Ridge Gourd(Tori)": ("vegetable", False),
    "Snake Gourd": ("vegetable", False),
    "Tamarind Leaf": ("vegetable", False),
    "Zucchini": ("vegetable", False),
    "Jack Fruit": ("fruit", False),
    "Jamun(Narale Hannu)": ("fruit", False),
    "Litchi": ("fruit", False),
    "Musk Melon": ("fruit", False),
    "Peach": ("fruit", False),
    "Plum": ("fruit", False),
    "Sapota": ("fruit", False),
    "Sweet Lime": ("fruit", False),
    "Watermelon": ("fruit", False),
    "Kulthi(Horse Gram)": ("pulse", True),
    "Moth": ("pulse", True),
    "Rajma": ("pulse", True),
    "Red Gram Dal(Arhar Dal)": ("pulse", True),
    "Cardamoms": ("spice", True),
    "Coriander(Seed)": ("spice", True),
    "Cumin Seed(Jeera)": ("spice", True),
    "Groundnut Oil": ("oilseed", True),
    "Safflower": ("oilseed", True),
    "Sunflower": ("oilseed", True),
    "Jute": ("cash_crop", True),
    "Cashewnuts": ("dry_fruit_nut", True),
    "Dry Grapes": ("dry_fruit_nut", True),
}

# Commodities introduced by migration 004 (approved 2026-11-20; classifications reviewed
# 2026-11-21). They are NOT among the 190 names migration 003 classified, so they live here
# rather than in PRODUCTION_COMMODITY_CATALOG.
#   * Paddy: one canonical commodity for every Paddy sub-type; Rice is never merged into it.
#   * Onion Green: spring onion, kept separate from bulb Onion.
# Both are reviewed: the loader may upsert their category/storable. Migration 004 also creates
# the commodity-to-crop mapping Paddy -> Rice (Rice commodity and Paddy commodity stay separate).
POST_MIGRATION_004_CLASSIFICATION: dict[str, tuple[str, bool, str]] = {
    "Paddy": ("cereal", True, REVIEW_STATUS_REVIEWED),
    "Onion Green": ("vegetable", False, REVIEW_STATUS_REVIEWED),
}


def compact_commodity_key(text_val: str) -> str:
    """Case-insensitive key that ignores spacing around slashes and parentheses."""
    compacted = re.sub(r"\s*/\s*", "/", text_val.lower())
    compacted = re.sub(r"\s*\(\s*", "(", compacted)
    compacted = re.sub(r"\s*\)\s*", ")", compacted)
    return re.sub(r"\s+", " ", compacted).strip()


def _build_key_index() -> dict[str, CatalogClassification]:
    index: dict[str, CatalogClassification] = {}
    additional = {
        name: CatalogClassification(category, storable, REVIEW_STATUS_REVIEWED)
        for name, (category, storable) in ADDITIONAL_REVIEWED_CLASSIFICATION.items()
    }
    post_004 = {
        name: CatalogClassification(category, storable, status)
        for name, (category, storable, status) in POST_MIGRATION_004_CLASSIFICATION.items()
    }
    for source in (PRODUCTION_COMMODITY_CATALOG, additional, post_004):
        for name, entry in source.items():
            key = compact_commodity_key(name)
            if key in index and index[key] != entry:
                raise ValueError(f"Conflicting catalog classification for key {key!r}")
            index.setdefault(key, entry)
    return index


_CATALOG_BY_KEY = _build_key_index()


def lookup_catalog_classification(commodity_name: str) -> CatalogClassification | None:
    """Return the catalog entry (with review status) for an exact or case/spacing-variant name."""
    if commodity_name in PRODUCTION_COMMODITY_CATALOG:
        return PRODUCTION_COMMODITY_CATALOG[commodity_name]
    return _CATALOG_BY_KEY.get(compact_commodity_key(commodity_name))


def _has_token(text_val: str, tokens: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<![a-z]){re.escape(tok)}(?![a-z])", text_val) for tok in tokens)


_DRIED_SPICE_LEAF = ("bay leaf", "tejpatta", "kasuri methi")
_FRESH_MARKERS = ("leaves", "leaf", "wet", "fali", "tender", "fresh", "veg")
_GREEN_PULSE_MARKERS = ("gram", "moong", "dal")
_DRY_MARKERS = ("dry", "dried")
_SPICES = (
    "ajwan", "ajwain", "black pepper", "pepper", "cardamom", "cardamoms", "cinnamon", "cinamon",
    "dalchini", "clove", "cloves", "nutmeg", "mace", "cumin", "cummin", "jeera", "fennel", "soanf",
    "saunf", "methi seed", "methi seeds", "fenugreek", "coriander seed", "corriander seed",
    "coriander(seed)", "dhaniya", "nigella", "kalonji", "star anise", "asafoetida", "hing",
    "tamarind", "turmeric", "chilli red", "chili red", "red chilli", "dry chilli", "dry chillies",
)
_PULSES = (
    "dal", "lentil", "masur", "masoor", "gram", "rajma", "moth", "moath", "kulthi", "urd", "urad",
    "lobia", "chana", "mataki", "matki", "teora", "khesari", "arhar", "tur", "moong", "peas(dry)",
    "field pea", "field bean", "cowpea",
)
_CEREALS = (
    "millet", "sorghum", "barley", "jowar", "bajra", "ragi", "atta", "rice", "paddy", "wheat",
    "maize", "kodo", "kutki", "samai", "same", "savi", "cumbu", "varagu", "foxtail", "proso",
    "rajgir", "rajgira", "oats",
)
_NUTS_DRY_FRUITS = (
    "almond", "badam", "walnut", "akhrot", "cashew", "cashewnut", "cashewnuts", "pista",
    "pistachio", "dates", "raisin", "raisins", "kishmish", "anjeer", "fig", "apricot",
)
_OILSEEDS = (
    "oil", "seed", "seeds", "sesame", "sesamum", "til", "castor", "copra", "sunflower", "safflower",
    "linseed", "niger", "taramira", "colza", "sarson", "chia", "chiaseeds", "groundnut", "soyabean",
    "soybean", "mustard",
)
_CASH_CROPS = (
    "cotton", "jute", "mesta", "rubber", "coffee", "tea", "tobacco", "arecanut", "betelnut",
    "supari", "mentha", "coconut",
)
_OTHER_STORABLES = ("jaggery", "gur", "sugar", "molasses", "firewood", "wood")
_FLOWERS = ("rose", "marigold", "jasmine", "tube rose", "gerbera", "jarbara", "aster", "astera", "flower", "lily")
_FRUITS = (
    "banana", "mango", "apple", "orange", "papaya", "guava", "lemon", "lime", "pineapple",
    "pomegranate", "melon", "plum", "peach", "pear", "litchi", "lichi", "sapota", "chiku", "chikoos",
    "grapes", "jackfruit", "jack fruit", "kinnow", "mousambi", "amla", "ber", "jamun", "avocado",
    "persimon", "persimmon", "custard apple", "seetapal", "kiwi", "strawberry",
)
_VEGETABLES = (
    "gourd", "guard", "squash", "cucumber", "cucumbar", "bhindi", "okra", "carrot", "radish",
    "raddish", "beet", "beetroot", "turnip", "spinach", "palak", "mushroom", "mushrooms", "capsicum",
    "beans", "bean", "drumstick", "tinda", "parval", "tori", "knol", "colocasia", "colacasia",
    "amaranthus", "yam", "tapioca", "pumpkin", "potato", "tomato", "onion", "brinjal", "cabbage",
    "cauliflower", "peas", "corn", "vegetable", "vegetables",
)


def heuristic_commodity_classification(commodity_name: str) -> tuple[str, bool]:
    """Keyword fallback for names absent from the reviewed catalog.

    Order matters: dried forms and spices are recognised before the generic ``seed``/``oil``
    oilseed rule, and ``dal`` only matches as a whole word (so ``Dalchini`` stays a spice).
    Returns ``UNCLASSIFIED_CATEGORY`` when nothing matches.
    """
    key = compact_commodity_key(commodity_name)
    if not key:
        return (UNCLASSIFIED_CATEGORY, False)

    if _has_token(key, _DRIED_SPICE_LEAF):
        return ("spice", True)
    if _has_token(key, _DRY_MARKERS):
        if _has_token(key, _NUTS_DRY_FRUITS + _FRUITS):
            return ("dry_fruit_nut", True)
        if _has_token(key, _PULSES + ("pea", "peas", "bean", "beans")):
            return ("pulse", True)
        return ("spice", True)
    if _has_token(key, _FRESH_MARKERS) or (
        _has_token(key, ("green",)) and not _has_token(key, _GREEN_PULSE_MARKERS)
    ):
        return ("vegetable", False)
    if _has_token(key, _SPICES):
        return ("spice", True)
    if _has_token(key, _NUTS_DRY_FRUITS):
        return ("dry_fruit_nut", True)
    if _has_token(key, _PULSES):
        return ("pulse", True)
    if _has_token(key, _CEREALS):
        return ("cereal", True)
    if _has_token(key, ("mentha",)):
        return ("cash_crop", True)
    if _has_token(key, _OILSEEDS):
        return ("oilseed", True)
    if _has_token(key, _CASH_CROPS):
        return ("cash_crop", True)
    if _has_token(key, _OTHER_STORABLES):
        return ("other", True)
    if _has_token(key, _FLOWERS):
        return ("flower", False)
    if _has_token(key, _FRUITS):
        return ("fruit", False)
    if _has_token(key, _VEGETABLES):
        return ("vegetable", False)
    return (UNCLASSIFIED_CATEGORY, False)
