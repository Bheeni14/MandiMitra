"""Deterministic Demo Fixture Generator (clearly labelled simulated data for demonstration when APIs/DB are offline)."""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from config import DATA_SAMPLE_DIR, TARGET_COMMODITIES

# Realistic representative APMC markets across 12 Indian agricultural states
DEMO_MARKETS: list[tuple[str, str, str, float]] = [
    ("Lasalgaon", "Nashik", "Maharashtra", 0.92),
    ("Pune", "Pune", "Maharashtra", 1.06),
    ("Agra", "Agra", "Uttar Pradesh", 0.94),
    ("Lucknow", "Lucknow", "Uttar Pradesh", 1.03),
    ("Indore", "Indore", "Madhya Pradesh", 0.91),
    ("Ujjain", "Ujjain", "Madhya Pradesh", 0.93),
    ("Kota", "Kota", "Rajasthan", 0.95),
    ("Jaipur", "Jaipur", "Rajasthan", 1.02),
    ("Unjha", "Mehsana", "Gujarat", 0.96),
    ("Rajkot", "Rajkot", "Gujarat", 0.94),
    ("Kolar", "Kolar", "Karnataka", 0.90),
    ("Bengaluru", "Bengaluru Urban", "Karnataka", 1.12),
    ("Ludhiana", "Ludhiana", "Punjab", 0.95),
    ("Patiala", "Patiala", "Punjab", 0.93),
    ("Karnal", "Karnal", "Haryana", 0.94),
    ("Hisar", "Hisar", "Haryana", 0.92),
    ("Kolkata", "Kolkata", "West Bengal", 1.15),
    ("Siliguri", "Darjeeling", "West Bengal", 1.10),
    ("Patna", "Patna", "Bihar", 1.04),
    ("Muzaffarpur", "Muzaffarpur", "Bihar", 0.99),
    ("Guntur", "Guntur", "Andhra Pradesh", 0.97),
    ("Kurnool", "Kurnool", "Andhra Pradesh", 0.95),
    ("Koyambedu", "Chennai", "Tamil Nadu", 1.14),
    ("Coimbatore", "Coimbatore", "Tamil Nadu", 1.08),
]

# Base price (Rs/quintal), seasonal amplitude, peak month (1..12), noise CV, base arrivals (tonnes/day)
COMMODITY_PROFILES: dict[str, tuple[float, float, int, float, float, str]] = {
    "Tomato": (2200.0, 0.42, 8, 0.14, 180.0, "Hybrid"),
    "Onion": (2100.0, 0.34, 10, 0.11, 420.0, "Red"),
    "Cabbage": (1450.0, 0.28, 7, 0.12, 95.0, "Local"),
    "Cauliflower": (1950.0, 0.32, 7, 0.13, 85.0, "Snowball"),
    "Brinjal": (1850.0, 0.24, 9, 0.11, 70.0, "Round"),
    "Green Chilli": (3600.0, 0.27, 6, 0.12, 65.0, "Guntur"),
    "Potato": (1650.0, 0.22, 11, 0.08, 380.0, "Jyoti"),
    "Garlic": (7800.0, 0.26, 12, 0.09, 55.0, "Desi"),
    "Turmeric": (9400.0, 0.14, 2, 0.06, 90.0, "Finger"),
    "Wheat": (2450.0, 0.09, 1, 0.03, 520.0, "Lokwan"),
    "Rice": (3350.0, 0.08, 9, 0.03, 460.0, "Common"),
    "Maize": (2150.0, 0.11, 8, 0.04, 290.0, "Yellow"),
    "Soybean": (4650.0, 0.13, 7, 0.05, 340.0, "Yellow"),
    "Groundnut": (6150.0, 0.12, 8, 0.05, 210.0, "Bold"),
    "Mustard": (5550.0, 0.11, 12, 0.04, 260.0, "Black"),
    "Chickpea": (5850.0, 0.12, 1, 0.05, 240.0, "Desi"),
    "Pigeon Pea (Tur)": (8900.0, 0.15, 9, 0.06, 150.0, "Red"),
    "Green Gram (Moong)": (7650.0, 0.14, 6, 0.06, 120.0, "Green"),
}


