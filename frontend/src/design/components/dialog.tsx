"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { useTranslations } from "next-intl";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;

export function DialogContent({
  className,
  children,
  title,
  description,
}: {
  className?: string;
  children: React.ReactNode;
  title: string;
  description?: string;
}) {
  const t = useTranslations("common");
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in" />
      <DialogPrimitive.Content
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-[var(--radius-card)]",
          "border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 shadow-[var(--elevation-3)]",
          // A centred fixed panel with no height limit grows past the top and bottom of the
          // window, and what falls off the bottom is the action row — so a form long enough
          // (the item dialog, once P6 added the purchase account and the weight) cannot be
          // submitted at all, on a short laptop screen or at any height with enough fields.
          // Cap it at the viewport and scroll the overflow instead of hiding it.
          "max-h-[calc(100vh-4rem)] overflow-y-auto",
          className,
        )}
      >
        <div className="mb-4 flex items-start justify-between">
          <div>
            <DialogPrimitive.Title className="font-display text-lg font-semibold">
              {title}
            </DialogPrimitive.Title>
            {description && (
              <DialogPrimitive.Description className="mt-1 text-sm text-[var(--vinea-ink-muted)]">
                {description}
              </DialogPrimitive.Description>
            )}
          </div>
          <DialogPrimitive.Close aria-label={t("close")} className="rounded-[var(--radius-control)] p-1 text-[var(--vinea-ink-subtle)] hover:bg-[var(--vinea-surface-sunken)]">
            <X className="size-4" />
          </DialogPrimitive.Close>
        </div>
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}
