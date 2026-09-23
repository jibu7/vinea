"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { FileSearch } from "lucide-react";
import { Button } from "@/design/components/button";
import { Field, Input } from "@/design/components/input";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError } from "@/features/auth/hooks";
import type { Currency } from "@/features/gl/types";
import {
  StatementAmountMode,
  StatementFormatPreset,
  StatementSignConvention,
} from "@/lib/api-enums";
import { formatDate, formatMoney, formatQuantity } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { usePreviewStatement, useUpdateBankAccount } from "./hooks";
import type { BankAccount, StatementFormat, StatementPreview } from "./types";

/**
 * Where a **custom** mapping starts: the server's `GENERIC_PRESET` (`app/banking/formats.py`),
 * spelled out so the form has something to show. It is a starting point and nothing more —
 * the editor always sends the whole mapping, and a generic account is saved as
 * `{ preset: "generic" }` and read with the server's own defaults, so a drift here changes
 * what a user starts typing over and never what a file is parsed as.
 */
const GENERIC: Required<StatementFormat> = {
  preset: StatementFormatPreset.GENERIC,
  delimiter: ",",
  encoding: "utf-8-sig",
  header_rows: 1,
  date_column: "Date",
  date_format: "%Y-%m-%d",
  booking_date_column: null,
  description_column: "Description",
  reference_column: "Reference",
  amount_mode: StatementAmountMode.DEBIT_CREDIT,
  debit_column: "Debit",
  credit_column: "Credit",
  amount_column: null,
  sign_convention: StatementSignConvention.CREDIT_POSITIVE,
  balance_column: "Balance",
  external_id_column: null,
  decimal_separator: ".",
  thousands_separator: ",",
};

/** Every text field the form holds, as text: `null` on the wire is `""` on the screen. */
type FormState = Record<keyof StatementFormat, string>;

function toForm(format: StatementFormat | null): FormState {
  const merged = { ...GENERIC, ...(format ?? {}) };
  return Object.fromEntries(
    Object.entries(merged).map(([key, value]) => [key, value === null ? "" : String(value)]),
  ) as FormState;
}

/** The mapping as the server reads it. Generic is sent as the preset alone, so the server's
 * defaults — not this file's copy of them — are what a generic account is parsed with. */
function toFormat(form: FormState): StatementFormat {
  if (form.preset === StatementFormatPreset.GENERIC) return { preset: StatementFormatPreset.GENERIC };
  const optional = (value: string) => (value.trim() === "" ? null : value.trim());
  return {
    preset: StatementFormatPreset.CUSTOM,
    delimiter: form.delimiter,
    encoding: form.encoding,
    header_rows: Number(form.header_rows || 0),
    date_column: form.date_column.trim(),
    date_format: form.date_format,
    booking_date_column: optional(form.booking_date_column),
    description_column: form.description_column.trim(),
    reference_column: optional(form.reference_column),
    amount_mode: form.amount_mode as StatementAmountMode,
    debit_column: optional(form.debit_column),
    credit_column: optional(form.credit_column),
    amount_column: optional(form.amount_column),
    sign_convention: form.sign_convention as StatementSignConvention,
    balance_column: optional(form.balance_column),
    external_id_column: optional(form.external_id_column),
    decimal_separator: form.decimal_separator,
    thousands_separator: form.thousands_separator === "" ? null : form.thousands_separator,
  };
}

/** `strptime` patterns a Rwandan bank export is actually written in. Not inferred from the file:
 * `03/04/2026` is 3 April or 4 March, and guessing moves six months of reconciliation. */
const DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%Y/%m/%d"];

/**
 * The **statement format** tab on the Bank accounts screen: the preset, the column mapping, and
 * **Test with a file**, which runs the preview under the mapping on the screen — not the one
 * stored — and shows the rows it read and every error with its row number.
 *
 * That is the point of the button. A mapping is only ever wrong against a file, and the file
 * that would find out is the next import; `statement_parse_error` refuses the whole import on
 * one bad row, which is right for an import and a poor way to learn a date format. Testing
 * writes nothing — not a statement, not a line, not the mapping — so it can be pressed as
 * often as it takes.
 */
