"""Google Gemini API client wrapper (official google-genai SDK) for grounded briefing generation."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from config import get_settings
from genai.prompts import SYSTEM_PROMPT, build_user_prompt
from genai.schemas import SignalInput

logger = logging.getLogger(__name__)

BRIEFING_KEYS: tuple[str, ...] = ("verdict", "reason_en", "caution_en", "reason_hi", "caution_hi")

# Kept deliberately flat: Gemini ignores minLength/maxLength and rejects overly complex schemas,
# so length and grounding rules are enforced client-side by genai.validator.
BRIEFING_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in BRIEFING_KEYS},
    "required": list(BRIEFING_KEYS),
}

MAX_OUTPUT_TOKENS = 8192
_MODEL_NAME_RE = re.compile(r"^gemini-[a-z0-9][a-z0-9.\-]*$")


class GeminiBriefingError(RuntimeError):
    """Base class for Gemini briefing failures; always triggers the deterministic fallback."""


class GeminiQuotaError(GeminiBriefingError):
    """429 RESOURCE_EXHAUSTED: free-tier rate limit or daily quota exhausted."""


class GeminiModelUnavailableError(GeminiBriefingError):
    """Configured model ID is unknown, retired, or not available to this project."""


class GeminiAuthError(GeminiBriefingError):
    """API key rejected or project not permitted (401/403)."""


class GeminiTimeoutError(GeminiBriefingError):
    """Request exceeded the configured timeout."""


class GeminiServiceError(GeminiBriefingError):
    """Any other Gemini API failure (invalid request, 5xx, network)."""


class GeminiMalformedResponseError(GeminiBriefingError):
    """Response was empty, blocked, truncated, or not a JSON object."""


class GeminiBriefingClient:
    """Thin, testable wrapper over Gemini `models.generate_content` with structured JSON output."""

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float = 30.0) -> None:
        settings = get_settings()
        self.api_key = (api_key if api_key is not None else settings.gemini_api_key).strip()
        chosen_model = (model if model is not None else settings.gemini_model).strip()
        self.validate_model_name(chosen_model)
        self.model = chosen_model
        self.timeout = timeout

    @staticmethod
    def validate_model_name(model_name: str) -> None:
        if not model_name or not _MODEL_NAME_RE.match(model_name):
            raise ValueError(
                f"Unsupported Gemini model '{model_name}'. Configure a valid Gemini model ID "
                "(starting with 'gemini-', e.g. 'gemini-3.8-flash') via GEMINI_MODEL."
            )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _redact(self, message: str) -> str:
        if self.api_key and self.api_key in message:
            message = message.replace(self.api_key, "***")
        return message

    @staticmethod
    def extract_json_payload(raw_text: str | None) -> dict[str, Any]:
        cleaned = (raw_text or "").strip()
        if not cleaned:
            raise GeminiMalformedResponseError("Empty response text received from Gemini API.")
        fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL)
        if fence_match:
            cleaned = fence_match.group(1)
        elif not cleaned.startswith("{"):
            brace_match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if brace_match:
                cleaned = brace_match.group(0)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise GeminiMalformedResponseError(f"Gemini response is not valid JSON: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise GeminiMalformedResponseError("Gemini response JSON is not an object.")
        return parsed

    def _build_sdk_client(self) -> Any:
        from google import genai as google_genai
        from google.genai import types

        # SDK auto-retry (default 5 attempts, up to 60s backoff) is disabled: the briefing
        # pipeline owns the single feedback retry and falls back immediately on API failure.
        http_options = types.HttpOptions(
            timeout=int(self.timeout * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        )
        return google_genai.Client(api_key=self.api_key, vertexai=False, http_options=http_options)

    def _build_config(self) -> Any:
        from google.genai import types

        return types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_json_schema=BRIEFING_RESPONSE_SCHEMA,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )

    def _translate_api_error(self, exc: Exception) -> GeminiBriefingError:
        code = getattr(exc, "code", None)
        status = str(getattr(exc, "status", "") or "").upper()
        detail = self._redact(str(getattr(exc, "message", None) or exc))
        label = f"{code} {status}".strip()

        if code == 429 or status == "RESOURCE_EXHAUSTED":
            return GeminiQuotaError(
                f"Gemini quota or rate limit exhausted for model '{self.model}' ({label}): {detail}"
            )
        if code == 404 or status == "NOT_FOUND":
            return GeminiModelUnavailableError(
                f"Gemini model '{self.model}' is unsupported or unavailable ({label}). "
                f"Check GEMINI_MODEL against the official models list: {detail}"
            )
        if code in (401, 403) or status in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
            return GeminiAuthError(
                f"Gemini API rejected the configured GEMINI_API_KEY or project access ({label}): {detail}"
            )
        if code == 400 and "model" in detail.lower():
            return GeminiModelUnavailableError(
                f"Gemini rejected model '{self.model}' or its parameters ({label}): {detail}"
            )
        if code in (408, 504) or status == "DEADLINE_EXCEEDED":
            return GeminiTimeoutError(f"Gemini request timed out for model '{self.model}' ({label}): {detail}")
        return GeminiServiceError(f"Gemini API error using model '{self.model}' ({label}): {detail}")

    @staticmethod
    def _describe_empty_response(response: Any) -> str:
        reasons: list[str] = []
        feedback = getattr(response, "prompt_feedback", None)
        block_reason = getattr(feedback, "block_reason", None) if feedback is not None else None
        if block_reason:
            reasons.append(f"prompt blocked ({block_reason})")
        for cand in getattr(response, "candidates", None) or []:
            finish = getattr(cand, "finish_reason", None)
            if finish:
                reasons.append(f"finish_reason={finish}")
        return "; ".join(reasons) or "no text parts returned"

    def generate_briefing_json(
        self,
        signal_input: SignalInput,
        retry_feedback: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured:
            raise GeminiAuthError("GEMINI_API_KEY is not configured.")

        import httpx
        from google.genai import errors as genai_errors

        self.validate_model_name(self.model)
        sdk_client = self._build_sdk_client()
        try:
            response = sdk_client.models.generate_content(
                model=self.model,
                contents=build_user_prompt(signal_input, retry_feedback=retry_feedback),
                config=self._build_config(),
            )
        except genai_errors.APIError as exc:
            raise self._translate_api_error(exc) from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise GeminiTimeoutError(
                f"Gemini request timed out after {self.timeout:.0f}s for model '{self.model}'."
            ) from exc
        except httpx.HTTPError as exc:
            raise GeminiServiceError(
                f"Network error contacting Gemini API: {self._redact(type(exc).__name__)}"
            ) from exc

        try:
            raw_text = response.text
        except (AttributeError, ValueError):
            raw_text = None
        if not raw_text or not raw_text.strip():
            raise GeminiMalformedResponseError(
                f"Empty Gemini response ({self._describe_empty_response(response)})."
            )
        return self.extract_json_payload(raw_text)
