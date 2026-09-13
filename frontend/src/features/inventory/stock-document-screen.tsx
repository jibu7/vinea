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
import { StatusChip } from "@/design/components/status-chip";
import { useToast } from "@/design/components/toast";
import { isApiError, useMe } from "@/features/auth/hooks";
import { useAccounts, useCurrencies, useProjects } from "@/features/gl/hooks";
import { toOptions } from "@/features/gl/lookups";
import { InventoryTransactionKind } from "@/lib/api-enums";
import { clearDraft, loadDraft, newDraftId, saveDraft } from "@/lib/drafts";
import { formatMoney } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { usePostAdjustment, usePostJournalBatch } from "./hooks";
import { useInventoryLineSupport, useOnHandByWarehouse } from "./line-support";
import type { StockDocumentLinePayload, StockDocumentPayload } from "./types";

export type StockDocumentVariant = "adjustment" | "batch";

interface StockDocumentDraft {
  documentDate: string;
  transactionTypeId: string;
  warehouseId: string;
  reference: string;
  description: string;
  projectId: string;
  rows: LineGridRow[];
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function blankDraft(): StockDocumentDraft {
  return {
    documentDate: today(),
    transactionTypeId: "",
    warehouseId: "",
    reference: "",
    description: "",
    projectId: "",
    rows: [emptyLineGridRow()],
  };
}

const INCREASE_KINDS: ReadonlySet<string> = new Set([
  InventoryTransactionKind.ADJUSTMENT_IN,
  InventoryTransactionKind.OPENING_BALANCE,
]);

/**
 * The two stock documents an operator keys by hand.
 *
 * An **adjustment** names its type and warehouse once, in the header, and its lines are items
 * and quantities: the grid hides the per-line warehouse and type columns because every line
 * shares the header's. A **journal batch** is the same grid with those columns shown — many
 * lines, each its own type and warehouse, the header's values only the defaults a new line
 * starts with. Both post through the same service; the batch is one unit of work.
 *
 * Nothing here computes a cost. An increase carries the unit cost the operator keyed, a
 * decrease is costed at the warehouse average on Post, a revaluation states a value — and the
 * footer's figure is labelled an estimate for that reason. The ledger's figure is the one on
 * the journal entry this screen lands on.
 */
export function StockDocumentScreen({ variant }: { variant: StockDocumentVariant }) {
  const ta = useTranslations("inventory.adjustments");
  const tb = useTranslations("inventory.journalBatches");
  const td = useTranslations("inventory.documents");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const isAdjustment = variant === "adjustment";

  const { data: me } = useMe();
  const companyId = me?.company?.id ?? 0;
  const userId = me?.user_id ?? 0;
  const draftModule = isAdjustment ? "inventory.adjustment" : "inventory.journal-batch";

  const [draftId, setDraftId] = useState<string>(newDraftId);
  const [form, setForm] = useState<StockDocumentDraft>(blankDraft);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const restored = useRef(false);

  const support = useInventoryLineSupport();
  const accounts = useAccounts();
  const projects = useProjects();
  const currencies = useCurrencies();
  const postAdjustment = usePostAdjustment();
  const postBatch = usePostJournalBatch();
  const posting = isAdjustment ? postAdjustment : postBatch;

  useEffect(() => {
    if (restored.current || !companyId || !userId) return;
    restored.current = true;
    const saved = loadDraft<StockDocumentDraft>(draftModule, companyId, userId);
    if (saved) {
      setDraftId(saved.draftId);
      setForm(saved.data);
      toast.show({ title: td("draftRestored"), tone: "neutral" });
    }
  }, [companyId, userId, draftModule, td, toast]);

  useEffect(() => {
    if (!restored.current || !companyId || !userId) return;
    saveDraft(draftModule, companyId, userId, {
      draftId,
      updatedAt: new Date().toISOString(),
      data: form,
    });
  }, [form, draftId, companyId, userId, draftModule]);

  // The default warehouse is the first thing the header needs and the one thing the seed
  // pack guarantees; offering it saves a pick on every document.
  useEffect(() => {
    if (form.warehouseId || support.warehouses.length === 0) return;
    const preferred = support.warehouses.find((w) => w.is_default && !w.is_in_transit);
    if (preferred) setForm((f) => (f.warehouseId ? f : { ...f, warehouseId: String(preferred.id) }));
  }, [form.warehouseId, support.warehouses]);

  const baseCurrency = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: baseCurrency?.code ?? "",
    decimalPlaces: baseCurrency?.decimal_places ?? 0,
    symbol: baseCurrency?.symbol ?? null,
  };

