"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { StatusChip } from "@/design/components/status-chip";
import { MatchStateChip } from "./match-state";
import type { EntryBankLine } from "./types";

/**
 * One journal line's reconciliation state on the GL entry page (P8 decision 10).
 *
 * Three readings, and the same three the workspace's ledger pane gives, because it is the same
 * query narrowed to the entry: **locked in `BRC-n`** — linked to that reconciliation's report,
 * which is where the proof it was signed off lives; **matched** by a rule, not locked yet; and
 * **outstanding** — in no match, the bank has not shown it — with "dated inside `BRC-n`" where it
 * was posted after a reconciliation covering its date had locked. A line not on a bank account
 * gets nothing: the cell is empty, not "outstanding".
 */
export function EntryBankLineCell({ line }: { line: EntryBankLine | null }) {
  const t = useTranslations("banking.entryLine");
  if (line === null) return null;
  return (
    <div className="flex flex-col items-start gap-1" data-testid="entry-bank-line">
      <span className="font-mono text-[11px] text-[var(--vinea-ink-subtle)]">{line.bank_account_code}</span>
      {line.is_outstanding ? (
        <StatusChip tone="warning" className="whitespace-nowrap">
          {t("outstanding")}
        </StatusChip>
      ) : line.reconciliation_id !== null ? (
        <Link
          href={`/gl/reports/bank-reconciliation?reconciliation=${line.reconciliation_id}`}
          data-testid="entry-bank-brc"
        >
          <MatchStateChip rule={line.match_rule} reconciliationNumber={line.reconciliation_number} />
        </Link>
      ) : (
        <MatchStateChip rule={line.match_rule} reconciliationNumber={null} />
      )}
      {line.dated_inside && (
        <span className="text-[11px] text-[var(--vinea-warning)]">
          {t("datedInside", { number: line.dated_inside })}
        </span>
      )}
    </div>
  );
}
