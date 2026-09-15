"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { useToast } from "@/design/components/toast";
import { isApiError } from "@/features/auth/hooks";
import { ItemType } from "@/lib/api-enums";
import { trimDecimalString } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useSaveBreakup } from "./hooks";
import { useOrderLineSupport } from "./order-support";
import type { SalesOrderLine } from "./types";

/**
 * Breakup: what goes in **this** order's box (P6 decision 8).
 *
 * The quantities here are the delivery's, not a per-kit rate — the service's own words, and
 * the reason the column is headed with the kit's quantity rather than "per kit". Asking an
 * operator to think in rates while looking at one order is how the wrong number gets typed.
 *
 * Editing this explosion changes **one order** and never the catalogue: the definition on the
 * Items screen is what the *next* kit line explodes from, and an order already taken keeps the
 * component lines it was keyed with. That separation is what `kit_breakup_edited` records, and
 * why changing such a line's quantity afterwards is refused unless the operator agrees to lose
 * this edit.
 *
 * A whole-list PUT, like the catalogue definition it came from: a box is one fact.
 */
export function BreakupDialog({
  orderId,
  line,
  components,
  onClose,
}: {
  orderId: number;
  line: SalesOrderLine;
  components: SalesOrderLine[];
  onClose: () => void;
}) {
  const t = useTranslations("orderEntry.breakup");
  const tc = useTranslations("orderEntry.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const support = useOrderLineSupport({ role: "sales" });
  const saveBreakup = useSaveBreakup();

  const [rows, setRows] = useState(() =>
    components.map((component) => ({
      itemId: String(component.item_id),
      quantity: trimDecimalString(component.quantity),
    })),
  );
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [banner, setBanner] = useState<string | null>(null);

  /** Substitutions are allowed — a kit may ship with a different bottle when the catalogue's
   * is out — but never another kit: kits do not nest, and the parent cannot be its own part. */
  const choices = useMemo(
    () =>
      support.items
        .filter(
          (item) => item.is_active && item.item_type !== ItemType.KIT && item.id !== line.item_id,
        )
        .map((item) => ({ value: String(item.id), label: support.itemLabel(item.id) })),
    [support, line.item_id],
  );

  const rowErrors = useMemo(() => {
    const out: Record<number, Record<string, string>> = {};
    for (const [key, messages] of Object.entries(fieldErrors)) {
      const match = /^components\.(\d+)\.(.+)$/.exec(key);
      if (match) out[Number(match[1])] = { ...out[Number(match[1])], [match[2]]: messages[0] };
    }
    return out;
  }, [fieldErrors]);

  async function handleSave() {
    setBanner(null);
    setFieldErrors({});
    try {
      await saveBreakup.mutateAsync({
        orderId,
        lineId: line.id,
        payload: {
          components: rows
            .filter((row) => row.itemId || row.quantity)
            .map((row) => ({ item_id: Number(row.itemId), quantity: row.quantity })),
        },
      });
      toast.show({ title: t("saved"), tone: "success" });
      onClose();
    } catch (err) {
      if (isApiError(err)) {
        setFieldErrors(err.fieldErrors);
        setBanner(err.message);
      } else {
        showApiError(err, t("saveFailed"));
      }
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        title={t("title", { item: support.itemLabel(line.item_id) })}
        description={t("subtitle", {
          quantity: trimDecimalString(line.quantity),
        })}
      >
        <div className="space-y-3 pt-2">
          {banner ? (
            <p className="rounded-[var(--radius-control)] border border-[var(--vinea-danger)] px-3 py-2 text-xs text-[var(--vinea-danger)]">
              {banner}
            </p>
          ) : null}
          {rows.map((row, index) => (
            <div key={index} className="flex items-end gap-2">
              <Field
                label={t("component")}
                error={rowErrors[index]?.item_id}
                className="flex-1"
              >
                <Combobox
                  options={choices}
                  value={row.itemId}
                  onValueChange={(value) =>
                    setRows((prev) =>
                      prev.map((r, i) => (i === index ? { ...r, itemId: value } : r)),
                    )
                  }
                  placeholder={t("chooseComponent")}
                />
              </Field>
              <Field label={t("quantity")} error={rowErrors[index]?.quantity} className="w-28">
                <Input
                  value={row.quantity}
                  onChange={(e) =>
                    setRows((prev) =>
                      prev.map((r, i) => (i === index ? { ...r, quantity: e.target.value } : r)),
                    )
                  }
                  inputMode="decimal"
                  className="text-right font-mono"
                />
              </Field>
              <button
                type="button"
                onClick={() => setRows((prev) => prev.filter((_, i) => i !== index))}
                aria-label={t("removeRow", { name: support.itemLabel(row.itemId) })}
                className="mb-2 rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-danger)]"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))}
          <Button
            variant="ghost"
            onClick={() => setRows((prev) => [...prev, { itemId: "", quantity: "1" }])}
            className="gap-1.5 text-xs"
          >
            <Plus className="size-3.5" /> {t("addRow")}
          </Button>
          <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("note")}</p>
          <div className="flex justify-end gap-2 pt-3">
            <Button variant="ghost" onClick={onClose}>
              {tc("cancel")}
            </Button>
            <Button variant="primary" onClick={handleSave} disabled={saveBreakup.isPending}>
              {saveBreakup.isPending ? tc("saving") : tc("save")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