  // A batch line that names no warehouse or type takes the header's — the same rule the
  // server applies to `transaction_type_id`, and what a blank cell means on this grid.
  const warehouseOfRow = (row: LineGridRow): number =>
    Number(isAdjustment ? form.warehouseId : row.warehouseId || form.warehouseId) || 0;
  const typeOfRow = (row: LineGridRow): string =>
    isAdjustment ? form.transactionTypeId : row.transactionTypeId || form.transactionTypeId;
  const kindOfRow = (row: LineGridRow) => support.kindOf(typeOfRow(row));

  /** Changing a header default fills the cells that were still blank, so what a line will
   * post with is what its row shows. Cells the operator set are left alone. */
  function withDefault(field: "warehouseId" | "transactionTypeId", value: string): LineGridRow[] {
    if (isAdjustment) return form.rows;
    return form.rows.map((row) => (row[field] ? row : { ...row, [field]: value }));
  }

  const onHand = useOnHandByWarehouse(form.rows.map(warehouseOfRow).concat(Number(form.warehouseId) || 0));
  const headerOnHand = onHand.get(Number(form.warehouseId) || 0);
  const itemOptions = useMemo(
    () => support.itemOptions(headerOnHand),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- support's functions are stable per its data
    [support.stockItems, support.uomById, headerOnHand],
  );

  const postableAccounts = useMemo(
    () => (accounts.data ?? []).filter((a) => a.is_postable && !a.is_control && a.is_active),
    [accounts.data],
  );

  const headerKind = support.kindOf(form.transactionTypeId);
  const filled = form.rows.filter(
    (row) =>
      row.itemId &&
      (kindOfRow(row) === InventoryTransactionKind.REVALUATION ? row.value : row.quantity) &&
      (isAdjustment || typeOfRow(row)),
  );

  /** What the operator has keyed, before the server costs it: unit cost × quantity on the
   * increases, the stated value on a revaluation. Decreases contribute nothing here — their
   * cost is the average at posting, which this screen does not know. */
  const estimatedValue = filled.reduce((sum, row) => {
    const kind = kindOfRow(row);
    if (kind === InventoryTransactionKind.REVALUATION) return sum + Number(row.value || 0);
    if (kind !== null && INCREASE_KINDS.has(kind)) {
      return sum + Number(row.unitCost || 0) * Number(row.quantity || 0);
    }
    return sum;
  }, 0);
  const increases = filled.filter((row) => {
    const kind = kindOfRow(row);
    return kind !== null && INCREASE_KINDS.has(kind);
  }).length;
  const decreases = filled.filter((row) => kindOfRow(row) === InventoryTransactionKind.ADJUSTMENT_OUT).length;

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

  /** An adjustment's warehouse and type live on the header, so a refusal the server keyed
   * to a line's copy of them is shown where the operator can change it. */
  function headerError(field: "warehouse_id" | "transaction_type_id"): string | undefined {
    const direct = fieldErrors[field]?.[0];
    if (direct || !isAdjustment) return direct;
    const onLine = Object.entries(fieldErrors).find(([key]) => key.endsWith(`.${field}`));
    return onLine?.[1][0];
  }

