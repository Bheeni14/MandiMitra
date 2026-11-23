"""Data validation, deduplication, 3-IQR outlier flagging, and DQ reject tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class ValidationResult:
    valid_df: pd.DataFrame
    rejects: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def _flag_outliers_3iqr(df: pd.DataFrame) -> pd.Series:
    """Flag modal_price outside [Q1 - 3*IQR, Q3 + 3*IQR] per (commodity, market) or commodity fallback."""
    if df.empty:
        return pd.Series(dtype=bool)

    comm_stats = df.groupby("commodity")["modal_price"].quantile([0.25, 0.75]).unstack()
    pair_counts = df.groupby(["commodity", "market"])["modal_price"].transform("count")

    q1_pair = df.groupby(["commodity", "market"])["modal_price"].transform(lambda s: s.quantile(0.25))
    q3_pair = df.groupby(["commodity", "market"])["modal_price"].transform(lambda s: s.quantile(0.75))

    q1_comm = df["commodity"].map(comm_stats[0.25]) if 0.25 in comm_stats.columns else df["modal_price"]
    q3_comm = df["commodity"].map(comm_stats[0.75]) if 0.75 in comm_stats.columns else df["modal_price"]

    use_pair = pair_counts >= 8
    q1 = q1_pair.where(use_pair, q1_comm)
    q3 = q3_pair.where(use_pair, q3_comm)
    iqr = q3 - q1

    valid_iqr = iqr > 0
    lower = q1 - 3.0 * iqr
    upper = q3 + 3.0 * iqr
    flags = valid_iqr & ((df["modal_price"] < lower) | (df["modal_price"] > upper))
    return flags.fillna(False)


def validate_price_records(
    df: pd.DataFrame,
    source_name: str = "agmarknet_api",
    stage: str = "validation_prices",
) -> ValidationResult:
    """Validate cleaned price DataFrame, log rejected rows with reasons, dedupe natural key, and flag 3-IQR outliers."""
    if df.empty:
        return ValidationResult(
            valid_df=df.copy(),
            rejects=[],
            stats={"input_rows": 0, "valid_rows": 0, "rejected_rows": 0, "outliers_flagged": 0},
        )

    rejects: list[dict[str, Any]] = []
    valid_rows: list[dict[str, Any]] = []

    for row in df.to_dict(orient="records"):
        raw = row.get("raw_record") or {k: v for k, v in row.items() if k != "raw_record"}
        reason: str | None = None

        if not row.get("arrival_date"):
            reason = "missing_or_invalid_arrival_date"
        elif not row.get("state"):
            reason = "missing_state"
        elif not row.get("market"):
            reason = "missing_market"
        elif not row.get("commodity"):
            reason = "missing_commodity"
        elif row.get("min_price") is None or row.get("max_price") is None or row.get("modal_price") is None:
            reason = "missing_price_fields"
        elif row["min_price"] <= 0 or row["max_price"] <= 0 or row["modal_price"] <= 0:
            reason = "non_positive_price"
        elif row["max_price"] < row["min_price"]:
            reason = "max_price_less_than_min_price"
        elif not (row["min_price"] <= row["modal_price"] <= row["max_price"]):
            reason = "modal_price_outside_min_max_range"

        if reason:
            rejects.append(
                {
                    "source_name": row.get("source_name") or source_name,
                    "stage": stage,
                    "reject_reason": reason,
                    "raw_record": raw,
                }
            )
        else:
            valid_rows.append(row)

    if not valid_rows:
        return ValidationResult(
            valid_df=pd.DataFrame(
                columns=[
                    "source_name", "arrival_date", "state", "district", "market",
                    "commodity", "variety", "grade", "min_price", "max_price",
                    "modal_price", "outlier_flag",
                ]
            ),
            rejects=rejects,
            stats={
                "input_rows": len(df),
                "valid_rows": 0,
                "rejected_rows": len(rejects),
                "outliers_flagged": 0,
            },
        )

    clean_df = pd.DataFrame(valid_rows)
    nat_key = ["arrival_date", "state", "district", "market", "commodity", "variety", "grade"]
    dup_mask = clean_df.duplicated(subset=nat_key, keep="first")
    if dup_mask.any():
        def _raw_commodity(rec: dict[str, Any]) -> str:
            raw_rec = rec.get("raw_record") or {}
            return str(raw_rec.get("commodity") or "").strip().lower() if isinstance(raw_rec, dict) else ""

        first_raw: dict[tuple[Any, ...], str] = {}
        for kept_row in clean_df[~dup_mask].to_dict(orient="records"):
            first_raw[tuple(kept_row[k] for k in nat_key)] = _raw_commodity(kept_row)
        for dup_row in clean_df[dup_mask].to_dict(orient="records"):
            kept_raw = first_raw.get(tuple(dup_row[k] for k in nat_key), "")
            # Two different raw commodity names (e.g. Paddy(Common) and Paddy(Basmati)) that normalise to
            # one canonical name and collide on variety/grade are not true duplicates: say so explicitly.
            collision = bool(kept_raw) and _raw_commodity(dup_row) not in ("", kept_raw)
            rejects.append(
                {
                    "source_name": dup_row.get("source_name") or source_name,
                    "stage": stage,
                    "reject_reason": "alias_collision_unresolved" if collision else "duplicate_natural_key",
                    "raw_record": dup_row.get("raw_record") or {},
                }
            )
        clean_df = clean_df[~dup_mask].copy()

    clean_df["min_price"] = clean_df["min_price"].round(2)
    clean_df["max_price"] = clean_df["max_price"].round(2)
    clean_df["modal_price"] = clean_df["modal_price"].round(2)
    clean_df["outlier_flag"] = _flag_outliers_3iqr(clean_df)

    if "raw_record" in clean_df.columns:
        clean_df = clean_df.drop(columns=["raw_record"])

    outliers_count = int(clean_df["outlier_flag"].sum())
    return ValidationResult(
        valid_df=clean_df.reset_index(drop=True),
        rejects=rejects,
        stats={
            "input_rows": len(df),
            "valid_rows": len(clean_df),
            "rejected_rows": len(rejects),
            "outliers_flagged": outliers_count,
        },
    )


def validate_arrival_records(
    df: pd.DataFrame,
    source_name: str = "ceda_archive",
    stage: str = "validation_arrivals",
) -> ValidationResult:
    """Validate arrival tonnage records and sum duplicates on (date, state, district, market, commodity)."""
    if df.empty:
        return ValidationResult(
            valid_df=df.copy(),
            rejects=[],
            stats={"input_rows": 0, "valid_rows": 0, "rejected_rows": 0, "duplicates_summed": 0},
        )

    rejects: list[dict[str, Any]] = []
    valid_rows: list[dict[str, Any]] = []

    for row in df.to_dict(orient="records"):
        raw = row.get("raw_record") or {k: v for k, v in row.items() if k != "raw_record"}
        reason: str | None = None
        if not row.get("arrival_date"):
            reason = "missing_or_invalid_arrival_date"
        elif not row.get("state"):
            reason = "missing_state"
        elif not row.get("market"):
            reason = "missing_market"
        elif not row.get("commodity"):
            reason = "missing_commodity"
        elif row.get("quantity_tonnes") is None:
            reason = "missing_arrival_quantity"
        elif row["quantity_tonnes"] < 0:
            reason = "negative_arrival_quantity"

        if reason:
            rejects.append(
                {
                    "source_name": row.get("source_name") or source_name,
                    "stage": stage,
                    "reject_reason": reason,
                    "raw_record": raw,
                }
            )
        else:
            valid_rows.append(row)

    if not valid_rows:
        return ValidationResult(
            valid_df=pd.DataFrame(
                columns=["source_name", "arrival_date", "state", "district", "market", "commodity", "quantity_tonnes"]
            ),
            rejects=rejects,
            stats={"input_rows": len(df), "valid_rows": 0, "rejected_rows": len(rejects), "duplicates_summed": 0},
        )

    clean_df = pd.DataFrame(valid_rows)
    pre_agg_len = len(clean_df)
    grouped = (
        clean_df.groupby(
            ["arrival_date", "state", "district", "market", "commodity"],
            as_index=False,
        )
        .agg({"quantity_tonnes": "sum", "source_name": "first"})
    )
    grouped["quantity_tonnes"] = grouped["quantity_tonnes"].round(3)
    summed_count = pre_agg_len - len(grouped)

    return ValidationResult(
        valid_df=grouped.reset_index(drop=True),
        rejects=rejects,
        stats={
            "input_rows": len(df),
            "valid_rows": len(grouped),
            "rejected_rows": len(rejects),
            "duplicates_summed": summed_count,
        },
    )


def validate_apy_records(
    df: pd.DataFrame,
    source_name: str = "des_apy",
    stage: str = "validation_apy",
) -> ValidationResult:
    """Validate APY crop production records and deduplicate by (crop_year, season, state, district, crop_name)."""
    if df.empty:
        return ValidationResult(
            valid_df=df.copy(),
            rejects=[],
            stats={"input_rows": 0, "valid_rows": 0, "rejected_rows": 0},
        )

    rejects: list[dict[str, Any]] = []
    valid_rows: list[dict[str, Any]] = []

    for row in df.to_dict(orient="records"):
        raw = row.get("raw_record") or {k: v for k, v in row.items() if k != "raw_record"}
        reason: str | None = None
        if not row.get("crop_year"):
            reason = "missing_or_invalid_crop_year"
        elif row.get("is_aggregate_state"):
            reason = "aggregate_state_summary_row_excluded"
        elif not row.get("state"):
            reason = "missing_state"
        elif row.get("is_aggregate_district"):
            reason = "aggregate_district_summary_row_excluded"
        elif not row.get("district") or row.get("district") == "Unknown":
            reason = "missing_district"
        elif not row.get("crop_name"):
            reason = "missing_crop_name"
        elif row.get("unit_error") is not None and not pd.isna(row.get("unit_error")) and str(row.get("unit_error")).strip():
            reason = str(row["unit_error"])
        elif (row.get("area_ha") is not None and row["area_ha"] < 0) or (
            row.get("production_t") is not None and row["production_t"] < 0
        ) or (row.get("yield_t_ha") is not None and row["yield_t_ha"] < 0):
            reason = "negative_production_metric"
        elif row.get("area_ha") is None and row.get("production_t") is None:
            reason = "missing_area_and_production"

        if reason:
            rejects.append(
                {
                    "source_name": row.get("source_name") or source_name,
                    "stage": stage,
                    "reject_reason": reason,
                    "raw_record": raw,
                }
            )
        else:
            valid_rows.append(row)

    if not valid_rows:
        return ValidationResult(
            valid_df=pd.DataFrame(
                columns=[
                    "source_name", "crop_year", "season", "state", "district",
                    "crop_name", "crop_type", "area_ha", "production_t", "yield_t_ha",
                ]
            ),
            rejects=rejects,
            stats={"input_rows": len(df), "valid_rows": 0, "rejected_rows": len(rejects)},
        )

    clean_df = pd.DataFrame(valid_rows)
    nat_key = ["crop_year", "season", "state", "district", "crop_name"]
    dup_mask = clean_df.duplicated(subset=nat_key, keep="first")
    if dup_mask.any():
        for dup_row in clean_df[dup_mask].to_dict(orient="records"):
            rejects.append(
                {
                    "source_name": dup_row.get("source_name") or source_name,
                    "stage": stage,
                    "reject_reason": "duplicate_apy_natural_key",
                    "raw_record": dup_row.get("raw_record") or {},
                }
            )
        clean_df = clean_df[~dup_mask].copy()

    for helper_col in ("raw_record", "is_aggregate_state", "is_aggregate_district", "unit_error"):
        if helper_col in clean_df.columns:
            clean_df = clean_df.drop(columns=[helper_col])

    return ValidationResult(
        valid_df=clean_df.reset_index(drop=True),
        rejects=rejects,
        stats={
            "input_rows": len(df),
            "valid_rows": len(clean_df),
            "rejected_rows": len(rejects),
        },
    )
