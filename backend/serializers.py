"""Safe JSON/Pydantic serialization helpers for Pandas DataFrames, NumPy scalars, dates, and SQLite/PG variants."""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd


def clean_scalar(value: Any) -> Any:
    """Convert NumPy/Pandas/Decimal/date scalars to plain JSON-safe Python values."""
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, Decimal):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.date().isoformat() if value.hour == 0 and value.minute == 0 and value.second == 0 else value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, dict):
        return {str(k): clean_scalar(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_scalar(v) for v in value]
    return value


def df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a Pandas DataFrame into JSON-safe dictionaries with None replacing NaN/NaT/Inf."""
    if df is None or df.empty:
        return []
    records: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        records.append({str(k): clean_scalar(v) for k, v in row.items()})
    return records


def parse_json_field(value: Any, default: Any = None) -> Any:
    """Handle SQLite stringified JSON vs PostgreSQL decoded JSONB fields uniformly."""
    if value is None:
        return {} if default is None else default
    if isinstance(value, (dict, list)):
        return clean_scalar(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return {} if default is None else default
        try:
            return clean_scalar(json.loads(s))
        except json.JSONDecodeError:
            return {"raw": s} if default is None else default
    return clean_scalar(value)
