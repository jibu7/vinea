"use client";

import { type ButtonHTMLAttributes, forwardRef } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";

/* Disabled state: a blanket `opacity-50` fades foreground *and* background toward the page
   behind them, which axe measured at 2.13:1 on a disabled primary button (white-ish ink over
   half-faded brand green) — a serious WCAG AA failure that P4's permission-gated screens made
   visible, since they render their primary action disabled until `me` resolves. Each variant
   therefore states a solid disabled pair instead: ink-subtle over the sunken surface, both
   already contrast-checked in tokens.css (~5.2-6.0:1 in either theme). */
const DISABLED_FILLED =
  "disabled:bg-[var(--vinea-surface-sunken)] disabled:text-[var(--vinea-ink-subtle)]";

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-control)] font-medium transition-colors disabled:pointer-events-none disabled:cursor-not-allowed",
  {
    variants: {
      variant: {
        primary: `bg-[var(--vinea-brand)] text-[var(--vinea-on-brand)] hover:bg-[var(--vinea-brand-strong)] ${DISABLED_FILLED}`,
        secondary:
          `border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] text-[var(--vinea-ink)] hover:bg-[var(--vinea-surface-sunken)] ${DISABLED_FILLED} disabled:border-[var(--vinea-border)]`,
        ghost:
          "text-[var(--vinea-ink-muted)] hover:bg-[var(--vinea-surface-sunken)] hover:text-[var(--vinea-ink)] disabled:text-[var(--vinea-ink-subtle)]",
        danger: `bg-[var(--vinea-danger)] text-[var(--vinea-on-danger)] hover:opacity-90 ${DISABLED_FILLED}`,
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
