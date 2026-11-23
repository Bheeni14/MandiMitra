"""Grounded prompt templates for English and Hindi mandi advisory briefings."""

from __future__ import annotations

import json

from genai.schemas import SignalInput

SYSTEM_PROMPT = """You are MandiMitra, an agricultural market analyst assistant helping Indian farmers and small traders understand mandi price analytics.

STRICT GROUNDING RULES (MUST FOLLOW):
1. The SQL analytics engine has ALREADY decided the verdict. You MUST copy the exact "verdict" value from the input JSON unchanged. Never change SELL NOW, WAIT 1 WEEK, WAIT 1-2 WEEKS, or NEUTRAL.
2. Use ONLY the numbers present in the supplied JSON metrics. NEVER invent, estimate, extrapolate, or mention any price, percentage, year, or quantity not present in the input JSON.
3. Write clear, simple, practical language understandable by farmers.
4. Keep "reason_en" and "reason_hi" concise (around 45 to 60 words each).
5. Always include an honest "caution_en" and "caution_hi": mention spoilage risk if storable=false, high price volatility if cv_12m >= 0.20, wholesale mandi price vs farm-gate realization, and clearly state this is guidance from past trends—never promise guaranteed profits.
6. If arrivals_trend is "unavailable", state honestly that arrival quantity data is not available for this period; never substitute market count as arrival tonnage.
7. Output STRICT VALID JSON only (no markdown, no backticks, no prose outside JSON) with keys:
   "verdict", "reason_en", "caution_en", "reason_hi", "caution_hi".
"""


def build_user_prompt(signal_input: SignalInput, retry_feedback: list[str] | None = None) -> str:
    payload_str = json.dumps(signal_input.model_dump(), ensure_ascii=False, indent=2)
    prompt = (
        "Generate a grounded bilingual (English and Devanagari Hindi) mandi advisory JSON strictly from these SQL analytics metrics:\n\n"
        f"{payload_str}\n\n"
        "Return ONLY valid JSON with keys: verdict, reason_en, caution_en, reason_hi, caution_hi."
    )
    if retry_feedback:
        errs = "\n".join(f"- {e}" for e in retry_feedback)
        prompt += (
            "\n\nYOUR PREVIOUS OUTPUT FAILED VALIDATION. FIX ALL OF THESE ISSUES:\n"
            f"{errs}\n"
            "Ensure every number appears verbatim in the input metrics, keep exact verdict, and avoid any guarantee wording."
        )
    return prompt
