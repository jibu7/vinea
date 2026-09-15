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
import { useProjects } from "@/features/gl/hooks";
import { toOptions } from "@/features/gl/lookups";
import { InventoryTransactionKind } from "@/lib/api-enums";
import { clearDraft, loadDraft, newDraftId, saveDraft } from "@/lib/drafts";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { usePostTransfer } from "./hooks";
import { useInventoryLineSupport, useOnHandByWarehouse } from "./line-support";
import type { TransferPayload } from "./types";
import { nowIso, todayIso } from "@/lib/format";

interface TransferDraft {
  transferDate: string;
  fromWarehouseId: string;
  toWarehouseId: string;
  reference: string;
  description: string;
  projectId: string;
  rows: LineGridRow[];
}

function blankDraft(): TransferDraft {
  return {
    transferDate: todayIso(),
    fromWarehouseId: "",
    toWarehouseId: "",
    reference: "",
    description: "",
    projectId: "",
    rows: [emptyLineGridRow()],
  };
}

const DRAFT_MODULE = "inventory.transfer";

/**
 * A new warehouse transfer. The two warehouses are on the header — a transfer is *from one
 * place to another*, not a list of moves that happen to pair up — so the grid runs with its
 * warehouse, type, cost and contra columns off: item, on hand at the source, quantity, unit.
 *
 * Two ways to post. **Transfer now** dispatches and receives in one transaction, for the van
 * that has already arrived. **Dispatch only** leaves the stock on the in-transit location
 * until somebody receives it from the transfers list. Both are the same document; the
 * difference is whether the second leg has happened yet.
 */
