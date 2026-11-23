export type ValidationTone = "success" | "info" | "error";

export interface ValidationStatusMeta {
  tone: ValidationTone;
  label: string;
}

/** Map backend `genai/briefing.py` validation_status values to UI meaning. */
export function describeValidationStatus(status: string): ValidationStatusMeta {
  switch (status) {
    case "validated_ai":
      return { tone: "success", label: "Validated AI briefing — all cited numbers match SQL ground truth" };
    case "validated_ai_retry":
      return {
        tone: "success",
        label: "Validated AI briefing after one retry — all cited numbers match SQL ground truth",
      };
    case "fallback_template":
      return {
        tone: "info",
        label: "Deterministic template fallback — validated against SQL ground truth",
      };
    case "failed":
      return { tone: "error", label: "Validation failed — briefing could not be verified" };
    default:
      return { tone: "error", label: `Unrecognized validation status: ${status}` };
  }
}
