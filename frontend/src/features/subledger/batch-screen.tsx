"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { IsoDatePicker } from "@/design/components/date-picker";
import {
  DocumentWorkspaceShell,
  useDocumentShortcuts,
} from "@/design/components/document-workspace";
import { Field, Input } from "@/design/components/input";
import {
  LineGrid,
  emptyLineGridRow,
  type LineErrors,
  type LineGridRow,
} from "@/design/components/line-grid";
import { StatusChip } from "@/design/components/status-chip";
import { useToast } from "@/design/components/toast";
import { isApiError, useMe } from "@/features/auth/hooks";
import { useAccounts, useBranches, useCurrencies, useProjects, useTaxCodes } from "@/features/gl/hooks";
import { toOptions } from "@/features/gl/lookups";
import { clearDraft, loadDraft, newDraftId, saveDraft } from "@/lib/drafts";
import { formatMoney } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { usePartners, usePostBatch } from "./hooks";
import { partnerCode, type BatchLinePayload, type BatchPayload, type PartnerRole } from "./types";

interface BatchDraft {
  batchDate: string;
  reference: string;
  rows: LineGridRow[];
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function blankDraft(): BatchDraft {
  return { batchDate: today(), reference: "", rows: [emptyLineGridRow()] };
}

/**
 * "Account receivable batches" / "Account payable batches" — the journal grid restricted to
 * the module: one partner per line, a contra account, and a signed amount.
 *
 * Each line posts as its own JNL-typed partner document, so it creates an open item that ages
 * and allocates like any other. The batch is one unit of work: the server refuses all of them
 * if any line is refused, which is why the footer says so rather than letting an operator
 * discover it halfway down a fifty-line run.
 */
export function BatchScreen({ role }: { role: PartnerRole }) {
  const t = useTranslations("arap.batches");
  const tc = useTranslations("arap.documents.common");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const { data: me } = useMe();
  const companyId = me?.company?.id ?? 0;
  const userId = me?.user_id ?? 0;
  const draftModule = `subledger.${role}.batch`;

  const [draftId, setDraftId] = useState<string>(newDraftId);
  const [form, setForm] = useState<BatchDraft>(blankDraft);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const restored = useRef(false);

  const partners = usePartners(role);
  const accounts = useAccounts();
  const branches = useBranches();
  const projects = useProjects();
  const taxCodes = useTaxCodes();
  const currencies = useCurrencies();
  const postBatch = usePostBatch(role);

  useEffect(() => {
    if (restored.current || !companyId || !userId) return;
    restored.current = true;
    const saved = loadDraft<BatchDraft>(draftModule, companyId, userId);
    if (saved) {
      setDraftId(saved.draftId);
      setForm(saved.data);
      toast.show({ title: tc("draftRestored"), tone: "neutral" });
    }
  }, [companyId, userId, draftModule, tc, toast]);

  useEffect(() => {
    if (!restored.current || !companyId || !userId) return;
    saveDraft(draftModule, companyId, userId, {
      draftId,
      updatedAt: new Date().toISOString(),
      data: form,
    });
  }, [form, draftId, companyId, userId, draftModule]);

  const baseCurrency = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: baseCurrency?.code ?? "",
    decimalPlaces: baseCurrency?.decimal_places ?? 0,
    symbol: baseCurrency?.symbol ?? null,
  };

  const postableAccounts = useMemo(
    () => (accounts.data ?? []).filter((a) => a.is_postable && !a.is_control && a.is_active),
    [accounts.data],
  );

  const filled = form.rows.filter((row) => row.partnerId && row.amount);
  const total = filled.reduce((sum, row) => sum + Number(row.amount || 0), 0);

  const lineErrors: LineErrors = useMemo(() => {
    const out: LineErrors = {};
    for (const [key, messages] of Object.entries(fieldErrors)) {
      const match = /^lines\.(\d+)(?:\.(.+))?$/.exec(key);
      if (match) {
        const field = match[2] ?? "amount";
        out[Number(match[1])] = { ...out[Number(match[1])], [field]: messages[0] };
      }
    }
    return out;
  }, [fieldErrors]);

