"use client";

import { type InputHTMLAttributes, type LabelHTMLAttributes, forwardRef } from "react";
import { cn } from "@/lib/cn";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-10 w-full rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)]",
        "bg-[var(--vinea-surface-raised)] px-3 text-sm text-[var(--vinea-ink)]",
        "placeholder:text-[var(--vinea-ink-subtle)]",
        "focus-visible:border-[var(--vinea-brand)]",
        "[[data-density=dense]_&]:h-8 [[data-density=dense]_&]:px-2",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn("mb-1.5 block text-xs font-medium text-[var(--vinea-ink-muted)]", className)}
      {...props}
    />
  );
}

export function FieldError({ children }: { children?: string }) {
  if (!children) return null;
  return <p className="mt-1 text-xs text-[var(--vinea-danger)]">{children}</p>;
}

export function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <Label>{label}</Label>
      {children}
      <FieldError>{error}</FieldError>
    </div>
  );
}
