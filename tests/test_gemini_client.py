"""Regression tests for the Gemini briefing client and orchestration (SDK fully mocked; no network)."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from google.genai import errors as genai_errors
from sqlalchemy import text

from analytics.signals import SignalResult, compute_signal_for_market
from database.connection import DatabaseManager
from genai.briefing import generate_grounded_briefing
from genai.fallback import generate_template_fallback
from genai.gemini_client import (
    BRIEFING_KEYS,
    GeminiAuthError,
    GeminiBriefingClient,
    GeminiMalformedResponseError,
    GeminiModelUnavailableError,
    GeminiQuotaError,
    GeminiServiceError,
    GeminiTimeoutError,
)
from genai.prompts import SYSTEM_PROMPT
from genai.schemas import SignalInput

FAKE_KEY = "test-only-fake-gemini-key-000"

# Captured at import time, before the autouse conftest guard replaces it for each test.
_REAL_BUILD_SDK_CLIENT = GeminiBriefingClient._build_sdk_client


def _signal_input() -> SignalInput:
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


def _fake_sdk(*responses: object) -> MagicMock:
    """Fake google-genai Client whose generate_content returns/raises the given items in order."""
    sdk = MagicMock()
    side_effects = []
    for item in responses:
        if isinstance(item, BaseException):
            side_effects.append(item)
        else:
            side_effects.append(SimpleNamespace(text=item, candidates=[], prompt_feedback=None))
    sdk.models.generate_content.side_effect = side_effects
    return sdk


def _install_sdk(monkeypatch: pytest.MonkeyPatch, sdk: MagicMock) -> None:
    monkeypatch.setattr(GeminiBriefingClient, "_build_sdk_client", lambda self: sdk)


def _api_error(code: int, status: str, message: str) -> genai_errors.APIError:
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, {"error": {"code": code, "message": message, "status": status}})


@pytest.fixture()
def tomato_signal(populated_db: DatabaseManager) -> SignalResult:
    sig = compute_signal_for_market(
        commodity_name="Tomato",
        market_name="Lasalgaon",
        state="Maharashtra",
        db=populated_db,
        as_of_date=date(2025, 6, 30),
        persist=True,
    )
    assert sig is not None
    return sig


def _grounded_json(sig: SignalResult) -> str:
    """A payload guaranteed to pass the validator for this exact SQL signal."""
    return json.dumps(generate_template_fallback(SignalInput(**sig.to_genai_input())).model_dump(), ensure_ascii=False)


# --------------------------------------------------------------------------- JSON parsing


@pytest.mark.parametrize(
    "raw",
    [
        '{"verdict": "WAIT 1 WEEK", "reason_en": "x"}',
        '```json\n{"verdict": "WAIT 1 WEEK", "reason_en": "x"}\n```',
        'Here is the JSON:\n{"verdict": "WAIT 1 WEEK", "reason_en": "x"}\nThanks',
    ],
)
def test_extract_json_payload_accepts_plain_fenced_and_wrapped(raw: str) -> None:
    parsed = GeminiBriefingClient.extract_json_payload(raw)
    assert parsed["verdict"] == "WAIT 1 WEEK"


@pytest.mark.parametrize("raw", ["", "   ", None, '{"verdict": "WAIT', "[1, 2, 3]", "not json at all"])
def test_extract_json_payload_rejects_malformed(raw: str | None) -> None:
    with pytest.raises(GeminiMalformedResponseError):
        GeminiBriefingClient.extract_json_payload(raw)


def test_extract_json_payload_preserves_devanagari() -> None:
    raw = json.dumps({"reason_hi": "टमाटर का भाव 2200 रुपये है।"}, ensure_ascii=False)
    assert GeminiBriefingClient.extract_json_payload(raw)["reason_hi"] == "टमाटर का भाव 2200 रुपये है।"


# --------------------------------------------------------------------------- request shape


def test_generate_briefing_json_uses_structured_output_and_only_sends_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    sig_in = _signal_input()
    sdk = _fake_sdk('{"verdict": "WAIT 1 WEEK", "reason_en": "a", "caution_en": "b", "reason_hi": "c", "caution_hi": "d"}')
    _install_sdk(monkeypatch, sdk)

    client = GeminiBriefingClient(api_key=FAKE_KEY, model="gemini-3.8-flash")
    payload = client.generate_briefing_json(sig_in)
    assert payload["verdict"] == "WAIT 1 WEEK"

    kwargs = sdk.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "gemini-3.8-flash"
    cfg = kwargs["config"]
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema["required"] == list(BRIEFING_KEYS)
    assert cfg.system_instruction == SYSTEM_PROMPT
    assert cfg.temperature is None  # Gemini 3.6+ ignores sampling params; never sent

    prompt = kwargs["contents"]
    assert isinstance(prompt, str)
    assert FAKE_KEY not in prompt
    assert json.dumps(sig_in.model_dump(), ensure_ascii=False, indent=2) in prompt


def test_retry_feedback_is_included_in_second_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = _fake_sdk('{"verdict": "WAIT 1 WEEK"}')
    _install_sdk(monkeypatch, sdk)
    client = GeminiBriefingClient(api_key=FAKE_KEY, model="gemini-3.8-flash")
    client.generate_briefing_json(_signal_input(), retry_feedback=["Ungrounded number 99.9"])
    assert "Ungrounded number 99.9" in sdk.models.generate_content.call_args.kwargs["contents"]


def test_sdk_client_disables_auto_retry_and_sets_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import google.genai as google_genai

    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(google_genai, "Client", _FakeClient)
    client = GeminiBriefingClient(api_key=FAKE_KEY, model="gemini-3.8-flash", timeout=12.0)
    _REAL_BUILD_SDK_CLIENT(client)

    assert captured["api_key"] == FAKE_KEY
    assert captured["vertexai"] is False
    http_options = captured["http_options"]
    assert http_options.timeout == 12000
    assert http_options.retry_options.attempts == 1


# --------------------------------------------------------------------------- error mapping


@pytest.mark.parametrize(
    ("exc", "expected", "match"),
    [
        (_api_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded for free tier"), GeminiQuotaError, "quota"),
        (_api_error(404, "NOT_FOUND", "models/gemini-x is not found"), GeminiModelUnavailableError, "unavailable"),
        (_api_error(400, "INVALID_ARGUMENT", "model does not support this"), GeminiModelUnavailableError, "rejected model"),
        (_api_error(403, "PERMISSION_DENIED", "API key not valid"), GeminiAuthError, "GEMINI_API_KEY"),
        (_api_error(503, "UNAVAILABLE", "The model is overloaded"), GeminiServiceError, "503"),
        (_api_error(504, "DEADLINE_EXCEEDED", "Deadline exceeded"), GeminiTimeoutError, "timed out"),
        (httpx.ReadTimeout("read timed out"), GeminiTimeoutError, "timed out"),
        (httpx.ConnectError("connection refused"), GeminiServiceError, "Network error"),
    ],
)
def test_api_errors_are_translated_clearly(
    monkeypatch: pytest.MonkeyPatch, exc: Exception, expected: type, match: str
) -> None:
    _install_sdk(monkeypatch, _fake_sdk(exc))
    client = GeminiBriefingClient(api_key=FAKE_KEY, model="gemini-3.8-flash")
    with pytest.raises(expected, match=match):
        client.generate_briefing_json(_signal_input())


def test_api_key_is_redacted_from_error_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_sdk(monkeypatch, _fake_sdk(_api_error(400, "INVALID_ARGUMENT", f"Bad key={FAKE_KEY} supplied")))
    client = GeminiBriefingClient(api_key=FAKE_KEY, model="gemini-3.8-flash")
    with pytest.raises(GeminiServiceError) as info:
        client.generate_briefing_json(_signal_input())
    assert FAKE_KEY not in str(info.value)
    assert "***" in str(info.value)


def test_empty_or_blocked_response_is_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = SimpleNamespace(
        text=None,
        prompt_feedback=SimpleNamespace(block_reason="SAFETY"),
        candidates=[SimpleNamespace(finish_reason="SAFETY")],
    )
    sdk = MagicMock()
    sdk.models.generate_content.return_value = blocked
    _install_sdk(monkeypatch, sdk)
    client = GeminiBriefingClient(api_key=FAKE_KEY, model="gemini-3.8-flash")
    with pytest.raises(GeminiMalformedResponseError, match="SAFETY"):
        client.generate_briefing_json(_signal_input())


def test_unconfigured_client_never_builds_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    builder = MagicMock()
    monkeypatch.setattr(GeminiBriefingClient, "_build_sdk_client", builder)
    client = GeminiBriefingClient(api_key="", model="gemini-3.8-flash")
    assert client.is_configured is False
    with pytest.raises(GeminiAuthError):
        client.generate_briefing_json(_signal_input())
    builder.assert_not_called()


# --------------------------------------------------------------------------- orchestration


def test_valid_first_response_is_validated_ai(
    monkeypatch: pytest.MonkeyPatch, populated_db: DatabaseManager, tomato_signal: SignalResult
) -> None:
    sdk = _fake_sdk(_grounded_json(tomato_signal))
    _install_sdk(monkeypatch, sdk)
    client = GeminiBriefingClient(api_key=FAKE_KEY, model="gemini-3.8-flash")

    record = generate_grounded_briefing(tomato_signal, db=populated_db, gemini_client=client, persist=True)
    assert record.validation_status == "validated_ai"
    assert record.used_fallback is False
    assert record.model_used == "gemini-3.8-flash"
    assert record.output.verdict == tomato_signal.verdict_label
    assert sdk.models.generate_content.call_count == 1

    with populated_db.readonly_connection() as conn:
        row = conn.execute(
            text("SELECT validation_status, model_used FROM briefings WHERE briefing_id = :b"),
            {"b": record.briefing_id},
        ).one()
    assert tuple(row) == ("validated_ai", "gemini-3.8-flash")


def test_malformed_json_is_retried_once_with_feedback_then_validated(
    monkeypatch: pytest.MonkeyPatch, populated_db: DatabaseManager, tomato_signal: SignalResult
) -> None:
    sdk = _fake_sdk('{"verdict": "WAIT', _grounded_json(tomato_signal))
    _install_sdk(monkeypatch, sdk)
    client = GeminiBriefingClient(api_key=FAKE_KEY, model="gemini-3.8-flash")

    record = generate_grounded_briefing(tomato_signal, db=populated_db, gemini_client=client, persist=True)
    assert record.validation_status == "validated_ai_retry"
    assert record.used_fallback is False
    assert sdk.models.generate_content.call_count == 2
    second_prompt = sdk.models.generate_content.call_args_list[1].kwargs["contents"]
    assert "Malformed Gemini JSON response" in second_prompt


def test_model_verdict_override_is_never_trusted(
    monkeypatch: pytest.MonkeyPatch, populated_db: DatabaseManager, tomato_signal: SignalResult
) -> None:
    other = "SELL NOW" if tomato_signal.verdict_label != "SELL NOW" else "NEUTRAL"
    drifted = json.loads(_grounded_json(tomato_signal))
    drifted["verdict"] = other
    sdk = _fake_sdk(json.dumps(drifted, ensure_ascii=False), json.dumps(drifted, ensure_ascii=False))
    _install_sdk(monkeypatch, sdk)
    client = GeminiBriefingClient(api_key=FAKE_KEY, model="gemini-3.8-flash")

    record = generate_grounded_briefing(tomato_signal, db=populated_db, gemini_client=client, persist=True)
    assert sdk.models.generate_content.call_count == 2
    assert record.used_fallback is True
    assert record.validation_status == "fallback_template"
    assert record.output.verdict == tomato_signal.verdict_label
    assert any("Verdict mismatch" in e for e in record.validation_errors)


def test_quota_exhaustion_falls_back_without_retry(
    monkeypatch: pytest.MonkeyPatch, populated_db: DatabaseManager, tomato_signal: SignalResult
) -> None:
    sdk = _fake_sdk(_api_error(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota"))
    _install_sdk(monkeypatch, sdk)
    client = GeminiBriefingClient(api_key=FAKE_KEY, model="gemini-3.8-flash")

    record = generate_grounded_briefing(tomato_signal, db=populated_db, gemini_client=client, persist=True)
    assert sdk.models.generate_content.call_count == 1
    assert record.used_fallback is True
    assert record.validation_status == "fallback_template"
    assert record.model_used == "deterministic_template_v1"
    assert any("quota" in e.lower() for e in record.validation_errors)
    assert record.briefing_id is not None


def test_quota_exhaustion_during_retry_still_falls_back(
    monkeypatch: pytest.MonkeyPatch, populated_db: DatabaseManager, tomato_signal: SignalResult
) -> None:
    sdk = _fake_sdk("not json", _api_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded"))
    _install_sdk(monkeypatch, sdk)
    client = GeminiBriefingClient(api_key=FAKE_KEY, model="gemini-3.8-flash")

    record = generate_grounded_briefing(tomato_signal, db=populated_db, gemini_client=client, persist=True)
    assert sdk.models.generate_content.call_count == 2
    assert record.validation_status == "fallback_template"
    assert any("Malformed" in e for e in record.validation_errors)
    assert any("quota" in e.lower() for e in record.validation_errors)


def test_no_key_demo_mode_uses_fallback_without_any_sdk_call(
    monkeypatch: pytest.MonkeyPatch, populated_db: DatabaseManager, tomato_signal: SignalResult
) -> None:
    builder = MagicMock()
    monkeypatch.setattr(GeminiBriefingClient, "_build_sdk_client", builder)
    monkeypatch.setattr(
        "genai.gemini_client.get_settings",
        lambda: SimpleNamespace(gemini_api_key="", gemini_model="gemini-3.8-flash"),
    )

    monkeypatch.setattr("genai.briefing.get_settings", lambda: SimpleNamespace(gemini_enabled=True))
    record = generate_grounded_briefing(tomato_signal, db=populated_db, persist=True)
    builder.assert_not_called()
    assert record.validation_status == "fallback_template"
    assert record.used_fallback is True
    assert any("GEMINI_API_KEY not configured" in e for e in record.validation_errors)
    assert record.output.reason_hi and record.output.caution_hi


def test_invalid_configured_model_does_not_crash_and_falls_back(
    monkeypatch: pytest.MonkeyPatch, populated_db: DatabaseManager, tomato_signal: SignalResult
) -> None:
    monkeypatch.setattr(
        "genai.gemini_client.get_settings",
        lambda: SimpleNamespace(gemini_api_key=FAKE_KEY, gemini_model="gpt-4o"),
    )
    monkeypatch.setattr("genai.briefing.get_settings", lambda: SimpleNamespace(gemini_enabled=True))
    record = generate_grounded_briefing(tomato_signal, db=populated_db, persist=True)
    assert record.validation_status == "fallback_template"
    assert any("Unsupported Gemini model" in e for e in record.validation_errors)
    assert all(FAKE_KEY not in e for e in record.validation_errors)


def test_gemini_disabled_by_default_even_with_key_makes_no_sdk_call(
    monkeypatch: pytest.MonkeyPatch, populated_db: DatabaseManager, tomato_signal: SignalResult
) -> None:
    """GEMINI_ENABLED defaults to false: a configured key alone must never trigger a Gemini call."""
    builder = MagicMock()
    monkeypatch.setattr(GeminiBriefingClient, "_build_sdk_client", builder)
    monkeypatch.setattr(
        "genai.gemini_client.get_settings",
        lambda: SimpleNamespace(gemini_api_key=FAKE_KEY, gemini_model="gemini-3.8-flash"),
    )
    monkeypatch.setattr("genai.briefing.get_settings", lambda: SimpleNamespace(gemini_enabled=False))

    record = generate_grounded_briefing(tomato_signal, db=populated_db, persist=False)
    builder.assert_not_called()
    assert record.briefing_id is None
    assert record.used_fallback is True
    assert record.validation_status == "fallback_template"
    assert any("Gemini disabled by configuration" in e for e in record.validation_errors)
    assert all(FAKE_KEY not in e for e in record.validation_errors)
