import { cn } from "@/lib/cn";
import { formatMoney, type CurrencyLike } from "@/lib/format";

/** Every money value in the app renders through this — never format currency inline. */
export function Money({
  amount,
  currency,
  className,
  showCode = true,
}: {
  amount: number;
  currency: CurrencyLike;
  className?: string;
  showCode?: boolean;
}) {
  const negative = amount < 0;
  return (
    <span
      className={cn(
        "font-mono tabular-nums",
        negative && "text-[var(--vinea-danger)]",
        className,
      )}
    >
      {negative && "("}
      {formatMoney(Math.abs(amount), currency, { showCode })}
      {negative && ")"}
    </span>
  );
}
