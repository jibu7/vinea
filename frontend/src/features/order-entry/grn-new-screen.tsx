"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
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
import { StatusChip } from "@/design/components/status-chip";
import { useToast } from "@/design/components/toast";
import { isApiError, useMe } from "@/features/auth/hooks";
import { useCurrencies, useProjects } from "@/features/gl/hooks";
import { toOptions } from "@/features/gl/lookups";
import { useInventoryLineSupport, useOnHandByWarehouse } from "@/features/inventory/line-support";
import { usePartners } from "@/features/subledger/hooks";
import { partnerCode } from "@/features/subledger/types";
import { clearDraft, loadDraft, newDraftId, saveDraft } from "@/lib/drafts";
import { dotted, formatMoney, formatQuantity, nowIso, todayIso, trimDecimalString } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useCreateGrn, useReceiveFromPurchaseOrder } from "./hooks";
import type { GrnLinePayload, PreparedGrnLine } from "./types";

const DRAFT_MODULE = "oe.goods-received";

interface GrnDraft {
  id: string;
  partnerId: string;
  grnDate: string;
  warehouseId: string;
  supplierReference: string;
  description: string;
  purchaseOrderId: string;
  rows: LineGridRow[];
}

function blankDraft(): GrnDraft {
  return {
    id: newDraftId(),
    partnerId: "",
    grnDate: todayIso(),
    warehouseId: "",
    supplierReference: "",
    description: "",
    purchaseOrderId: "",
    rows: [emptyLineGridRow(), emptyLineGridRow(), emptyLineGridRow()],
  };
}

/** The order line a row came from, parked in the row's grid id. A receipt that has forgotten
 * which order line it fills would post the accrual and leave the order open for ever. */
const FROM_ORDER = "po-line:";

function orderLineIdOf(row: LineGridRow): number | null {
  return row.id.startsWith(FROM_ORDER) ? Number(row.id.slice(FROM_ORDER.length)) : null;
}

/**
 * A goods receipt — the first half of Evolution's two-step (decision 6).
 *
 * **This is the document that posts**, and the only one in the order-to-receipt chain that
 * does: stock in at what the delivery note says it cost, and the other side to *Goods received
 * not invoiced* — a control account whose balance is then provably the value of everything
 * received and not yet invoiced. The supplier invoice comes second and relieves that accrual;
 * any disagreement on price goes to purchase price variance, not into the stock value.
 *
 * Two ways in, one screen. **From a purchase order** the service prepares the open stock lines
 * and each row says what is still outstanding, so receiving 5 of 20 needs one number changed
 * rather than a whole document keyed. **Standalone**, for goods that arrived without a
 * purchase order having been raised — which happens, and refusing to record it would only move
 * the stock off the system.
 *
 * Quantities may be lowered and rows removed; raising one past what the order has left is
 * refused by the service, on the line, because that is where the order's remaining quantity is
 * actually known.
 */
