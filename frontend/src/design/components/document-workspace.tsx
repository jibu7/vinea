"use client";

import { useEffect } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowLeft, Command as CommandIcon } from "lucide-react";
import { ThemeToggle } from "./theme-toggle";

/** Ctrl+Enter posts, Esc cancels — wired at the document level while the workspace is mounted. */
export function useDocumentShortcuts({
  onPost,
  onCancel,
  canPost,
}: {
  onPost: () => void;
  onCancel: () => void;
  canPost: boolean;
}) {
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        if (canPost) onPost();
      } else if (e.key === "Escape") {
        if (e.defaultPrevented) return;
        onCancel();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onPost, onCancel, canPost]);
}

export function DocumentWorkspaceShell({
  backHref,
  title,
  subtitle,
  statusChip,
  errorBanner,
  children,
  footer,
}: {
  backHref: string;
  title: string;
  subtitle?: string;
  statusChip?: React.ReactNode;
  errorBanner?: string | null;
  children: React.ReactNode;
  footer: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href={backHref} className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]" aria-label="Back">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{title}</h1>
            {subtitle && <p className="text-xs text-[var(--vinea-ink-muted)]">{subtitle}</p>}
          </div>
          {statusChip}
        </div>
        <div className="flex items-center gap-2">
          <button className="flex items-center gap-2 rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] px-3 py-1.5 text-sm text-[var(--vinea-ink-subtle)]">
            <CommandIcon className="size-3.5" /> Search
            <kbd className="rounded border border-[var(--vinea-border)] px-1 text-[10px]">Ctrl K</kbd>
          </button>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-5xl space-y-4">
          {errorBanner && (
            // `role="alert"` so a screen reader is told without having to find it, and a
            // testid so a test can assert the refusal landed *here* — in the workspace, above
            // the grid — rather than in a toast that scrolls away. Errors inline, not toasts,
            // is a P3 rule; this is the surface that has to be nameable for it to be testable.
            <div
              role="alert"
              data-testid="workspace-error"
              className="flex items-start gap-2 rounded-[var(--radius-control)] border border-[var(--vinea-danger)] bg-[var(--vinea-danger-soft)] px-4 py-3 text-sm text-[var(--vinea-danger)]"
            >
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              {errorBanner}
            </div>
          )}
          {children}
        </div>
      </main>

      <footer className="sticky bottom-0 border-t border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3 shadow-[var(--elevation-2)]">
        <div className="mx-auto max-w-5xl">{footer}</div>
      </footer>
    </div>
  );
}
