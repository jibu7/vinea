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
  const t = useTranslations("maintenance");
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

  const controlAccounts = useMemo(
    () => ({
      ar: (accounts.data ?? []).filter((a) => a.control_type === "ar"),
      ap: (accounts.data ?? []).filter((a) => a.control_type === "ap"),
    }),
    [accounts.data],
  );
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
      toast.show({ title: "AR/AP defaults saved", tone: "success" });
    } catch (err) {
      showApiError(err, "Couldn't save AR/AP defaults");
    }
  }

  function picker(key: Key, label: string, options: { value: string; label: string }[], hint: string) {
    return (
      <Field label={label}>
        <Combobox
          options={[{ value: "", label: "Not set" }, ...options]}
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
      title={t("arApDefaults")}
      description="Control accounts and the automatic postings the subledger makes on your behalf"
      width="narrow"
    >
      <MaintenanceCard icon={<Landmark className="size-4" />} title="Control accounts">
        {picker(
          "ar_control_account_id",
          "Accounts receivable control",
          toOptions(controlAccounts.ar, (a) => `${a.code} · ${a.name}`),
          "Choose an AR control account…",
        )}
        {picker(
          "ap_control_account_id",
          "Accounts payable control",
          toOptions(controlAccounts.ap, (a) => `${a.code} · ${a.name}`),
          "Choose an AP control account…",
        )}
        <p className="flex items-start gap-1.5 text-xs text-[var(--vinea-ink-muted)]">
          <ShieldAlert className="mt-0.5 size-3.5 shrink-0 text-[var(--vinea-warning)]" />
          Control accounts are subledger-only: a manual journal aimed at one is rejected with
          <span className="mx-1 font-mono">control_account_direct_posting</span>, and every
          subledger line touching one carries a partner.
        </p>
      </MaintenanceCard>

      <MaintenanceCard icon={<Sliders className="size-4" />} title="Realized exchange difference">
        {picker("realized_fx_gain_account_id", "Realized FX gain", accountOptions, "Choose an account…")}
        {picker("realized_fx_loss_account_id", "Realized FX loss", accountOptions, "Choose an account…")}
        <p className="text-xs text-[var(--vinea-ink-muted)]">
          Posted at allocation, on the allocation date, for the base-currency difference between
          the two documents&rsquo; booking rates. Both keys may point at the same account.
        </p>
      </MaintenanceCard>

      <MaintenanceCard icon={<Percent className="size-4" />} title="Settlement discount">
        {picker(
          "settlement_discount_granted_account_id",
          "Discount granted (AR)",
          accountOptions,
          "Choose an account…",
        )}
        {picker(
          "settlement_discount_received_account_id",
          "Discount received (AP)",
          accountOptions,
          "Choose an account…",
        )}
        <p className="text-xs text-[var(--vinea-ink-muted)]">
          Taken when the allocation happens, not at invoice time, and posted gross — the VAT
          treatment of a discount is a fiscalization-phase question.
        </p>
      </MaintenanceCard>

      <MaintenanceCard icon={<Banknote className="size-4" />} title="Post-dated instruments">
        {picker(
          "post_dated_receivable_account_id",
          "Post-dated receivable",
          accountOptions,
          "Choose an account…",
        )}
        {picker(
          "post_dated_payable_account_id",
          "Post-dated payable",
          accountOptions,
          "Choose an account…",
        )}
        <p className="text-xs text-[var(--vinea-ink-muted)]">
          A receipt or payment with a future maturity date books its cash side here instead of
          the bank, and transfers on or after maturity. It stays allocatable meanwhile.
        </p>
      </MaintenanceCard>

      <div className="flex items-center justify-between">
        {!canEdit && (
          <p className="text-xs text-[var(--vinea-ink-subtle)]">
            These keys live on GL settings — changing them needs
            <span className="mx-1 font-mono">gl:setup_manage</span>.
          </p>
        )}
        <Button
          variant="primary"
          className="ml-auto"
          disabled={!canEdit || updateDefaults.isPending}
          onClick={handleSave}
        >
          {updateDefaults.isPending ? "Saving…" : "Save changes"}
        </Button>
      </div>
    </MaintenancePage>
  );
}
