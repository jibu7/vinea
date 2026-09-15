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
  type LineErrors,
  type LineGridRow,
} from "@/design/components/line-grid";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { useToast } from "@/design/components/toast";
import { isApiError, useMe } from "@/features/auth/hooks";
import { useProjects } from "@/features/gl/hooks";
import { toOptions } from "@/features/gl/lookups";
import { usePartners, usePaymentTerms, useSalesReps } from "@/features/subledger/hooks";
import { partnerCode } from "@/features/subledger/types";
import { TaxMode } from "@/lib/api-enums";
import { clearDraft, loadDraft, newDraftId, saveDraft } from "@/lib/drafts";
import { dotted, nowIso, todayIso } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useCreateSalesOrder } from "./hooks";
import { useOrderLineSupport } from "./order-support";
import type { OrderLinePayload } from "./types";

const DRAFT_MODULE = "oe.sales-order";

interface SalesOrderDraft {
  id: string;
  partnerId: string;
  orderDate: string;
  expectedDate: string;
  reference: string;
  description: string;
  warehouseId: string;
  paymentTermsId: string;
  salesRepId: string;
  taxMode: TaxMode;
  rows: LineGridRow[];
}

function blankDraft(): SalesOrderDraft {
  return {
    id: newDraftId(),
    partnerId: "",
    orderDate: todayIso(),
    expectedDate: "",
    reference: "",
    description: "",
    warehouseId: "",
    paymentTermsId: "",
    salesRepId: "",
    taxMode: TaxMode.EXCLUSIVE,
    rows: [emptyLineGridRow(), emptyLineGridRow(), emptyLineGridRow()],
  };
}

/**
 * A new sales order (P6 decision 3).
 *
 * **It posts nothing.** No journal entry, no stock move: the order claims its `SO-` number and
 * records a promise, and every quantity it holds — committed, invoiced, remaining,
 * backordered — is a query over its lines, never a column. What it can be refused for is
 * `exceeds_available`, and only when `backorder_policy` is `block`: under the default `allow`
 * an order may promise more than the warehouse holds, and the shortfall shows as a backorder
 * rather than stopping the sale.
 *
 * A **kit** line is entered as one line — the kit item and how many — and the service explodes
 * it into components on save (decision 8). Nothing here does that arithmetic: a grid that
 * exploded kits in the browser would be a second implementation of the catalogue, and the one
 * that matters is the one that writes the lines.
 *
 * The draft's UUID is the `Idempotency-Key`, so a retried save replays rather than claiming a
 * second number.
 */