export function StatementFormatEditor({
  account,
  currency,
  canManage,
  canPreview,
}: {
  account: BankAccount;
  currency: Currency | undefined;
  canManage: boolean;
  /** `bank:statement_import` — the permission the preview endpoint sits behind. */
  canPreview: boolean;
}) {
  const t = useTranslations("banking.format");
  const tc = useTranslations("banking.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const update = useUpdateBankAccount();
  const preview = usePreviewStatement();

  const [form, setForm] = useState<FormState>(() => toForm(account.statement_format));
  const [formatError, setFormatError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<StatementPreview | null>(null);

  useEffect(() => {
    setForm(toForm(account.statement_format));
  }, [account.statement_format]);

  const custom = form.preset === StatementFormatPreset.CUSTOM;
  const signed = form.amount_mode === StatementAmountMode.SIGNED;
  const set = (key: keyof StatementFormat) => (value: string) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const currencyLike = currency
    ? { code: currency.code, symbol: currency.symbol, decimalPlaces: currency.decimal_places }
    : null;
  const money = (value: string | null) =>
    value === null || currencyLike === null ? tc("emptyValue") : formatMoney(Number(value), currencyLike);

  const dateOptions = useMemo(() => {
    const values = DATE_FORMATS.includes(form.date_format)
      ? DATE_FORMATS
      : [form.date_format, ...DATE_FORMATS];
    return values.map((value) => ({ value, label: value }));
  }, [form.date_format]);

  function choosePreset(value: string) {
    // Choosing custom starts from what is on the screen — the generic columns, or the stored
    // custom mapping — so a user edits the one field their bank does differently.
    setForm((prev) => ({ ...prev, preset: value }));
  }

  async function handleSave() {
    setFormatError(null);
    try {
      await update.mutateAsync({ id: account.id, payload: { statement_format: toFormat(form) } });
      toast.show({ title: t("saved"), tone: "success" });
    } catch (err) {
      if (isApiError(err) && err.code === "statement_format_invalid") setFormatError(err.message);
      showApiError(err, t("saveFailed"));
    }
  }

  async function handleTest() {
    if (!file) return;
    setFormatError(null);
    try {
      setResult(
        await preview.mutateAsync({ bankAccountId: account.id, file, format: toFormat(form) }),
      );
    } catch (err) {
      setResult(null);
      if (isApiError(err) && err.code === "statement_format_invalid") setFormatError(err.message);
      showApiError(err, t("testFailed"));
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Field label={t("preset")}>
          <Select
            options={[
              { value: StatementFormatPreset.GENERIC, label: t("presetLabel.generic") },
              { value: StatementFormatPreset.CUSTOM, label: t("presetLabel.custom") },
            ]}
            value={form.preset}
            onValueChange={choosePreset}
          />
        </Field>
        <Field label={t("amountMode")}>
          <Select
            options={[
              { value: StatementAmountMode.DEBIT_CREDIT, label: t("amountModeLabel.debit_credit") },
              { value: StatementAmountMode.SIGNED, label: t("amountModeLabel.signed") },
            ]}
            value={form.amount_mode}
            onValueChange={set("amount_mode")}
          />
        </Field>
      </div>

      {!custom ? (
        <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("genericNote")}</p>
      ) : (
        <div className="space-y-3" data-testid="format-mapping">
          <div className="grid grid-cols-3 gap-3">
            <Field label={t("delimiter")}>
              <Select
                options={[
                  { value: ",", label: t("delimiterLabel.comma") },
                  { value: ";", label: t("delimiterLabel.semicolon") },
                  { value: "\t", label: t("delimiterLabel.tab") },
                  { value: "|", label: t("delimiterLabel.pipe") },
                ]}
                value={form.delimiter}
                onValueChange={set("delimiter")}
              />
            </Field>
            <Field label={t("encoding")}>
              <Select
                options={["utf-8-sig", "utf-8", "latin-1", "cp1252"].map((value) => ({
                  value,
                  label: value,
                }))}
                value={form.encoding}
                onValueChange={set("encoding")}
              />
            </Field>
            <Field label={t("headerRows")}>
              <Input
                type="number"
                min={0}
                max={20}
                value={form.header_rows}
                onChange={(e) => set("header_rows")(e.target.value)}
              />
            </Field>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <Field label={t("dateColumn")}>
              <Input value={form.date_column} onChange={(e) => set("date_column")(e.target.value)} />
            </Field>
            <Field label={t("dateFormat")}>
              <Select options={dateOptions} value={form.date_format} onValueChange={set("date_format")} />
            </Field>
            <Field label={t("bookingDateColumn")}>
              <Input
                value={form.booking_date_column}
                onChange={(e) => set("booking_date_column")(e.target.value)}
                placeholder={t("optional")}
              />
            </Field>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <Field label={t("descriptionColumn")}>
              <Input
                value={form.description_column}
                onChange={(e) => set("description_column")(e.target.value)}
              />
            </Field>
            <Field label={t("referenceColumn")}>
              <Input
                value={form.reference_column}
                onChange={(e) => set("reference_column")(e.target.value)}
                placeholder={t("optional")}
              />
            </Field>
            <Field label={t("externalIdColumn")}>
              <Input
                value={form.external_id_column}
                onChange={(e) => set("external_id_column")(e.target.value)}
                placeholder={t("optional")}
              />
            </Field>
          </div>
          {signed ? (
            <div className="grid grid-cols-3 gap-3">
              <Field label={t("amountColumn")}>
                <Input
                  value={form.amount_column}
                  onChange={(e) => set("amount_column")(e.target.value)}
                />
              </Field>
              <Field label={t("signConvention")} className="col-span-2">
                <Select
                  options={[
                    {
                      value: StatementSignConvention.CREDIT_POSITIVE,
                      label: t("signConventionLabel.credit_positive"),
                    },
                    {
                      value: StatementSignConvention.DEBIT_POSITIVE,
                      label: t("signConventionLabel.debit_positive"),
                    },
                  ]}
                  value={form.sign_convention}
                  onValueChange={set("sign_convention")}
                />
              </Field>
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-3">
              <Field label={t("debitColumn")}>
                <Input value={form.debit_column} onChange={(e) => set("debit_column")(e.target.value)} />
              </Field>
              <Field label={t("creditColumn")}>
                <Input
                  value={form.credit_column}
                  onChange={(e) => set("credit_column")(e.target.value)}
                />
              </Field>
              <Field label={t("balanceColumn")}>
                <Input
                  value={form.balance_column}
                  onChange={(e) => set("balance_column")(e.target.value)}
                  placeholder={t("optional")}
                />
              </Field>
            </div>
          )}
          <div className="grid grid-cols-3 gap-3">
            {signed ? (
              <Field label={t("balanceColumn")}>
                <Input
                  value={form.balance_column}
                  onChange={(e) => set("balance_column")(e.target.value)}
                  placeholder={t("optional")}
                />
              </Field>
            ) : null}
            <Field label={t("decimalSeparator")}>
              <Select
                options={[
                  { value: ".", label: t("separatorLabel.point") },
                  { value: ",", label: t("separatorLabel.comma") },
                ]}
                value={form.decimal_separator}
                onValueChange={set("decimal_separator")}
              />
            </Field>
            <Field label={t("thousandsSeparator")}>
              <Select
                options={[
                  { value: ",", label: t("separatorLabel.comma") },
                  { value: ".", label: t("separatorLabel.point") },
                  { value: " ", label: t("separatorLabel.space") },
                  { value: "'", label: t("separatorLabel.apostrophe") },
                ]}
                value={form.thousands_separator}
                onValueChange={set("thousands_separator")}
              />
            </Field>
          </div>
        </div>
      )}

      {formatError ? (
        <p className="text-xs text-[var(--vinea-danger)]" role="alert" data-testid="format-error">
          {formatError}
        </p>
      ) : null}

      <div className="flex justify-end">
        <Button variant="primary" onClick={handleSave} disabled={!canManage || update.isPending}>
          {update.isPending ? tc("saving") : t("save")}
        </Button>
      </div>

      <div className="space-y-3 border-t border-[var(--vinea-border)] pt-4">
        <p className="text-xs text-[var(--vinea-ink-muted)]">{t("testNote")}</p>
        <div className="flex items-end gap-3">
          <Field label={t("testFile")} className="flex-1">
            <Input
              type="file"
              accept=".csv,text/csv,text/plain"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setResult(null);
              }}
            />
          </Field>
          <Button
            variant="secondary"
            onClick={handleTest}
            disabled={!file || !canPreview || preview.isPending}
            className="gap-1.5"
          >
            <FileSearch className="size-3.5" /> {t("test")}
          </Button>
        </div>

        {result ? (
          <div className="space-y-3" data-testid="format-test-result">
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
              {result.from_date && result.to_date ? (
                <span className="text-[var(--vinea-ink-muted)]">
                  {t("dateRange", {
                    from: formatDate(result.from_date),
                    to: formatDate(result.to_date),
                  })}
                </span>
              ) : null}
              {result.duplicate_file ? (
                <StatusChip tone="warning">{t("duplicateFile")}</StatusChip>
              ) : null}
            </div>
            <dl className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <dt className="text-[var(--vinea-ink-subtle)]">{t("openingBalance")}</dt>
                <dd className="font-mono tabular-nums text-[var(--vinea-ink)]">
                  {money(result.opening_balance)}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--vinea-ink-subtle)]">{t("closingBalance")}</dt>
                <dd className="font-mono tabular-nums text-[var(--vinea-ink)]">
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
                      <TD className="text-right font-mono text-xs tabular-nums">
                        {money(line.balance_after)}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
