"""Strict validator for GenAI briefings: schema, verdict consistency, number grounding, and safety checks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from genai.schemas import BriefingOutput, SignalInput

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

_GUARANTEE_PATTERNS = (
    r"\bguarantee[ds]?\b",
    r"\b100%\s*(?:sure|certain|profit)\b",
    r"\brisk[-\s]?free\b",
    r"\bdefinitely\s+(?:rise|increase|profit)\b",
    r"\bassured\s+profit\b",
    r"गारंटी",
    r"पक्का\s*मुनाफा",
    r"निश्चित\s*लाभ",
)

# Allowed time-horizon numbers ONLY when tied directly to the unit word
_ALLOWED_WINDOW_NUM_UNITS: list[tuple[str, tuple[str, ...]]] = [
    ("1", ("week", "weeks", "सप्ताह", "हफ्ते", "हफ़्ते")),
    ("2", ("week", "weeks", "सप्ताह", "हफ्ते", "हफ़्ते")),
    ("4", ("week", "weeks", "सप्ताह", "हफ्ते", "हफ़्ते")),
    ("7", ("day", "days", "दिन", "दिनों")),
    ("14", ("day", "days", "दिन", "दिनों")),
    ("28", ("day", "days", "दिन", "दिनों")),
    ("90", ("day", "days", "दिन", "दिनों")),
    ("3", ("month", "months", "year", "years", "महीने", "माह", "साल", "वर्ष", "वर्षों")),
    ("12", ("month", "months", "महीने", "माह")),
]


@dataclass
class BriefingValidationResult:
    is_valid: bool
    output: BriefingOutput | None = None
    errors: list[str] = field(default_factory=list)
    extracted_numbers: list[float] = field(default_factory=list)


def _normalize_digits(text: str) -> str:
    return text.translate(_DEVANAGARI_DIGITS)


def _allowed_metric_numbers(signal_input: SignalInput) -> set[float]:
    allowed: set[float] = set()
    numeric_fields = [
        signal_input.current_modal_price,
        signal_input.price_change_4w_pct,
        signal_input.vs_seasonal_avg_pct,
        signal_input.arrivals_change_14d_pct,
        signal_input.cv_12m,
        signal_input.state_price_gap_pct,
        signal_input.observation_count_90d,
    ]
    for val in numeric_fields:
        if val is None:
            continue
        f = float(val)
        for cand in (f, abs(f)):
            allowed.add(round(cand, 4))
            allowed.add(round(cand, 2))
            allowed.add(round(cand, 1))
            allowed.add(float(round(cand)))

    # If CV is expressed as percentage (e.g. 0.21 -> 21 or 21.0%)
    if signal_input.cv_12m is not None:
        cv_pct = float(signal_input.cv_12m) * 100.0
        allowed.add(round(cv_pct, 2))
        allowed.add(round(cv_pct, 1))
        allowed.add(float(round(cv_pct)))

    return allowed


def _strip_allowed_window_phrases(text: str, signal_input: SignalInput | None = None) -> str:
    cleaned = _normalize_digits(text)
    # Strip explicit ISO / DD-MM-YYYY / DD/MM/YYYY date strings so full dates pass without authorizing standalone numbers
    if signal_input is not None:
        for dt_str in (signal_input.analysis_window_start, signal_input.analysis_window_end):
            if dt_str:
                cleaned = cleaned.replace(dt_str, " ")
    cleaned = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", cleaned)
    cleaned = re.sub(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b", " ", cleaned)

    # Remove explicit window expressions such as "1-2 weeks", "4 weeks", "28 days", "3 years", "12 months"
    # Exclude Devanagari danda punctuation (\u0964, \u0965) from word-continuation lookahead
    cleaned = re.sub(
        r"(?<!\d)1\s*[-–to]+\s*2\s*(?:week|weeks|सप्ताह|हफ्ते|हफ़्ते)(?![A-Za-z0-9_\u0900-\u0963\u0966-\u097F])",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    for num, units in _ALLOWED_WINDOW_NUM_UNITS:
        unit_pat = "|".join(re.escape(u) for u in units)
        cleaned = re.sub(
            rf"(?<!\d){re.escape(num)}(?:[-\s]+)(?:{unit_pat})(?![A-Za-z0-9_\u0900-\u0963\u0966-\u097F])",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
    return cleaned


def _extract_numbers(text: str, signal_input: SignalInput | None = None) -> list[float]:
    stripped = _strip_allowed_window_phrases(text, signal_input=signal_input)
    # Remove thousands separators inside numbers: e.g. 2,450.50 -> 2450.50
    normalized = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", stripped)
    matches = re.findall(r"[-+]?\d+(?:\.\d+)?", normalized)
    nums: list[float] = []
    for m in matches:
        try:
            nums.append(float(m))
        except ValueError:
            continue
    return nums


def _number_is_grounded(num: float, allowed: set[float], tol: float = 0.06) -> bool:
    for cand in (num, abs(num)):
        if any(abs(cand - a) <= tol for a in allowed):
            return True
    return False


def validate_briefing(raw_payload: dict[str, Any], signal_input: SignalInput) -> BriefingValidationResult:
    """Validate GenAI briefing output against schema, SQL verdict, number grounding, Hindi script, and safety rules."""
    errors: list[str] = []

    try:
        output = BriefingOutput(**raw_payload)
    except ValidationError as exc:
        return BriefingValidationResult(
            is_valid=False,
            output=None,
            errors=[f"Schema validation error: {err['loc']} - {err['msg']}" for err in exc.errors()],
        )

    # 1. Verdict consistency (SQL decides, GenAI never changes verdict)
    expected_verdict = signal_input.verdict.strip().upper()
    actual_verdict = output.verdict.strip().upper()
    if actual_verdict != expected_verdict:
        errors.append(f"Verdict mismatch: expected '{signal_input.verdict}', got '{output.verdict}'.")

    # 2. Devanagari Hindi script validation
    if not re.search(r"[\u0900-\u097F]", output.reason_hi):
        errors.append("reason_hi must contain valid Hindi (Devanagari) text.")
    if not re.search(r"[\u0900-\u097F]", output.caution_hi):
        errors.append("caution_hi must contain valid Hindi (Devanagari) text.")

    # 3. Guarantee / unsupported promise check
    combined = " ".join([output.reason_en, output.caution_en, output.reason_hi, output.caution_hi])
    for pat in _GUARANTEE_PATTERNS:
        if re.search(pat, combined, flags=re.IGNORECASE):
            errors.append(f"Unsupported profit guarantee language matched pattern: {pat}")

    # 4. Number grounding check across all output fields
    allowed = _allowed_metric_numbers(signal_input)
    all_extracted: list[float] = []
    for field_name, field_text in (
        ("reason_en", output.reason_en),
        ("caution_en", output.caution_en),
        ("reason_hi", output.reason_hi),
        ("caution_hi", output.caution_hi),
    ):
        extracted = _extract_numbers(field_text, signal_input=signal_input)
        all_extracted.extend(extracted)
        for num in extracted:
            if not _number_is_grounded(num, allowed):
                errors.append(
                    f"Ungrounded number '{num}' in {field_name}; not found in supplied SQL metrics."
                )

    # 5. Conciseness guard (~60 words target; warn/fail only if excessively bloated)
    en_words = len(output.reason_en.split())
    if en_words > 110:
        errors.append(f"reason_en too verbose ({en_words} words; target ~60 words, max 110).")

    return BriefingValidationResult(
        is_valid=len(errors) == 0,
        output=output,
        errors=errors,
        extracted_numbers=all_extracted,
    )