export function GrnNewScreen() {
  const t = useTranslations("orderEntry.goodsReceipt");
  const tc = useTranslations("orderEntry.common");
  const router = useRouter();
  const searchParams = useSearchParams();
  const purchaseOrderId = searchParams.get("purchase_order_id");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const { data: me } = useMe();
  const companyId = me?.company?.id ?? 0;
  const userId = me?.user_id ?? 0;

  const [form, setForm] = useState<GrnDraft>(blankDraft);
  const [outstanding, setOutstanding] = useState<PreparedGrnLine[]>([]);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const prepared = useRef(false);
  const restored = useRef(false);

  const support = useInventoryLineSupport();
  const partners = usePartners("ap", {});
  const projects = useProjects();
  const currencies = useCurrencies();
  const createGrn = useCreateGrn();
  const receive = useReceiveFromPurchaseOrder();

  // --- from a purchase order: ask the service what is still open, once --------------------
  useEffect(() => {
    if (!purchaseOrderId || prepared.current) return;
    prepared.current = true;
    receive
      .mutateAsync(Number(purchaseOrderId))
      .then((grn) => {
        setOutstanding(grn.lines);
        setForm({
          id: newDraftId(),
          partnerId: String(grn.partner_id),
          grnDate: grn.grn_date,
          warehouseId: grn.warehouse_id ? String(grn.warehouse_id) : "",
          supplierReference: grn.supplier_reference ?? "",
          description: grn.description,
          purchaseOrderId: String(grn.purchase_order_id ?? purchaseOrderId),
          rows: grn.lines.map((line) =>
            emptyLineGridRow({
              id: `${FROM_ORDER}${line.purchase_order_line_id ?? 0}`,
              itemId: String(line.item_id),
              warehouseId: line.warehouse_id ? String(line.warehouse_id) : "",
              quantity: trimDecimalString(line.quantity),
              unitCost: trimDecimalString(line.unit_cost),
              description: line.description ?? "",
              projectId: line.project_id ? String(line.project_id) : "",
            }),
          ),
        });
      })
      .catch((err) => {
        if (isApiError(err)) setBanner(err.message);
        else showApiError(err, t("prepareFailed"));
      });
  }, [purchaseOrderId, receive, showApiError, t]);

  // A standalone receipt keeps a draft; one prepared from an order does not — the order is the
  // record of what is owed, and a stale copy of it restored days later is worse than nothing.
  useEffect(() => {
    if (purchaseOrderId || restored.current || !companyId || !userId) return;
    restored.current = true;
    const saved = loadDraft<GrnDraft>(DRAFT_MODULE, companyId, userId);
    if (saved) setForm(saved.data);
  }, [purchaseOrderId, companyId, userId]);

  useEffect(() => {
    if (purchaseOrderId || !restored.current || !companyId || !userId) return;
    saveDraft(DRAFT_MODULE, companyId, userId, {
      draftId: form.id,
      updatedAt: nowIso(),
      data: form,
    });
  }, [purchaseOrderId, form, companyId, userId]);

  const defaultWarehouse = support.warehouses.find((w) => w.is_active && !w.is_in_transit);
  useEffect(() => {
    if (purchaseOrderId || form.warehouseId || !defaultWarehouse) return;
    setForm((prev) =>
      prev.warehouseId ? prev : { ...prev, warehouseId: String(defaultWarehouse.id) },
    );
  }, [purchaseOrderId, defaultWarehouse, form.warehouseId]);

  const warehouseOfRow = (row: LineGridRow) =>
    Number(row.warehouseId || form.warehouseId || 0) || 0;
  const onHand = useOnHandByWarehouse(
    form.rows.map(warehouseOfRow).concat(Number(form.warehouseId) || 0),
  );
  const headerOnHand = onHand.get(Number(form.warehouseId) || 0);
  const itemOptions = useMemo(
    () => support.itemOptions(headerOnHand),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- support's functions are stable per its data
    [support.stockItems, support.uomById, headerOnHand],
  );

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
  /** The value the operator has keyed, before the server costs it: a receipt is the one stock
   * document whose value *is* what was typed, because a delivery note states it. */
  const keyedValue = filled.reduce(
    (sum, row) => sum + Number(row.unitCost || 0) * Number(row.quantity || 0),
    0,
  );
  const canPost =
    Boolean(form.partnerId) && Boolean(form.description) && filled.length > 0 && !createGrn.isPending;

  /** The currency the unit costs are being keyed in: the supplier's if they have one, the
   * company's otherwise — which is exactly how the service resolves it when the body leaves
   * `currency_id` out. Printing this figure with the base currency's symbol while a euro cost
   * was typed is the P4 defect this is written to avoid. */
  const supplier = (partners.data ?? []).find((p) => String(p.id) === form.partnerId);
  const currency = useMemo(() => {
    const rows = currencies.data ?? [];
    const found =
      rows.find((c) => c.id === supplier?.currency_id) ?? rows.find((c) => c.is_base);
    return {
      code: found?.code ?? "",
      decimalPlaces: found?.decimal_places ?? 0,
      symbol: found?.symbol ?? null,
    };
  }, [currencies.data, supplier?.currency_id]);

  async function handlePost() {
    if (!canPost) return;
    setBanner(null);
    setFieldErrors({});
    const lines: GrnLinePayload[] = filled.map((row) => ({
      item_id: Number(row.itemId),
      quantity: row.quantity,
      unit_cost: row.unitCost || "0",
      uom_id: row.uomId ? Number(row.uomId) : null,
      warehouse_id: row.warehouseId ? Number(row.warehouseId) : null,
      description: row.description || null,
      project_id: row.projectId ? Number(row.projectId) : null,
      purchase_order_line_id: orderLineIdOf(row),
    }));
    try {
      const grn = await createGrn.mutateAsync({
        idempotencyKey: form.id,
        payload: {
          partner_id: Number(form.partnerId),
          grn_date: form.grnDate,
          description: form.description,
          warehouse_id: form.warehouseId ? Number(form.warehouseId) : null,
          purchase_order_id: form.purchaseOrderId ? Number(form.purchaseOrderId) : null,
          supplier_reference: form.supplierReference || null,
          lines,
        },
      });
      if (!purchaseOrderId) clearDraft(DRAFT_MODULE, companyId, userId);
      toast.show({ title: t("posted", { number: grn.number }), tone: "success" });
      router.push(`/oe/goods-received/${grn.id}`);
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
    onCancel: () => router.push("/oe/goods-received"),
    canPost,
  });

  return (
    <DocumentWorkspaceShell
      backHref="/oe/goods-received"
      title={t("title")}
      subtitle={purchaseOrderId ? t("fromOrderSubtitle") : t("subtitle")}
      statusChip={<StatusChip tone="neutral">{tc("draft")}</StatusChip>}
      errorBanner={banner}
      footer={
        <div className="flex items-center justify-between gap-6">
          <span className="text-sm text-[var(--vinea-ink-muted)]">
            {t("keyedValue")}{" "}
            <span
              className="font-mono tabular-nums text-[var(--vinea-ink)]"
              data-testid="grn-keyed-value"
            >
              {formatMoney(keyedValue, currency)}
            </span>
          </span>
          <div className="flex items-center gap-3">
            <p className="max-w-md text-xs text-[var(--vinea-ink-subtle)]">{t("postsNote")}</p>
            <Button variant="primary" disabled={!canPost} onClick={handlePost}>
              {createGrn.isPending ? tc("saving") : t("post")}
            </Button>
          </div>
        </div>
      }
    >
      <section className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-3">
        <Field label={tc("supplier")} error={fieldErrors.partner_id?.[0]}>
          {purchaseOrderId ? (
            // Not a disabled picker: the receipt belongs to the order's supplier and there is
            // nothing to choose, so it reads as a fact rather than as a control somebody has
            // to work out how to unlock.
            <p className="flex h-10 items-center text-sm font-medium text-[var(--vinea-ink)]">
              {supplier ? dotted(partnerCode(supplier, "ap"), supplier.name) : tc("loading")}
            </p>
          ) : (
            <Combobox
              options={(partners.data ?? []).map((partner) => ({
                value: String(partner.id),
                label: dotted(partnerCode(partner, "ap"), partner.name),
              }))}
              value={form.partnerId}
              onValueChange={(v) => setForm({ ...form, partnerId: v })}
              placeholder={t("chooseSupplier")}
            />
          )}
        </Field>
        <Field label={t("grnDate")} error={fieldErrors.grn_date?.[0]}>
          <IsoDatePicker
            value={form.grnDate}
            onValueChange={(v) => setForm({ ...form, grnDate: v })}
          />
        </Field>
        <Field label={tc("warehouse")} error={fieldErrors.warehouse_id?.[0]}>
          <Combobox
            options={support.warehouseOptions}
            value={form.warehouseId}
            onValueChange={(v) => setForm({ ...form, warehouseId: v })}
            placeholder={t("chooseWarehouse")}
          />
        </Field>
        <Field label={t("supplierReference")} error={fieldErrors.supplier_reference?.[0]}>
          <Input
            value={form.supplierReference}
            onChange={(e) => setForm({ ...form, supplierReference: e.target.value })}
            placeholder={t("supplierReferencePlaceholder")}
          />
        </Field>
        <Field
          label={tc("description")}
          error={fieldErrors.description?.[0]}
          className="sm:col-span-2"
        >
          <Input
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            placeholder={t("descriptionPlaceholder")}
          />
        </Field>
      </section>

      {outstanding.length > 0 && (
        <section className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-sunken)]/40 p-4">
          <h2 className="text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
            {t("outstandingTitle")}
          </h2>
          <ul className="mt-2 space-y-1" data-testid="grn-outstanding">
            {outstanding.map((line, index) => (
              <li key={index} className="flex justify-between text-xs text-[var(--vinea-ink)]">
                <span>{support.itemById.get(line.item_id)?.code ?? line.item_id}</span>
                <span className="font-mono tabular-nums">
                  {t("stillOutstanding", {
                    quantity: formatQuantity(
                      Number(line.remaining),
                      support.uomById.get(support.itemById.get(line.item_id)?.base_uom_id ?? 0)
                        ?.decimal_places ?? 0,
                    ),
                  })}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-[var(--vinea-ink-subtle)]">{t("outstandingNote")}</p>
        </section>
      )}

      <section className="space-y-2">
        <h2 className="text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
          {t("lines")}
        </h2>
        <LineGrid
          mode="inventory"
          rows={form.rows}
          onRowsChange={(rows) => setForm({ ...form, rows })}
          errors={lineErrors}
          accountOptions={[]}
          inventoryColumns={{
            warehouse: true,
            transactionType: false,
            unitCost: true,
            value: false,
            contra: false,
          }}
          itemOptions={itemOptions}
          warehouseOptions={support.warehouseOptions}
          uomOptionsFor={support.uomOptionsFor}
          conversionFor={support.conversionFor}
          onHandFor={(row) => {
            if (!row.itemId || !warehouseOfRow(row)) return undefined;
            const held = onHand.get(warehouseOfRow(row))?.get(Number(row.itemId));
            const base = support.baseUomOf(row);
            return `${support.formatBase(Number(row.itemId), held?.quantity ?? "0")}${
              base ? ` ${base.code}` : ""
            }`;
          }}
          projectOptions={toOptions(projects.data ?? [], (p) => dotted(p.code, p.name))}
          rowDefaults={{ warehouseId: form.warehouseId }}
        />
      </section>
    </DocumentWorkspaceShell>
  );
}
