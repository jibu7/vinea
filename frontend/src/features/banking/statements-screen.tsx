"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { FileSearch, Plus, Trash2, Upload } from "lucide-react";
import { Button } from "@/design/components/button";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { useCompanyDetails } from "@/features/gl/hooks";
import { StatementStatus } from "@/lib/api-enums";
import { newDraftId } from "@/lib/drafts";
import { formatDate, formatQuantity, todayIso } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { BankAccountFilter, useAccountMoney, useBankAccountChoice } from "./account-picker";
import {
  useAutoMatch,
  useImportStatement,
  useKeyManualStatement,
  usePreviewStatement,
  useStatements,
} from "./hooks";
import { StatementPreviewResult } from "./statement-preview-result";
import type { StatementImportResult, StatementPreview } from "./types";

interface ManualLine {
  id: string;
  valueDate: string;
  description: string;
  reference: string;
  moneyIn: string;
  moneyOut: string;
}

function blankLine(valueDate: string): ManualLine {
  return { id: newDraftId(), valueDate, description: "", reference: "", moneyIn: "", moneyOut: "" };
}

/** A keyed line's signed amount, **credit positive** as stored: money in is `+`, money out
 * is `−`. Kept as text — the server parses it as a `Decimal`, and a float in between is the
 * thing ADR-06 forbids. */
function signedAmount(line: ManualLine): string {
  if (line.moneyIn.trim()) return line.moneyIn.trim();
  if (line.moneyOut.trim()) return `-${line.moneyOut.trim()}`;
  return "";
}

/**
 * **Bank statements** — Transactions → General Ledger, after FX revaluation (P8 step 7,
 * Appendix C.1.14).
 *
 * A statement is **the bank's record, imported as it came** (decision 3). This screen lists an
 * account's statements and brings new ones in, two ways: **Import** reads an export under the
 * account's mapping, shows what it read — the rows, the opening and closing it derived, how many
 * lines it already holds, every error by row — and writes nothing until *Import* is pressed on
 * that preview; **Key a paper statement** takes the lines one by one onto the same table and the
 * same path. Either way the result is "N new, M skipped": an overlapping export is the normal
 * case, not a fault. After an import the screen runs the account's **auto-match** (decision 4,
 * "on import, and on demand" — the on-import half), so the result reads "N new, M skipped,
 * K matched" for anyone who may reconcile.
 *
 * Nothing on this screen moves money. A statement line becomes a ledger line only by somebody
 * posting it from the reconciliation workspace, through the kernel.
 */