  const canPost = filled.length > 0 && !postBatch.isPending;

  async function handlePost() {
    setBanner(null);
    setFieldErrors({});
    const lines: BatchLinePayload[] = filled.map((row) => ({
      partner_id: Number(row.partnerId),
      contra_account_id: Number(row.accountId),
      amount: row.amount,
      description: row.description || t("referencePlaceholder"),
      tax_code_id: row.taxCodeId ? Number(row.taxCodeId) : null,
      branch_id: row.branchId ? Number(row.branchId) : null,
      project_id: row.projectId ? Number(row.projectId) : null,
    }));
    const payload: BatchPayload = {
      batch_date: form.batchDate,
      reference: form.reference || null,
      lines,
    };
    try {
      const result = await postBatch.mutateAsync({ payload, idempotencyKey: draftId });
      clearDraft(draftModule, companyId, userId);
      toast.show({ title: t("posted", { count: result.documents.length }), tone: "success" });
      // A batch produces many documents, so there is no single entry to land on. Stay put
      // with a fresh draft — the toast reports how many posted, and the run continues.
      setForm(blankDraft());
      setDraftId(newDraftId());
    } catch (err) {
      if (isApiError(err)) {
        setFieldErrors(err.fieldErrors);
        setBanner(err.message);
      } else {
        showApiError(err, t("postFailed"));
      }
    }
  }

  useDocumentShortcuts({ onPost: handlePost, onCancel: () => router.push("/"), canPost });

  return (
    <DocumentWorkspaceShell
      backHref="/"
      title={t(role === "ar" ? "titleAr" : "titleAp")}
      subtitle={t(role === "ar" ? "subtitleAr" : "subtitleAp")}
      statusChip={<StatusChip tone="neutral">{t("draft")}</StatusChip>}
      errorBanner={banner}
      footer={
        <div className="flex items-center justify-between gap-6">
          <div className="flex items-center gap-6 text-sm">
            <span className="text-[var(--vinea-ink-muted)]">
              {t("total")}{" "}
              <span className="font-mono tabular-nums text-[var(--vinea-ink)]">
                {formatMoney(total, baseLike)}
              </span>
            </span>
          </div>
          <div className="flex items-center gap-3">
            <p className="max-w-md text-xs text-[var(--vinea-ink-subtle)]">{t("atomicNote")}</p>
            <Button variant="primary" disabled={!canPost} onClick={handlePost}>
              {postBatch.isPending ? t("posting") : t("post")}
            </Button>
          </div>
        </div>
      }
    >
      <section className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-3">
        <Field label={t("batchDate")} error={fieldErrors.batch_date?.[0]}>
          <IsoDatePicker
            value={form.batchDate}
            onValueChange={(v) => setForm({ ...form, batchDate: v })}
          />
        </Field>
        <Field label={t("reference")} className="sm:col-span-2">
          <Input
            value={form.reference}
            onChange={(e) => setForm({ ...form, reference: e.target.value })}
            placeholder={t("referencePlaceholder")}
          />
        </Field>
      </section>

      <section className="space-y-2">
        <div className="flex items-baseline justify-between">
          <h2 className="text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
            {t("lines")}
          </h2>
          <p className="text-xs text-[var(--vinea-ink-subtle)]">
            {t(role === "ar" ? "signHintAr" : "signHintAp")}
          </p>
        </div>
        <LineGrid
          mode="batch"
          rows={form.rows}
          onRowsChange={(rows) => setForm({ ...form, rows })}
          errors={lineErrors}
          partnerOptions={(partners.data ?? []).map((p) => ({
            value: String(p.id),
            label: `${partnerCode(p, role)} · ${p.name}`,
          }))}
          accountOptions={toOptions(postableAccounts, (a) => `${a.code} · ${a.name}`)}
          branchOptions={toOptions(branches.data ?? [], (b) => `${b.code} · ${b.name}`)}
          projectOptions={toOptions(projects.data ?? [], (p) => `${p.code} · ${p.name}`)}
          taxCodeOptions={toOptions(taxCodes.data ?? [], (x) => `${x.code} · ${x.name}`)}
        />
      </section>
    </DocumentWorkspaceShell>
  );
}
