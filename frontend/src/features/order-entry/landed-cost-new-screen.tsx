"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import {
  DocumentWorkspaceShell,
  useDocumentShortcuts,
} from "@/design/components/document-workspace";
import { Field, Input } from "@/design/components/input";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useMe } from "@/features/auth/hooks";
import { useCurrencies } from "@/features/gl/hooks";
import { useInventoryLineSupport } from "@/features/inventory/line-support";
import { GrnStatus, LandedCostBasis } from "@/lib/api-enums";
import { clearDraft, loadDraft, newDraftId, saveDraft } from "@/lib/drafts";
import { dotted, formatMoney, formatQuantity, nowIso, todayIso } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import {
  useCreateLandedCost,
  useGoodsReceived,
  useGrns,
  useLandedCostPreview,
} from "./hooks";
import type { LandedCostShare } from "./types";

const DRAFT_MODULE = "oe.landed-cost";

interface LandedCostDraft {
  id: string;
  costDate: string;
  description: string;
  reference: string;
  amount: string;
  basis: LandedCostBasis;
  grnIds: number[];
  selected: number[];
}

function blankDraft(): LandedCostDraft {
  return {
    id: newDraftId(),
    costDate: todayIso(),
    description: "",
    reference: "",
    amount: "",
    basis: LandedCostBasis.VALUE,
    grnIds: [],
    selected: [],
  };
}

/**
 * A landed cost: freight, duty, clearing — a cost incurred *for* goods, spread into what they
 * are worth (P6 decision 9).
 *
 * **The preview is the point.** The shares are shown before anything posts, and Post runs the
 * same function that computed them: a preview with arithmetic of its own is a preview that
 * will one day show a person one set of shares and write another. What the preview cannot
 * promise is the stockless rule — whether a location still holds the item is settled under the
 * costing lock at posting — so a share marked as going to cost of sales says "would", and the
 * note under the table says why.
 *
 * Targets are **receipt lines**, from any receipt and any supplier: one freight bill routinely
 * covers consignments from several, and a screen that allowed only one would have the operator
 * splitting the bill by hand and posting the halves.
 */
