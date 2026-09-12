"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Landmark, ShieldAlert, Sliders } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Field } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { Select } from "@/design/components/select";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import { useAccounts } from "@/features/gl/hooks";
import {
  useInventoryDefaults,
  useSaveInventoryDefaults,
  useWarehouses,
} from "@/features/inventory/hooks";
import type { NegativeStockPolicy } from "@/features/inventory/types";
import { dotted } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

type AccountKey =
  | "inventory_account_id"
  | "inventory_in_transit_account_id"
  | "inventory_adjustment_account_id"
  | "stock_count_variance_account_id"
  | "cogs_account_id";

const ACCOUNT_KEYS: readonly AccountKey[] = [
  "inventory_account_id",
  "inventory_in_transit_account_id",
  "inventory_adjustment_account_id",
  "stock_count_variance_account_id",
  "cogs_account_id",
];

/**
 * The wire value of `ControlType.INVENTORY`. Spelled out as a constant because the phase
 * prompt and the ADRs call this control type "INV" while the enum serialises `"inventory"` —
 * comparing against the wrong one leaves the picker empty and the saved account showing as
 * "Not set", which is a screen that renders perfectly and tells the operator something
 * untrue. Caught by `e2e/inventory-maintenance.spec.ts`, which is the point of opening a
 * screen with data in it.
 */
const INVENTORY_CONTROL_TYPE = "inventory";

const EMPTY: Record<AccountKey, string> = {
  inventory_account_id: "",
  inventory_in_transit_account_id: "",
  inventory_adjustment_account_id: "",
  stock_count_variance_account_id: "",
  cogs_account_id: "",
};

/**
 * The inventory keys on `gl_settings` — the same settings mechanism every other module uses
 * (P5 decision 10), not a second store.
 *
 * The two control keys only offer accounts carrying `control_type = "INV"`, because the
 * posting guard rejects anything else and offering an ordinary account here would only buy
 * the operator a 409. The consequence of those being control accounts is the note on the
 * card: opening stock cannot arrive as a GL journal, it comes through an inventory journal
 * batch under an opening-balance type.
 */
export default function InventoryDefaultsPage() {
  const t = useTranslations("inventory.defaults");
  const tc = useTranslations("inventory.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canEdit = useHasPermission()("inv:setup_manage");

  const defaults = useInventoryDefaults();
  const saveDefaults = useSaveInventoryDefaults();
  const accounts = useAccounts();
  const warehouses = useWarehouses();

  const [form, setForm] = useState<Record<AccountKey, string>>(EMPTY);
  const [policy, setPolicy] = useState<NegativeStockPolicy>("block");
  const [warehouseId, setWarehouseId] = useState("");

  useEffect(() => {
    if (!defaults.data) return;
    const next = { ...EMPTY };
    for (const key of ACCOUNT_KEYS) {
      const value = defaults.data[key];
      next[key] = value ? String(value) : "";
    }
    setForm(next);
    setPolicy(defaults.data.negative_stock_policy);
    setWarehouseId(
      defaults.data.default_warehouse_id ? String(defaults.data.default_warehouse_id) : "",
    );
  }, [defaults.data]);

  const usable = useMemo(
    () => (accounts.data ?? []).filter((a) => a.is_postable && a.is_active),
    [accounts.data],
  );
  const inventoryControl = useMemo(
    () => usable.filter((a) => a.control_type === INVENTORY_CONTROL_TYPE),
    [usable],
  );
  const ordinary = useMemo(() => usable.filter((a) => !a.is_control), [usable]);

  function options(rows: typeof usable) {
    return [
      { value: "", label: t("chooseAccount") },
      ...rows.map((a) => ({ value: String(a.id), label: dotted(a.code, a.name) })),
    ];
  }

  function picker(key: AccountKey, label: string, rows: typeof usable) {
    return (
      <Field label={label}>
        <Combobox
          options={options(rows)}
          value={form[key]}
          onValueChange={(value) => setForm((prev) => ({ ...prev, [key]: value }))}
          placeholder={t("chooseAccount")}
        />
      </Field>
    );
  }

  async function handleSave() {
    const payload: Record<string, number | string | null> = {};
    for (const key of ACCOUNT_KEYS) {
      payload[key] = form[key] ? Number(form[key]) : null;
    }
    payload.negative_stock_policy = policy;
    payload.default_warehouse_id = warehouseId ? Number(warehouseId) : null;
    try {
      await saveDefaults.mutateAsync(payload);
      toast.show({ title: t("saved"), tone: "success" });
    } catch (err) {
      showApiError(err, t("saveFailed"));
    }
  }

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      width="narrow"
      actions={
        <Button
          variant="primary"
          onClick={handleSave}
          disabled={!canEdit || saveDefaults.isPending}
          className="text-xs"
        >
          {saveDefaults.isPending ? tc("saving") : tc("save")}
        </Button>
      }
    >
      <MaintenanceCard icon={<Landmark className="size-4" />} title={t("accountsTitle")}>
        <div className="space-y-3">
          {picker("inventory_account_id", t("inventoryAccount"), inventoryControl)}
          {picker("inventory_in_transit_account_id", t("inTransitAccount"), inventoryControl)}
          {picker("inventory_adjustment_account_id", t("adjustmentAccount"), ordinary)}
          {picker("stock_count_variance_account_id", t("countVarianceAccount"), ordinary)}
          {picker("cogs_account_id", t("cogsAccount"), ordinary)}
        </div>
        <p className="flex items-start gap-2 pt-3 text-xs text-[var(--vinea-ink-subtle)]">
          <ShieldAlert className="size-4 shrink-0 text-[var(--vinea-warning)]" />
          {t("controlNote")}
        </p>
        <p className="pt-2 text-xs text-[var(--vinea-ink-subtle)]">{t("cogsNote")}</p>
      </MaintenanceCard>

      <MaintenanceCard icon={<Sliders className="size-4" />} title={t("behaviourTitle")}>
        <div className="space-y-3">
          <Field label={t("negativeStockPolicy")}>
            <Select
              options={[
                { value: "block", label: t("policyBlock") },
                { value: "allow", label: t("policyAllow") },
              ]}
              value={policy}
              onValueChange={(value) => setPolicy(value as NegativeStockPolicy)}
            />
          </Field>
          <Field label={t("defaultWarehouse")}>
            <Combobox
              options={[
                { value: "", label: t("chooseWarehouse") },
                ...(warehouses.data ?? []).map((w) => ({
                  value: String(w.id),
                  label: dotted(w.code, w.name),
                })),
              ]}
              value={warehouseId}
              onValueChange={setWarehouseId}
              placeholder={t("chooseWarehouse")}
            />
          </Field>
        </div>
        <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("policyNote")}</p>
      </MaintenanceCard>
    </MaintenancePage>
  );
}
