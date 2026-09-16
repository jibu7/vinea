"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";
import { Button } from "@/design/components/button";
import { QueryState } from "@/design/components/query-state";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { MaintenancePage } from "@/design/components/maintenance-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { StockCountStatus } from "@/lib/api-enums";
import { formatDate, todayIso } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useCountSessions, useOpenCountSession } from "./hooks";
import { useInventoryLineSupport } from "./line-support";

export const COUNT_STATUS_TONE: Record<string, "neutral" | "success" | "warning" | "danger" | "info"> = {
  [StockCountStatus.COUNTING]: "info",
  [StockCountStatus.COMPLETED]: "success",
  [StockCountStatus.CANCELLED]: "neutral",
};

export const COUNT_STATUS_KEY: Record<string, "statusCounting" | "statusCompleted" | "statusCancelled"> = {
  [StockCountStatus.COUNTING]: "statusCounting",
  [StockCountStatus.COMPLETED]: "statusCompleted",
  [StockCountStatus.CANCELLED]: "statusCancelled",
};

/**
 * Inventory count sessions: the list, and the dialog that starts one. Starting a session
 * freezes the warehouse's quantities as the sheet's system column (decision 7); everything
 * after that — counting, review, Process — happens on the session's own page.
 */
export function CountsScreen() {
  const t = useTranslations("inventory.counts");
  const tc = useTranslations("inventory.common");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();
  const canStart = hasPermission("inv:transactions_adjust");

  const sessions = useCountSessions();
  const support = useInventoryLineSupport();
  const open = useOpenCountSession();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [warehouseId, setWarehouseId] = useState("");
  const [countDate, setCountDate] = useState(todayIso);
  const [description, setDescription] = useState("");
  const [reference, setReference] = useState("");
  const [includeZero, setIncludeZero] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});

  const warehouseCode = (id: number) => support.warehouses.find((w) => w.id === id)?.code ?? String(id);

  async function handleStart() {
    setFieldErrors({});
    try {
      const session = await open.mutateAsync({
        warehouse_id: Number(warehouseId),
        count_date: countDate,
        description: description || t("newTitle"),
        reference: reference || null,
        include_zero_balances: includeZero,
      });
      toast.show({ title: t("started", { number: session.number }), tone: "success" });
      setDialogOpen(false);
      router.push(`/inventory/counts/${session.id}`);
    } catch (err) {
      if (isApiError(err) && Object.keys(err.fieldErrors).length > 0) {
        setFieldErrors(err.fieldErrors);
      } else {
        showApiError(err, t("startFailed"));
      }
    }
  }

  const rows = sessions.data?.items ?? [];

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      actions={
        canStart ? (
          <Button variant="primary" onClick={() => setDialogOpen(true)}>
            <Plus className="size-4" /> {t("new")}
          </Button>
        ) : undefined
      }
    >
      <Table>
        <THead>
          <TR>
            <TH>{t("number")}</TH>
            <TH>{t("date")}</TH>
            <TH>{t("warehouse")}</TH>
            <TH>{t("description")}</TH>
            <TH>{t("status")}</TH>
          </TR>
        </THead>
        <TBody>
          {rows.length === 0 && (
            <TR>
              <TD colSpan={5} className="text-[var(--vinea-ink-muted)]">
                <QueryState query={sessions} isEmpty empty={t("empty")} testId="query" className="py-0 text-left" />
              </TD>
            </TR>
          )}
          {rows.map((row) => (
            <TR key={row.id} data-count-session={row.number}>
              <TD>
                <Link
                  href={`/inventory/counts/${row.id}`}
                  className="font-mono text-[var(--vinea-brand)] hover:underline"
                  aria-label={t("open", { number: row.number })}
                >
                  {row.number}
                </Link>
              </TD>
              <TD>{formatDate(row.count_date)}</TD>
              <TD className="font-mono">{warehouseCode(row.warehouse_id)}</TD>
              <TD className="text-[var(--vinea-ink-muted)]">{row.description}</TD>
              <TD>
                <StatusChip tone={COUNT_STATUS_TONE[row.status] ?? "neutral"}>
                  {t(COUNT_STATUS_KEY[row.status] ?? "statusCounting")}
                </StatusChip>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent title={t("newTitle")} description={t("subtitle")}>
          <div className="space-y-4">
            <Field label={t("warehouse")} error={fieldErrors.warehouse_id?.[0]}>
              <Combobox
                options={support.warehouseOptions}
                value={warehouseId}
                onValueChange={setWarehouseId}
                placeholder={t("chooseWarehouse")}
              />
            </Field>
            <Field label={t("countDate")} error={fieldErrors.count_date?.[0]}>
              <IsoDatePicker value={countDate} onValueChange={setCountDate} />
            </Field>
            <Field label={t("description")} error={fieldErrors.description?.[0]}>
              <Input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t("descriptionPlaceholder")}
              />
            </Field>
            <Field label={t("reference")}>
              <Input value={reference} onChange={(e) => setReference(e.target.value)} />
            </Field>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={includeZero}
                onChange={(e) => setIncludeZero(e.target.checked)}
                className="size-4"
              />
              {t("includeZero")}
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setDialogOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button variant="primary" onClick={handleStart} disabled={!warehouseId || open.isPending}>
                {open.isPending ? t("starting") : t("start")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </MaintenancePage>
  );
}