export function LandedCostNewScreen() {
  const t = useTranslations("orderEntry.landedCost");
  const tc = useTranslations("orderEntry.common");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const { data: me } = useMe();
  const companyId = me?.company?.id ?? 0;
  const userId = me?.user_id ?? 0;

  const [form, setForm] = useState<LandedCostDraft>(blankDraft);
  const [shares, setShares] = useState<LandedCostShare[] | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const restored = useRef(false);

  const receipts = useGoodsReceived({ limit: 100 });
  const grns = useGrns(form.grnIds);
  const support = useInventoryLineSupport({ includeInactiveItems: true });
  const currencies = useCurrencies();
  const preview = useLandedCostPreview();
  const createCost = useCreateLandedCost();

  useEffect(() => {
    if (restored.current || !companyId || !userId) return;
    restored.current = true;
    const saved = loadDraft<LandedCostDraft>(DRAFT_MODULE, companyId, userId);
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

  const base = (currencies.data ?? []).find((c) => c.is_base);
  const currency = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };

  /** Only a receipt that still exists as stock value can carry a share: a reversed one has
   * nothing left to add to, and `grn_reversed` refuses it. */
  const receiptOptions = useMemo(
    () =>
      (receipts.data?.items ?? [])
        .filter((row) => row.status !== GrnStatus.REVERSED && !form.grnIds.includes(row.id))
        .map((row) => ({
          value: String(row.id),
          label: dotted(row.number, row.partner_name),
        })),
    [receipts.data, form.grnIds],
  );

  const targetLines = useMemo(
    () => grns.flatMap((grn) => grn.lines.map((line) => ({ grn, line }))),
    [grns],
  );
  const shareByLine = useMemo(
    () => new Map((shares ?? []).map((share) => [share.grn_line_id, share])),
    [shares],
  );

  const selected = form.selected.filter((id) => targetLines.some(({ line }) => line.id === id));

  /** `weight_missing` and `grn_reversed` are keyed by the target's **position in the list that
   * was sent** (`grn_line_ids.0`), which is a number no operator can see. Resolved back to the
   * receipt line it names, so the message lands on the row that caused it rather than only in
   * the banner over a table of twenty. */
  const rowErrors = useMemo(() => {
    const out = new Map<number, string>();
    for (const [key, messages] of Object.entries(fieldErrors)) {
      const match = /^grn_line_ids\.(\d+)$/.exec(key);
      const lineId = match ? selected[Number(match[1])] : undefined;
      if (lineId !== undefined) out.set(lineId, messages[0]);
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `selected` is derived per render
  }, [fieldErrors, form.selected, targetLines]);
  const canPreview = Boolean(form.amount) && selected.length > 0 && !preview.isPending;
  const canPost =
    canPreview && Boolean(form.description) && shares !== null && !createCost.isPending;

  function toggleLine(lineId: number) {
    // A change of target invalidates the shares that were computed for the old set. Leaving
    // them on screen would be showing arithmetic about a document that no longer exists.
    setShares(null);
    setForm((prev) => ({
      ...prev,
      selected: prev.selected.includes(lineId)
        ? prev.selected.filter((id) => id !== lineId)
        : [...prev.selected, lineId],
    }));
  }

  async function runPreview() {
    if (!canPreview) return;
    setBanner(null);
    setFieldErrors({});
    try {
      const result = await preview.mutateAsync({
        amount: form.amount,
        basis: form.basis,
        grn_line_ids: selected,
      });
      setShares(result.shares);
    } catch (err) {
      if (isApiError(err)) {
        setFieldErrors(err.fieldErrors);
        setBanner(err.message);
      } else {
        showApiError(err, t("previewFailed"));
      }
    }
  }

  async function handlePost() {
    if (!canPost) return;
    setBanner(null);
    setFieldErrors({});
    try {
      const document = await createCost.mutateAsync({
        idempotencyKey: form.id,
        payload: {
          cost_date: form.costDate,
          description: form.description,
          reference: form.reference || null,
          amount: form.amount,
          basis: form.basis,
          grn_line_ids: selected,
        },
      });
      clearDraft(DRAFT_MODULE, companyId, userId);
      toast.show({ title: t("posted", { number: document.number }), tone: "success" });
      router.push(`/oe/landed-costs/${document.id}`);
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
    onCancel: () => router.push("/oe/landed-costs"),
    canPost,
  });

  const allocated = (shares ?? []).reduce((sum, share) => sum + Number(share.share), 0);

  return (
    <DocumentWorkspaceShell
      backHref="/oe/landed-costs"
      title={t("newTitle")}
      subtitle={t("subtitle")}
      statusChip={<StatusChip tone="neutral">{tc("draft")}</StatusChip>}
      errorBanner={banner}
      footer={
        <div className="flex items-center justify-between gap-6">
          <div className="flex items-center gap-6 text-sm">
            <span className="text-[var(--vinea-ink-muted)]">
              {t("targets")}{" "}
              <span
                className="font-mono tabular-nums text-[var(--vinea-ink)]"
                data-testid="landed-cost-targets"
              >
                {selected.length}
              </span>
            </span>
            <span className="text-[var(--vinea-ink-muted)]">
              {t("allocated")}{" "}
              <span
                className="font-mono tabular-nums text-[var(--vinea-ink)]"
                data-testid="landed-cost-allocated"
              >
                {formatMoney(allocated, currency)}
              </span>
            </span>
          </div>
          <div className="flex items-center gap-3">
            <p className="max-w-sm text-xs text-[var(--vinea-ink-subtle)]">{t("postsNote")}</p>
            <Button variant="secondary" disabled={!canPreview} onClick={runPreview}>
              {preview.isPending ? tc("loading") : t("preview")}
            </Button>
            <Button variant="primary" disabled={!canPost} onClick={handlePost}>
              {createCost.isPending ? tc("saving") : t("post")}
            </Button>
          </div>
        </div>
      }
    >
      <section className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-4">
        <Field label={t("costDate")} error={fieldErrors.cost_date?.[0]}>
          <IsoDatePicker
            value={form.costDate}
            onValueChange={(v) => setForm({ ...form, costDate: v })}
          />
        </Field>
        <Field label={t("amount")} error={fieldErrors.amount?.[0]}>
          <Input
            value={form.amount}
            onChange={(e) => {
              setShares(null);
              setForm({ ...form, amount: e.target.value });
            }}
            inputMode="decimal"
            className="text-right font-mono tabular-nums"
            placeholder={t("amountPlaceholder")}
          />
        </Field>
        <Field label={t("basis")} error={fieldErrors.basis?.[0]}>
          <Select
            options={Object.values(LandedCostBasis).map((value) => ({
              value,
              label: t(`basis_${value}`),
            }))}
            value={form.basis}
            onValueChange={(v) => {
              setShares(null);
              setForm({ ...form, basis: v as LandedCostBasis });
            }}
            ariaLabel={t("basis")}
          />
        </Field>
        <Field label={t("reference")}>
          <Input
            value={form.reference}
            onChange={(e) => setForm({ ...form, reference: e.target.value })}
          />
        </Field>
        <Field
          label={tc("description")}
          error={fieldErrors.description?.[0]}
          className="sm:col-span-4"
        >
          <Input
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            placeholder={t("descriptionPlaceholder")}
          />
        </Field>
      </section>

      <section className="space-y-2">
        <div className="flex items-end justify-between gap-3">
          <h2 className="text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
            {t("targetsTitle")}
          </h2>
          <div className="flex w-96 items-end gap-2">
            <Field label={t("addReceipt")} className="flex-1">
              <Combobox
                options={receiptOptions}
                value=""
                onValueChange={(v) =>
                  setForm((prev) => ({ ...prev, grnIds: [...prev.grnIds, Number(v)] }))
                }
                placeholder={t("chooseReceipt")}
              />
            </Field>
            <Plus className="mb-3 size-3.5 text-[var(--vinea-ink-subtle)]" aria-hidden />
          </div>
        </div>
        {targetLines.length === 0 ? (
          <p className="py-10 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {t("noTargets")}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-12">{t("include")}</TH>
                <TH className="w-28">{t("receipt")}</TH>
                <TH>{tc("item")}</TH>
                <TH className="w-24 text-right">{tc("quantity")}</TH>
                <TH className="w-32 text-right">{tc("value")}</TH>
                <TH className="w-28 text-right">{t("weight")}</TH>
                <TH className="w-32 text-right">{t("share")}</TH>
              </TR>
            </THead>
            <TBody>
              {targetLines.map(({ grn, line }) => {
                const item = support.itemById.get(line.item_id);
                const decimals = support.uomById.get(item?.base_uom_id ?? 0)?.decimal_places ?? 0;
                const share = shareByLine.get(line.id);
                const checked = form.selected.includes(line.id);
                return (
                  <TR key={line.id}>
                    <TD>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleLine(line.id)}
                        aria-label={t("includeLine", {
                          number: grn.number,
                          item: item ? item.code : String(line.item_id),
                        })}
                        className="size-4 accent-[var(--vinea-brand)]"
                      />
                    </TD>
                    <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                      {grn.number}
                    </TD>
                    <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                      {item ? dotted(item.code, item.name) : line.item_id}
                    </TD>
                    <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]">
                      {formatQuantity(Number(line.quantity), decimals)}
                    </TD>
                    <TD
                      className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]"
                      data-testid="target-value"
                    >
                      {formatMoney(Number(line.value), currency)}
                    </TD>
                    <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink-muted)]">
                      {share ? formatQuantity(Number(share.weight), 4) : tc("emptyValue")}
                    </TD>
                    <TD className="text-right">
                      {rowErrors.has(line.id) ? (
                        <span
                          className="text-xs text-[var(--vinea-danger)]"
                          data-testid="target-error"
                        >
                          {rowErrors.get(line.id)}
                        </span>
                      ) : share ? (
                        <span
                          className="font-mono tabular-nums text-xs text-[var(--vinea-ink)]"
                          data-testid="target-share"
                        >
                          {formatMoney(Number(share.share), currency)}
                          {share.would_go_to_cogs ? (
                            <span className="ml-2">
                              <StatusChip tone="warning">{t("toCogs")}</StatusChip>
                            </span>
                          ) : null}
                        </span>
                      ) : (
                        <span className="text-xs text-[var(--vinea-ink-subtle)]">
                          {tc("emptyValue")}
                        </span>
                      )}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        )}
        <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("previewNote")}</p>
      </section>
    </DocumentWorkspaceShell>
  );
}
