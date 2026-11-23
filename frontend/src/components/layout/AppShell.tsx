"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ArrowRight,
  BarChart3,
  BookOpen,
  Brain,
  Database,
  LayoutDashboard,
  Menu,
  Search,
  Sprout,
  TrendingUp,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    title: "Markets",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard },
      { href: "/market-explorer", label: "Market Explorer", icon: Search },
      { href: "/price-analytics", label: "Price Analytics", icon: BarChart3 },
    ],
  },
  {
    title: "Advisory",
    items: [{ href: "/ai-advisor", label: "AI Crop Advisor", icon: Brain }],
  },
  {
    title: "Methodology",
    items: [
      { href: "/backtesting", label: "Backtesting", icon: TrendingUp },
      { href: "/data-sources", label: "Data Sources", icon: Database },
      { href: "/about", label: "About", icon: BookOpen },
    ],
  },
];

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

function Brand({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <Link href="/" onClick={onNavigate} className="flex items-center gap-3">
      <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-linear-to-br from-[#4fb06d] to-[#1d7a44] text-white shadow-[0_4px_14px_-4px_rgba(79,176,109,0.6)]">
        <Sprout className="h-5 w-5" aria-hidden="true" />
      </div>
      <div>
        <div className="text-[17px] font-semibold tracking-tight text-white">MandiMitra</div>
        <div className="text-[11px] leading-tight text-sidebar-muted">Crop price intelligence</div>
      </div>
    </Link>
  );
}

function SidebarContent({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  const onAdvisor = pathname.startsWith("/ai-advisor");
  return (
    <div className="flex h-full flex-col bg-sidebar px-4 py-6 text-sidebar-foreground">
      <div className="px-2">
        <Brand onNavigate={onNavigate} />
      </div>

      <nav aria-label="Main navigation" className="mt-8 flex-1 space-y-7 overflow-y-auto">
        {NAV_GROUPS.map((group) => (
          <div key={group.title}>
            <p className="px-3 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-sidebar-muted/80">
              {group.title}
            </p>
            <ul className="mt-2 space-y-0.5">
              {group.items.map((item) => {
                const active = isActive(pathname, item.href);
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={onNavigate}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors",
                        active
                          ? "bg-sidebar-active font-medium text-white"
                          : "text-sidebar-foreground/75 hover:bg-white/5 hover:text-white"
                      )}
                    >
                      {active ? (
                        <span
                          className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-[#6fd08c]"
                          aria-hidden="true"
                        />
                      ) : null}
                      <Icon
                        className={cn(
                          "h-[18px] w-[18px] shrink-0 transition-colors",
                          active ? "text-[#8fe0a6]" : "text-sidebar-muted group-hover:text-sidebar-foreground"
                        )}
                      />
                      <span className="truncate">{item.label}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      {!onAdvisor ? (
        <Link
          href="/ai-advisor"
          onClick={onNavigate}
          className="group mt-6 block rounded-2xl border border-sidebar-border bg-linear-to-br from-white/8 to-white/2 p-4 transition-colors hover:from-white/12"
        >
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            <Brain className="h-4 w-4 text-[#8fe0a6]" aria-hidden="true" />
            Sell now or wait?
          </div>
          <p className="mt-1 text-xs leading-relaxed text-sidebar-muted">
            Get a plain-language recommendation for your crop and mandi, in English or Hindi.
          </p>
          <span className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-[#8fe0a6]">
            Ask the advisor
            <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
          </span>
        </Link>
      ) : null}
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = React.useState(false);
  const closeMobile = React.useCallback(() => setMobileOpen(false), []);

  React.useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileOpen]);

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 lg:block">
        <SidebarContent pathname={pathname} />
      </aside>

      {/* Mobile slide-over drawer */}
      {mobileOpen ? (
        <div className="fixed inset-0 z-50 flex lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation menu">
          <div className="fixed inset-0 bg-[#0b1f14]/50 backdrop-blur-[2px]" onClick={closeMobile} />
          <div className="relative z-10 h-full w-72 max-w-[85vw] shadow-2xl">
            <button
              type="button"
              aria-label="Close navigation menu"
              onClick={closeMobile}
              className="absolute right-3 top-6 z-10 rounded-lg p-1.5 text-sidebar-muted hover:bg-white/10 hover:text-white"
            >
              <X className="h-5 w-5" />
            </button>
            <SidebarContent pathname={pathname} onNavigate={closeMobile} />
          </div>
        </div>
      ) : null}

      {/* Main content area */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile top bar */}
        <header className="sticky top-0 z-30 flex items-center justify-between border-b border-border bg-white/90 px-4 py-3 backdrop-blur-md lg:hidden">
          <Link href="/" className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary text-white">
              <Sprout className="h-4 w-4" aria-hidden="true" />
            </span>
            <span className="font-semibold tracking-tight text-foreground">MandiMitra</span>
          </Link>
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation menu"
            className="rounded-xl border border-border bg-white p-2 text-foreground shadow-xs hover:bg-muted"
          >
            <Menu className="h-5 w-5" />
          </button>
        </header>

        <main key={pathname} className="page-enter mx-auto w-full max-w-350 flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-10">
          {children}
        </main>
      </div>
    </div>
  );
}
