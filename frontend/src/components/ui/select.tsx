import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

/** Shared look for form labels and native inputs so every filter bar reads as one system. */
export const fieldLabelClass = "text-xs font-medium text-muted-foreground";
export const fieldControlClass =
  "h-10 w-full rounded-xl border border-input bg-white px-3 text-sm text-foreground shadow-xs transition-colors hover:border-[#c3cec5] focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-50";

export interface SelectFieldProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label: string;
  options: Array<{ value: string; label: string }>;
  containerClassName?: string;
}

export function SelectField({
  id,
  label,
  options,
  containerClassName,
  className,
  ...props
}: SelectFieldProps) {
  const generatedId = React.useId();
  const selectId = id ?? generatedId;

  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", containerClassName)}>
      <label htmlFor={selectId} className={fieldLabelClass}>
        {label}
      </label>
      <div className="relative">
        <select
          id={selectId}
          className={cn(fieldControlClass, "cursor-pointer appearance-none truncate pr-9", className)}
          {...props}
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <ChevronDown
          className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
      </div>
    </div>
  );
}
