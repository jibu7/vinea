"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Banknote, Landmark, Percent, ShieldAlert, Sliders } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Field } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import { useAccounts } from "@/features/gl/hooks";
import { toOptions } from "@/features/gl/lookups";
import { useArApDefaults, useUpdateArApDefaults } from "@/features/subledger/hooks";
import type { ArApDefaults } from "@/features/subledger/types";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

type Key = keyof ArApDefaults;

const EMPTY: Record<Key, string> = {
  ar_control_account_id: "",
  ap_control_account_id: "",
  realized_fx_gain_account_id: "",
  realized_fx_loss_account_id: "",
  settlement_discount_granted_account_id: "",
  settlement_discount_received_account_id: "",
  post_dated_receivable_account_id: "",
  post_dated_payable_account_id: "",
};

/**
 * The AR/AP keys on `gl_settings`. Control accounts must carry the matching `control_type`
 * — the posting guard rejects anything else — so those two pickers only offer real control
 * accounts; every other key is an ordinary postable account.
 */
export default function ArApDefaultsPage() {
  const t = useTranslations("arap.defaults");
  const tc = useTranslations("arap.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canEdit = useHasPermission()("gl:setup_manage");

  const defaults = useArApDefaults();
  const updateDefaults = useUpdateArApDefaults();
  const accounts = useAccounts();

  const [form, setForm] = useState<Record<Key, string>>(EMPTY);

  useEffect(() => {
    if (!defaults.data) return;
    const next = { ...EMPTY };
    for (const key of Object.keys(EMPTY) as Key[]) {
      const value = defaults.data[key];
      next[key] = value ? String(value) : "";
    }
    setForm(next);
  }, [defaults.data]);

  // `masters.update_ar_ap_defaults` requires postable *and* active on every key, control
  // accounts included — offering an inactive one here only buys the operator a 409.
  const controlAccounts = useMemo(() => {
    const usable = (accounts.data ?? []).filter((a) => a.is_postable && a.is_active);
    return {
      ar: usable.filter((a) => a.control_type === "ar"),
      ap: usable.filter((a) => a.control_type === "ap"),
    };
  }, [accounts.data]);
  const postable = useMemo(
    () => (accounts.data ?? []).filter((a) => a.is_postable && !a.is_control && a.is_active),
    [accounts.data],
  );

  async function handleSave() {
    const payload: Record<string, number | null> = {};
    for (const key of Object.keys(EMPTY) as Key[]) {
      payload[key] = form[key] ? Number(form[key]) : null;
    }
    try {
      await updateDefaults.mutateAsync(payload);
      toast.show({ title: t("saved"), tone: "success" });
    } catch (err) {
      showApiError(err, t("saveFailed"));
    }
  }

  function picker(key: Key, label: string, options: { value: string; label: string }[], hint: string) {
    return (
      <Field label={label}>
        <Combobox
          options={[{ value: "", label: tc("notSet") }, ...options]}
          value={form[key]}
          onValueChange={(v) => setForm({ ...form, [key]: v })}
          placeholder={hint}
        />
      </Field>
    );
  }

  const accountOptions = toOptions(postable, (a) => `${a.code} · ${a.name}`);

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      width="narrow"
    >
      <MaintenanceCard icon={<Landmark className="size-4" />} title={t("controlAccounts")}>
        {picker(
          "ar_control_account_id",
          t("arControl"),
          toOptions(controlAccounts.ar, (a) => `${a.code} · ${a.name}`),
          t("chooseArControl"),
        )}
        {picker(
          "ap_control_account_id",
          t("apControl"),
          toOptions(controlAccounts.ap, (a) => `${a.code} · ${a.name}`),
          t("chooseApControl"),
        )}
        <p className="flex items-start gap-1.5 text-xs text-[var(--vinea-ink-muted)]">
          <ShieldAlert className="mt-0.5 size-3.5 shrink-0 text-[var(--vinea-warning)]" />
          <span>{t("controlNote")}</span>
        </p>
      </MaintenanceCard>

      <MaintenanceCard icon={<Sliders className="size-4" />} title={t("fx")}>
        {picker("realized_fx_gain_account_id", t("fxGain"), accountOptions, t("chooseAccount"))}
        {picker("realized_fx_loss_account_id", t("fxLoss"), accountOptions, t("chooseAccount"))}
        <p className="text-xs text-[var(--vinea-ink-muted)]">{t("fxNote")}</p>
      </MaintenanceCard>

      <MaintenanceCard icon={<Percent className="size-4" />} title={t("discount")}>
        {picker(
          "settlement_discount_granted_account_id",
          t("discountGranted"),
          accountOptions,
          t("chooseAccount"),
        )}
        {picker(
          "settlement_discount_received_account_id",
          t("discountReceived"),
          accountOptions,
          t("chooseAccount"),
        )}
        <p className="text-xs text-[var(--vinea-ink-muted)]">{t("discountNote")}</p>
      </MaintenanceCard>

      <MaintenanceCard icon={<Banknote className="size-4" />} title={t("postDated")}>
        {picker(
          "post_dated_receivable_account_id",
          t("postDatedReceivable"),
          accountOptions,
          t("chooseAccount"),
        )}
        {picker(
          "post_dated_payable_account_id",
          t("postDatedPayable"),
          accountOptions,
          t("chooseAccount"),
        )}
        <p className="text-xs text-[var(--vinea-ink-muted)]">{t("postDatedNote")}</p>
      </MaintenanceCard>

      <div className="flex items-center justify-between">
        {!canEdit && (
          <p className="text-xs text-[var(--vinea-ink-subtle)]">
            {t("readOnlyNote")}
          </p>
        )}
        <Button
          variant="primary"
          className="ml-auto"
          disabled={!canEdit || updateDefaults.isPending}
          onClick={handleSave}
        >
          {updateDefaults.isPending ? tc("saving") : t("saveChanges")}
        </Button>
      </div>
    </MaintenancePage>
  );
}
