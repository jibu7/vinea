"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Edit2, Plus, Truck, Warehouse as WarehouseIcon } from "lucide-react";
import { Button } from "@/design/components/button";
import { QueryState } from "@/design/components/query-state";
import { Combobox } from "@/design/components/combobox";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import { useBranches } from "@/features/gl/hooks";
import {
  useCreateWarehouse,
  useUpdateWarehouse,
  useWarehouses,
} from "@/features/inventory/hooks";
import type { Warehouse } from "@/features/inventory/types";
import { dotted } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

/**
 * Warehouses, including the in-transit one.
 *
 * Every other reader of `/inventory/warehouses` leaves `include_in_transit` off, because a
 * picker that offered it would let somebody send stock to the place stock only passes
 * through. This screen is the exception and deliberately so: it is the one page where an
 * operator should be able to see that the location exists, which branch it sits in, and that
 * it is not theirs to edit.
 */
export default function WarehousesPage() {
  const t = useTranslations("inventory.warehouses");
  const tc = useTranslations("inventory.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canEdit = useHasPermission()("inv:setup_manage");

  const [includeInactive, setIncludeInactive] = useState(false);
  const warehouses = useWarehouses({ includeInactive, includeInTransit: true });
  const branches = useBranches();
  const createWarehouse = useCreateWarehouse();
  const updateWarehouse = useUpdateWarehouse();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Warehouse | null>(null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [branchId, setBranchId] = useState("");
  const [isDefault, setIsDefault] = useState(false);

  const branchName = (id: number) =>
    branches.data?.find((b) => b.id === id)?.name ?? tc("emptyValue");

  function startCreate() {
    setEditing(null);
    setCode("");
    setName("");
    setBranchId("");
    setIsDefault(false);
    setOpen(true);
  }

  function startEdit(warehouse: Warehouse) {
    setEditing(warehouse);
    setCode(warehouse.code);
    setName(warehouse.name);
    setBranchId(String(warehouse.branch_id));
    setIsDefault(warehouse.is_default);
    setOpen(true);
  }

  async function handleSave() {
    try {
      if (editing) {
        await updateWarehouse.mutateAsync({
          warehouseId: editing.id,
          payload: { name, branch_id: Number(branchId), is_default: isDefault },
        });
        toast.show({ title: t("updated"), tone: "success" });
      } else {
        await createWarehouse.mutateAsync({
          code,
          name,
          branch_id: Number(branchId),
          is_default: isDefault,
        });
        toast.show({ title: t("created"), tone: "success" });
      }
      setOpen(false);
    } catch (err) {
      showApiError(err, t("saveFailed"));
    }
  }

  async function toggleActive(warehouse: Warehouse) {
    try {
      await updateWarehouse.mutateAsync({
        warehouseId: warehouse.id,
        payload: { is_active: !warehouse.is_active },
      });
      toast.show({
        title: warehouse.code,
        description: warehouse.is_active ? tc("deactivated") : tc("activated"),
        tone: "success",
      });
    } catch (err) {
      showApiError(err, t("updateFailed"));
    }
  }

  const rows = warehouses.data ?? [];

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      actions={
        <Button
          variant="primary"
          onClick={startCreate}
          disabled={!canEdit}
          className="gap-1.5 text-xs"
        >
          <Plus className="size-3.5" /> {t("new")}
        </Button>
      }
    >
      <MaintenanceCard
        icon={<WarehouseIcon className="size-4" />}
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
        {rows.length === 0 ? (
          <QueryState query={warehouses} isEmpty empty={t("empty")} testId="query" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-28">{tc("code")}</TH>
                <TH>{tc("name")}</TH>
                <TH className="w-48">{t("branch")}</TH>
                <TH className="w-40 text-right">{tc("status")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((warehouse) => (
                <TR key={warehouse.id}>
                  <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                    {warehouse.code}
                  </TD>
                  <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                    <span className="inline-flex items-center gap-2">
                      {warehouse.name}
                      {warehouse.is_default ? (
                        <StatusChip tone="info">{t("isDefault")}</StatusChip>
                      ) : null}
                      {warehouse.is_in_transit ? (
                        <StatusChip tone="warning">
                          <span className="inline-flex items-center gap-1">
                            <Truck className="size-3" />
                            {t("inTransit")}
                          </span>
                        </StatusChip>
                      ) : null}
                    </span>
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {branchName(warehouse.branch_id)}
                  </TD>
                  <TD className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      {warehouse.is_in_transit ? null : (
                        <button
                          type="button"
                          onClick={() => startEdit(warehouse)}
                          aria-label={tc("editLabel", { name: warehouse.name })}
                          className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                        >
                          <Edit2 className="size-3.5" />
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => toggleActive(warehouse)}
                        disabled={!canEdit || warehouse.is_in_transit}
                        aria-label={tc(
                          warehouse.is_active ? "deactivateLabel" : "activateLabel",
                          { name: warehouse.name },
                        )}
                      >
                        <StatusChip tone={warehouse.is_active ? "success" : "neutral"}>
                          {warehouse.is_active ? tc("active") : tc("inactive")}
                        </StatusChip>
                      </button>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
        <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("inTransitNote")}</p>
      </MaintenanceCard>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title={editing ? t("editTitle", { name: editing.name }) : t("newTitle")}>
          <div className="space-y-3 pt-2">
            <Field label={tc("code")}>
              <Input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                disabled={!!editing}
                className="font-mono"
                placeholder={t("codePlaceholder")}
              />
            </Field>
            <Field label={tc("name")}>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("namePlaceholder")}
              />
            </Field>
            <Field label={t("branch")}>
              <Combobox
                options={(branches.data ?? []).map((branch) => ({
                  value: String(branch.id),
                  label: dotted(branch.code, branch.name),
                }))}
                value={branchId}
                onValueChange={setBranchId}
                placeholder={t("chooseBranch")}
              />
            </Field>
            <label className="flex items-center gap-2 text-xs text-[var(--vinea-ink-muted)]">
              <input
                type="checkbox"
                checked={isDefault}
                onChange={(e) => setIsDefault(e.target.checked)}
                className="size-3.5"
              />
              {t("defaultLabel")}
            </label>
            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button
                variant="primary"
                disabled={!code || !name || !branchId || !canEdit}
                onClick={handleSave}
              >
                {tc("save")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </MaintenancePage>
  );
}
