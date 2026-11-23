"""Briefing orchestration: Gemini API -> validator -> 1 retry -> fallback -> persist audit record."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from analytics.signals import SignalResult
from config import get_settings
from database.connection import DatabaseManager, get_db_manager
from genai.fallback import generate_template_fallback
from genai.gemini_client import GeminiBriefingClient, GeminiMalformedResponseError
from genai.schemas import BriefingOutput, SignalInput
from genai.validator import BriefingValidationResult, validate_briefing

logger = logging.getLogger(__name__)


@dataclass
class BriefingRecord:
    briefing_id: int | None
    signal_input: SignalInput
    output: BriefingOutput
    validation_status: str  # validated_ai | validated_ai_retry | fallback_template | failed
    validation_errors: list[str]
    model_used: str
    used_fallback: bool


def _request_and_validate(
    client: GeminiBriefingClient,
    signal_input: SignalInput,
    retry_feedback: list[str] | None = None,
) -> BriefingValidationResult:
    """One Gemini call + validation. Malformed JSON counts as a validation failure (retryable)."""
    try:
        raw = client.generate_briefing_json(signal_input, retry_feedback=retry_feedback)
    except GeminiMalformedResponseError as exc:
        return BriefingValidationResult(is_valid=False, errors=[f"Malformed Gemini JSON response: {exc}"])
    return validate_briefing(raw, signal_input)


def generate_grounded_briefing(
    signal: SignalResult,
    db: DatabaseManager | None = None,
    gemini_client: GeminiBriefingClient | None = None,
    persist: bool = True,
) -> BriefingRecord:
    """Generate a validated bilingual briefing for a deterministic SQL SignalResult."""
    signal_input = SignalInput(**signal.to_genai_input())

    validation_errors: list[str] = []
    output: BriefingOutput | None = None
    validation_status = "fallback_template"
    used_fallback = True
    model_used = "template_fallback"

    client: GeminiBriefingClient | None = gemini_client
    if client is None:
        if not get_settings().gemini_enabled:
            # Gemini is OFF by default (GEMINI_ENABLED). A configured API key alone never triggers a call;
            # when enabled, a missing key is reported by the client as 'not configured' and no SDK call is made.
            validation_errors.append("Gemini disabled by configuration (GEMINI_ENABLED=false); used deterministic template.")
        else:
            try:
                client = GeminiBriefingClient()
            except ValueError as exc:
                validation_errors.append(f"Gemini client configuration error: {exc}")
                logger.warning("Gemini client misconfigured (%s); using validated template fallback.", exc)

    if client is not None and client.is_configured:
        try:
            val_1 = _request_and_validate(client, signal_input)
            if val_1.is_valid and val_1.output is not None:
                output = val_1.output
                validation_status = "validated_ai"
                used_fallback = False
                model_used = client.model
            else:
                validation_errors.extend(val_1.errors)
                logger.warning("Gemini briefing failed validation (attempt 1): %s", val_1.errors)
                val_2 = _request_and_validate(client, signal_input, retry_feedback=val_1.errors)
                if val_2.is_valid and val_2.output is not None:
                    output = val_2.output
                    validation_status = "validated_ai_retry"
                    used_fallback = False
                    model_used = client.model
                else:
                    validation_errors.extend([f"Retry: {e}" for e in val_2.errors])
                    logger.warning("Gemini briefing failed validation after retry: %s", val_2.errors)
        except Exception as exc:
            validation_errors.append(f"Gemini API invocation error: {exc}")
            logger.warning("Gemini API unavailable/failed (%s); falling back to validated template.", exc)
    elif client is not None:
        validation_errors.append("GEMINI_API_KEY not configured; used deterministic template fallback.")

    if output is None:
        output = generate_template_fallback(signal_input)
        fallback_check = validate_briefing(output.model_dump(), signal_input)
        if not fallback_check.is_valid:
            validation_errors.extend([f"Fallback check: {e}" for e in fallback_check.errors])
            validation_status = "failed"
        else:
            validation_status = "fallback_template"
        used_fallback = True
        model_used = "deterministic_template_v1"

    briefing_id: int | None = None
    if persist:
        db = db or get_db_manager()
        briefing_id = _save_briefing(
            db=db,
            commodity_id=signal.commodity_id,
            market_id=signal.market_id,
            signal_id=signal.signal_id,
            signal_input=signal_input,
            output=output,
            validation_status=validation_status,
            validation_errors=validation_errors,
            model_used=model_used,
            used_fallback=used_fallback,
        )

    return BriefingRecord(
        briefing_id=briefing_id,
        signal_input=signal_input,
        output=output,
        validation_status=validation_status,
        validation_errors=validation_errors,
        model_used=model_used,
        used_fallback=used_fallback,
    )


def _save_briefing(
    db: DatabaseManager,
    commodity_id: int,
    market_id: int,
    signal_id: int | None,
    signal_input: SignalInput,
    output: BriefingOutput,
    validation_status: str,
    validation_errors: list[str],
    model_used: str,
    used_fallback: bool,
) -> int:
    t_br = db.table_ref("analytics", "briefings")
    sig_json = json.dumps(signal_input.model_dump(), ensure_ascii=False)
    err_json = json.dumps(validation_errors, ensure_ascii=False)

    with db.transaction() as conn:
        if db.backend == "postgresql":
            bid = conn.execute(
                text(
                    f"""
                    INSERT INTO {t_br} (
                        commodity_id, market_id, signal_id, signal_json,
                        verdict, reason_en, caution_en, reason_hi, caution_hi,
                        validation_status, validation_errors, model_used, used_fallback
                    ) VALUES (
                        :cid, :mid, :sid, CAST(:sig_json AS jsonb),
                        :verdict, :reason_en, :caution_en, :reason_hi, :caution_hi,
                        :val_status, CAST(:val_errors AS jsonb), :model_used, :used_fallback
                    )
                    RETURNING briefing_id
                    """
                ),
                {
                    "cid": commodity_id,
                    "mid": market_id,
                    "sid": signal_id,
                    "sig_json": sig_json,
                    "verdict": output.verdict,
                    "reason_en": output.reason_en,
                    "caution_en": output.caution_en,
                    "reason_hi": output.reason_hi,
                    "caution_hi": output.caution_hi,
                    "val_status": validation_status,
                    "val_errors": err_json,
                    "model_used": model_used,
                    "used_fallback": used_fallback,
                },
            ).scalar_one()
        else:
            res = conn.execute(
                text(
                    f"""
                    INSERT INTO {t_br} (
                        commodity_id, market_id, signal_id, signal_json,
                        verdict, reason_en, caution_en, reason_hi, caution_hi,
                        validation_status, validation_errors, model_used, used_fallback
                    ) VALUES (
                        :cid, :mid, :sid, :sig_json,
                        :verdict, :reason_en, :caution_en, :reason_hi, :caution_hi,
                        :val_status, :val_errors, :model_used, :used_fallback
                    )
                    """
                ),
                {
                    "cid": commodity_id,
                    "mid": market_id,
                    "sid": signal_id,
                    "sig_json": sig_json,
                    "verdict": output.verdict,
                    "reason_en": output.reason_en,
                    "caution_en": output.caution_en,
                    "reason_hi": output.reason_hi,
                    "caution_hi": output.caution_hi,
                    "val_status": validation_status,
                    "val_errors": err_json,
                    "model_used": model_used,
                    "used_fallback": int(used_fallback),
                },
            )
            bid = int(res.lastrowid)
    return int(bid)
