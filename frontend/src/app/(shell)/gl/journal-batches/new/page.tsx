"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { Field, Input } from "@/design/components/input";
import { DatePicker } from "@/design/components/date-picker";
import { StatusChip } from "@/design/components/status-chip";
import { LineGrid, emptyLineGridRow, type LineGridRow, type LineErrors } from "@/design/components/line-grid";
import { Money } from "@/design/components/money";
import { DocumentWorkspaceShell, useDocumentShortcuts } from "@/design/components/document-workspace";
import { useToast } from "@/design/components/toast";
import { useMe } from "@/features/auth/hooks";
import { useCreateJournalEntry, useExchangeRates } from "@/features/gl/hooks";
import { useGLLookups, toOptions, byId } from "@/features/gl/lookups";
import type { JournalEntryCreatePayload, JournalLineInput } from "@/features/gl/types";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { loadDraft, saveDraft, clearDraft, newDraftId, type Draft } from "@/lib/drafts";
import { dotted, formatDate, roundHalfUp } from "@/lib/format";

interface JournalDraftData {
  entryDate: string;
  description: string;
  reference?: string;
  branchId: string;
  rows: LineGridRow[];
}

function toNumber(v: string): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

export default function NewJournalBatchPage() {
  const t = useTranslations("gl");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const { data: me } = useMe();
  const { accounts, branches, projects, currencies, taxCodes } = useGLLookups();
  const createEntry = useCreateJournalEntry();

  const baseCurrency = useMemo(
    () => currencies.data?.find((c) => c.is_base),
    [currencies.data],
  );
  const exchangeRates = useExchangeRates();
  const currencyById = byId(currencies.data);

  const [isHydrated, setIsHydrated] = useState(false);
  const [draftId, setDraftId] = useState<string>("");
  const [entryDate, setEntryDate] = useState<Date>(new Date());
  const [description, setDescription] = useState("");
  const [reference, setReference] = useState("");
  const [branchId, setBranchId] = useState("");
  const [rows, setRows] = useState<LineGridRow[]>([emptyLineGridRow(), emptyLineGridRow()]);
  const [lineErrors, setLineErrors] = useState<LineErrors>({});
  const [dateError, setDateError] = useState<string>();
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  // Hydrate from an existing draft, or start a fresh one, once we know who's asking.
  useEffect(() => {
    if (isHydrated || !me?.company || !me.user_id || branches.isLoading) return;
    const existing = loadDraft<JournalDraftData>("journal", me.company.id, me.user_id);
    const mainBranch = branches.data?.find((b) => b.is_main);
    if (existing) {
      setDraftId(existing.draftId);
      setEntryDate(new Date(existing.data.entryDate));
      setDescription(existing.data.description);
      setReference(existing.data.reference ?? "");
      setBranchId(existing.data.branchId);
      setRows(existing.data.rows);
    } else {
      setDraftId(newDraftId());
      if (mainBranch) setBranchId(String(mainBranch.id));
    }
    setIsHydrated(true);
  }, [me, branches.data, isHydrated]);

  // Autosave client-side — nothing touches the ledger until Post.
  useEffect(() => {
    if (!isHydrated || !draftId || !me?.company || !me.user_id) return;
    const draft: Draft<JournalDraftData> = {
      draftId,
      updatedAt: new Date().toISOString(),
      data: { entryDate: entryDate.toISOString(), description, reference, branchId, rows },
    };
    saveDraft("journal", me.company.id, me.user_id, draft);
  }, [draftId, entryDate, description, reference, branchId, rows, me, isHydrated]);

  const totals = useMemo(() => {
    const debit = rows.reduce((s, r) => s + toNumber(r.debit), 0);
    const credit = rows.reduce((s, r) => s + toNumber(r.credit), 0);
    return { debit, credit, difference: roundHalfUp(debit - credit, 6) };
  }, [rows]);
  const balanced = totals.difference === 0;
  const hasTwoLines = rows.filter((r) => r.accountId && (r.debit || r.credit)).length >= 2;
  const canPost = balanced && hasTwoLines && !createEntry.isPending;

  function buildPayload(): JournalEntryCreatePayload {
    const lines: JournalLineInput[] = rows
      .filter((r) => r.accountId && (r.debit || r.credit))
      .map((r) => ({
        gl_account_id: Number(r.accountId),
        debit: r.debit || "0",
        credit: r.credit || "0",
        branch_id: r.branchId ? Number(r.branchId) : undefined,
        project_id: r.projectId ? Number(r.projectId) : undefined,
        currency_id: r.currencyId ? Number(r.currencyId) : undefined,
        exchange_rate: r.exchangeRate || undefined,
        tax_code_id: r.taxCodeId ? Number(r.taxCodeId) : undefined,
        description: r.description || undefined,
      }));
    return {
      entry_date: entryDate.toISOString().slice(0, 10),
      description,
      reference: reference.trim() || undefined,
      branch_id: branchId ? Number(branchId) : undefined,
      lines,
    };
  }

  async function handlePost() {
    if (!canPost || !me?.company || !me.user_id) return;
    setDateError(undefined);
    setErrorBanner(null);
    setLineErrors({});
    try {
      const entry = await createEntry.mutateAsync({ payload: buildPayload(), idempotencyKey: draftId });
      clearDraft("journal", me.company.id, me.user_id);
      toast.show({ title: t("entryPosted"), description: entry.number, tone: "success" });
      router.push(`/gl/entries/${entry.id}`);
    } catch (err) {
      const fieldErrors = (err as { fieldErrors?: Record<string, string[]> }).fieldErrors;
      const message = (err as { message?: string }).message;
      const nextLineErrors: LineErrors = {};
      const docErrors: string[] = [];

      if (fieldErrors && Object.keys(fieldErrors).length > 0) {
        for (const [key, msgs] of Object.entries(fieldErrors)) {
          const match = key.match(/^lines\.(\d+)\.(.+)$/);
          if (match) {
            const lineIdx = Number(match[1]);
            const field = match[2];
            nextLineErrors[lineIdx] = nextLineErrors[lineIdx] || {};
            nextLineErrors[lineIdx][field] = msgs.join(", ");
          } else if (key === "entry_date") {
            setDateError(msgs.join(", "));
          } else {
            docErrors.push(msgs.join(", "));
          }
        }
      } else if (message) {
        docErrors.push(message);
      }

      setLineErrors(nextLineErrors);
      if (docErrors.length > 0) {
        setErrorBanner(docErrors.join(" · "));
      } else {
        setErrorBanner(null);
      }
    }
  }

  useDocumentShortcuts({ onPost: handlePost, onCancel: () => router.push("/"), canPost });

  return (
    <DocumentWorkspaceShell
      backHref="/"
      title={t("journalBatch")}
      subtitle={draftId ? `Draft ${draftId.slice(0, 8)}` : undefined}
      statusChip={<StatusChip tone={balanced ? "success" : "danger"}>{balanced ? "Balanced" : "Unbalanced"}</StatusChip>}
      errorBanner={errorBanner}
      footer={
        <div className="flex items-center justify-between">
          <div className="flex gap-8 text-sm">
            <div>
              <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("totalDebit")}</p>
              {baseCurrency && <Money amount={totals.debit} currency={{ code: baseCurrency.code, decimalPlaces: baseCurrency.decimal_places, symbol: baseCurrency.symbol }} className="font-semibold" />}
            </div>
            <div>
              <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("totalCredit")}</p>
              {baseCurrency && <Money amount={totals.credit} currency={{ code: baseCurrency.code, decimalPlaces: baseCurrency.decimal_places, symbol: baseCurrency.symbol }} className="font-semibold" />}
            </div>
            <div>
              <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("difference")}</p>
              {baseCurrency && (
                <Money
                  amount={totals.difference}
                  currency={{ code: baseCurrency.code, decimalPlaces: baseCurrency.decimal_places, symbol: baseCurrency.symbol }}
                  className={balanced ? "font-semibold text-[var(--vinea-success)]" : "font-semibold text-[var(--vinea-danger)]"}
                />
              )}
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => router.push("/")}>{t("cancel")}</Button>
            <Button variant="primary" disabled={!canPost} onClick={handlePost}>
              {createEntry.isPending ? t("posting") : t("post")}
            </Button>
          </div>
        </div>
      }
    >
      <div className="grid grid-cols-1 gap-4 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-5 sm:grid-cols-2 lg:grid-cols-4">
        <Field label={t("date")} error={dateError}>
          <DatePicker value={entryDate} onValueChange={setEntryDate} />
        </Field>
        <Field label={t("branch")}>
          <select
            value={branchId}
            onChange={(e) => setBranchId(e.target.value)}
            aria-label={t("branch")}
            className="h-10 w-full rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] px-3 text-sm"
          >
            <option value="">{t("emptyValue")}</option>
            {(branches.data ?? []).map((b) => (
              <option key={b.id} value={b.id}>{dotted(b.code, b.name)}</option>
            ))}
          </select>
        </Field>
        <Field label={t("reference")}>
          <Input value={reference} onChange={(e) => setReference(e.target.value)} placeholder={t("referencePlaceholder")} />
        </Field>
        <Field label={t("description")}>
          <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder={t("journalDescriptionPlaceholder")} />
        </Field>
      </div>

      <LineGrid
        mode="journal"
        rows={rows}
        onRowsChange={setRows}
        errors={lineErrors}
        accountOptions={toOptions(accounts.data, (a) => `${a.code} · ${a.name}`)}
        branchOptions={toOptions(branches.data, (b) => `${b.code} · ${b.name}`)}
        projectOptions={toOptions(projects.data, (p) => `${p.code} · ${p.name}`)}
        currencyOptions={toOptions(currencies.data, (c) => `${c.code} \u2014 ${c.name}`)}
        taxCodeOptions={toOptions(taxCodes.data, (tc) => `${tc.code} (${tc.rate_pct}%)`)}
        baseCurrencyId={baseCurrency ? String(baseCurrency.id) : undefined}
        rowDefaults={{ branchId }}
        rateForCurrency={(currencyId) => {
          const rate = exchangeRates.data?.find((r) => r.currency_id === Number(currencyId));
          return rate?.rate;
        }}
      />
      <p className="text-xs text-[var(--vinea-ink-subtle)]">
        {formatDate(entryDate)} {currencyById.size > 0 && baseCurrency ? `\u00b7 base currency ${baseCurrency.code}` : ""}
      </p>
    </DocumentWorkspaceShell>
  );
}
