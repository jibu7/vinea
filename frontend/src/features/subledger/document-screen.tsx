"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import {
  DocumentWorkspaceShell,
  useDocumentShortcuts,
} from "@/design/components/document-workspace";
import { Field, Input } from "@/design/components/input";
import {
  LineGrid,
  emptyLineGridRow,
  lineNet,
  type LineErrors,
  type LineGridRow,
} from "@/design/components/line-grid";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { useToast } from "@/design/components/toast";
import { useMe } from "@/features/auth/hooks";
import { useAccounts, useBranches, useCurrencies, useProjects, useTaxCodes } from "@/features/gl/hooks";
import { toOptions } from "@/features/gl/lookups";
import { isApiError } from "@/features/auth/hooks";
import { clearDraft, loadDraft, newDraftId, saveDraft } from "@/lib/drafts";
import { formatMoney } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { dueDateFor } from "./due-date";
import type { DocumentScreenSpec } from "./document-kinds";
import { usePartnerEnquiry, usePartners, usePaymentTerms, usePostDocument, useSalesReps } from "./hooks";
import {
  INSTRUMENT_TYPES,
  partnerCode,
  type DocumentCreatePayload,
  type DocumentLinePayload,
  type InstrumentType,
  type TaxMode,
} from "./types";

interface DocumentDraft {
  partnerId: string;
  documentDate: string;
  dueDate: string;
  dueDateTouched: boolean;
  paymentTermsId: string;
  salesRepId: string;
  currencyId: string;
  exchangeRate: string;
  branchId: string;
  projectId: string;
  reference: string;
  description: string;
  taxMode: TaxMode;
  rows: LineGridRow[];
  amount: string;
  cashAccountId: string;
  instrumentType: InstrumentType;
  maturityDate: string;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function blankDraft(): DocumentDraft {
  return {
    partnerId: "",
    documentDate: today(),
    dueDate: today(),
    dueDateTouched: false,
    paymentTermsId: "",
    salesRepId: "",
    currencyId: "",
    exchangeRate: "",
    branchId: "",
    projectId: "",
    reference: "",
    description: "",
    taxMode: "exclusive",
    rows: [emptyLineGridRow()],
    amount: "",
    cashAccountId: "",
    instrumentType: "bank",
    maturityDate: "",
  };
}

/**
 * All six AR/AP transaction screens. The backend handles invoice / credit note / settlement
 * for both roles through one service and one role/kind matrix; this is the same matrix on
 * the client, so there is no `ar_invoice_screen.tsx` to drift from an `ap_invoice_screen.tsx`.
 *
 * Nothing here computes what posts. The footer sums the exclusive lines the operator typed,
 * and the due date is a display default (see `due-date.ts`) sent only when edited — tax,
 * totals and the journal are the server's.
 */
export function DocumentScreen({ spec }: { spec: DocumentScreenSpec }) {
  const { role, kind, isSettlement } = spec;
  const t = useTranslations("arap.documents.common");
  const ts = useTranslations(`arap.documents.${spec.messages}`);
  const tr = useTranslations(`arap.role.${role}`);
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const { data: me } = useMe();
  const companyId = me?.company?.id ?? 0;
  const userId = me?.user_id ?? 0;
  const draftModule = `subledger.${role}.${spec.slug}`;

  const [draftId, setDraftId] = useState<string>(newDraftId);
  const [form, setForm] = useState<DocumentDraft>(blankDraft);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const restored = useRef(false);

  const partners = usePartners(role);
  const terms = usePaymentTerms();
  const reps = useSalesReps();
  const accounts = useAccounts();
  const branches = useBranches();
  const projects = useProjects();
  const currencies = useCurrencies();
  const taxCodes = useTaxCodes();
  const postDocument = usePostDocument(role);

  const partnerId = form.partnerId ? Number(form.partnerId) : null;
  const enquiry = usePartnerEnquiry(role, partnerId, form.documentDate || undefined);

  // --- draft autosave, keyed by the UUID that becomes the Idempotency-Key ----------------
  useEffect(() => {
    if (restored.current || !companyId || !userId) return;
    restored.current = true;
    const saved = loadDraft<DocumentDraft>(draftModule, companyId, userId);
    if (saved) {
      setDraftId(saved.draftId);
      setForm(saved.data);
      toast.show({ title: t("draftRestored"), tone: "neutral" });
    }
  }, [companyId, userId, draftModule, t, toast]);

  useEffect(() => {
    if (!restored.current || !companyId || !userId) return;
    saveDraft(draftModule, companyId, userId, {
      draftId,
      updatedAt: new Date().toISOString(),
      data: form,
    });
  }, [form, draftId, companyId, userId, draftModule]);

  const selectedTerms = useMemo(
    () => (terms.data ?? []).find((row) => String(row.id) === form.paymentTermsId),
    [terms.data, form.paymentTermsId],
  );

  // Terms or document date changing re-derives the default, unless the operator has typed
  // their own — an edited due date is theirs to keep.
  useEffect(() => {
    if (isSettlement || form.dueDateTouched) return;
    setForm((current) => ({ ...current, dueDate: dueDateFor(selectedTerms, current.documentDate) }));
  }, [selectedTerms, form.documentDate, form.dueDateTouched, isSettlement]);

  const baseCurrency = (currencies.data ?? []).find((c) => c.is_base);
  const currencyId = form.currencyId ? Number(form.currencyId) : baseCurrency?.id;
  const currency = (currencies.data ?? []).find((c) => c.id === currencyId);
  const currencyLike = {
    code: currency?.code ?? "",
    decimalPlaces: currency?.decimal_places ?? 0,
    symbol: currency?.symbol ?? null,
  };

  const postableAccounts = useMemo(
    () => (accounts.data ?? []).filter((a) => a.is_postable && !a.is_control && a.is_active),
    [accounts.data],
  );
  const cashAccounts = useMemo(
    () =>
      (accounts.data ?? []).filter(
        (a) => a.is_postable && a.is_active && (a.control_type === "bank" || a.control_type === "cash"),
      ),
    [accounts.data],
  );

  const exclusiveTotal = isSettlement
    ? Number(form.amount || 0)
    : form.rows.reduce((sum, row) => sum + lineNet(row), 0);

  const lineErrors: LineErrors = useMemo(() => {
    const out: LineErrors = {};
    for (const [key, messages] of Object.entries(fieldErrors)) {
      const match = /^lines\.(\d+)\.(.+)$/.exec(key);
      if (match) out[Number(match[1])] = { ...out[Number(match[1])], [match[2]]: messages[0] };
    }
    return out;
  }, [fieldErrors]);

  const isPostDated = isSettlement && form.maturityDate > form.documentDate;
  const canPost =
    !!form.partnerId &&
    !!form.description &&
    !postDocument.isPending &&
    (isSettlement ? !!form.amount && !!form.cashAccountId : form.rows.some((r) => r.unitPrice));

  function patch(next: Partial<DocumentDraft>) {
    setForm((current) => ({ ...current, ...next }));
  }

  async function handlePost() {
    setBanner(null);
    setFieldErrors({});
    const lines: DocumentLinePayload[] = form.rows
      .filter((row) => row.unitPrice)
      .map((row) => ({
        description: row.description || null,
        quantity: row.quantity || "1",
        unit_price: row.unitPrice,
        discount_percent: row.discountPercent || "0",
        gl_account_id: row.accountId ? Number(row.accountId) : null,
        tax_code_id: row.taxCodeId ? Number(row.taxCodeId) : null,
        branch_id: row.branchId ? Number(row.branchId) : null,
        project_id: row.projectId ? Number(row.projectId) : null,
      }));

    const payload: DocumentCreatePayload = {
      kind,
      partner_id: Number(form.partnerId),
      document_date: form.documentDate,
      // Untouched means "let the server derive it from the terms" — see due-date.ts.
      due_date: isSettlement ? null : form.dueDateTouched ? form.dueDate : null,
      currency_id: form.currencyId ? Number(form.currencyId) : null,
      exchange_rate: form.exchangeRate || null,
      branch_id: form.branchId ? Number(form.branchId) : null,
      project_id: form.projectId ? Number(form.projectId) : null,
      payment_terms_id: form.paymentTermsId ? Number(form.paymentTermsId) : null,
      sales_rep_id: role === "ar" && form.salesRepId ? Number(form.salesRepId) : null,
      tax_mode: form.taxMode,
      reference: form.reference || null,
      description: form.description,
      ...(isSettlement
        ? {
            amount: form.amount,
            cash_account_id: Number(form.cashAccountId),
            instrument_type: form.instrumentType,
            maturity_date: form.maturityDate || null,
          }
        : { lines }),
    };

    try {
      const document = await postDocument.mutateAsync({ payload, idempotencyKey: draftId });
      clearDraft(draftModule, companyId, userId);
      toast.show({ title: t("postedToast", { number: document.number }), tone: "success" });
      router.push(`/gl/entries/${document.journal_entry_id}`);
    } catch (err) {
      if (isApiError(err)) {
        setFieldErrors(err.fieldErrors);
        setBanner(err.message);
      } else {
        showApiError(err, t("postFailed"));
      }
    }
  }

  useDocumentShortcuts({
    onPost: handlePost,
    onCancel: () => router.push("/"),
    canPost,
  });

  const selectedPartner = (partners.data ?? []).find((p) => String(p.id) === form.partnerId);
  const headroom = enquiry.data?.credit_available;

  return (
    <DocumentWorkspaceShell
      backHref="/"
      title={ts("title")}
      subtitle={ts("subtitle")}
      statusChip={<StatusChip tone="neutral">{t("draft")}</StatusChip>}
      errorBanner={banner}
      footer={
        <div className="flex items-center justify-between gap-6">
          <div className="flex items-center gap-6 text-sm">
            <span className="text-[var(--vinea-ink-muted)]">
              {t("footerExclusive")}{" "}
              <span className="font-mono tabular-nums text-[var(--vinea-ink)]">
                {formatMoney(exclusiveTotal, currencyLike)}
              </span>
            </span>
            {/* Tax and the inclusive total are the server's — computed half-up to the
                document currency's decimals on Post, never guessed at here. */}
            <span className="text-[var(--vinea-ink-subtle)]">
              {t("footerTax")} {t("serverComputed")}
            </span>
            <span className="text-[var(--vinea-ink-subtle)]">
              {t("footerInclusive")} {t("serverComputed")}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <p className="max-w-md text-xs text-[var(--vinea-ink-subtle)]">{t("footerNote")}</p>
            <Button variant="primary" disabled={!canPost} onClick={handlePost}>
              {postDocument.isPending ? t("posting") : t("post")}
            </Button>
          </div>
        </div>
      }
    >
      <section className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-3">
        <Field label={tr("partner")} error={fieldErrors.partner_id?.[0]}>
          <Combobox
            options={(partners.data ?? []).map((p) => ({
              value: String(p.id),
              label: `${partnerCode(p, role)} · ${p.name}`,
            }))}
            value={form.partnerId}
            onValueChange={(v) => patch({ partnerId: v })}
            placeholder={t("choosePartner")}
          />
        </Field>

        {/* The typeahead's whole point: what this partner already owes, and what is left of
            their limit, before another document is added to it. */}
        <div className="sm:col-span-2 flex items-end gap-4 text-xs">
          {selectedPartner && enquiry.data && (
            <>
              <span className="text-[var(--vinea-ink-muted)]">
                {t("openBalance")}{" "}
                <span className="font-mono tabular-nums text-[var(--vinea-ink)]">
                  {formatMoney(Number(enquiry.data.balance_base), {
                    code: baseCurrency?.code ?? "",
                    decimalPlaces: baseCurrency?.decimal_places ?? 0,
                    symbol: baseCurrency?.symbol ?? null,
                  })}
                </span>
              </span>
              <span className="text-[var(--vinea-ink-muted)]">
                {t("creditHeadroom")}{" "}
                {headroom === null || headroom === undefined ? (
                  <span className="text-[var(--vinea-ink-subtle)]">{t("noLimit")}</span>
                ) : (
                  <StatusChip tone={Number(headroom) < 0 ? "danger" : "success"}>
                    {Number(headroom) < 0
                      ? t("overLimit")
                      : formatMoney(Number(headroom), {
                          code: baseCurrency?.code ?? "",
                          decimalPlaces: baseCurrency?.decimal_places ?? 0,
                          symbol: baseCurrency?.symbol ?? null,
                        })}
                  </StatusChip>
                )}
              </span>
            </>
          )}
        </div>

        <Field label={t("documentDate")} error={fieldErrors.document_date?.[0]}>
          <IsoDatePicker value={form.documentDate} onValueChange={(v) => patch({ documentDate: v })} />
        </Field>

        {!isSettlement && (
          <>
            <Field label={t("paymentTerms")}>
              <Combobox
                options={[
                  { value: "", label: t("none") },
                  ...toOptions(terms.data ?? [], (x) => `${x.code} · ${x.name}`),
                ]}
                value={form.paymentTermsId}
                onValueChange={(v) => patch({ paymentTermsId: v })}
                placeholder={t("none")}
              />
            </Field>
            <Field
              label={t("dueDate")}
              error={fieldErrors.due_date?.[0]}
            >
              <IsoDatePicker
                value={form.dueDate}
                onValueChange={(v) => patch({ dueDate: v, dueDateTouched: true })}
              />
            </Field>
          </>
        )}

        <Field label={t("currency")}>
          <Combobox
            options={[
              { value: "", label: t("baseCurrency") },
              ...toOptions(currencies.data ?? [], (c) => `${c.code} · ${c.name}`),
            ]}
            value={form.currencyId}
            onValueChange={(v) => patch({ currencyId: v })}
            placeholder={t("baseCurrency")}
          />
        </Field>
        {form.currencyId && Number(form.currencyId) !== baseCurrency?.id && (
          <Field label={t("exchangeRate")} error={fieldErrors.exchange_rate?.[0]}>
            <Input
              value={form.exchangeRate}
              onChange={(e) => patch({ exchangeRate: e.target.value })}
              inputMode="decimal"
              className="text-right font-mono tabular-nums"
            />
          </Field>
        )}

        <Field label={t("branch")}>
          <Combobox
            options={[
              { value: "", label: t("none") },
              ...toOptions(branches.data ?? [], (b) => `${b.code} · ${b.name}`),
            ]}
            value={form.branchId}
            onValueChange={(v) => patch({ branchId: v })}
            placeholder={t("none")}
          />
        </Field>
        <Field label={t("project")}>
          <Combobox
            options={[
              { value: "", label: t("none") },
              ...toOptions(projects.data ?? [], (p) => `${p.code} · ${p.name}`),
            ]}
            value={form.projectId}
            onValueChange={(v) => patch({ projectId: v })}
            placeholder={t("none")}
          />
        </Field>

        {role === "ar" && !isSettlement && (
          <Field label={t("salesRep")}>
            <Combobox
              options={[
                { value: "", label: t("none") },
                ...toOptions(reps.data ?? [], (r) => `${r.code} · ${r.name}`),
              ]}
              value={form.salesRepId}
              onValueChange={(v) => patch({ salesRepId: v })}
              placeholder={t("none")}
            />
          </Field>
        )}

        {!isSettlement && (
          <Field label={t("taxMode")}>
            <Select
              options={[
                { value: "exclusive", label: t("taxModeExclusive") },
                { value: "inclusive", label: t("taxModeInclusive") },
              ]}
              value={form.taxMode}
              onValueChange={(v) => patch({ taxMode: v as TaxMode })}
            />
          </Field>
        )}

        <Field label={t("reference")}>
          <Input value={form.reference} onChange={(e) => patch({ reference: e.target.value })} />
        </Field>
        <Field
          label={t("description")}
          error={fieldErrors.description?.[0]}
          className="sm:col-span-2"
        >
          <Input
            value={form.description}
            onChange={(e) => patch({ description: e.target.value })}
            placeholder={ts("descriptionPlaceholder")}
          />
        </Field>
      </section>

      {isSettlement ? (
        <section className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-4">
          <Field label={t("amount")} error={fieldErrors.amount?.[0]}>
            <Input
              value={form.amount}
              onChange={(e) => patch({ amount: e.target.value })}
              inputMode="decimal"
              className="text-right font-mono tabular-nums"
              placeholder={t("zeroPlaceholder")}
            />
          </Field>
          <Field label={t("cashAccount")} error={fieldErrors.cash_account_id?.[0]}>
            <Combobox
              options={toOptions(cashAccounts, (a) => `${a.code} · ${a.name}`)}
              value={form.cashAccountId}
              onValueChange={(v) => patch({ cashAccountId: v })}
              placeholder={t("chooseCashAccount")}
            />
          </Field>
          <Field label={t("instrumentType")}>
            <Select
              options={INSTRUMENT_TYPES.map((value) => ({
                value,
                label: t(`instrument${value.charAt(0).toUpperCase()}${value.slice(1)}`),
              }))}
              value={form.instrumentType}
              onValueChange={(v) => patch({ instrumentType: v as InstrumentType })}
            />
          </Field>
          <Field label={t("maturityDate")} error={fieldErrors.maturity_date?.[0]}>
            <IsoDatePicker value={form.maturityDate} onValueChange={(v) => patch({ maturityDate: v })} />
          </Field>
          {isPostDated && (
            <p className="sm:col-span-4 text-xs text-[var(--vinea-warning)]">{t("postDatedNotice")}</p>
          )}
        </section>
      ) : (
        <section className="space-y-2">
          <h2 className="text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
            {t("lines")}
          </h2>
          <LineGrid
            mode="document"
            rows={form.rows}
            onRowsChange={(rows) => patch({ rows })}
            errors={lineErrors}
            accountOptions={toOptions(postableAccounts, (a) => `${a.code} · ${a.name}`)}
            branchOptions={toOptions(branches.data ?? [], (b) => `${b.code} · ${b.name}`)}
            projectOptions={toOptions(projects.data ?? [], (p) => `${p.code} · ${p.name}`)}
            taxCodeOptions={toOptions(taxCodes.data ?? [], (x) => `${x.code} · ${x.name}`)}
          />
        </section>
      )}
    </DocumentWorkspaceShell>
  );
}