def generate_demo_datasets(
    out_dir: Path | None = None,
    start_date: date = date(2024, 1, 1),
    end_date: date = date(2026, 11, 18),
    seed: int = 42,
) -> dict[str, Path]:
    """Generate reproducible, clearly labelled demo CSV fixtures for prices, CEDA arrivals, and APY."""
    out_dir = out_dir or DATA_SAMPLE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    # Trading dates: 3 days/week (Mon, Wed, Fri) + continuous final 14 days so Q6 has dense coverage
    all_days: list[date] = []
    cur = start_date
    while cur <= end_date:
        if cur >= end_date - timedelta(days=14) or cur.weekday() in (0, 2, 4):
            all_days.append(cur)
        cur += timedelta(days=1)

    total_days = len(all_days)
    spec_names = [s.name for s in TARGET_COMMODITIES]

    price_rows: list[dict] = []
    arrival_rows: list[dict] = []

    # Assign 6-8 commodities per market deterministically so every commodity spans >= 8 states
    for m_idx, (mkt, dist, state, state_factor) in enumerate(DEMO_MARKETS):
        chosen_commodities = [
            spec_names[(m_idx + k * 2) % len(spec_names)] for k in range(7)
        ]
        # Ensure flagship demo pair Tomato/Onion/Wheat appear in major hubs
        if state in ("Maharashtra", "Karnataka", "Uttar Pradesh", "Madhya Pradesh", "Delhi"):
            for key_c in ("Tomato", "Onion", "Wheat"):
                if key_c not in chosen_commodities:
                    chosen_commodities.append(key_c)
        chosen_commodities = sorted(set(chosen_commodities))

        for comm in chosen_commodities:
            base_p, amp, peak_mo, noise_cv, base_arr, variety = COMMODITY_PROFILES[comm]
            # Smooth AR(1) market-commodity wave + seasonal cycle
            ar_shock = 0.0
            arr_shock = 0.0
            pair_bias = rng.uniform(0.95, 1.05)

            for idx, d in enumerate(all_days):
                day_of_year = d.timetuple().tm_yday
                peak_doy = (peak_mo - 0.5) * 30.4
                phase = 2.0 * math.pi * (day_of_year - peak_doy) / 365.25
                seasonal_mult = 1.0 + amp * math.cos(phase)

                # Slow multi-week trend component (AR(1) persistence makes 4w momentum predictive)
                ar_shock = 0.88 * ar_shock + rng.normal(0.0, noise_cv * 0.45)
                arr_shock = 0.82 * arr_shock + rng.normal(0.0, 0.14)

                # Inverse supply-price coupling: high seasonal prices attract arrivals or high arrivals depress price
                arr_mult = max(0.25, 1.0 - 0.45 * amp * math.cos(phase) + arr_shock)
                qty_tonnes = round(base_arr * arr_mult * rng.uniform(0.88, 1.12), 2)

                # Slight lagged arrival pressure on price
                supply_effect = -0.14 * arr_shock
                modal = base_p * state_factor * pair_bias * seasonal_mult * math.exp(ar_shock + supply_effect)
                modal = max(350.0, round(modal, 2))

                # Inject 1 rare realistic spike (~0.08% chance) to exercise 3-IQR outlier flagging
                if idx > 30 and rng.random() < 0.0008:
                    modal = round(modal * 2.8, 2)

                spread_low = rng.uniform(0.84, 0.93)
                spread_high = rng.uniform(1.07, 1.18)
                min_p = round(modal * spread_low, 2)
                max_p = round(modal * spread_high, 2)

                price_rows.append(
                    {
                        "state": state,
                        "district": dist,
                        "market": mkt,
                        "commodity": comm,
                        "variety": variety,
                        "grade": "FAQ",
                        "arrival_date": d.isoformat(),
                        "min_price": min_p,
                        "max_price": max_p,
                        "modal_price": modal,
                    }
                )
                arrival_rows.append(
                    {
                        "state": state,
                        "district": dist,
                        "market": mkt,
                        "commodity": comm,
                        "arrival_date": d.isoformat(),
                        "quantity_tonnes": qty_tonnes,
                    }
                )

    # Inject 24 realistic dirty records (~0.05%) to verify DQ reject logging & dedup in end-to-end demo
    if len(price_rows) >= 10:
        for i in range(6):
            bad1 = dict(price_rows[i * 50])
            bad1["modal_price"] = bad1["max_price"] + 500  # modal outside min/max
            price_rows.append(bad1)
            bad2 = dict(price_rows[i * 50 + 1])
            bad2["min_price"] = -120  # non-positive price
            price_rows.append(bad2)
            dup = dict(price_rows[i * 50 + 2])  # exact natural-key duplicate
            price_rows.append(dup)
            missing_dt = dict(price_rows[i * 50 + 3])
            missing_dt["arrival_date"] = ""  # missing key field
            price_rows.append(missing_dt)

    # Generate APY Crop Production records (2024-2026) matching 12 states & mapped crops
    apy_rows: list[dict] = []
    state_districts = {}
    for _, dist, st, _ in DEMO_MARKETS:
        state_districts.setdefault(st, []).append(dist)

    for yr in (2024, 2025, 2026):
        for st, dists in state_districts.items():
            # State production factor inversely tied to state price factor for realistic Q7 relationship
            st_p_factor = next(f for _, _, s, f in DEMO_MARKETS if s == st)
            prod_hub_boost = max(0.4, (1.25 - st_p_factor) * 4.0)
            for spec in TARGET_COMMODITIES:
                season = "Rabi" if spec.name in ("Wheat", "Potato", "Mustard", "Chickpea", "Garlic") else "Kharif"
                base_area = {"cereal": 45000.0, "oilseed": 28000.0, "pulse": 22000.0, "vegetable": 8500.0, "spice": 4200.0}.get(spec.category, 10000.0)
                base_yld = {"vegetable": 18.5, "cereal": 3.4, "oilseed": 1.4, "pulse": 1.1, "spice": 5.2}.get(spec.category, 2.5)
                for dist in dists:
                    area = round(base_area * prod_hub_boost * rng.uniform(0.65, 1.45), 2)
                    yld = round(base_yld * rng.uniform(0.82, 1.22), 4)
                    prod = round(area * yld, 2)
                    apy_rows.append(
                        {
                            "crop_year": f"{yr}-{str(yr + 1)[-2:]}",
                            "season": season,
                            "state": st,
                            "district": dist,
                            "crop_name": spec.crop_name,
                            "crop_type": spec.crop_type,
                            "area_ha": area,
                            "production_t": prod,
                            "yield_t_ha": yld,
                        }
                    )

    prices_path = out_dir / "demo_agmarknet_prices.csv"
    arrivals_path = out_dir / "demo_ceda_arrivals.csv"
    apy_path = out_dir / "demo_apy_production.csv"

    pd.DataFrame(price_rows).to_csv(prices_path, index=False)
    pd.DataFrame(arrival_rows).to_csv(arrivals_path, index=False)
    pd.DataFrame(apy_rows).to_csv(apy_path, index=False)

    return {"prices": prices_path, "arrivals": arrivals_path, "apy": apy_path}


def ensure_demo_fixtures_exist() -> dict[str, Path]:
    paths = {
        "prices": DATA_SAMPLE_DIR / "demo_agmarknet_prices.csv",
        "arrivals": DATA_SAMPLE_DIR / "demo_ceda_arrivals.csv",
        "apy": DATA_SAMPLE_DIR / "demo_apy_production.csv",
    }
    if not all(p.exists() for p in paths.values()):
        return generate_demo_datasets()
    return paths


if __name__ == "__main__":
    generated = generate_demo_datasets()
    print({k: str(v) for k, v in generated.items()})