  const canPost =
    filled.length > 0 &&
    !posting.isPending &&
    (!isAdjustment || (Boolean(form.transactionTypeId) && Boolean(form.warehouseId)));

  async function handlePost() {
    if (!canPost) return;
    setBanner(null);
    setFieldErrors({});
    const lines: StockDocumentLinePayload[] = filled.map((row) => {
      const kind = kindOfRow(row);
      const revaluation = kind === InventoryTransactionKind.REVALUATION;
      const increase = kind !== null && INCREASE_KINDS.has(kind);
      return {
        item_id: Number(row.itemId),
        warehouse_id: warehouseOfRow(row),
        quantity: revaluation ? undefined : row.quantity,
        uom_id: row.uomId ? Number(row.uomId) : null,
        unit_cost: increase && row.unitCost ? row.unitCost : null,
        value: revaluation ? row.value : null,
        transaction_type_id: isAdjustment ? null : Number(typeOfRow(row)),
        contra_account_id: row.accountId ? Number(row.accountId) : null,
        project_id: row.projectId ? Number(row.projectId) : form.projectId ? Number(form.projectId) : null,
        description: row.description || null,
      };
    });
    const payload: StockDocumentPayload = {
      document_date: form.documentDate,
      description: form.description || (isAdjustment ? ta("title") : tb("title")),
      reference: form.reference || null,
      transaction_type_id: form.transactionTypeId ? Number(form.transactionTypeId) : null,
      lines,
    };
    try {
      const document = await posting.mutateAsync({ payload, idempotencyKey: draftId });
      clearDraft(draftModule, companyId, userId);
      toast.show({ title: td("postedToast", { number: document.number }), tone: "success" });
      if (isAdjustment && document.journal_entry_id !== null) {
        router.push(`/gl/entries/${document.journal_entry_id}`);
        return;
      }
      // A batch is many lines with no single figure to land on, and an adjustment that
      // moved no value has no entry: stay put with a fresh draft, the toast names the number.
      setForm({ ...blankDraft(), warehouseId: form.warehouseId, transactionTypeId: form.transactionTypeId });
      setDraftId(newDraftId());
    } catch (err) {
      if (isApiError(err)) {
        setFieldErrors(err.fieldErrors);
        setBanner(err.message);
      } else {
        showApiError(err, td("postFailed"));
      }
    }
  }

  useDocumentShortcuts({ onPost: handlePost, onCancel: () => router.push("/"), canPost });

  const projectOptions = toOptions(projects.data ?? [], (p) => `${p.code} · ${p.name}`);

