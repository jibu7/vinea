"use client";

import { useTranslations } from "next-intl";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import type { Currency } from "@/features/gl/types";
import { formatDate, formatMoney, formatQuantity } from "@/lib/format";
import type { StatementPreview } from "./types";

/**
 * What a statement file reads as: the counts, the dates it spans, the opening and closing the
 * parser derived, the errors by row, and the rows themselves.
 *
 * **One rendering for both places a file is read before it is written** — *Test with a file* on
 * the Bank accounts format editor (P8 step 6) and **Import**'s preview on Bank statements
 * (step 7). They are the same endpoint over the same file, and two renderings of one preview is
 * how the format editor would come to show a file as clean that the import then refused.
 *
 * `currency` is the account's own: a statement is in the account's currency, and a USD
 * account's `495.00` is `$ 495.00`, not a base-currency figure.
 */
export function StatementPreviewResult({
  result,
  currency,
  testId,
}: {
  result: StatementPreview;
  currency: Currency | undefined;
  testId: string;
}) {
  const t = useTranslations("banking.format");
  const tc = useTranslations("banking.common");
  const currencyLike = currency
    ? { code: currency.code, symbol: currency.symbol, decimalPlaces: currency.decimal_places }
    : null;
  const money = (value: string | null) =>
    value === null || currencyLike === null ? tc("emptyValue") : formatMoney(Number(value), currencyLike);

  return (
    <div className="space-y-3" data-testid={testId}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <StatusChip tone={result.errors.length === 0 ? "success" : "danger"}>
          {result.errors.length === 0
            ? t("readCleanly")
            : t("errorCount", { count: formatQuantity(result.errors.length, 0) })}
        </StatusChip>
        <span className="text-[var(--vinea-ink)]">
          {t("lineCounts", {
            lines: formatQuantity(result.line_count, 0),
            fresh: formatQuantity(result.new_count, 0),
            held: formatQuantity(result.skipped_count, 0),
          })}
        </span>
        {result.lines_skipped_no_amount > 0 ? (
          <span className="text-[var(--vinea-ink-muted)]" data-testid={`${testId}-no-amount`}>
            {t("noAmountSkipped", { count: formatQuantity(result.lines_skipped_no_amount, 0) })}
          </span>
        ) : null}
        {result.from_date && result.to_date ? (
          <span className="text-[var(--vinea-ink-muted)]">
            {t("dateRange", {
              from: formatDate(result.from_date),
              to: formatDate(result.to_date),
            })}
          </span>
        ) : null}
        {result.duplicate_file ? <StatusChip tone="warning">{t("duplicateFile")}</StatusChip> : null}
      </div>
      <dl className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <dt className="text-[var(--vinea-ink-subtle)]">{t("openingBalance")}</dt>
          <dd className="font-mono tabular-nums text-[var(--vinea-ink)]" data-testid={`${testId}-opening`}>
            {money(result.opening_balance)}
          </dd>
        </div>
        <div>
          <dt className="text-[var(--vinea-ink-subtle)]">{t("closingBalance")}</dt>
          <dd className="font-mono tabular-nums text-[var(--vinea-ink)]" data-testid={`${testId}-closing`}>
            {money(result.closing_balance)}
          </dd>
        </div>
      </dl>

      {result.errors.length > 0 ? (
        <Table>
          <THead>
            <TR>
              <TH className="w-16 text-right">{t("row")}</TH>
              <TH className="w-40">{t("column")}</TH>
              <TH>{t("problem")}</TH>
            </TR>
          </THead>
          <TBody>
            {result.errors.map((error, index) => (
              <TR key={`${error.row}-${index}`}>
                <TD className="text-right font-mono text-xs">{error.row}</TD>
                <TD className="font-mono text-xs">{error.column ?? tc("emptyValue")}</TD>
                <TD className="text-xs text-[var(--vinea-danger)]">{error.message}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      ) : null}

      {result.lines.length > 0 ? (
        <Table>
          <THead>
            <TR>
              <TH className="w-12 text-right">{t("row")}</TH>
              <TH className="w-24">{t("valueDate")}</TH>
              <TH>{t("lineDescription")}</TH>
              <TH className="w-28">{t("lineReference")}</TH>
              <TH className="w-32 text-right">{t("amount")}</TH>
              <TH className="w-32 text-right">{t("balanceAfter")}</TH>
            </TR>
          </THead>
          <TBody>
            {result.lines.map((line) => (
              <TR key={line.row}>
                <TD className="text-right font-mono text-xs">{line.row}</TD>
                <TD className="text-xs">{formatDate(line.value_date)}</TD>
                <TD className="text-xs">
                  {line.description}
                  {line.already_held ? (
                    <StatusChip tone="neutral" className="ml-2">
                      {t("alreadyHeld")}
                    </StatusChip>
                  ) : null}
                </TD>
                <TD className="font-mono text-xs">{line.reference ?? tc("emptyValue")}</TD>
                <TD className="text-right font-mono text-xs tabular-nums">{money(line.amount)}</TD>
                <TD className="text-right font-mono text-xs tabular-nums">{money(line.balance_after)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      ) : null}
    </div>
  );
}
