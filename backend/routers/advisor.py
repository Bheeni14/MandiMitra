"""Flagship AI Crop Advisor endpoint: SQL decides, Gemini explains & validator verifies."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from analytics.signals import SignalResult
from backend.dependencies import get_db
from backend.schemas import (
    AdvisorBriefingSchema,
    AdvisorHistoryPoint,
    AdvisorRequest,
    AdvisorResponse,
    AdvisorSignalSchema,
    RecentBriefingItem,
)
from backend.serializers import clean_scalar, df_to_records
from config import get_settings
from database.connection import DatabaseManager
from genai.briefing import BriefingRecord
from services.concurrency import run_bundles, run_queries
from services.dashboard_service import get_advisor_recommendation
from services.data_service import query_market_arrivals, query_price_explorer, query_recent_briefings

router = APIRouter(prefix="/api/advisor", tags=["advisor"])

# In-process request cache preserving Streamlit session_state behavior:
# language toggling or repeated views reuse the exact SQL signal & validated briefing
# unless force_refresh=True is explicitly requested.
# Bounded (least-recently-used entries are dropped) so a public deployment's memory cannot grow without limit.
_AdvisorKey = tuple[str, bool, str, str, str, str | None]
_ADVISOR_CACHE: OrderedDict[_AdvisorKey, tuple[SignalResult, BriefingRecord]] = OrderedDict()
# When each entry was last recomputed; force_refresh within the cooldown is served from cache, so repeated
# "regenerate" requests cannot spend the Gemini quota.
_ADVISOR_COMPUTED_AT: dict[_AdvisorKey, float] = {}
ADVISOR_CACHE_MAX_ENTRIES = 512
ADVISOR_REFRESH_COOLDOWN_SECONDS = 60.0


def clear_advisor_cache() -> None:
    _ADVISOR_CACHE.clear()
    _ADVISOR_COMPUTED_AT.clear()


def _store_advisor_result(key: _AdvisorKey, value: tuple[SignalResult, BriefingRecord], forced: bool) -> None:
    _ADVISOR_CACHE[key] = value
    _ADVISOR_CACHE.move_to_end(key)
    if forced:
        _ADVISOR_COMPUTED_AT[key] = time.monotonic()
    while len(_ADVISOR_CACHE) > ADVISOR_CACHE_MAX_ENTRIES:
        evicted, _ = _ADVISOR_CACHE.popitem(last=False)
        _ADVISOR_COMPUTED_AT.pop(evicted, None)


def _serialize_signal(signal: SignalResult) -> AdvisorSignalSchema:
    return AdvisorSignalSchema(
        signal_id=signal.signal_id,
        commodity_id=signal.commodity_id,
        commodity=signal.commodity,
        category=signal.category,
        storable=signal.storable,
        market_id=signal.market_id,
        market=signal.market,
        district=signal.district,
        state=signal.state,
        signal_date=signal.signal_date.isoformat(),
        verdict_class=signal.verdict_class,
        verdict_label=signal.verdict_label,
        current_modal_price=clean_scalar(signal.current_modal_price),
        price_change_4w_pct=clean_scalar(signal.price_change_4w_pct),
        vs_seasonal_avg_pct=clean_scalar(signal.vs_seasonal_avg_pct),
        arrivals_trend=signal.arrivals_trend,
        arrivals_change_14d_pct=clean_scalar(signal.arrivals_change_14d_pct),
        cv_12m=clean_scalar(signal.cv_12m),
        state_price_gap_pct=clean_scalar(signal.state_price_gap_pct),
        evidence_completeness=signal.evidence_completeness,
        observation_count_90d=signal.observation_count_90d,
        analysis_window_start=signal.analysis_window_start.isoformat() if signal.analysis_window_start else None,
        analysis_window_end=signal.analysis_window_end.isoformat() if signal.analysis_window_end else None,
        data_sources=list(signal.data_sources),
        insufficient_evidence=signal.insufficient_evidence,
        rule_breakdown=clean_scalar(signal.rule_breakdown),
        thresholds_used=clean_scalar(signal.thresholds_used),
    )


def _serialize_briefing(briefing: BriefingRecord) -> AdvisorBriefingSchema:
    return AdvisorBriefingSchema(
        briefing_id=briefing.briefing_id,
        verdict=briefing.output.verdict,
        reason_en=briefing.output.reason_en,
        caution_en=briefing.output.caution_en,
        reason_hi=briefing.output.reason_hi,
        caution_hi=briefing.output.caution_hi,
        validation_status=briefing.validation_status,
        validation_errors=list(briefing.validation_errors),
        model_used=briefing.model_used,
        used_fallback=briefing.used_fallback,
    )


def _history_queries(
    db: DatabaseManager,
    commodity: str,
    market: str,
    state: str,
    as_of_date: date | None,
) -> dict[str, Callable[[], pd.DataFrame]]:
    """The two independent reads behind the price & arrivals chart."""
    return {
        "hist_p": lambda: query_price_explorer(
            db,
            state=state,
            market=market,
            commodity=commodity,
            end_date=as_of_date,
            limit=400,
        ),
        "hist_a": lambda: query_market_arrivals(
            db,
            commodity=commodity,
            market=market,
            state=state,
            limit=400,
        ),
    }


def _build_history_series(
    hist_p: pd.DataFrame,
    hist_a: pd.DataFrame,
    as_of_date: date | None,
) -> list[AdvisorHistoryPoint]:
    if as_of_date and not hist_a.empty:
        hist_a = hist_a[hist_a["arrival_date"] <= as_of_date]

    if hist_p.empty and hist_a.empty:
        return []

    if not hist_p.empty:
        p_daily = (
            hist_p.groupby("arrival_date", as_index=False)["modal_price"]
            .mean()
            .sort_values("arrival_date")
            .tail(120)
        )
        p_daily["modal_price"] = p_daily["modal_price"].round(2)
    else:
        p_daily = pd.DataFrame(columns=["arrival_date", "modal_price"])

    if not hist_a.empty:
        a_daily = (
            hist_a.groupby("arrival_date", as_index=False)["quantity_tonnes"]
            .sum()
            .sort_values("arrival_date")
            .tail(120)
        )
        a_daily["quantity_tonnes"] = a_daily["quantity_tonnes"].round(2)
    else:
        a_daily = pd.DataFrame(columns=["arrival_date", "quantity_tonnes"])

    if not p_daily.empty and not a_daily.empty:
        merged = p_daily.merge(a_daily, on="arrival_date", how="outer").sort_values("arrival_date").tail(120)
    elif not p_daily.empty:
        merged = p_daily.copy()
        merged["quantity_tonnes"] = None
    else:
        merged = a_daily.copy()
        merged["modal_price"] = None

    return [AdvisorHistoryPoint(**r) for r in df_to_records(merged)]


@router.post("/recommendation", response_model=AdvisorResponse)
def post_advisor_recommendation(
    payload: AdvisorRequest,
    db: DatabaseManager = Depends(get_db),
) -> AdvisorResponse:
    cache_key = (
        db.database_url,
        db.is_demo,
        payload.commodity,
        payload.market,
        payload.state,
        payload.as_of_date.isoformat() if payload.as_of_date else None,
    )

    def load_recommendation() -> tuple[SignalResult, BriefingRecord, bool]:
        if cache_key in _ADVISOR_CACHE:
            refreshed_at = _ADVISOR_COMPUTED_AT.get(cache_key)
            recently_refreshed = (
                refreshed_at is not None and time.monotonic() - refreshed_at < ADVISOR_REFRESH_COOLDOWN_SECONDS
            )
            if not payload.force_refresh or recently_refreshed:
                _ADVISOR_CACHE.move_to_end(cache_key)
                cached_signal, cached_briefing = _ADVISOR_CACHE[cache_key]
                return cached_signal, cached_briefing, True
        new_signal, new_briefing = get_advisor_recommendation(
            db=db,
            commodity=payload.commodity,
            market=payload.market,
            state=payload.state,
            as_of_date=payload.as_of_date,
        )
        if new_signal is None or new_briefing is None:
            raise HTTPException(
                status_code=404,
                detail="Could not compute a recommendation for this selection (insufficient or missing historical price data).",
            )
        _store_advisor_result(cache_key, (new_signal, new_briefing), forced=payload.force_refresh)
        return new_signal, new_briefing, False

    # The chart history and the recent-briefings list don't depend on the recommendation, so on PostgreSQL they
    # load while it is computed. When this request may persist a new briefing, the list is read afterwards so
    # it still includes that briefing.
    persists = get_settings().advisor_persistence_allowed(db.is_demo)
    context_queries: dict[str, Callable[[], Any]] = _history_queries(
        db, payload.commodity, payload.market, payload.state, payload.as_of_date
    )
    if not persists:
        context_queries["recent"] = lambda: query_recent_briefings(db, commodity=payload.commodity, limit=8)
    loaded = run_bundles(
        db,
        {
            "recommendation": load_recommendation,
            "context": lambda: run_queries(db, context_queries),
        },
    )
    signal, briefing, was_cached = loaded["recommendation"]
    context = loaded["context"]

    history_series = _build_history_series(context["hist_p"], context["hist_a"], payload.as_of_date)
    recent_br_df = (
        context["recent"] if "recent" in context else query_recent_briefings(db, commodity=payload.commodity, limit=8)
    )
    recent_briefings = [
        RecentBriefingItem(
            briefing_id=int(r["briefing_id"]),
            commodity=str(r["commodity"]),
            market=str(r["market"]),
            state=str(r["state"]),
            generated_at=str(r["generated_at"]),
            verdict=str(r["verdict"]),
            validation_status=str(r["validation_status"]),
            model_used=str(r["model_used"]),
            used_fallback=bool(r["used_fallback"]),
        )
        for r in df_to_records(recent_br_df)
    ]

    return AdvisorResponse(
        cached=was_cached,
        is_demo=db.is_demo,
        signal=_serialize_signal(signal),
        briefing=_serialize_briefing(briefing),
        genai_input=clean_scalar(signal.to_genai_input()),
        history_series=history_series,
        recent_briefings=recent_briefings,
    )
