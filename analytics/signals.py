"""Deterministic SELL NOW / WAIT / NEUTRAL SQL-backed Signal Engine."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import text

from analytics.metrics import fetch_signal_features_sql
from config import SignalThresholds
from database.connection import DatabaseManager, get_db_manager

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    commodity_id: int
    commodity: str
    category: str
    storable: bool
    market_id: int
    market: str
    district: str
    state: str
    signal_date: date
    verdict_class: str  # SELL NOW | WAIT | NEUTRAL
    verdict_label: str  # SELL NOW | WAIT 1 WEEK | WAIT 1-2 WEEKS | NEUTRAL
    current_modal_price: float | None
    price_change_4w_pct: float | None
    vs_seasonal_avg_pct: float | None
    arrivals_trend: str
    arrivals_change_14d_pct: float | None
    cv_12m: float | None
    state_price_gap_pct: float | None
    evidence_completeness: str  # HIGH | MEDIUM | LOW
    observation_count_90d: int
    analysis_window_start: date | None
    analysis_window_end: date | None
    data_sources: list[str]
    insufficient_evidence: bool
    rule_breakdown: dict[str, Any] = field(default_factory=dict)
    thresholds_used: dict[str, Any] = field(default_factory=dict)
    signal_id: int | None = None

    def to_genai_input(self) -> dict[str, Any]:
        return {
            "commodity": self.commodity,
            "market": self.market,
            "district": self.district,
            "state": self.state,
            "verdict_class": self.verdict_class,
            "verdict": self.verdict_label,
            "current_modal_price": self.current_modal_price,
            "price_change_4w_pct": self.price_change_4w_pct,
            "vs_seasonal_avg_pct": self.vs_seasonal_avg_pct,
            "arrivals_trend": self.arrivals_trend,
            "arrivals_change_14d_pct": self.arrivals_change_14d_pct,
            "cv_12m": self.cv_12m,
            "state_price_gap_pct": self.state_price_gap_pct,
            "storable": self.storable,
            "evidence_completeness": self.evidence_completeness,
            "observation_count_90d": self.observation_count_90d,
            "analysis_window_start": self.analysis_window_start.isoformat() if self.analysis_window_start else None,
            "analysis_window_end": self.analysis_window_end.isoformat() if self.analysis_window_end else None,
            "insufficient_evidence": self.insufficient_evidence,
        }


def evaluate_signal_rules(
    features: dict[str, Any],
    thresholds: SignalThresholds | None = None,
) -> SignalResult:
    """Apply deterministic rule logic on SQL-computed features (zero LLM involvement)."""
    cfg = thresholds or SignalThresholds.load_tuned_or_default()

    obs_recent = int(features.get("obs_recent_7d") or 0)
    obs_prior = int(features.get("obs_prior_7d") or 0)
    obs_90d = int(features.get("obs_90d") or 0)
    recent_avg = features.get("recent_7d_avg")
    prior_avg = features.get("prior_4w_avg")
    seasonal_avg = features.get("seasonal_avg_price")
    seasonal_years = int(features.get("seasonal_years") or 0)

    current_modal = round(float(recent_avg), 2) if recent_avg is not None else None
    price_change_4w_pct: float | None = None
    if recent_avg is not None and prior_avg is not None and prior_avg > 0:
        price_change_4w_pct = round(((recent_avg - prior_avg) / prior_avg) * 100.0, 2)

    vs_seasonal_avg_pct: float | None = None
    if recent_avg is not None and seasonal_avg is not None and seasonal_avg > 0:
        vs_seasonal_avg_pct = round(((recent_avg - seasonal_avg) / seasonal_avg) * 100.0, 2)

    arr_obs_recent = int(features.get("arr_obs_recent") or 0)
    arr_obs_prev = int(features.get("arr_obs_prev") or 0)
    arr_recent = float(features.get("arr_recent_tonnes") or 0.0)
    arr_prev = float(features.get("arr_prev_tonnes") or 0.0)

    arrivals_change_14d_pct: float | None = None
    arrivals_trend = "unavailable"
    if arr_obs_recent >= 2 and arr_obs_prev >= 2 and arr_prev > 0:
        arrivals_change_14d_pct = round(((arr_recent - arr_prev) / arr_prev) * 100.0, 2)
        if arrivals_change_14d_pct >= cfg.arrivals_rise_pct:
            arrivals_trend = "rising"
        elif arrivals_change_14d_pct <= -cfg.arrivals_not_rising_pct:
            arrivals_trend = "falling"
        else:
            arrivals_trend = "stable"

    cv_12m = round(float(features["cv_12m"]), 4) if features.get("cv_12m") is not None else None
    state_gap = (
        round(float(features["state_price_gap_pct"]), 2)
        if features.get("state_price_gap_pct") is not None
        else None
    )

    has_min_coverage = (
        obs_recent >= cfg.min_obs_recent_7d
        and obs_prior >= cfg.min_obs_prior_7d
        and obs_90d >= cfg.min_obs_90d
        and price_change_4w_pct is not None
    )

    has_arrivals = arrivals_trend != "unavailable"
    has_seasonal = vs_seasonal_avg_pct is not None and seasonal_years >= 1
    if has_min_coverage and has_arrivals and has_seasonal:
        completeness = "HIGH"
    elif has_min_coverage and (has_arrivals or has_seasonal):
        completeness = "MEDIUM"
    else:
        completeness = "LOW"

    storable = bool(features["storable"])
    triggers: list[str] = []

    if not has_min_coverage:
        verdict_class = "NEUTRAL"
        insufficient = True
        triggers.append(
            f"Insufficient observations (7d={obs_recent}, prior4w={obs_prior}, 90d={obs_90d}); defaulting to NEUTRAL."
        )
    else:
        insufficient = False
        assert price_change_4w_pct is not None

        neg_momentum = price_change_4w_pct <= cfg.momentum_sell_pct
        pos_momentum = price_change_4w_pct >= cfg.momentum_wait_pct
        arrivals_surge = (
            arrivals_change_14d_pct is not None and arrivals_change_14d_pct >= cfg.arrivals_rise_pct
        )
        arrivals_not_rising = (
            arrivals_change_14d_pct is None or arrivals_change_14d_pct <= cfg.arrivals_not_rising_pct
        )
        in_seasonal_peak = (
            vs_seasonal_avg_pct is not None and vs_seasonal_avg_pct >= cfg.seasonal_peak_pct
        )
        below_seasonal_peak = (
            vs_seasonal_avg_pct is None or vs_seasonal_avg_pct < cfg.seasonal_peak_pct
        )

        # SELL NOW triggers
        if neg_momentum:
            triggers.append(f"Negative 4-week price momentum ({price_change_4w_pct:+.2f}% <= {cfg.momentum_sell_pct:+.2f}%).")
        if arrivals_surge and not pos_momentum:
            triggers.append(f"Arrivals surged +{arrivals_change_14d_pct:.2f}% (>= +{cfg.arrivals_rise_pct:.1f}%), adding supply pressure.")
        if in_seasonal_peak and not (pos_momentum and arrivals_trend == "falling"):
            triggers.append(
                f"Price is {vs_seasonal_avg_pct:+.2f}% above historical seasonal baseline (>= +{cfg.seasonal_peak_pct:.1f}% peak band)."
            )

        if neg_momentum or (arrivals_surge and not pos_momentum) or (in_seasonal_peak and not (pos_momentum and arrivals_trend == "falling")):
            verdict_class = "SELL NOW"
        elif pos_momentum and arrivals_not_rising and below_seasonal_peak:
            verdict_class = "WAIT"
            triggers.append(f"Positive 4-week momentum ({price_change_4w_pct:+.2f}% >= +{cfg.momentum_wait_pct:.2f}%).")
            triggers.append(
                f"Arrivals are {arrivals_trend}" + (f" ({arrivals_change_14d_pct:+.2f}%)." if arrivals_change_14d_pct is not None else " (no arrival series; momentum+seasonal rule applied).")
            )
            if vs_seasonal_avg_pct is not None:
                triggers.append(f"Current price is {vs_seasonal_avg_pct:+.2f}% vs seasonal baseline (below peak band).")
        else:
            verdict_class = "NEUTRAL"
            triggers.append("Mixed or range-bound signals between momentum, arrivals, and seasonal bands.")

    if verdict_class == "WAIT":
        verdict_label = "WAIT 1 WEEK" if not storable else "WAIT 1-2 WEEKS"
    else:
        verdict_label = verdict_class

    if not storable and verdict_class == "WAIT":
        triggers.append("Perishable crop: WAIT duration strictly capped at 1 week with spoilage caution.")
    if cv_12m is not None and cv_12m >= cfg.high_volatility_cv:
        triggers.append(f"Elevated 12-month volatility (CV={cv_12m:.2f} >= {cfg.high_volatility_cv:.2f}).")

    return SignalResult(
        commodity_id=int(features["commodity_id"]),
        commodity=str(features["commodity"]),
        category=str(features["category"]),
        storable=storable,
        market_id=int(features["market_id"]),
        market=str(features["market"]),
        district=str(features["district"]),
        state=str(features["state"]),
        signal_date=features["as_of_date"],
        verdict_class=verdict_class,
        verdict_label=verdict_label,
        current_modal_price=current_modal,
        price_change_4w_pct=price_change_4w_pct,
        vs_seasonal_avg_pct=vs_seasonal_avg_pct,
        arrivals_trend=arrivals_trend,
        arrivals_change_14d_pct=arrivals_change_14d_pct,
        cv_12m=cv_12m,
        state_price_gap_pct=state_gap,
        evidence_completeness=completeness,
        observation_count_90d=obs_90d,
        analysis_window_start=features.get("window_start"),
        analysis_window_end=features.get("window_end"),
        data_sources=list(features.get("data_sources") or []),
        insufficient_evidence=insufficient,
        rule_breakdown={
            "triggers": triggers,
            "obs_recent_7d": obs_recent,
            "obs_prior_7d": obs_prior,
            "obs_90d": obs_90d,
            "seasonal_years": seasonal_years,
            "has_arrivals_data": has_arrivals,
        },
        thresholds_used=cfg.model_dump(),
    )


def save_signal_to_db(db: DatabaseManager, signal: SignalResult) -> int:
    """Persist computed deterministic signal idempotently to analytics.market_signals."""
    t_sig = db.table_ref("analytics", "market_signals")
    params = {
        "signal_date": signal.signal_date.isoformat() if db.backend == "sqlite" else signal.signal_date,
        "commodity_id": signal.commodity_id,
        "market_id": signal.market_id,
        "verdict_class": signal.verdict_class,
        "verdict_label": signal.verdict_label,
        "current_modal_price": signal.current_modal_price,
        "price_change_4w_pct": signal.price_change_4w_pct,
        "vs_seasonal_avg_pct": signal.vs_seasonal_avg_pct,
        "arrivals_trend": signal.arrivals_trend,
        "arrivals_change_14d_pct": signal.arrivals_change_14d_pct,
        "cv_12m": signal.cv_12m,
        "state_price_gap_pct": signal.state_price_gap_pct,
        "storable": signal.storable if db.backend == "postgresql" else int(signal.storable),
        "evidence_completeness": signal.evidence_completeness,
        "observation_count_90d": signal.observation_count_90d,
        "analysis_window_start": (
            signal.analysis_window_start.isoformat()
            if (db.backend == "sqlite" and signal.analysis_window_start)
            else signal.analysis_window_start
        ),
        "analysis_window_end": (
            signal.analysis_window_end.isoformat()
            if (db.backend == "sqlite" and signal.analysis_window_end)
            else signal.analysis_window_end
        ),
        "data_sources": ", ".join(signal.data_sources) if signal.data_sources else None,
        "rationale_metrics": json.dumps(signal.rule_breakdown, default=str),
        "threshold_config": json.dumps(signal.thresholds_used, default=str),
    }

    with db.transaction() as conn:
        if db.backend == "postgresql":
            sig_id = conn.execute(
                text(
                    f"""
                    INSERT INTO {t_sig} (
                        signal_date, commodity_id, market_id, verdict_class, verdict_label,
                        current_modal_price, price_change_4w_pct, vs_seasonal_avg_pct,
                        arrivals_trend, arrivals_change_14d_pct, cv_12m, state_price_gap_pct,
                        storable, evidence_completeness, observation_count_90d,
                        analysis_window_start, analysis_window_end, data_sources,
                        rationale_metrics, threshold_config
                    ) VALUES (
                        :signal_date, :commodity_id, :market_id, :verdict_class, :verdict_label,
                        :current_modal_price, :price_change_4w_pct, :vs_seasonal_avg_pct,
                        :arrivals_trend, :arrivals_change_14d_pct, :cv_12m, :state_price_gap_pct,
                        :storable, :evidence_completeness, :observation_count_90d,
                        :analysis_window_start, :analysis_window_end, :data_sources,
                        CAST(:rationale_metrics AS jsonb), CAST(:threshold_config AS jsonb)
                    )
                    ON CONFLICT (signal_date, commodity_id, market_id) DO UPDATE
                    SET verdict_class = excluded.verdict_class,
                        verdict_label = excluded.verdict_label,
                        current_modal_price = excluded.current_modal_price,
                        price_change_4w_pct = excluded.price_change_4w_pct,
                        vs_seasonal_avg_pct = excluded.vs_seasonal_avg_pct,
                        arrivals_trend = excluded.arrivals_trend,
                        arrivals_change_14d_pct = excluded.arrivals_change_14d_pct,
                        cv_12m = excluded.cv_12m,
                        state_price_gap_pct = excluded.state_price_gap_pct,
                        storable = excluded.storable,
                        evidence_completeness = excluded.evidence_completeness,
                        observation_count_90d = excluded.observation_count_90d,
                        analysis_window_start = excluded.analysis_window_start,
                        analysis_window_end = excluded.analysis_window_end,
                        data_sources = excluded.data_sources,
                        rationale_metrics = excluded.rationale_metrics,
                        threshold_config = excluded.threshold_config,
                        computed_at = now()
                    RETURNING signal_id
                    """
                ),
                params,
            ).scalar_one()
        else:
            conn.execute(
                text(
                    f"""
                    INSERT INTO {t_sig} (
                        signal_date, commodity_id, market_id, verdict_class, verdict_label,
                        current_modal_price, price_change_4w_pct, vs_seasonal_avg_pct,
                        arrivals_trend, arrivals_change_14d_pct, cv_12m, state_price_gap_pct,
                        storable, evidence_completeness, observation_count_90d,
                        analysis_window_start, analysis_window_end, data_sources,
                        rationale_metrics, threshold_config
                    ) VALUES (
                        :signal_date, :commodity_id, :market_id, :verdict_class, :verdict_label,
                        :current_modal_price, :price_change_4w_pct, :vs_seasonal_avg_pct,
                        :arrivals_trend, :arrivals_change_14d_pct, :cv_12m, :state_price_gap_pct,
                        :storable, :evidence_completeness, :observation_count_90d,
                        :analysis_window_start, :analysis_window_end, :data_sources,
                        :rationale_metrics, :threshold_config
                    )
                    ON CONFLICT (signal_date, commodity_id, market_id) DO UPDATE
                    SET verdict_class = excluded.verdict_class,
                        verdict_label = excluded.verdict_label,
                        current_modal_price = excluded.current_modal_price,
                        price_change_4w_pct = excluded.price_change_4w_pct,
                        vs_seasonal_avg_pct = excluded.vs_seasonal_avg_pct,
                        arrivals_trend = excluded.arrivals_trend,
                        arrivals_change_14d_pct = excluded.arrivals_change_14d_pct,
                        cv_12m = excluded.cv_12m,
                        state_price_gap_pct = excluded.state_price_gap_pct,
                        storable = excluded.storable,
                        evidence_completeness = excluded.evidence_completeness,
                        observation_count_90d = excluded.observation_count_90d,
                        analysis_window_start = excluded.analysis_window_start,
                        analysis_window_end = excluded.analysis_window_end,
                        data_sources = excluded.data_sources,
                        rationale_metrics = excluded.rationale_metrics,
                        threshold_config = excluded.threshold_config
                    """
                ),
                params,
            )
            sig_id = conn.execute(
                text(
                    f"""
                    SELECT signal_id FROM {t_sig}
                    WHERE signal_date = :signal_date AND commodity_id = :commodity_id AND market_id = :market_id
                    """
                ),
                {
                    "signal_date": params["signal_date"],
                    "commodity_id": params["commodity_id"],
                    "market_id": params["market_id"],
                },
            ).scalar_one()

    signal.signal_id = int(sig_id)
    return int(sig_id)


def compute_signal_for_market(
    commodity_name: str,
    market_name: str,
    state: str,
    db: DatabaseManager | None = None,
    as_of_date: date | None = None,
    thresholds: SignalThresholds | None = None,
    persist: bool = True,
) -> SignalResult | None:
    """Fetch SQL features strictly up to as_of_date, compute deterministic verdict, and optionally persist."""
    db = db or get_db_manager()
    features = fetch_signal_features_sql(
        db=db,
        commodity_name=commodity_name,
        market_name=market_name,
        state=state,
        as_of_date=as_of_date,
    )
    if not features:
        return None
    result = evaluate_signal_rules(
        features,
        thresholds=thresholds or SignalThresholds.load_tuned_or_default(is_demo=db.is_demo),
    )
    if persist:
        save_signal_to_db(db, result)
    return result
