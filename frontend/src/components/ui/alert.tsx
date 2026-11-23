import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const alertVariants = cva("relative w-full rounded-2xl border px-4 py-3.5 text-sm", {
  variants: {
    variant: {
      default: "border-border bg-card text-foreground",
      demo: "border-[#ecd9a4] bg-tone-gold-soft text-[#5c4304]",
      live: "border-[#c5e0cc] bg-tone-green-soft text-[#123f24]",
      destructive: "border-[#f1c9bb] bg-[#fff5f1] text-[#7c2d12]",
      info: "border-border bg-muted/70 text-foreground",
    },
  },
  defaultVariants: {
    variant: "default",
  },
});

export interface AlertProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof alertVariants> {}

export function Alert({ className, variant, ...props }: AlertProps) {
  return <div role="alert" className={cn(alertVariants({ variant }), className)} {...props} />;
}
