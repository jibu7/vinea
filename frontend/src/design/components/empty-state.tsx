import { cn } from "@/lib/cn";

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-[var(--radius-card)]",
        "border border-dashed border-[var(--vinea-border-strong)] px-6 py-12 text-center",
        className,
      )}
    >
      {icon && <div className="text-[var(--vinea-ink-subtle)]">{icon}</div>}
      <div>
        <p className="font-display text-base font-medium">{title}</p>
        {description && <p className="mt-1 text-sm text-[var(--vinea-ink-muted)]">{description}</p>}
      </div>
      {action}
    </div>
  );
}
