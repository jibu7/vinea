"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Landmark, Sliders } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Field } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { useToast } from "@/design/components/toast";
import { useFiscalItemClasses } from "@/features/fiscal/hooks";
import { useAccounts, useGLSettings, useUpdateGLSettings } from "@/features/gl/hooks";
import type { GLAccount, GLSettingsPayload } from "@/features/gl/types";
import { AccountClass } from "@/lib/api-enums";
import { dotted } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

/** Every account key this screen writes. The two P2 ones, then P7's five. */
type AccountKey =
  | "retained_earnings_account_id"
  | "rounding_difference_account_id"
  | "vat_settlement_account_id"
  | "ar_revaluation_account_id"
  | "ap_revaluation_account_id"
  | "unrealized_fx_gain_account_id"
  | "unrealized_fx_loss_account_id";

const ACCOUNT_KEYS: readonly AccountKey[] = [
  "retained_earnings_account_id",
  "rounding_difference_account_id",
  "vat_settlement_account_id",
  "ar_revaluation_account_id",
  "ap_revaluation_account_id",
  "unrealized_fx_gain_account_id",
  "unrealized_fx_loss_account_id",
];

const EMPTY = Object.fromEntries(ACCOUNT_KEYS.map((key) => [key, ""])) as Record<
  AccountKey,
  string
>;

/** The P&L classes, for the two unrealized-FX contras. Named once rather than inline twice. */
const PROFIT_AND_LOSS = [AccountClass.INCOME, AccountClass.EXPENSE];

/**
 * GL defaults — the account-determination chain's company-wide keys.
 *
 * P7 adds a **Tax and revaluation** block: the VAT settlement account, the two revaluation
 * contras and the unrealized gain/loss pair, plus the default purchase class code. All six
 * were seeded by the Rwanda pack at step 1 and had nowhere to be *set* until this screen — a
 * tenant that did not come from the pack had a VAT return that refused to file
 * (`required_setting` on `vat_settlement_account_id`) and no way in the product to fix it.
 *
 * **Each picker offers a different list**, and the difference is the phase's design rather
 * than a preference (P7 decisions 12 and 13):
 *
 * * **VAT settlement** offers **liabilities**. One account, one balance: a net payable sits
 *   as a credit and a net credit position as a debit on the same account, which is the shape
 *   an accountant reconciles against RRA's own statement.
 * * **AR revaluation** offers **assets** and **AP revaluation** **liabilities**. They are
 *   deliberately *not* the AR/AP control accounts: a control account's balance is the sum of
 *   open items at their booking rates, and a revaluation posted there would break exactly the
 *   invariant `assert_subledger_invariants` proves.
 * * **Unrealized gain / loss** offer **income and expense**, and both lists are the same
 *   because an SME that books both to one P&L account is making a legitimate choice — what
 *   they may not do is book an unrealized movement to the balance sheet.
 *
 * **No control account is offered anywhere on this screen**, and `_postable_account` refuses
 * one server-side along with anything of the wrong class (`invalid_gl_setting_account`,
 * `invalid_gl_setting_account_class`). A picker that can only ask for what the service will
 * accept is the version of that rule a person can see.
 */