export function TransferNewScreen() {
  const t = useTranslations("inventory.transfers");
  const td = useTranslations("inventory.documents");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const { data: me } = useMe();
  const companyId = me?.company?.id ?? 0;
  const userId = me?.user_id ?? 0;

  const [draftId, setDraftId] = useState<string>(newDraftId);
  const [form, setForm] = useState<TransferDraft>(blankDraft);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const restored = useRef(false);

  const support = useInventoryLineSupport();
  const projects = useProjects();
  const postTransfer = usePostTransfer();

  useEffect(() => {
    if (restored.current || !companyId || !userId) return;
    restored.current = true;
    const saved = loadDraft<TransferDraft>(DRAFT_MODULE, companyId, userId);
    if (saved) {
      setDraftId(saved.draftId);
      setForm(saved.data);
      toast.show({ title: td("draftRestored"), tone: "neutral" });
    }
  }, [companyId, userId, td, toast]);

  useEffect(() => {
    if (!restored.current || !companyId || !userId) return;
    saveDraft(DRAFT_MODULE, companyId, userId, {
      draftId,
      updatedAt: nowIso(),
      data: form,
    });
  }, [form, draftId, companyId, userId]);

  const fromId = Number(form.fromWarehouseId) || 0;
  const onHand = useOnHandByWarehouse([fromId]);
  const sourceOnHand = onHand.get(fromId);
  const itemOptions = useMemo(
    () => support.itemOptions(sourceOnHand),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- support's functions are stable per its data
    [support.stockItems, support.uomById, sourceOnHand],
  );

  // Neither side may be the other, and neither may be the in-transit location — that one is
  // where the stock goes *between* them, and the options above already leave it out.
  const fromOptions = support.warehouseOptions.filter((o) => o.value !== form.toWarehouseId);
  const toOptionsList = support.warehouseOptions.filter((o) => o.value !== form.fromWarehouseId);

  const filled = form.rows.filter((row) => row.itemId && row.quantity);

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

  const sameWarehouse = Boolean(form.fromWarehouseId) && form.fromWarehouseId === form.toWarehouseId;
  const canPost =
    filled.length > 0 &&
    Boolean(form.fromWarehouseId) &&
    Boolean(form.toWarehouseId) &&
    !sameWarehouse &&
    !postTransfer.isPending;

  async function handlePost(receiveNow: boolean) {
    if (!canPost) return;
    setBanner(null);
    setFieldErrors({});
    const transferType = support.typeByKind(InventoryTransactionKind.TRANSFER);
    const payload: TransferPayload = {
      transfer_date: form.transferDate,
      description: form.description || t("newTitle"),
      reference: form.reference || null,
      from_warehouse_id: Number(form.fromWarehouseId),
      to_warehouse_id: Number(form.toWarehouseId),
      transaction_type_id: transferType?.id ?? null,
      project_id: form.projectId ? Number(form.projectId) : null,
      receive_now: receiveNow,
      lines: filled.map((row) => ({
        item_id: Number(row.itemId),
        quantity: row.quantity,
        uom_id: row.uomId ? Number(row.uomId) : null,
        description: row.description || null,
      })),
    };
    try {
      const transfer = await postTransfer.mutateAsync({ payload, idempotencyKey: draftId });
      clearDraft(DRAFT_MODULE, companyId, userId);
      toast.show({
        title: receiveNow ? t("transferred", { number: transfer.number }) : t("dispatched", { number: transfer.number }),
        tone: "success",
      });
      setForm(blankDraft());
      setDraftId(newDraftId());
      router.push("/inventory/transfers");
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
    onPost: () => handlePost(true),
    onCancel: () => router.push("/inventory/transfers"),
    canPost,
  });

  return (
    <DocumentWorkspaceShell
      backHref="/inventory/transfers"
      title={t("newTitle")}
      subtitle={t("subtitle")}
      statusChip={<StatusChip tone="neutral">{td("draft")}</StatusChip>}
      errorBanner={banner}
      footer={
        <div className="flex items-center justify-between gap-6">
          <div className="flex items-center gap-6 text-sm">
            <span className="text-[var(--vinea-ink-muted)]">
              {t("footerLines")}{" "}
              <span className="font-mono tabular-nums text-[var(--vinea-ink)]" data-testid="footer-lines">
                {filled.length}
              </span>
            </span>
          </div>
          <div className="flex items-center gap-3">
            <p className="max-w-md text-xs text-[var(--vinea-ink-subtle)]">{t("nowNote")}</p>
            <Button variant="secondary" disabled={!canPost} onClick={() => handlePost(false)}>
              {t("dispatchOnly")}
            </Button>
            <Button variant="primary" disabled={!canPost} onClick={() => handlePost(true)}>
              {postTransfer.isPending ? t("dispatching") : t("transferNow")}
            </Button>
          </div>
        </div>
      }
    >
      <section className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-3">
        <Field label={t("transferDate")} error={fieldErrors.transfer_date?.[0]}>
          <IsoDatePicker
            value={form.transferDate}
            onValueChange={(v) => setForm({ ...form, transferDate: v })}
          />
        </Field>
        <Field label={t("from")} error={fieldErrors.from_warehouse_id?.[0] ?? (sameWarehouse ? t("sameWarehouse") : undefined)}>
          <Combobox
            options={fromOptions}
            value={form.fromWarehouseId}
            onValueChange={(v) => setForm({ ...form, fromWarehouseId: v })}
            placeholder={t("chooseFrom")}
          />
        </Field>
        <Field label={t("to")} error={fieldErrors.to_warehouse_id?.[0]}>
          <Combobox
            options={toOptionsList}
            value={form.toWarehouseId}
            onValueChange={(v) => setForm({ ...form, toWarehouseId: v })}
            placeholder={t("chooseTo")}
          />
        </Field>
        <Field label={t("reference")}>
          <Input value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} />
        </Field>
        <Field label={td("description")} error={fieldErrors.description?.[0]}>
          <Input
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            placeholder={t("descriptionPlaceholder")}
          />
        </Field>
        <Field label={td("project")}>
          <Combobox
            options={[{ value: "", label: td("none") }, ...toOptions(projects.data ?? [], (p) => `${p.code} · ${p.name}`)]}
            value={form.projectId}
            onValueChange={(v) => setForm({ ...form, projectId: v })}
            placeholder={td("none")}
          />
        </Field>
      </section>

      <section className="space-y-2">
        <div className="flex items-baseline justify-between">
          <h2 className="text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
            {t("lines")}
          </h2>
          <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("inTransitNote")}</p>
        </div>
        <LineGrid
          mode="inventory"
          rows={form.rows}
          onRowsChange={(rows) => setForm({ ...form, rows })}
          errors={lineErrors}
          inventoryColumns={{ warehouse: false, transactionType: false, unitCost: false, value: false, contra: false }}
          accountOptions={[]}
          itemOptions={itemOptions}
          uomOptionsFor={support.uomOptionsFor}
          conversionFor={support.conversionFor}
          lineKindFor={() => InventoryTransactionKind.TRANSFER}
          onHandFor={(row) => {
            if (!row.itemId || !fromId) return undefined;
            const held = sourceOnHand?.get(Number(row.itemId));
            const base = support.baseUomOf(row);
            return `${support.formatBase(Number(row.itemId), held?.quantity ?? "0")}${base ? ` ${base.code}` : ""}`;
          }}
          projectOptions={toOptions(projects.data ?? [], (p) => `${p.code} · ${p.name}`)}
        />
      </section>
    </DocumentWorkspaceShell>
  );
}
