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
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { useAccounts } from "@/features/gl/hooks";
import { useWarehouses } from "@/features/inventory/hooks";
import { useOrderDefaults, useSaveOrderDefaults } from "@/features/order-entry/hooks";
import type { OrderDefaultsPayload } from "@/features/order-entry/types";
import { BackorderPolicy, ControlType } from "@/lib/api-enums";
import { dotted } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

type AccountKey =
  | "grn_accrual_account_id"
  | "purchase_price_variance_account_id"
  | "landed_cost_clearing_account_id";

const ACCOUNT_KEYS: readonly AccountKey[] = [
  "grn_accrual_account_id",
  "purchase_price_variance_account_id",
  "landed_cost_clearing_account_id",
];

const EMPTY: Record<AccountKey, string> = {
  grn_accrual_account_id: "",
  purchase_price_variance_account_id: "",
  landed_cost_clearing_account_id: "",
};

/**
 * The order-entry keys on `gl_settings` — the same settings row every other module's defaults
 * live on (P6 decision 10), not a second store.
 *
 * The three accounts are offered from **different** lists, and the difference is the phase's
 * design rather than a stylistic choice:
 *
 * * **GRN accrual** offers only accounts carrying `ControlType.GRN_ACCRUAL`. The proof that
 *   the accrual balance equals Σ (received − relieved) rests on nothing else being able to
 *   post there, and that comes from the control type. `update_order_defaults` refuses
 *   anything else with `invalid_grn_accrual_account`, so offering an ordinary account here
 *   would only earn the operator a 409.
 * * **Purchase price variance** and **landed cost clearing** offer ordinary postable
 *   accounts and no control account of any kind. Clearing is deliberately plain: freight
 *   arrives on a forwarder's supplier invoice as a GL line and duty as a cashbook payment to
 *   RRA, and a control account would refuse both — the account would be impossible to get
 *   money into. The service refuses one with `contra_is_a_control_account`.
 *
 * **No "Not set" option.** The Inventory defaults screen offers one because its keys may be
 * cleared; these may not (`required_setting`), and a NULL here does not fail at the save, it
 * fails at whichever posting next needs it — the first GRN, or the first match that has a
 * variance — long after the operator who cleared it has gone. A picker that can only ask for
 * what the service will accept is the version of that rule a person can see.
 */
export default function OrderDefaultsPage() {
  const t = useTranslations("orderEntry.defaults");
  const tc = useTranslations("orderEntry.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canEdit = useHasPermission()("oe:setup_manage");

  const defaults = useOrderDefaults();
  const saveDefaults = useSaveOrderDefaults();
  const accounts = useAccounts();
  const warehouses = useWarehouses();

  const [form, setForm] = useState<Record<AccountKey, string>>(EMPTY);
  const [policy, setPolicy] = useState<BackorderPolicy>(BackorderPolicy.ALLOW);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});

  useEffect(() => {
    if (!defaults.data) return;
    const next = { ...EMPTY };
    for (const key of ACCOUNT_KEYS) {
      const value = defaults.data[key];
      next[key] = value ? String(value) : "";
    }
    setForm(next);
    setPolicy(defaults.data.backorder_policy);
  }, [defaults.data]);

  const usable = useMemo(
    () => (accounts.data ?? []).filter((a) => a.is_postable && a.is_active),
    [accounts.data],
  );
  const accrualControl = useMemo(
    () => usable.filter((a) => a.control_type === ControlType.GRN_ACCRUAL),
    [usable],
  );
  const ordinary = useMemo(() => usable.filter((a) => !a.is_control), [usable]);

  /** The default warehouse, read-only: sales and purchase orders default their lines from it,
   * and the Inventory defaults screen is the one that writes it. Two screens writing one key
   * is how they come to disagree. */
  const defaultWarehouse = (warehouses.data ?? []).find(
    (w) => w.id === defaults.data?.default_warehouse_id,
  );

  function picker(key: AccountKey, label: string, rows: typeof usable) {
    return (
      <Field label={label} error={fieldErrors[key]?.[0]}>
        <Combobox
          options={rows.map((a) => ({ value: String(a.id), label: dotted(a.code, a.name) }))}
          value={form[key]}
          onValueChange={(value) => setForm((prev) => ({ ...prev, [key]: value }))}
          placeholder={t("chooseAccount")}
        />
      </Field>
    );
  }

  async function handleSave() {
    // Only the keys that hold something are sent. An unset one is left to the service's own
    // default rather than being cleared, which it would refuse.
    const payload: OrderDefaultsPayload = { backorder_policy: policy };
    for (const key of ACCOUNT_KEYS) {
      if (form[key]) payload[key] = Number(form[key]);
    }
    setFieldErrors({});
    try {
      await saveDefaults.mutateAsync(payload);
      toast.show({ title: t("saved"), tone: "success" });
    } catch (err) {
      // Inline on the field the backend names — `grn_accrual_account_id`, not "something was
      // wrong somewhere on this form".
      if (isApiError(err)) setFieldErrors(err.fieldErrors);
      showApiError(err, t("saveFailed"));
    }
  }

  // A screen that could not read the settings must say so. Left to itself, every picker
  // would fall back to its "Not set" placeholder and the screen would state, in three
  // places, that a company with a fully seeded posting map has no accounts configured —
  // which is the P4 defect class exactly: rendering perfectly and saying something untrue.
  // `GET /oe/defaults` refuses a caller holding none of the five order-entry permissions,
  // and that is the way to reach this: the sidebar hides the row from them, a typed URL
  // does not.
  if (defaults.isError) {
    return (
      <MaintenancePage title={t("title")} description={t("subtitle")} width="narrow">
        <MaintenanceCard icon={<ShieldAlert className="size-4" />} title={t("accountsTitle")}>
          <p className="py-6 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {t("loadFailed")}
          </p>
        </MaintenanceCard>
      </MaintenancePage>
    );
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
          {picker("grn_accrual_account_id", t("accrualAccount"), accrualControl)}
          {picker("purchase_price_variance_account_id", t("varianceAccount"), ordinary)}
          {picker("landed_cost_clearing_account_id", t("clearingAccount"), ordinary)}
        </div>
        <p className="flex items-start gap-2 pt-3 text-xs text-[var(--vinea-ink-subtle)]">
          <ShieldAlert className="size-4 shrink-0 text-[var(--vinea-warning)]" />
          {t("accrualNote")}
        </p>
        <p className="pt-2 text-xs text-[var(--vinea-ink-subtle)]">{t("clearingNote")}</p>
      </MaintenanceCard>

      <MaintenanceCard icon={<Sliders className="size-4" />} title={t("behaviourTitle")}>
        <div className="space-y-3">
          <Field label={t("backorderPolicy")}>
            <Select
              options={[
                { value: BackorderPolicy.ALLOW, label: t("policyAllow") },
                { value: BackorderPolicy.BLOCK, label: t("policyBlock") },
              ]}
              value={policy}
              onValueChange={(value) => setPolicy(value as BackorderPolicy)}
            />
          </Field>
          <Field label={t("defaultWarehouse")}>
            <p className="px-1 py-1.5 text-xs text-[var(--vinea-ink)]">
              {defaultWarehouse
                ? dotted(defaultWarehouse.code, defaultWarehouse.name)
                : tc("emptyValue")}
            </p>
          </Field>
        </div>
        <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("policyNote")}</p>
        <p className="pt-2 text-xs text-[var(--vinea-ink-subtle)]">{t("warehouseNote")}</p>
      </MaintenanceCard>
    </MaintenancePage>
  );
}
