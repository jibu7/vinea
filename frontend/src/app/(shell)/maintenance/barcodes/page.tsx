"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { ScanBarcode } from "lucide-react";
import { Field, Input } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useBarcodes } from "@/features/inventory/hooks";
import { dotted, trimDecimalString } from "@/lib/format";

/**
 * Every barcode in the company, and what each one stands for.
 *
 * The Items screen answers "what codes does this item have"; this one answers the scanner's
 * question, "what does this code mean", which is the one you cannot ask on the Items screen
 * because you have to know the item first. It is also the only view in which a code that has
 * somehow been attached to two items would be visible — which is why it lists by barcode
 * rather than by item.
 *
 * Read-only on purpose: a barcode belongs to an item, so it is created and edited where the
 * item is. Nothing here would be true of a barcode with no item behind it.
 *
 * **Not "Variable barcodes".** This shipped under that name at P5 step 6 and the owner
 * corrected it at review: a variable barcode is the POS scale-label pattern — a prefix, then
 * item-code digits, then weight or price digits, decoded at the till — and that is a P11
 * screen with its own row in the tree. What this is, is the plain per-item barcode listing.
 */
export default function BarcodesPage() {
  const t = useTranslations("inventory.barcodes");
  const tc = useTranslations("inventory.common");

  const [search, setSearch] = useState("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const barcodes = useBarcodes({ search, includeInactive });
  const rows = barcodes.data?.items ?? [];

  return (
    <MaintenancePage title={t("title")} description={t("subtitle")} backHref="/maintenance/inventory-items">
      <MaintenanceCard
        icon={<ScanBarcode className="size-4" />}
        title={t("title")}
        actions={
          <label className="flex items-center gap-1.5 text-xs text-[var(--vinea-ink-muted)]">
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(e) => setIncludeInactive(e.target.checked)}
              className="size-3.5"
            />
            {tc("showInactive")}
          </label>
        }
      >
        <div className="pb-3">
          <Field label={tc("search")}>
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("searchPlaceholder")}
            />
          </Field>
        </div>

        {rows.length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {barcodes.isLoading ? tc("loading") : t("empty")}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-48">{t("barcode")}</TH>
                <TH>{t("item")}</TH>
                <TH className="w-40">{t("uom")}</TH>
                <TH className="w-32 text-right">{t("packQuantity")}</TH>
                <TH className="w-28 text-right">{tc("status")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((row) => (
                <TR key={row.id}>
                  <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                    {row.barcode}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink)]">
                    {dotted(row.item_code, row.item_name)}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {dotted(row.uom_code, row.uom_name)}
                  </TD>
                  <TD className="text-right font-mono text-xs text-[var(--vinea-ink)]">
                    {trimDecimalString(row.pack_quantity)}
                  </TD>
                  <TD className="text-right">
                    <StatusChip tone={row.is_active ? "success" : "neutral"}>
                      {row.is_active ? tc("active") : tc("inactive")}
                    </StatusChip>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
        <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("uniqueNote")}</p>
      </MaintenanceCard>
    </MaintenancePage>
  );
}