  return (
    <DocumentWorkspaceShell
      backHref="/"
      title={isAdjustment ? ta("title") : tb("title")}
      subtitle={isAdjustment ? ta("subtitle") : tb("subtitle")}
      statusChip={<StatusChip tone="neutral">{td("draft")}</StatusChip>}
      errorBanner={banner}
      footer={
        <div className="flex items-center justify-between gap-6">
          <div className="flex items-center gap-6 text-sm">
            <span className="text-[var(--vinea-ink-muted)]">
              {td("lines")}{" "}
              <span className="font-mono tabular-nums text-[var(--vinea-ink)]" data-testid="footer-lines">
                {filled.length}
              </span>
            </span>
            {!isAdjustment && (
              <>
                <span className="text-[var(--vinea-ink-muted)]">
                  {tb("footerIncreases")}{" "}
                  <span className="font-mono tabular-nums text-[var(--vinea-ink)]">{increases}</span>
                </span>
                <span className="text-[var(--vinea-ink-muted)]">
                  {tb("footerDecreases")}{" "}
                  <span className="font-mono tabular-nums text-[var(--vinea-ink)]">{decreases}</span>
                </span>
              </>
            )}
            <span className="text-[var(--vinea-ink-muted)]">
              {td("value")}{" "}
              <span className="font-mono tabular-nums text-[var(--vinea-ink)]" data-testid="footer-value">
                {formatMoney(estimatedValue, baseLike)}
              </span>
            </span>
          </div>
          <div className="flex items-center gap-3">
            <p className="max-w-md text-xs text-[var(--vinea-ink-subtle)]">
              {isAdjustment ? ta("valueNote") : tb("atomicNote")}
            </p>
            <Button variant="primary" disabled={!canPost} onClick={handlePost}>
              {posting.isPending ? td("posting") : td("post")}
            </Button>
          </div>
        </div>
      }
    >
      <section className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-3">
        <Field label={td("date")} error={fieldErrors.document_date?.[0]}>
          <IsoDatePicker
            value={form.documentDate}
            onValueChange={(v) => setForm({ ...form, documentDate: v })}
          />
        </Field>
        <Field
          label={isAdjustment ? td("transactionType") : tb("defaultType")}
          error={headerError("transaction_type_id")}
        >
          <Combobox
            options={support.typeOptions}
            value={form.transactionTypeId}
            onValueChange={(v) => setForm({ ...form, transactionTypeId: v, rows: withDefault("transactionTypeId", v) })}
            placeholder={td("chooseTransactionType")}
          />
        </Field>
        <Field
          label={isAdjustment ? td("warehouse") : tb("defaultWarehouse")}
          error={headerError("warehouse_id")}
        >
          <Combobox
            options={support.warehouseOptions}
            value={form.warehouseId}
            onValueChange={(v) => setForm({ ...form, warehouseId: v, rows: withDefault("warehouseId", v) })}
            placeholder={td("chooseWarehouse")}
          />
        </Field>
        <Field label={td("reference")}>
          <Input
            value={form.reference}
            onChange={(e) => setForm({ ...form, reference: e.target.value })}
          />
        </Field>
        <Field label={td("description")} error={fieldErrors.description?.[0]}>
          <Input
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            placeholder={isAdjustment ? ta("descriptionPlaceholder") : tb("descriptionPlaceholder")}
          />
        </Field>
        <Field label={td("project")}>
          <Combobox
            options={[{ value: "", label: td("none") }, ...projectOptions]}
            value={form.projectId}
            onValueChange={(v) => setForm({ ...form, projectId: v })}
            placeholder={td("none")}
          />
        </Field>
      </section>

      <section className="space-y-2">
        <div className="flex items-baseline justify-between">
          <h2 className="text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
            {td("lines")}
          </h2>
          <p className="text-xs text-[var(--vinea-ink-subtle)]">
            {isAdjustment
              ? headerKind === InventoryTransactionKind.REVALUATION
                ? ta("revaluationNote")
                : ta("insufficientHint")
              : tb("unitCostHint")}
          </p>
        </div>
        <LineGrid
          mode="inventory"
          rows={form.rows}
          onRowsChange={(rows) => setForm({ ...form, rows })}
          errors={lineErrors}
          rowDefaults={isAdjustment ? {} : { warehouseId: form.warehouseId, transactionTypeId: form.transactionTypeId }}
          inventoryColumns={isAdjustment ? { warehouse: false, transactionType: false } : undefined}
          itemOptions={itemOptions}
          warehouseOptions={support.warehouseOptions}
          transactionTypeOptions={support.typeOptions}
          uomOptionsFor={support.uomOptionsFor}
          conversionFor={support.conversionFor}
          lineKindFor={kindOfRow}
          onHandFor={(row) => {
            const held = onHand.get(warehouseOfRow(row))?.get(Number(row.itemId));
            if (!row.itemId || !warehouseOfRow(row)) return undefined;
            const base = support.baseUomOf(row);
            return `${support.formatBase(Number(row.itemId), held?.quantity ?? "0")}${base ? ` ${base.code}` : ""}`;
          }}
          accountOptions={toOptions(postableAccounts, (a) => `${a.code} · ${a.name}`)}
          projectOptions={projectOptions}
        />
      </section>
    </DocumentWorkspaceShell>
  );
}