export default function DefaultsPage() {
  const t = useTranslations("maintenance");
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const settingsQuery = useGLSettings();
  const updateSettings = useUpdateGLSettings();
  const accountsQuery = useAccounts();

  const [form, setForm] = useState<Record<AccountKey, string>>(EMPTY);
  const [purchaseClass, setPurchaseClass] = useState("");
  const [classSearch, setClassSearch] = useState("");
  const itemClasses = useFiscalItemClasses(classSearch);

  /** Postable, active, and never a control account — the floor every picker here sits on. */
  const usable = useMemo(
    () => (accountsQuery.data ?? []).filter((a) => a.is_postable && a.is_active && !a.is_control),
    [accountsQuery.data],
  );
  const inClass = (classes: readonly string[]): GLAccount[] =>
    usable.filter((account) => classes.includes(account.class));

  useEffect(() => {
    if (!settingsQuery.data) return;
    const next = { ...EMPTY };
    for (const key of ACCOUNT_KEYS) {
      const value = settingsQuery.data[key];
      next[key] = value ? String(value) : "";
    }
    setForm(next);
    setPurchaseClass(settingsQuery.data.fiscal_default_purchase_class_code ?? "");
    setClassSearch(settingsQuery.data.fiscal_default_purchase_class_code ?? "");
  }, [settingsQuery.data]);

  async function handleSave() {
    // Only the keys that hold something are sent: an unset one is left alone rather than
    // cleared, which would turn a configured company into one whose next VAT filing refuses.
    const payload: GLSettingsPayload = {};
    for (const key of ACCOUNT_KEYS) {
      if (form[key]) payload[key] = Number(form[key]);
    }
    if (purchaseClass) payload.fiscal_default_purchase_class_code = purchaseClass;
    else payload.clear_fiscal_default_purchase_class_code = true;
    try {
      await updateSettings.mutateAsync(payload);
      toast.show({ title: t("saved"), tone: "success" });
    } catch (err) {
      showApiError(err, t("glDefaultsSaveFailed"));
    }
  }

  function picker(key: AccountKey, label: string, rows: GLAccount[]) {
    return (
      <Field label={label}>
        <Combobox
          options={rows.map((a) => ({ value: String(a.id), label: dotted(a.code, a.name) }))}
          value={form[key]}
          onValueChange={(value) => setForm((prev) => ({ ...prev, [key]: value }))}
          placeholder={t("chooseAccount")}
        />
      </Field>
    );
  }

  return (
    <MaintenancePage
      title={t("defaults")}
      description={t("defaultsSubtitle")}
      width="narrow"
      actions={
        <Button
          variant="primary"
          disabled={updateSettings.isPending}
          onClick={handleSave}
          className="text-xs"
        >
          {updateSettings.isPending ? t("saving") : t("save")}
        </Button>
      }
    >
      <MaintenanceCard icon={<Sliders className="size-4" />} title={t("generalSettings")}>
        <div className="space-y-3">
          {picker("retained_earnings_account_id", t("retainedEarningsAccount"), usable)}
          {picker("rounding_difference_account_id", t("roundingDifferenceAccount"), usable)}
        </div>
      </MaintenanceCard>

      <MaintenanceCard icon={<Landmark className="size-4" />} title={t("taxDefaults")}>
        <div className="space-y-3">
          {picker(
            "vat_settlement_account_id",
            t("vatSettlementAccount"),
            inClass([AccountClass.LIABILITY]),
          )}
          {picker(
            "ar_revaluation_account_id",
            t("arRevaluationAccount"),
            inClass([AccountClass.ASSET]),
          )}
          {picker(
            "ap_revaluation_account_id",
            t("apRevaluationAccount"),
            inClass([AccountClass.LIABILITY]),
          )}
          {picker(
            "unrealized_fx_gain_account_id",
            t("unrealizedFxGainAccount"),
            inClass(PROFIT_AND_LOSS),
          )}
          {picker(
            "unrealized_fx_loss_account_id",
            t("unrealizedFxLossAccount"),
            inClass(PROFIT_AND_LOSS),
          )}
          <Field label={t("defaultPurchaseClass")}>
            <Combobox
              options={[
                { value: "", label: t("emptyValue") },
                ...(itemClasses.data ?? []).map((row) => ({
                  value: row.item_cls_cd,
                  label: dotted(row.item_cls_cd, row.item_cls_nm),
                })),
              ]}
              value={purchaseClass}
              onValueChange={setPurchaseClass}
              onSearch={setClassSearch}
              fallbackLabel={purchaseClass}
              placeholder={t("searchItemClass")}
            />
          </Field>
        </div>
        <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("taxDefaultsNote")}</p>
        <p className="pt-2 text-xs text-[var(--vinea-ink-subtle)]">
          {t("defaultPurchaseClassNote")}
        </p>
      </MaintenanceCard>
    </MaintenancePage>
  );
}
