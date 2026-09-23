"use client";

import { useTranslations } from "next-intl";
import { StatusChip } from "@/design/components/status-chip";
import type { BankMatchRule } from "@/lib/api-enums";
import { formatQuantity } from "@/lib/format";

/**
 * A line's match, said the same way on the statement detail and both workspace panes.
 *
 * Three states, and they call for different things: **unmatched** is work to do; **matched**
 * names the rule that made it (a `reference` hit is trusted differently from an
 * `amount_date` one) and, on a statement line, how many ledger lines it holds — three on a
 * payment run's single debit; **locked in `BRC-n`** is history, and withdrawing it means
 * reopening that reconciliation first.
 */
export function MatchStateChip({
  rule,
  reconciliationNumber,
  ledgerLineCount,
}: {
  rule: BankMatchRule | null;
  reconciliationNumber: string | null;
  /** Statement side only: how many ledger lines the match holds. */
  ledgerLineCount?: number;
}) {
  const t = useTranslations("banking.match");
  if (rule === null) {
    return <StatusChip tone="warning">{t("unmatched")}</StatusChip>;
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <StatusChip tone={reconciliationNumber ? "neutral" : "success"}>
        {reconciliationNumber
          ? t("lockedIn", { number: reconciliationNumber })
          : t("matchedBy", { rule: t(`ruleLabel.${rule}`) })}
      </StatusChip>
      {ledgerLineCount !== undefined && ledgerLineCount > 0 ? (
        <span className="text-[11px] text-[var(--vinea-ink-subtle)]">
          {t("ledgerLines", { count: formatQuantity(ledgerLineCount, 0) })}
        </span>
      ) : null}
    </span>
  );
}
