"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { cn } from "@/lib/cn";
import { ThemeToggle } from "./theme-toggle";

/**
 * The chrome every Maintenance screen shares — back link, title, subtitle, header actions,
 * theme toggle, dense density and a centred scroll body. Screens supply only their content;
 * P4 added eight of them at once, so the header stopped being worth copying.
 */
export function MaintenancePage({
  title,
  description,
  backHref = "/",
  actions,
  width = "wide",
  children,
}: {
  title: string;
  description?: string;
  backHref?: string;
  actions?: React.ReactNode;
  /** `wide` suits listings, `narrow` a single settings form. */
  width?: "wide" | "narrow";
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link
            href={backHref}
            className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
            aria-label="Back"
          >
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{title}</h1>
            {description && (
              <p className="text-xs text-[var(--vinea-ink-muted)]">{description}</p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {actions}
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className={cn("mx-auto space-y-6", width === "narrow" ? "max-w-2xl" : "max-w-5xl")}>
          {children}
        </div>
      </main>
    </div>
  );
}

/** A titled card — the panel every maintenance listing and settings block sits in. */
export function MaintenanceCard({
  icon,
  title,
  actions,
  children,
}: {
  icon?: React.ReactNode;
  title: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-4 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {icon && <span className="text-[var(--vinea-brand)]">{icon}</span>}
          <h2 className="font-display text-base font-semibold">{title}</h2>
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}
