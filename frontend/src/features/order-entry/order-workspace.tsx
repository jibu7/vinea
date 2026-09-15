"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Dialog, DialogContent } from "@/design/components/dialog";
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
import { dotted, nowIso, todayIso, trimDecimalString } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import {
  useCreatePurchaseOrder,
  useCreateSalesOrder,
  usePurchaseOrder,
  useSalesOrder,
  useUpdatePurchaseOrder,
  useUpdateSalesOrder,
} from "./hooks";
import { useOrderLineSupport } from "./order-support";
import type { OrderLinePayload } from "./types";

export type OrderRole = "sales" | "purchase";

/**
 * A row seeded from a saved line keeps the line's id in its grid id, so a PUT can say which
 * line it means. An invoice line points at an order line, so an edit that dropped the ids and
 * re-created every line would break that link — and a grid row has no other home for it.
 */
const SEEDED = "line:";

function lineIdOf(row: LineGridRow): number | null {
  return row.id.startsWith(SEEDED) ? Number(row.id.slice(SEEDED.length)) : null;
}

interface OrderDraft {
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

function blankDraft(): OrderDraft {
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

/** What a row was worth when it was seeded, so the save knows which quantities the operator
 * actually moved. The unit is part of it: 1 BOX12 and 12 EA are the same commitment and
 * neither is the same edit. */
function quantityFingerprint(row: LineGridRow): string {
  return `${trimDecimalString(row.quantity || "0")}|${row.uomId}`;
}

/**
 * The order workspace — **one** screen for a new order and for editing one, on both sides of
 * the house (P6 decision 3).
 *
 * The four screens the plan asks for (new sales order, edit sales order, new purchase order,
 * edit purchase order) differ in three fields and which endpoint they call. Writing them out
 * four times would be four places for the kit rules, the draft key and the price defaulting to
 * drift apart, so they are one component with a role and an optional order to load.
 *
 * **It posts nothing.** No journal entry, no stock move: the order claims its number and
 * records a promise, and every quantity it holds — committed, invoiced or received, remaining,
 * backordered — is a query over its lines, never a column. What it can be refused for is
 * `exceeds_available`, and only when `backorder_policy` is `block`: under the default `allow`
 * a sales order may promise more than the warehouse holds and the shortfall shows as a
 * backorder rather than stopping the sale.
 *
 * A **kit** line is entered as one line — the kit item and how many — and the service explodes
 * it into components on save (decision 8). Nothing here does that arithmetic: a grid that
 * exploded kits in the browser would be a second implementation of the catalogue, and the one
 * that matters is the one that writes the lines. Editing such a line's quantity after Breakup
 * has touched it is refused with `kit_breakup_would_reset`, and the dialog below is the
 * "somebody says so" that refusal is waiting for.
 *
 * The draft's UUID is the `Idempotency-Key`, so a retried save replays rather than claiming a
 * second number. An **edit** keeps no draft: the order is already saved, and a half-typed
 * change restored days later over a document that has moved on is worse than losing it.
 */
export function OrderWorkspace({ role, orderId }: { role: OrderRole; orderId?: number }) {
  const isSales = role === "sales";
  const editing = orderId !== undefined;
  // A template literal rather than a ternary of two whole namespaces, so the i18n coverage
  // scan can see which catalogue this screen reads — it expands `orderEntry.${…}` to every
  // block under it and checks each key resolves in at least one.
  const t = useTranslations(`orderEntry.${isSales ? "salesOrder" : "purchaseOrder"}`);
  const tc = useTranslations("orderEntry.common");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const { data: me } = useMe();
  const companyId = me?.company?.id ?? 0;
  const userId = me?.user_id ?? 0;
  const listHref = isSales ? "/oe/sales-orders" : "/oe/purchase-orders";
  const draftModule = isSales ? "oe.sales-order" : "oe.purchase-order";

  const [form, setForm] = useState<OrderDraft>(blankDraft);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [banner, setBanner] = useState<string | null>(null);
  /** The consent dialog: which lines would be re-exploded, and the service's own words in
   * case this screen's reading of "which" comes back empty. */
  const [resetAsk, setResetAsk] = useState<{ labels: string[]; message: string } | null>(null);
  const seeded = useRef<Record<string, string>>({});
  const restored = useRef(false);
  const loaded = useRef(false);

  const support = useOrderLineSupport({ role });
  const partners = usePartners(isSales ? "ar" : "ap", {});
  const terms = usePaymentTerms();
  const reps = useSalesReps();
  const projects = useProjects();
  const createSales = useCreateSalesOrder();
  const updateSales = useUpdateSalesOrder();
  const createPurchase = useCreatePurchaseOrder();
  const updatePurchase = useUpdatePurchaseOrder();
  const pending =
    createSales.isPending ||
    updateSales.isPending ||
    createPurchase.isPending ||
    updatePurchase.isPending;

  const salesOrder = useSalesOrder(editing && isSales ? orderId : null);
  const purchaseOrder = usePurchaseOrder(editing && !isSales ? orderId : null);
  const existing = isSales ? salesOrder.data : purchaseOrder.data;

  // --- seeding: a saved order once, or a restored draft once ------------------------------
  useEffect(() => {
    if (!editing || loaded.current || !existing) return;
    loaded.current = true;
    // Kit components are derived lines. They are shown on the detail screen and edited through
    // Breakup; offering them here as rows would invite an edit the service would only explode
    // away again.
    const parents = existing.lines.filter(
      (line) => !("kit_parent_line_id" in line) || line.kit_parent_line_id === null,
    );
    const rows = parents.map((line) =>
      emptyLineGridRow({
        id: `${SEEDED}${line.id}`,
        itemId: String(line.item_id),
        warehouseId: String(line.warehouse_id),
        uomId: String(line.uom_id),
        description: line.description ?? "",
        quantity: trimDecimalString(line.quantity),
        unitPrice: trimDecimalString(line.unit_price),
        discountPercent: trimDecimalString(line.discount_percent),
        taxCodeId: line.tax_code_id ? String(line.tax_code_id) : "",
        projectId: line.project_id ? String(line.project_id) : "",
      }),
    );
    seeded.current = Object.fromEntries(rows.map((row) => [row.id, quantityFingerprint(row)]));
    setForm({
      id: newDraftId(),
      partnerId: String(existing.partner_id),
      orderDate: existing.order_date,
      expectedDate: existing.expected_date ?? "",
      reference: existing.reference ?? "",
      description: existing.description,
      warehouseId: String(existing.warehouse_id),
      paymentTermsId:
        "payment_terms_id" in existing && existing.payment_terms_id
          ? String(existing.payment_terms_id)
          : "",
      salesRepId:
        "sales_rep_id" in existing && existing.sales_rep_id ? String(existing.sales_rep_id) : "",
      taxMode: existing.tax_mode,
      rows,
    });
  }, [editing, existing]);

  useEffect(() => {
    if (editing || restored.current || !companyId || !userId) return;
    restored.current = true;
    const saved = loadDraft<OrderDraft>(draftModule, companyId, userId);
    if (saved) setForm(saved.data);
  }, [editing, companyId, userId, draftModule]);

  useEffect(() => {
    if (editing || !restored.current || !companyId || !userId) return;
    saveDraft(draftModule, companyId, userId, {
      draftId: form.id,
      updatedAt: nowIso(),
      data: form,
    });
  }, [editing, form, companyId, userId, draftModule]);

  // The inventory default, until the operator says otherwise. Sales and purchase orders both
  // take it (decision 10); the line grid then defaults each row from the header.
  const defaultWarehouse = support.warehouses.find((w) => w.is_active && !w.is_in_transit);
  useEffect(() => {
    if (editing || form.warehouseId || !defaultWarehouse) return;
    setForm((prev) =>
      prev.warehouseId ? prev : { ...prev, warehouseId: String(defaultWarehouse.id) },
    );
  }, [editing, defaultWarehouse, form.warehouseId]);

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

  const filled = useMemo(
    () => form.rows.filter((row) => row.itemId && row.quantity),
    [form.rows],
  );

  /** The kit lines this save would re-explode: broken up by hand, and moved. Empty on a new
   * order, because nothing has been broken up yet. */
  const wouldReset = useMemo(() => {
    if (!editing || !existing) return [] as LineGridRow[];
    const edited = new Set(
      existing.lines
        .filter((line) => "kit_breakup_edited" in line && line.kit_breakup_edited)
        .map((line) => line.id),
    );
    return filled.filter((row) => {
      const id = lineIdOf(row);
      return (
        id !== null && edited.has(id) && seeded.current[row.id] !== quantityFingerprint(row)
      );
    });
  }, [editing, existing, filled]);

  const canSave = Boolean(form.partnerId) && Boolean(form.description) && filled.length > 0 && !pending;

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
          unitPrice: support.cataloguePrice(row.itemId),
          taxCodeId: support.defaultTaxCode(row.itemId),
          warehouseId: row.warehouseId || prev.warehouseId,
        };
      }),
    }));
  }

  function payloadLines(resetBreakup: boolean): OrderLinePayload[] {
    return filled.map((row) => {
      const lineId = lineIdOf(row);
      const moved = lineId !== null && seeded.current[row.id] !== quantityFingerprint(row);
      return {
        item_id: Number(row.itemId),
        quantity: row.quantity,
        line_id: lineId,
        uom_id: row.uomId ? Number(row.uomId) : null,
        unit_price: row.unitPrice ? row.unitPrice : null,
        discount_percent: row.discountPercent || "0",
        tax_code_id: row.taxCodeId ? Number(row.taxCodeId) : null,
        warehouse_id: row.warehouseId ? Number(row.warehouseId) : null,
        project_id: row.projectId ? Number(row.projectId) : null,
        description: row.description || null,
        // Consent is per line and only where it is needed: saying yes to re-exploding the two
        // packs whose quantity changed must not quietly re-explode a third that did not.
        ...(resetBreakup && moved ? { reset_breakup: true } : {}),
      };
    });
  }

  async function save(resetBreakup: boolean) {
    if (!canSave) return;
    setBanner(null);
    setFieldErrors({});
    const lines = payloadLines(resetBreakup);
    const base = {
      partner_id: Number(form.partnerId),
      order_date: form.orderDate,
      description: form.description,
      expected_date: form.expectedDate || null,
      reference: form.reference || null,
      warehouse_id: form.warehouseId ? Number(form.warehouseId) : null,
      tax_mode: form.taxMode,
      lines,
    };
    const salesExtras = {
      payment_terms_id: form.paymentTermsId ? Number(form.paymentTermsId) : null,
      sales_rep_id: form.salesRepId ? Number(form.salesRepId) : null,
    };
    try {
      let saved;
      if (editing && isSales) {
        saved = await updateSales.mutateAsync({
          orderId: orderId!,
          payload: { ...base, ...salesExtras },
        });
      } else if (editing) {
        saved = await updatePurchase.mutateAsync({ orderId: orderId!, payload: base });
      } else if (isSales) {
        saved = await createSales.mutateAsync({
          idempotencyKey: form.id,
          payload: { ...base, ...salesExtras },
        });
      } else {
        saved = await createPurchase.mutateAsync({ idempotencyKey: form.id, payload: base });
      }
      if (!editing) clearDraft(draftModule, companyId, userId);
      setResetAsk(null);
      toast.show({ title: t("saved", { number: saved.number }), tone: "success" });
      router.push(`${listHref}/${saved.id}`);
    } catch (err) {
      if (isApiError(err)) {
        // The one refusal this screen can answer: the service is not saying no, it is asking
        // whether the hand-edited explosion may go.
        if (err.code === "kit_breakup_would_reset") {
          setResetAsk({
            labels: wouldReset.map((row) => support.itemLabel(row.itemId)),
            message: err.message,
          });
          return;
        }
        setFieldErrors(err.fieldErrors);
        setBanner(err.message);
      } else {
        showApiError(err, t("saveFailed"));
      }
    }
  }

  useDocumentShortcuts({
    onPost: () => save(false),
    onCancel: () => router.push(listHref),
    canPost: canSave,
  });

  return (
    <DocumentWorkspaceShell
      backHref={listHref}
      title={editing ? t("editTitle", { number: existing?.number ?? "" }) : t("newTitle")}
      subtitle={t("subtitle")}
      statusChip={<StatusChip tone="neutral">{editing ? tc("editing") : tc("draft")}</StatusChip>}
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
            <Button variant="primary" disabled={!canSave} onClick={() => save(false)}>
              {pending ? tc("saving") : t("save")}
            </Button>
          </div>
        </div>
      }
    >
      <section className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-3">
        <Field label={isSales ? tc("customer") : tc("supplier")} error={fieldErrors.partner_id?.[0]}>
          <Combobox
            options={(partners.data ?? []).map((partner) => ({
              value: String(partner.id),
              label: dotted(partnerCode(partner, isSales ? "ar" : "ap"), partner.name),
            }))}
            value={form.partnerId}
            onValueChange={(v) => setForm({ ...form, partnerId: v })}
            placeholder={t("choosePartner")}
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
        {isSales && (
          <>
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
          </>
        )}
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
          <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("linesNote")}</p>
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

      <Dialog open={resetAsk !== null} onOpenChange={(open) => !open && setResetAsk(null)}>
        <DialogContent title={t("resetTitle")} description={t("resetNote")}>
          <div className="space-y-3 pt-2">
            {/* Naming the lines is the point of asking at all. If this screen's reading of
                which ones moved comes back empty, the service's own message says which line
                and from what quantity to what — an empty list would be a question with
                nothing in it. */}
            {resetAsk && resetAsk.labels.length > 0 ? (
              <ul
                className="list-disc space-y-1 pl-5 text-xs text-[var(--vinea-ink)]"
                data-testid="reset-breakup-items"
              >
                {resetAsk.labels.map((label) => (
                  <li key={label}>{label}</li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-[var(--vinea-ink)]" data-testid="reset-breakup-items">
                {resetAsk?.message}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setResetAsk(null)}>
                {t("resetKeep")}
              </Button>
              <Button variant="primary" onClick={() => save(true)} data-testid="reset-breakup-confirm">
                {t("resetConfirm")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </DocumentWorkspaceShell>
  );
}