export function SalesOrderNewScreen() {
  const t = useTranslations("orderEntry.salesOrder");
  const tc = useTranslations("orderEntry.common");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const { data: me } = useMe();
  const companyId = me?.company?.id ?? 0;
  const userId = me?.user_id ?? 0;

  const [form, setForm] = useState<SalesOrderDraft>(blankDraft);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const restored = useRef(false);

  const support = useOrderLineSupport({ role: "sales" });
  const partners = usePartners("ar", {});
  const terms = usePaymentTerms();
  const reps = useSalesReps();
  const projects = useProjects();
  const createOrder = useCreateSalesOrder();

  useEffect(() => {
    if (restored.current || !companyId || !userId) return;
    restored.current = true;
    const saved = loadDraft<SalesOrderDraft>(DRAFT_MODULE, companyId, userId);
    if (saved) setForm(saved.data);
  }, [companyId, userId]);

  useEffect(() => {
    if (!restored.current || !companyId || !userId) return;
    saveDraft(DRAFT_MODULE, companyId, userId, {
      draftId: form.id,
      updatedAt: nowIso(),
      data: form,
    });
  }, [form, companyId, userId]);

  // The inventory default, until the operator says otherwise. Sales and purchase orders both
  // take it (decision 10); the line grid then defaults each row from the header.
  const defaultWarehouse = support.warehouses.find((w) => w.is_active && !w.is_in_transit);
  useEffect(() => {
    if (!form.warehouseId && defaultWarehouse) {
      setForm((prev) => (prev.warehouseId ? prev : { ...prev, warehouseId: String(defaultWarehouse.id) }));
    }
  }, [defaultWarehouse, form.warehouseId]);

  const lineErrors: LineErrors = useMemo(() => {
    const out: LineErrors = {};
    for (const [key, messages] of Object.entries(fieldErrors)) {
      const match = /^lines\.(\d+)(?:\.(.+))?$/.exec(key);
      if (match) {
        const field = match[2] ?? "quantity";
        out[Number(match[1])] = { ...out[Number(match[1])], [field]: messages[0] };
      }
    }
    return out;
  }, [fieldErrors]);

  const filled = form.rows.filter((row) => row.itemId && row.quantity);
  const canSave =
    Boolean(form.partnerId) && Boolean(form.description) && filled.length > 0 && !createOrder.isPending;

  /** An item carries its own price and its own default tax code; picking one fills both,
   * and typing over either afterwards is the operator's business (decision 1). */
  function onRowsChange(rows: LineGridRow[]) {
    setForm((prev) => ({
      ...prev,
      rows: rows.map((row, index) => {
        const before = prev.rows[index];
        if (!row.itemId || before?.itemId === row.itemId) return row;
        return {
          ...row,
          unitPrice: support.sellingPrice(row.itemId),
          taxCodeId: support.defaultTaxCode(row.itemId),
          warehouseId: row.warehouseId || prev.warehouseId,
        };
      }),
    }));
  }

  async function handleSave() {
    if (!canSave) return;
    setBanner(null);
    setFieldErrors({});
    const lines: OrderLinePayload[] = filled.map((row) => ({
      item_id: Number(row.itemId),
      quantity: row.quantity,
      uom_id: row.uomId ? Number(row.uomId) : null,
      unit_price: row.unitPrice ? row.unitPrice : null,
      discount_percent: row.discountPercent || "0",
      tax_code_id: row.taxCodeId ? Number(row.taxCodeId) : null,
      warehouse_id: row.warehouseId ? Number(row.warehouseId) : null,
      project_id: row.projectId ? Number(row.projectId) : null,
      description: row.description || null,
    }));
    try {
      const order = await createOrder.mutateAsync({
        idempotencyKey: form.id,
        payload: {
          partner_id: Number(form.partnerId),
          order_date: form.orderDate,
          description: form.description,
          expected_date: form.expectedDate || null,
          reference: form.reference || null,
          warehouse_id: form.warehouseId ? Number(form.warehouseId) : null,
          payment_terms_id: form.paymentTermsId ? Number(form.paymentTermsId) : null,
          sales_rep_id: form.salesRepId ? Number(form.salesRepId) : null,
          tax_mode: form.taxMode,
          lines,
        },
      });
      clearDraft(DRAFT_MODULE, companyId, userId);
      toast.show({ title: t("saved", { number: order.number }), tone: "success" });
      router.push(`/oe/sales-orders/${order.id}`);
    } catch (err) {
      if (isApiError(err)) {
        setFieldErrors(err.fieldErrors);
        setBanner(err.message);
      } else {
        showApiError(err, t("saveFailed"));
      }
    }
  }

  useDocumentShortcuts({
    onPost: handleSave,
    onCancel: () => router.push("/oe/sales-orders"),
    canPost: canSave,
  });

  return (
    <DocumentWorkspaceShell
      backHref="/oe/sales-orders"
      title={t("newTitle")}
      subtitle={t("subtitle")}
      statusChip={<StatusChip tone="neutral">{tc("draft")}</StatusChip>}
      errorBanner={banner}
      footer={
        <div className="flex items-center justify-between gap-6">
          <span className="text-sm text-[var(--vinea-ink-muted)]">
            {t("footerLines")}{" "}
            <span
              className="font-mono tabular-nums text-[var(--vinea-ink)]"
              data-testid="footer-lines"
            >
              {filled.length}
            </span>
          </span>
          <div className="flex items-center gap-3">
            <p className="max-w-md text-xs text-[var(--vinea-ink-subtle)]">{t("postsNothing")}</p>
            <Button variant="primary" disabled={!canSave} onClick={handleSave}>
              {createOrder.isPending ? tc("saving") : t("save")}
            </Button>
          </div>
        </div>
      }
    >
      <section className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-3">
        <Field label={tc("customer")} error={fieldErrors.partner_id?.[0]}>
          <Combobox
            options={(partners.data ?? []).map((partner) => ({
              value: String(partner.id),
              label: dotted(partnerCode(partner, "ar"), partner.name),
            }))}
            value={form.partnerId}
            onValueChange={(v) => setForm({ ...form, partnerId: v })}
            placeholder={t("chooseCustomer")}
          />
        </Field>
        <Field label={t("orderDate")} error={fieldErrors.order_date?.[0]}>
          <IsoDatePicker
            value={form.orderDate}
            onValueChange={(v) => setForm({ ...form, orderDate: v })}
          />
        </Field>
        <Field label={t("expectedDate")}>
          <IsoDatePicker
            value={form.expectedDate}
            onValueChange={(v) => setForm({ ...form, expectedDate: v })}
          />
        </Field>
        <Field label={tc("description")} error={fieldErrors.description?.[0]}>
          <Input
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            placeholder={t("descriptionPlaceholder")}
          />
        </Field>
        <Field label={t("reference")}>
          <Input
            value={form.reference}
            onChange={(e) => setForm({ ...form, reference: e.target.value })}
          />
        </Field>
        <Field label={t("warehouse")} error={fieldErrors.warehouse_id?.[0]}>
          <Combobox
            options={support.warehouseOptions}
            value={form.warehouseId}
            onValueChange={(v) => setForm({ ...form, warehouseId: v })}
            placeholder={t("chooseWarehouse")}
          />
        </Field>
        <Field label={t("paymentTerms")}>
          <Combobox
            options={[
              { value: "", label: tc("none") },
              ...toOptions(terms.data ?? [], (term) => dotted(term.code, term.name)),
            ]}
            value={form.paymentTermsId}
            onValueChange={(v) => setForm({ ...form, paymentTermsId: v })}
            placeholder={tc("none")}
          />
        </Field>
        <Field label={t("salesRep")}>
          <Combobox
            options={[
              { value: "", label: tc("none") },
              ...toOptions(reps.data ?? [], (rep) => dotted(rep.code, rep.name)),
            ]}
            value={form.salesRepId}
            onValueChange={(v) => setForm({ ...form, salesRepId: v })}
            placeholder={tc("none")}
          />
        </Field>
        <Field label={t("taxMode")}>
          <Select
            options={[
              { value: TaxMode.EXCLUSIVE, label: t("taxExclusive") },
              { value: TaxMode.INCLUSIVE, label: t("taxInclusive") },
            ]}
            value={form.taxMode}
            onValueChange={(v) => setForm({ ...form, taxMode: v as TaxMode })}
          />
        </Field>
      </section>

      <section className="space-y-2">
        <div className="flex items-baseline justify-between">
          <h2 className="text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
            {t("lines")}
          </h2>
          <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("kitNote")}</p>
        </div>
        <LineGrid
          mode="order"
          rows={form.rows}
          onRowsChange={onRowsChange}
          errors={lineErrors}
          accountOptions={[]}
          itemOptions={support.itemOptions}
          warehouseOptions={support.warehouseOptions}
          taxCodeOptions={support.taxCodeOptions}
          uomOptionsFor={support.uomOptionsFor}
          conversionFor={support.conversionFor}
          projectOptions={toOptions(projects.data ?? [], (p) => dotted(p.code, p.name))}
          rowDefaults={{ warehouseId: form.warehouseId }}
        />
      </section>
    </DocumentWorkspaceShell>
  );
}
