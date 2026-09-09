"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Field, Input } from "@/design/components/input";
import { Select } from "@/design/components/select";
import { useToast } from "@/design/components/toast";
import { useAccounts, useBranches, useProjects, useTaxCodes } from "@/features/gl/hooks";
import { toOptions } from "@/features/gl/lookups";
import { trimDecimalString } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { usePartnerSettings, usePaymentTerms, useSalesReps, useSavePartnerSettings } from "./hooks";
import type { PartnerRole, RoleSettingsPayload, TaxMode } from "./types";

const NONE = "";

function idOrNull(value: string): number | null {
  return value ? Number(value) : null;
}

/**
 * The per-role settings block: control-account override, terms, credit limit, sales rep and
 * the posting defaults. One form for both roles — `role` picks the endpoint, the control
 * accounts on offer, whether a sales rep is meaningful, and which `arap.role.*` copy applies.
 */
export function PartnerSettingsForm({
  role,
  partnerId,
  canEdit,
}: {
  role: PartnerRole;
  partnerId: number;
  canEdit: boolean;
}) {
  const t = useTranslations("arap.settings");
  const tc = useTranslations("arap.common");
  const tr = useTranslations(`arap.role.${role}`);
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const settings = usePartnerSettings(role, partnerId);
  const save = useSavePartnerSettings(role);
  const accounts = useAccounts();
  const branches = useBranches();
  const projects = useProjects();
  const taxCodes = useTaxCodes();
  const terms = usePaymentTerms();
  const reps = useSalesReps();

  const [controlAccountId, setControlAccountId] = useState(NONE);
  const [paymentTermsId, setPaymentTermsId] = useState(NONE);
  const [creditLimit, setCreditLimit] = useState("");
  const [salesRepId, setSalesRepId] = useState(NONE);
  const [taxCodeId, setTaxCodeId] = useState(NONE);
  const [branchId, setBranchId] = useState(NONE);
  const [projectId, setProjectId] = useState(NONE);
  const [glAccountId, setGlAccountId] = useState(NONE);
  const [taxMode, setTaxMode] = useState<TaxMode>("exclusive");
  const [onHold, setOnHold] = useState(false);

  useEffect(() => {
    const row = settings.data;
    setControlAccountId(row?.control_account_id ? String(row.control_account_id) : NONE);
    setPaymentTermsId(row?.payment_terms_id ? String(row.payment_terms_id) : NONE);
    setCreditLimit(row?.credit_limit ? trimDecimalString(row.credit_limit) : "");
    setSalesRepId(row?.sales_rep_id ? String(row.sales_rep_id) : NONE);
    setTaxCodeId(row?.default_tax_code_id ? String(row.default_tax_code_id) : NONE);
    setBranchId(row?.default_branch_id ? String(row.default_branch_id) : NONE);
    setProjectId(row?.default_project_id ? String(row.default_project_id) : NONE);
    setGlAccountId(row?.default_gl_account_id ? String(row.default_gl_account_id) : NONE);
    setTaxMode(row?.tax_mode ?? "exclusive");
    setOnHold(row?.is_on_hold ?? false);
  }, [settings.data]);

  // Only a control account of this role's own type may override the default — the posting
  // guard rejects anything else at the engine, so never offer it here.
  const controlAccounts = useMemo(
    () => (accounts.data ?? []).filter((a) => a.control_type === role && a.is_active),
    [accounts.data, role],
  );
  const revenueOrCostAccounts = useMemo(
    () => (accounts.data ?? []).filter((a) => a.is_postable && !a.is_control && a.is_active),
    [accounts.data],
  );

  async function handleSave() {
    const payload: RoleSettingsPayload = {
      control_account_id: idOrNull(controlAccountId),
      payment_terms_id: idOrNull(paymentTermsId),
      // Blank means "no limit" (NULL); an explicit 0 means no credit at all.
      credit_limit: creditLimit.trim() === "" ? null : creditLimit.trim(),
      sales_rep_id: role === "ar" ? idOrNull(salesRepId) : null,
      default_tax_code_id: idOrNull(taxCodeId),
      default_branch_id: idOrNull(branchId),
      default_project_id: idOrNull(projectId),
      default_gl_account_id: idOrNull(glAccountId),
      tax_mode: taxMode,
      is_on_hold: onHold,
    };
    try {
      await save.mutateAsync({ partnerId, payload });
      toast.show({ title: t("saved"), tone: "success" });
    } catch (err) {
      showApiError(err, t("saveFailed"));
    }
  }

  const noneOption = { value: NONE, label: tc("none") };

  return (
    <div className="space-y-3">
      <Field label={tr("controlAccountOverride")}>
        <Combobox
          options={[
            { value: NONE, label: t("companyDefault") },
            ...toOptions(controlAccounts, (a) => `${a.code} · ${a.name}`),
          ]}
          value={controlAccountId}
          onValueChange={setControlAccountId}
          placeholder={t("companyDefault")}
        />
      </Field>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label={t("paymentTerms")}>
          <Combobox
            options={[noneOption, ...toOptions(terms.data ?? [], (x) => `${x.code} · ${x.name}`)]}
            value={paymentTermsId}
            onValueChange={setPaymentTermsId}
            placeholder={tc("none")}
          />
        </Field>
        <Field label={t("creditLimit")}>
          <Input
            value={creditLimit}
            onChange={(e) => setCreditLimit(e.target.value)}
            inputMode="decimal"
            placeholder={t("creditLimitPlaceholder")}
            className="text-right font-mono tabular-nums"
          />
        </Field>
      </div>

      {role === "ar" && (
        <Field label={t("salesRep")}>
          <Combobox
            options={[noneOption, ...toOptions(reps.data ?? [], (r) => `${r.code} · ${r.name}`)]}
            value={salesRepId}
            onValueChange={setSalesRepId}
            placeholder={tc("none")}
          />
        </Field>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label={t("defaultTaxCode")}>
          <Combobox
            options={[noneOption, ...toOptions(taxCodes.data ?? [], (x) => `${x.code} · ${x.name}`)]}
            value={taxCodeId}
            onValueChange={setTaxCodeId}
            placeholder={tc("none")}
          />
        </Field>
        <Field label={t("taxMode")}>
          <Select
            options={[
              { value: "exclusive", label: t("taxModeExclusive") },
              { value: "inclusive", label: t("taxModeInclusive") },
            ]}
            value={taxMode}
            onValueChange={(v) => setTaxMode(v as TaxMode)}
          />
        </Field>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label={t("defaultBranch")}>
          <Combobox
            options={[noneOption, ...toOptions(branches.data ?? [], (b) => `${b.code} · ${b.name}`)]}
            value={branchId}
            onValueChange={setBranchId}
            placeholder={tc("none")}
          />
        </Field>
        <Field label={t("defaultProject")}>
          <Combobox
            options={[noneOption, ...toOptions(projects.data ?? [], (x) => `${x.code} · ${x.name}`)]}
            value={projectId}
            onValueChange={setProjectId}
            placeholder={tc("none")}
          />
        </Field>
      </div>

      <Field label={tr("defaultGlAccount")}>
        <Combobox
          options={[
            noneOption,
            ...toOptions(revenueOrCostAccounts, (a) => `${a.code} · ${a.name}`),
          ]}
          value={glAccountId}
          onValueChange={setGlAccountId}
          placeholder={tc("none")}
        />
      </Field>

      <label className="flex items-center gap-2 text-xs text-[var(--vinea-ink-muted)]">
        <input
          type="checkbox"
          checked={onHold}
          onChange={(e) => setOnHold(e.target.checked)}
          className="size-3.5"
        />
        {tr("onHold")}
      </label>

      <div className="flex justify-end pt-2">
        <Button
          variant="primary"
          disabled={!canEdit || save.isPending || settings.isLoading}
          onClick={handleSave}
        >
          {save.isPending ? tc("saving") : t("saveSettings")}
        </Button>
      </div>
    </div>
  );
}
