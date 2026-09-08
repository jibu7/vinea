"use client";

import {
  type InputHTMLAttributes,
  type LabelHTMLAttributes,
  type ReactNode,
  createContext,
  forwardRef,
  useContext,
  useId,
} from "react";
import { cn } from "@/lib/cn";

/** Set by `Field`, read by its labelable descendants (`Input`, `Combobox`, `DatePicker`) so
 * every field gets an accessible name without each call site wiring aria-labelledby by hand. */
const FieldLabelContext = createContext<string | undefined>(undefined);
export function useFieldLabelId(): string | undefined {
  return useContext(FieldLabelContext);
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => {
    const labelId = useFieldLabelId();
    const hasOwnAccessibleName = props["aria-label"] != null || props["aria-labelledby"] != null;
    return (
      <input
        ref={ref}
        aria-labelledby={hasOwnAccessibleName ? undefined : labelId}
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
    );
  },
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
  className,
  children,
}: {
  label: string;
  error?: string;
  className?: string;
  children: ReactNode;
}) {
  const labelId = useId();
  return (
    <div className={className}>
      <Label id={labelId}>{label}</Label>
      <FieldLabelContext.Provider value={labelId}>{children}</FieldLabelContext.Provider>
      <FieldError>{error}</FieldError>
    </div>
  );
}
