import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset transition-colors",
  {
    variants: {
      variant: {
        default: "bg-tone-green-soft text-tone-green ring-[#c5e0cc]",
        gold: "bg-tone-gold-soft text-tone-gold ring-[#ecd9a4]",
        terracotta: "bg-tone-terracotta-soft text-tone-terracotta ring-[#f1c9bb]",
        neutral: "bg-tone-slate-soft text-tone-slate ring-[#dde3ea]",
        outline: "bg-white text-foreground ring-border",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
);

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
