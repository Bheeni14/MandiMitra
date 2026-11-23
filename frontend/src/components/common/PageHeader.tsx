import * as React from "react";

/**
 * Consistent page title block: small eyebrow, title, one-line description, and right-aligned actions.
 * Deliberately has no "use client" directive so server pages (e.g. About) can pass an icon component to it.
 */
export function PageHeader({
  eyebrow,
  title,
  description,
  icon: Icon,
  badges,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: React.ReactNode;
  icon?: React.ComponentType<{ className?: string }>;
  badges?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="flex min-w-0 items-start gap-3.5">
        {Icon ? (
          <div className="hidden h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-sm sm:flex">
            <Icon className="h-5 w-5" aria-hidden="true" />
          </div>
        ) : null}
        <div className="min-w-0">
          {eyebrow ? (
            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-primary/80">{eyebrow}</p>
          ) : null}
          <div className="mt-0.5 flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight text-foreground sm:text-[28px]">{title}</h1>
            {badges}
          </div>
          {description ? (
            <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted-foreground">{description}</p>
          ) : null}
        </div>
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-end gap-2">{actions}</div> : null}
    </header>
  );
}
