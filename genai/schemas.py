"""Pydantic schemas for grounded GenAI input metrics and bilingual briefing output."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SignalInput(BaseModel):
    commodity: str
    market: str
    district: str = ""
    state: str = ""
    verdict_class: str  # SELL NOW | WAIT | NEUTRAL
    verdict: str  # SELL NOW | WAIT 1 WEEK | WAIT 1-2 WEEKS | NEUTRAL
    current_modal_price: float | None = None
    price_change_4w_pct: float | None = None
    vs_seasonal_avg_pct: float | None = None
    arrivals_trend: str = "unavailable"
    arrivals_change_14d_pct: float | None = None
    cv_12m: float | None = None
    state_price_gap_pct: float | None = None
    storable: bool = True
    evidence_completeness: str = "MEDIUM"
    observation_count_90d: int = 0
    analysis_window_start: str | None = None
    analysis_window_end: str | None = None
    insufficient_evidence: bool = False


class BriefingOutput(BaseModel):
    verdict: str = Field(..., min_length=3, max_length=40)
    reason_en: str = Field(..., min_length=15, max_length=800)
    caution_en: str = Field(..., min_length=10, max_length=600)
    reason_hi: str = Field(..., min_length=10, max_length=900)
    caution_hi: str = Field(..., min_length=10, max_length=700)