export function StatementsScreen({ requestedAccountId }: { requestedAccountId: number | null }) {
  const t = useTranslations("banking.statements");
  const tc = useTranslations("banking.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();
  const canImport = hasPermission("bank:statement_import");
  const canReconcile = hasPermission("bank:reconcile");
  const company = useCompanyDetails();

  const { accounts, banks, selected } = useBankAccountChoice(requestedAccountId);
  const { currency, money } = useAccountMoney(selected);
  const [includeVoid, setIncludeVoid] = useState(false);
  const statements = useStatements(selected?.id ?? null, includeVoid);
  const rows = statements.data ?? [];

  // --- Import ---------------------------------------------------------------------------------
  const preview = usePreviewStatement();
  const importStatement = useImportStatement();
  const [importOpen, setImportOpen] = useState(false);
  const [importKey, setImportKey] = useState(() => newDraftId());
  const [file, setFile] = useState<File | null>(null);
  const [previewResult, setPreviewResult] = useState<StatementPreview | null>(null);
  const [keyedOpening, setKeyedOpening] = useState("");
  const [keyedClosing, setKeyedClosing] = useState("");
  const [importError, setImportError] = useState<string | null>(null);
  const [imported, setImported] = useState<StatementImportResult | null>(null);
  /** How many lines the auto-match chained to Import matched, or `null` when it did not run —
   * the importer lacks `bank:reconcile`, or the statement was keyed rather than imported. */
  const [autoMatched, setAutoMatched] = useState<number | null>(null);
  const autoMatch = useAutoMatch();

  function startImport() {
    setFile(null);
    setPreviewResult(null);
    setKeyedOpening("");
    setKeyedClosing("");
    setImportError(null);
    setImportKey(newDraftId());
    setImportOpen(true);
  }

  async function handlePreview() {
    if (!selected || !file) return;
    setImportError(null);
    try {
      setPreviewResult(await preview.mutateAsync({ bankAccountId: selected.id, file }));
    } catch (err) {
      setPreviewResult(null);
      if (isApiError(err)) setImportError(err.message);
      else showApiError(err, t("previewFailed"));
    }
  }

  /** The balances the file does not carry. A format with a balance column derives both and
   * nothing is asked; one without says so and asks for the two figures off the paper. */
  const needsKeyedBalances =
    previewResult !== null &&
    (previewResult.opening_balance === null || previewResult.closing_balance === null);

  /** Why the preview cannot be imported, or `null` when it can — said before the button. */
  function importBlockedReason(): string | null {
    if (!previewResult) return t("previewFirst");
    if (previewResult.errors.length > 0) {
      return t("fixErrorsFirst", { count: formatQuantity(previewResult.errors.length, 0) });
    }
    if (previewResult.duplicate_file) return t("alreadyImported");
    if (needsKeyedBalances && (!keyedOpening.trim() || !keyedClosing.trim())) return t("keyBalances");
    return null;
  }

  async function handleImport() {
    if (!selected || !file || importBlockedReason()) return;
    setImportError(null);
    try {
      const result = await importStatement.mutateAsync({
        bankAccountId: selected.id,
        file,
        openingBalance: needsKeyedBalances ? keyedOpening : "",
        closingBalance: needsKeyedBalances ? keyedClosing : "",
        idempotencyKey: importKey,
      });
      setImported(result);
      setAutoMatched(null);
      setImportOpen(false);
      setImportKey(newDraftId());
      toast.show({ title: t("imported", { number: result.statement.number }), tone: "success" });
      // Decision 4's "auto-match on import", read as **the screen chaining the account's
      // auto-match to the confirm** — the import service writes lines and nothing else, and the
      // tape calls auto-match after each import explicitly. It is the same endpoint the
      // workspace's Auto-match presses, so it needs `bank:reconcile`; without it the import
      // stands and the result says "N new, M skipped" as before. A failure here is not the
      // import's: the statement is in, and the workspace's Auto-match is one press away.
      if (canReconcile) {
        try {
          const matched = await autoMatch.mutateAsync({ bankAccountId: selected.id });
          setAutoMatched(matched.matched.length);
        } catch (err) {
          showApiError(err, t("autoMatchFailed"));
        }
      }
    } catch (err) {
      if (isApiError(err)) setImportError(err.message);
      else showApiError(err, t("importFailed"));
    }
  }

  // --- Key a paper statement -----------------------------------------------------------------
  const keyManual = useKeyManualStatement();
  const [manualOpen, setManualOpen] = useState(false);
  const [manualKey, setManualKey] = useState(() => newDraftId());
  const [manualOpening, setManualOpening] = useState("");
  const [manualClosing, setManualClosing] = useState("");
  const [manualLines, setManualLines] = useState<ManualLine[]>([]);
  const [manualErrors, setManualErrors] = useState<Record<string, string[]>>({});
  const [manualError, setManualError] = useState<string | null>(null);

  function startManual() {
    setManualOpening("");
    setManualClosing("");
    setManualLines([blankLine(todayIso())]);
    setManualErrors({});
    setManualError(null);
    setManualKey(newDraftId());
    setManualOpen(true);
  }

  function setLine(id: string, patch: Partial<ManualLine>) {
    setManualLines((lines) => lines.map((line) => (line.id === id ? { ...line, ...patch } : line)));
  }

  const keyedLines = manualLines.filter((line) => line.description.trim() && signedAmount(line));
  const manualTotal = keyedLines.reduce((sum, line) => sum + Number(signedAmount(line)), 0);
  const manualCanPost =
    keyedLines.length > 0 &&
    manualOpening.trim() !== "" &&
    manualClosing.trim() !== "" &&
    !keyManual.isPending;

  async function handleManual() {
    if (!selected || !manualCanPost) return;
    setManualErrors({});
    setManualError(null);
    try {
      const result = await keyManual.mutateAsync({
        payload: {
          bank_account_id: selected.id,
          opening_balance: manualOpening.trim(),
          closing_balance: manualClosing.trim(),
          lines: keyedLines.map((line) => ({
            value_date: line.valueDate,
            description: line.description.trim(),
            reference: line.reference.trim() || null,
            amount: signedAmount(line),
            balance_after: null,
          })),
        },
        idempotencyKey: manualKey,
      });
      setImported(result);
      setAutoMatched(null);
      setManualOpen(false);
      setManualKey(newDraftId());
      toast.show({ title: t("keyed", { number: result.statement.number }), tone: "success" });
    } catch (err) {
      if (isApiError(err)) {
        setManualErrors(err.fieldErrors);
        setManualError(err.message);
      } else showApiError(err, t("keyFailed"));
    }
  }

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      asOfLabel={selected ? `${selected.code} · ${selected.name}` : undefined}
      filters={
        <div className="flex flex-wrap items-end gap-3">
          <BankAccountFilter banks={banks} selected={selected} basePath="/bank/statements" />
          <label className="flex h-10 items-center gap-1.5 text-xs text-[var(--vinea-ink-muted)]">
            <input
              type="checkbox"
              checked={includeVoid}
              onChange={(e) => setIncludeVoid(e.target.checked)}
              className="size-3.5"
            />
            {t("showVoid")}
          </label>
          {canImport && selected ? (
            <div className="ml-auto flex gap-2">
              <Button variant="secondary" onClick={startManual} className="gap-1.5">
                <Plus className="size-3.5" /> {t("keyPaper")}
              </Button>
              <Button variant="primary" onClick={startImport} className="gap-1.5">
                <Upload className="size-3.5" /> {t("import")}
              </Button>
            </div>
          ) : null}
        </div>
      }
    >
      {imported ? (
        <ReportPanel>
          <div className="flex flex-wrap items-center gap-3 text-sm" data-testid="import-result">
            <StatusChip tone="success">{imported.statement.number}</StatusChip>
            <span className="text-[var(--vinea-ink)]">
              {autoMatched === null
                ? t("resultCounts", {
                    fresh: formatQuantity(imported.new_count, 0),
                    skipped: formatQuantity(imported.skipped_count, 0),
                  })
                : t("resultCountsMatched", {
                    fresh: formatQuantity(imported.new_count, 0),
                    skipped: formatQuantity(imported.skipped_count, 0),
                    matched: formatQuantity(autoMatched, 0),
                  })}
            </span>
            <Link
              href={`/bank/statements/${imported.statement.id}`}
              className="text-xs text-[var(--vinea-brand)] underline"
            >
              {t("openStatement")}
            </Link>
            <Link
              href={`/bank/reconciliations?account=${imported.statement.bank_account_id}`}
              className="text-xs text-[var(--vinea-brand)] underline"
            >
              {t("goReconcile")}
            </Link>
          </div>
        </ReportPanel>
      ) : null}

      <ReportPanel>
        {rows.length === 0 ? (
          <QueryState
            query={selected ? statements : accounts}
            isEmpty
            empty={selected ? t("empty") : t("noBankAccount")}
            testId="statements"
          />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-32">{t("number")}</TH>
                <TH className="w-24">{t("source")}</TH>
                <TH className="w-28">{t("from")}</TH>
                <TH className="w-28">{t("to")}</TH>
                <TH className="w-36 text-right">{t("opening")}</TH>
                <TH className="w-36 text-right">{t("closing")}</TH>
                <TH className="w-20 text-right">{t("lines")}</TH>
                <TH className="w-20 text-right">{t("skipped")}</TH>
                <TH className="w-24">{tc("status")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((row) => (
                <TR key={row.id} data-statement={row.number}>
                  <TD className="font-mono text-xs font-semibold">
                    <Link href={`/bank/statements/${row.id}`} className="text-[var(--vinea-brand)] underline">
                      {row.number}
                    </Link>
                  </TD>
                  <TD className="text-xs">{t(`sourceLabel.${row.source}`)}</TD>
                  <TD className="text-xs">{formatDate(row.from_date)}</TD>
                  <TD className="text-xs">{formatDate(row.to_date)}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">{money(row.opening_balance)}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">{money(row.closing_balance)}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">{formatQuantity(row.line_count, 0)}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {formatQuantity(row.lines_skipped, 0)}
                  </TD>
                  <TD>
                    <StatusChip tone={row.status === StatementStatus.OPEN ? "success" : "neutral"}>
                      {t(`statusLabel.${row.status}`)}
                    </StatusChip>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </ReportPanel>

      <Dialog open={importOpen} onOpenChange={setImportOpen}>
        <DialogContent
          title={t("importTitle", { account: selected ? `${selected.code} · ${selected.name}` : "" })}
          description={t("importDescription")}
          className="max-w-4xl"
        >
          <div className="space-y-4 pt-2">
            <div className="flex items-end gap-3">
              <Field label={t("statementFile")} className="flex-1">
                <Input
                  type="file"
                  accept=".csv,text/csv,text/plain"
                  onChange={(e) => {
                    setFile(e.target.files?.[0] ?? null);
                    setPreviewResult(null);
                    // A different file is a different request: the key the last one was
                    // tried under must not be offered for this one.
                    setImportKey(newDraftId());
                  }}
                />
              </Field>
              <Button
                variant="secondary"
                onClick={handlePreview}
                disabled={!file || preview.isPending}
                className="gap-1.5"
              >
                <FileSearch className="size-3.5" /> {t("preview")}
              </Button>
            </div>

            {previewResult ? (
              <StatementPreviewResult result={previewResult} currency={currency} testId="import-preview" />
            ) : null}

            {needsKeyedBalances ? (
              <div className="space-y-2">
                <p className="text-xs text-[var(--vinea-ink-muted)]">{t("noBalanceColumn")}</p>
                <div className="grid grid-cols-2 gap-3">
                  <Field label={t("keyedOpening")}>
                    <Input
                      value={keyedOpening}
                      inputMode="decimal"
                      onChange={(e) => setKeyedOpening(e.target.value)}
                      className="font-mono"
                    />
                  </Field>
                  <Field label={t("keyedClosing")}>
                    <Input
                      value={keyedClosing}
                      inputMode="decimal"
                      onChange={(e) => setKeyedClosing(e.target.value)}
                      className="font-mono"
                    />
                  </Field>
                </div>
              </div>
            ) : null}

            {importError ? (
              <p
                data-testid="import-error"
                className="rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
              >
                {importError}
              </p>
            ) : null}
          </div>
          <div className="mt-4 flex items-center justify-end gap-3 border-t border-[var(--vinea-border)] pt-3">
            {importBlockedReason() ? (
              <p className="mr-auto text-xs text-[var(--vinea-ink-muted)]" data-testid="import-blocked">
                {importBlockedReason()}
              </p>
            ) : null}
            <Button variant="ghost" onClick={() => setImportOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button
              variant="primary"
              disabled={importBlockedReason() !== null || importStatement.isPending}
              onClick={handleImport}
            >
              {t("confirmImport")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={manualOpen} onOpenChange={setManualOpen}>
        <DialogContent
          title={t("manualTitle", { account: selected ? `${selected.code} · ${selected.name}` : "" })}
          description={t("manualDescription")}
          className="max-w-4xl"
        >
          <div className="space-y-4 pt-2">
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("paperOpening")} error={manualErrors.opening_balance?.[0]}>
                <Input
                  value={manualOpening}
                  inputMode="decimal"
                  onChange={(e) => setManualOpening(e.target.value)}
                  className="font-mono"
                />
              </Field>
              <Field label={t("paperClosing")} error={manualErrors.closing_balance?.[0]}>
                <Input
                  value={manualClosing}
                  inputMode="decimal"
                  onChange={(e) => setManualClosing(e.target.value)}
                  className="font-mono"
                />
              </Field>
            </div>
            <Table>
              <THead>
                <TR>
                  <TH className="w-40">{t("valueDate")}</TH>
                  <TH>{t("lineDescription")}</TH>
                  <TH className="w-28">{t("lineReference")}</TH>
                  <TH className="w-28 text-right">{t("moneyIn")}</TH>
                  <TH className="w-28 text-right">{t("moneyOut")}</TH>
                  <TH className="w-10" />
                </TR>
              </THead>
              <TBody>
                {manualLines.map((line, index) => (
                  <TR key={line.id} data-manual-line={index + 1}>
                    <TD>
                      <IsoDatePicker
                        value={line.valueDate}
                        onValueChange={(value) => setLine(line.id, { valueDate: value })}
                      />
                    </TD>
                    <TD>
                      <Input
                        value={line.description}
                        aria-label={t("lineDescriptionAria", { line: index + 1 })}
                        onChange={(e) => setLine(line.id, { description: e.target.value })}
                      />
                    </TD>
                    <TD>
                      <Input
                        value={line.reference}
                        aria-label={t("lineReferenceAria", { line: index + 1 })}
                        onChange={(e) => setLine(line.id, { reference: e.target.value })}
                      />
                    </TD>
                    <TD>
                      <Input
                        value={line.moneyIn}
                        inputMode="decimal"
                        aria-label={t("moneyInAria", { line: index + 1 })}
                        onChange={(e) => setLine(line.id, { moneyIn: e.target.value, moneyOut: "" })}
                        className="text-right font-mono"
                      />
                    </TD>
                    <TD>
                      <Input
                        value={line.moneyOut}
                        inputMode="decimal"
                        aria-label={t("moneyOutAria", { line: index + 1 })}
                        onChange={(e) => setLine(line.id, { moneyOut: e.target.value, moneyIn: "" })}
                        className="text-right font-mono"
                      />
                    </TD>
                    <TD>
                      <button
                        type="button"
                        aria-label={t("removeLineAria", { line: index + 1 })}
                        disabled={manualLines.length === 1}
                        onClick={() => setManualLines((lines) => lines.filter((row) => row.id !== line.id))}
                        className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)] disabled:opacity-40"
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <div className="flex items-center justify-between">
              <Button
                variant="ghost"
                className="gap-1.5 text-xs"
                onClick={() =>
                  setManualLines((lines) => [
                    ...lines,
                    blankLine(lines[lines.length - 1]?.valueDate ?? todayIso()),
                  ])
                }
              >
                <Plus className="size-3.5" /> {t("addLine")}
              </Button>
              <p className="text-xs text-[var(--vinea-ink-muted)]" data-testid="manual-total">
                {t("manualTotal", {
                  count: formatQuantity(keyedLines.length, 0),
                  total: money(manualTotal),
                })}
              </p>
            </div>
            {manualError ? (
              <p
                data-testid="manual-error"
                className="rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
              >
                {manualError}
              </p>
            ) : null}
          </div>
          <div className="mt-4 flex justify-end gap-2 border-t border-[var(--vinea-border)] pt-3">
            <Button variant="ghost" onClick={() => setManualOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button variant="primary" disabled={!manualCanPost} onClick={handleManual}>
              {t("saveStatement")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </ReportPage>
  );
}
