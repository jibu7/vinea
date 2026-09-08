"use client";

import { type ButtonHTMLAttributes, forwardRef } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-control)] font-medium transition-colors disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        primary: "bg-[var(--vinea-brand)] text-[var(--vinea-on-brand)] hover:bg-[var(--vinea-brand-strong)]",
        secondary:
          "border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] text-[var(--vinea-ink)] hover:bg-[var(--vinea-surface-sunken)]",
        ghost: "text-[var(--vinea-ink-muted)] hover:bg-[var(--vinea-surface-sunken)] hover:text-[var(--vinea-ink)]",
        danger: "bg-[var(--vinea-danger)] text-[var(--vinea-on-danger)] hover:opacity-90",
      },
      size: {
        sm: "h-8 px-3 text-sm",
        md: "h-10 px-4 text-sm",
        lg: "h-11 px-5 text-base",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  ),
);
Button.displayName = "Button";
