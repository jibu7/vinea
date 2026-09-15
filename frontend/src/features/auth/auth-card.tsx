"use client";

import type { ReactNode } from "react";

/**
 * The frame the signed-out screens share — sign in, reset, verify, accept an invitation.
 *
 * These are the only pages a user can reach before there is a session, so they are outside the
 * `(shell)` group and have no nav, no company and no `me`. Five of them would otherwise repeat
 * the same centred card; the markup is lifted here so a change to it is one change.
 *
 * `/login` deliberately still carries its own copy. Folding it in would mean editing the one
 * screen every e2e signs in through, for a tidy-up, in a PR about something else.
 */
export function AuthCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  /** Optional: the "working…" state of a one-shot flow is a heading and nothing else. */
  children?: ReactNode;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--vinea-surface-sunken)] px-4">
      <div className="w-full max-w-sm rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-8 shadow-[var(--elevation-2)]">
        <h1 className="font-display text-2xl font-semibold">{title}</h1>
        {subtitle && (
          <p className="mt-1 text-sm text-[var(--vinea-ink-muted)]">{subtitle}</p>
        )}
        {children}
      </div>
    </div>
  );
}

/** The terminal state of a one-shot flow: a heading, a sentence, and a way onward. Used for
 * both outcomes — "check your email" and "that link has expired" read the same shape. */
export function AuthOutcome({
  title,
  body,
  children,
}: {
  title: string;
  body: string;
  children?: ReactNode;
}) {
  return (
    <AuthCard title={title} subtitle={body}>
      {children && <div className="mt-6 space-y-3">{children}</div>}
    </AuthCard>
  );
}
