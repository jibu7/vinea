"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { Field, Input } from "@/design/components/input";
import { DatePicker } from "@/design/components/date-picker";
import { Combobox } from "@/design/components/combobox";
import { LineGrid, emptyLineGridRow, type LineGridRow, type LineErrors } from "@/design/components/line-grid";
import { Money } from "@/design/components/money";
import { DocumentWorkspaceShell, useDocumentShortcuts } from "@/design/components/document-workspace";
import { useToast } from "@/design/components/toast";
import { useMe } from "@/features/auth/hooks";
import { useCreateCashbookEntry } from "@/features/gl/hooks";
import { useGLLookups, toOptions } from "@/features/gl/lookups";
import type { CashbookEntryCreatePayload, CashbookLineInput } from "@/features/gl/types";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { loadDraft, saveDraft, clearDraft, newDraftId, type Draft } from "@/lib/drafts";

interface CashbookDraftData {
  entryDate: string;
  description: string;
  reference: string;
  branchId: string;
  cashAccountId: string;
  kind: "receipt" | "payment";
  rows: LineGridRow[];
}

function toNumber(v: string): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

export default function NewCashbookBatchPage() {
  const t = useTranslations("gl");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const { data: me } = useMe();
  const { accounts, branches, projects, currencies, taxCodes } = useGLLookups();
  const createEntry = useCreateCashbookEntry();

  const baseCurrency = useMemo(() => currencies.data?.find((c) => c.is_base), [currencies.data]);
  const cashAccounts = useMemo(
    () => (accounts.data ?? []).filter((a) => a.control_type === "bank" || a.control_type === "cash"),
    [accounts.data],
  );

  const hydrated = useRef(false);
  const [draftId, setDraftId] = useState<string>("");
  const [entryDate, setEntryDate] = useState<Date>(new Date());
  const [description, setDescription] = useState("");
  const [reference, setReference] = useState("");
  const [branchId, setBranchId] = useState("");
  const [cashAccountId, setCashAccountId] = useState("");
  const [kind, setKind] = useState<"receipt" | "payment">("receipt");
  const [rows, setRows] = useState<LineGridRow[]>([emptyLineGridRow()]);
  const [lineErrors, setLineErrors] = useState<LineErrors>({});
  const [dateError, setDateError] = useState<string>();
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  useEffect(() => {
    if (hydrated.current || !me?.company || !me.user_id || branches.isLoading) return;
    hydrated.current = true;
    const existing = loadDraft<CashbookDraftData>("cashbook", me.company.id, me.user_id);
    const mainBranch = branches.data?.find((b) => b.is_main);
    if (existing) {
      setDraftId(existing.draftId);
      setEntryDate(new Date(existing.data.entryDate));
      setDescription(existing.data.description);
      setReference(existing.data.reference);
      setBranchId(existing.data.branchId);
      setCashAccountId(existing.data.cashAccountId);
      setKind(existing.data.kind);
      setRows(existing.data.rows);
    } else {
      setDraftId(newDraftId());
      if (mainBranch) setBranchId(String(mainBranch.id));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me, branches.data]);

  useEffect(() => {
    if (!hydrated.current || !draftId || !me?.company || !me.user_id) return;
    const draft: Draft<CashbookDraftData> = {
      draftId,
      updatedAt: new Date().toISOString(),
      data: { entryDate: entryDate.toISOString(), description, reference, branchId, cashAccountId, kind, rows },
    };
    saveDraft("cashbook", me.company.id, me.user_id, draft);
  }, [draftId, entryDate, description, reference, branchId, cashAccountId, kind, rows, me]);

  const total = useMemo(() => rows.reduce((s, r) => s + toNumber(r.amount), 0), [rows]);
  const hasLines = rows.some((r) => r.accountId && r.amount);
  const canPost = hasLines && total > 0 && !!cashAccountId && !createEntry.isPending;

  function buildPayload(): CashbookEntryCreatePayload {
    const lines: CashbookLineInput[] = rows
      .filter((r) => r.accountId && r.amount)
      .map((r) => ({
        gl_account_id: Number(r.accountId),
        amount: r.amount,
        tax_code_id: r.taxCodeId ? Number(r.taxCodeId) : undefined,
        tax_inclusive: r.taxInclusive,
        branch_id: r.branchId ? Number(r.branchId) : undefined,
        project_id: r.projectId ? Number(r.projectId) : undefined,
        description: r.description || undefined,
      }));
    return {
      entry_date: entryDate.toISOString().slice(0, 10),
      description,
      cash_account_id: Number(cashAccountId),
      kind,
      branch_id: branchId ? Number(branchId) : undefined,
      reference: reference || undefined,
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
      clearDraft("cashbook", me.company.id, me.user_id);
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
      title={t("cashbookBatch")}
      subtitle={draftId ? `Draft ${draftId.slice(0, 8)}` : undefined}
      errorBanner={errorBanner}
      footer={
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("total")}</p>
            {baseCurrency && (
              <Money
                amount={total}
                currency={{ code: baseCurrency.code, decimalPlaces: baseCurrency.decimal_places, symbol: baseCurrency.symbol }}
                className="font-semibold"
              />
            )}
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
        <Field label={t("bankCashAccount")}>
          <Combobox
            options={toOptions(cashAccounts, (a) => `${a.code} \u00b7 ${a.name}`)}
            value={cashAccountId}
            onValueChange={setCashAccountId}
            placeholder={"Select account\u2026"}
          />
        </Field>
        <Field label=" ">
          <div className="flex h-10 items-center gap-4">
            <label className="flex items-center gap-1.5 text-sm">
              <input type="radio" checked={kind === "receipt"} onChange={() => setKind("receipt")} className="accent-[var(--vinea-brand)]" />
              {t("receipt")}
            </label>
            <label className="flex items-center gap-1.5 text-sm">
              <input type="radio" checked={kind === "payment"} onChange={() => setKind("payment")} className="accent-[var(--vinea-brand)]" />
              {t("payment")}
            </label>
          </div>
        </Field>
        <Field label={t("reference")}>
          <Input value={reference} onChange={(e) => setReference(e.target.value)} />
        </Field>
        <Field label={t("description")}>
          <Input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t("cashbookDescriptionPlaceholder")}
            className="sm:col-span-2 lg:col-span-4"
          />
        </Field>
      </div>

      <LineGrid
        mode="cashbook"
        rows={rows}
        onRowsChange={setRows}
        errors={lineErrors}
        accountOptions={toOptions(
          (accounts.data ?? []).filter((a) => a.is_postable && a.control_type !== "bank" && a.control_type !== "cash"),
          (a) => `${a.code} \u00b7 ${a.name}`,
        )}
        branchOptions={toOptions(branches.data, (b) => `${b.code} \u00b7 ${b.name}`)}
        projectOptions={toOptions(projects.data, (p) => `${p.code} \u00b7 ${p.name}`)}
        taxCodeOptions={toOptions(taxCodes.data, (tc) => `${tc.code} (${tc.rate_pct}%)`)}
        rowDefaults={{ branchId }}
      />
    </DocumentWorkspaceShell>
  );
}
