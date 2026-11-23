"""Deterministic, strictly grounded bilingual template fallback briefing generator."""

from __future__ import annotations

from genai.schemas import BriefingOutput, SignalInput


def generate_template_fallback(signal_input: SignalInput) -> BriefingOutput:
    """Produce a farmer-friendly EN/HI briefing strictly from supplied metrics when Gemini fails or is unconfigured."""
    s = signal_input

    if s.insufficient_evidence:
        reason_en = (
            f"For {s.commodity} in {s.market} ({s.state}), available trading observations over the last 90 days "
            f"({s.observation_count_90d} records) are too limited for a strong recommendation. "
            f"Signal stays {s.verdict}."
        )
        caution_en = (
            "Limited reporting prevents reliable trend judgment. Verify local mandi rates in person; "
            "this is data guidance, not a price guarantee."
        )
        reason_hi = (
            f"{s.market} ({s.state}) में {s.commodity} के पिछले 90 दिनों में केवल {s.observation_count_90d} "
            f"व्यापार रिकॉर्ड उपलब्ध हैं, जो स्पष्ट निर्णय के लिए पर्याप्त नहीं हैं। इसलिए संकेत {s.verdict} है।"
        )
        caution_hi = (
            "पर्याप्त डेटा न होने से ट्रेंड स्पष्ट नहीं है। मंडी में ताजा भाव स्वयं जांचें; "
            "यह केवल पिछले आंकड़ों पर आधारित मार्गदर्शन है, मुनाफे की गारंटी नहीं।"
        )
        return BriefingOutput(
            verdict=s.verdict,
            reason_en=reason_en,
            caution_en=caution_en,
            reason_hi=reason_hi,
            caution_hi=caution_hi,
        )

    price_str = f"Rs {s.current_modal_price:.2f}" if s.current_modal_price is not None else "latest available rate"
    mom_dir_en = "rose" if (s.price_change_4w_pct or 0) >= 0 else "fell"
    mom_abs = abs(s.price_change_4w_pct) if s.price_change_4w_pct is not None else 0.0

    seas_part_en = ""
    seas_part_hi = ""
    if s.vs_seasonal_avg_pct is not None:
        seas_pos_en = "above" if s.vs_seasonal_avg_pct >= 0 else "below"
        seas_pos_hi = "ऊपर" if s.vs_seasonal_avg_pct >= 0 else "नीचे"
        seas_abs = abs(s.vs_seasonal_avg_pct)
        seas_part_en = f" (sitting {seas_abs:.2f}% {seas_pos_en} the seasonal average)"
        seas_part_hi = f" और मौसमी औसत से {seas_abs:.2f}% {seas_pos_hi} है"

    if s.arrivals_trend == "unavailable":
        arr_part_en = " Arrival quantity data is unavailable for this window."
        arr_part_hi = " इस अवधि के लिए आवक मात्रा डेटा उपलब्ध नहीं है।"
    elif s.arrivals_change_14d_pct is not None:
        arr_abs = abs(s.arrivals_change_14d_pct)
        arr_hi_map = {"rising": "बढ़ रही है", "falling": "घट रही है", "stable": "स्थिर है"}
        arr_part_en = f" Mandi arrivals are {s.arrivals_trend} ({arr_abs:.2f}% over 14 days)."
        arr_part_hi = f" पिछले 14 दिनों में आवक {arr_hi_map.get(s.arrivals_trend, s.arrivals_trend)} ({arr_abs:.2f}%)।"
    else:
        arr_part_en = f" Mandi arrivals are {s.arrivals_trend}."
        arr_part_hi = " मंडी आवक सामान्य बनी हुई है।"

    reason_en = (
        f"{s.commodity} in {s.market} ({s.state}) averages {price_str} per quintal. "
        f"Prices {mom_dir_en} {mom_abs:.2f}% over 4 weeks{seas_part_en}.{arr_part_en} "
        f"Rule verdict: {s.verdict}."
    )

    mom_dir_hi = "बढ़े हैं" if (s.price_change_4w_pct or 0) >= 0 else "गिरे हैं"
    reason_hi = (
        f"{s.market} ({s.state}) में {s.commodity} का औसत भाव {price_str} प्रति क्विंटल है। "
        f"पिछले 4 सप्ताह में भाव {mom_abs:.2f}% {mom_dir_hi}{seas_part_hi}।{arr_part_hi} "
        f"सुझाव: {s.verdict}।"
    )

    caution_bits_en: list[str] = []
    caution_bits_hi: list[str] = []
    if not s.storable:
        caution_bits_en.append("Perishable crop: hold only if safe storage exists to avoid spoilage.")
        caution_bits_hi.append("जल्दी खराब होने वाली फसल है; सुरक्षित भंडारण होने पर ही रुकें।")
    if s.cv_12m is not None and s.cv_12m >= 0.20:
        caution_bits_en.append(f"High price volatility (CV {s.cv_12m:.2f} over 12 months).")
        caution_bits_hi.append(f"भाव में उतार-चढ़ाव अधिक है (12 महीने का CV {s.cv_12m:.2f})।")
    caution_bits_en.append("Mandi wholesale prices differ from farm-gate net realization; guidance only, not a profit promise.")
    caution_bits_hi.append("ये थोक मंडी भाव हैं; यह केवल मार्गदर्शन है, मुनाफे का कोई वादा नहीं।")

    return BriefingOutput(
        verdict=s.verdict,
        reason_en=reason_en,
        caution_en=" ".join(caution_bits_en),
        reason_hi=reason_hi,
        caution_hi=" ".join(caution_bits_hi),
    )
