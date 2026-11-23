"""Unit tests for GenAI briefing validator, number grounding, retry logic, and template fallback."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from analytics.signals import compute_signal_for_market
from database.connection import DatabaseManager
from genai.briefing import generate_grounded_briefing
from genai.fallback import generate_template_fallback
from genai.gemini_client import GeminiBriefingClient
from genai.schemas import SignalInput
from genai.validator import validate_briefing


def _sample_signal_input() -> SignalInput:
    return SignalInput(
        commodity="Tomato",
        market="Lasalgaon",
        district="Nashik",
        state="Maharashtra",
        verdict_class="WAIT",
        verdict="WAIT 1 WEEK",
        current_modal_price=2200.0,
        price_change_4w_pct=5.2,
        vs_seasonal_avg_pct=-3.4,
        arrivals_trend="falling",
        arrivals_change_14d_pct=-12.5,
        cv_12m=0.21,
        state_price_gap_pct=24.0,
        storable=False,
        evidence_completeness="HIGH",
        observation_count_90d=38,
        analysis_window_start="2024-07-01",
        analysis_window_end="2025-06-30",
        insufficient_evidence=False,
    )


def test_validator_accepts_grounded_bilingual_output() -> None:
    sig = _sample_signal_input()
    payload = {
        "verdict": "WAIT 1 WEEK",
        "reason_en": "Tomato in Lasalgaon averages Rs 2200 per quintal, up 5.2% over 4 weeks and 3.4% below seasonal norms while arrivals fell 12.5%.",
        "caution_en": "Tomato is perishable (12 months CV 0.21); hold only for 1 week with safe storage. Guidance only, not financial advice.",
        "reason_hi": "लासलगांव में टमाटर का भाव 2200 रुपये प्रति क्विंटल है, जो 4 सप्ताह में 5.2% बढ़ा और मौसमी औसत से 3.4% नीचे है, आवक 12.5% घटी है।",
        "caution_hi": "टमाटर जल्दी खराब होने वाली फसल है (CV 0.21); सुरक्षित भंडारण हो तभी 1 सप्ताह रुकें। यह केवल मार्गदर्शन है।",
    }
    res = validate_briefing(payload, sig)
    assert res.is_valid, res.errors


def test_validator_rejects_ungrounded_numbers_verdict_drift_and_guarantees() -> None:
    sig = _sample_signal_input()

    # 1. Hallucinated price & percentage (both English and Devanagari digits)
    bad_num = {
        "verdict": "WAIT 1 WEEK",
        "reason_en": "Tomato jumped 19.5% to Rs 3500 per quintal.",
        "caution_en": "Hold safely.",
        "reason_hi": "टमाटर के भाव ३५०० रुपये तक पहुंचे।",
        "caution_hi": "सुरक्षित भंडारण रखें।",
    }
    res1 = validate_briefing(bad_num, sig)
    assert not res1.is_valid
    assert any("Ungrounded number" in e for e in res1.errors)

    # 2. Verdict drift (LLM trying to override SQL verdict)
    bad_verdict = {
        "verdict": "SELL NOW",
        "reason_en": "Prices rose 5.2% to Rs 2200.",
        "caution_en": "Watch spoilage risk.",
        "reason_hi": "भाव 5.2% बढ़कर 2200 रुपये हुए।",
        "caution_hi": "खराब होने का जोखिम देखें।",
    }
    res2 = validate_briefing(bad_verdict, sig)
    assert not res2.is_valid
    assert any("Verdict mismatch" in e for e in res2.errors)

    # 3. Guarantee promise
    bad_guarantee = {
        "verdict": "WAIT 1 WEEK",
        "reason_en": "Prices rose 5.2% to Rs 2200.",
        "caution_en": "You will get guaranteed profit.",
        "reason_hi": "भाव 5.2% बढ़कर 2200 रुपये हुए।",
        "caution_hi": "मुनाफे की गारंटी है।",
    }
    res3 = validate_briefing(bad_guarantee, sig)
    assert not res3.is_valid
    assert any("guarantee" in e.lower() for e in res3.errors)


def test_fallback_passes_validator() -> None:
    sig = _sample_signal_input()
    fb = generate_template_fallback(sig)
    check = validate_briefing(fb.model_dump(), sig)
    assert check.is_valid, check.errors


def test_briefing_retries_once_then_uses_fallback_on_failure(populated_db: DatabaseManager) -> None:
    sig = compute_signal_for_market(
        commodity_name="Tomato",
        market_name="Lasalgaon",
        state="Maharashtra",
        db=populated_db,
        as_of_date=date(2025, 6, 30),
        persist=True,
    )
    assert sig is not None

    # Mock client returns hallucinated JSON twice -> must retry once, then fall back cleanly and persist
    mock_client = MagicMock(spec=GeminiBriefingClient)
    mock_client.is_configured = True
    mock_client.model = "gemini-mock"
    mock_client.generate_briefing_json.return_value = {
        "verdict": "SELL NOW",  # mismatches SQL verdict
        "reason_en": "Invented 99.9% surge.",
        "caution_en": "Guaranteed profit!",
        "reason_hi": "पक्का मुनाफा होगा।",
        "caution_hi": "सावधान रहें।",
    }

    record = generate_grounded_briefing(sig, db=populated_db, gemini_client=mock_client, persist=True)
    assert mock_client.generate_briefing_json.call_count == 2
    assert record.used_fallback is True
    assert record.validation_status == "fallback_template"
    assert record.output.verdict == sig.verdict_label
    assert record.briefing_id is not None

    # Also test API exception fallback
    err_client = MagicMock(spec=GeminiBriefingClient)
    err_client.is_configured = True
    err_client.model = "gemini-mock"
    err_client.generate_briefing_json.side_effect = RuntimeError("API timeout")

    record2 = generate_grounded_briefing(sig, db=populated_db, gemini_client=err_client, persist=True)
    assert record2.used_fallback is True
    assert record2.validation_status == "fallback_template"

    with populated_db.readonly_connection() as conn:
        cnt = conn.execute(text("SELECT COUNT(*) FROM briefings")).scalar_one()
    assert cnt >= 2


def test_validator_blocks_date_token_leak_and_allows_28d_3y_phrases() -> None:
    sig = _sample_signal_input()
    sig.analysis_window_start = "2024-11-18"
    sig.analysis_window_end = "2025-11-18"

    # Quoting full window dates + "28 days" + "3 years" is valid
    ok_payload = {
        "verdict": "WAIT 1 WEEK",
        "reason_en": "From 2024-11-18 to 2025-11-18, Tomato averages Rs 2200, up 5.2% vs 28 days ago and 3.4% below the 3 years seasonal average.",
        "caution_en": "Perishable crop (CV 0.21); hold at most 1 week. Not financial advice.",
        "reason_hi": "2024-11-18 से 2025-11-18 तक भाव 2200 रुपये रहा, जो 28 दिनों में 5.2% बढ़ा और पिछले 3 वर्षों के औसत से 3.4% नीचे है।",
        "caution_hi": "टमाटर जल्दी खराब होता है (CV 0.21); सुरक्षित भंडारण हो तभी 1 सप्ताह रुकें।",
    }
    res_ok = validate_briefing(ok_payload, sig)
    assert res_ok.is_valid, res_ok.errors

    # Using date component "18%" or "11%" or ungrounded "28%" MUST fail
    bad_date_leak = {
        **ok_payload,
        "reason_en": "Tomato rose 18% and 28% from 2025-11-18.",
    }
    res_bad = validate_briefing(bad_date_leak, sig)
    assert not res_bad.is_valid
    assert any("18.0" in e for e in res_bad.errors)
    assert any("28.0" in e for e in res_bad.errors)


def test_gemini_model_config_and_unsupported_model_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.genai import errors as genai_errors

    from config import Settings
    from genai.gemini_client import GeminiModelUnavailableError

    assert Settings.model_fields["gemini_model"].default == "gemini-3.8-flash"

    for bad_name in ("gpt-4o", "", "text-bison-001", "Gemini 3.8 Flash"):
        with pytest.raises(ValueError, match="Unsupported Gemini model"):
            GeminiBriefingClient(api_key="dummy-key", model=bad_name)

    client = GeminiBriefingClient(api_key="dummy-key", model="gemini-3.5-flash-lite")
    assert client.model == "gemini-3.5-flash-lite"

    not_found = genai_errors.ClientError(
        404,
        {"error": {"code": 404, "message": "models/gemini-unknown-model is not found", "status": "NOT_FOUND"}},
    )
    fake_sdk = MagicMock()
    fake_sdk.models.generate_content.side_effect = not_found
    monkeypatch.setattr(GeminiBriefingClient, "_build_sdk_client", lambda self: fake_sdk)

    bad_model = GeminiBriefingClient(api_key="dummy-key", model="gemini-unknown-model")
    with pytest.raises(GeminiModelUnavailableError, match="unsupported or unavailable"):
        bad_model.generate_briefing_json(_sample_signal_input())

