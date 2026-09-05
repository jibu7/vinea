import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";

export const chipVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium",
  {
    variants: {
      tone: {
        neutral: "bg-[var(--vinea-surface-sunken)] text-[var(--vinea-ink-muted)]",
        success: "bg-[var(--vinea-success-soft)] text-[var(--vinea-success)]",
        warning: "bg-[var(--vinea-warning-soft)] text-[var(--vinea-warning)]",
        danger: "bg-[var(--vinea-danger-soft)] text-[var(--vinea-danger)]",
        info: "bg-[var(--vinea-info-soft)] text-[var(--vinea-info)]",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface StatusChipProps extends VariantProps<typeof chipVariants> {
  children: React.ReactNode;
  className?: string;
}

export function StatusChip({ tone, className, children }: StatusChipProps) {
  return (
    <span className={cn(chipVariants({ tone }), className)}>
      <span className="size-1.5 rounded-full bg-current" />
      {children}
    </span>
  );
}
