"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft, Edit2, Percent, Plus } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { DatePicker } from "@/design/components/date-picker";
import { Dialog, DialogContent, DialogTrigger } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { StatusChip } from "@/design/components/status-chip";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useToast } from "@/design/components/toast";
import {
  useAccounts,
  useCreateTaxCode,
  useTaxCodes,
  useUpdateTaxCode,
} from "@/features/gl/hooks";
import { byId, toOptions } from "@/features/gl/lookups";
import type { TaxCode } from "@/features/gl/types";
import { dotted, formatDate } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

export default function TaxTypesPage() {
  const t = useTranslations("maintenance");
  const tCommon = useTranslations("common");
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const taxCodesQuery = useTaxCodes();
  const accountsQuery = useAccounts();
  const accountById = byId(accountsQuery.data);
  const postableAccounts = useMemo(
    () => (accountsQuery.data ?? []).filter((a) => a.is_postable),
    [accountsQuery.data],
  );

  const createTaxCode = useCreateTaxCode();
  const updateTaxCode = useUpdateTaxCode();

  // Dialog state
  const [open, setOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [nature, setNature] = useState<string>("output");
  const [ratePct, setRatePct] = useState("18");
  const [glAccountId, setGlAccountId] = useState("");
  const [validFrom, setValidFrom] = useState<Date>(new Date());
  const [validTo, setValidTo] = useState<Date | undefined>(undefined);

  function startCreate() {
    setEditingId(null);
    setCode("");
    setName("");
    setNature("output");
    setRatePct("18");
    setGlAccountId("");
    setValidFrom(new Date());
    setValidTo(undefined);
    setOpen(true);
  }

  function startEdit(tc: TaxCode) {
    setEditingId(tc.id);
    setCode(tc.code);
    setName(tc.name);
    setNature(tc.nature);
    setRatePct(String(Number(tc.rate_pct)));
    setGlAccountId(tc.gl_account_id ? String(tc.gl_account_id) : "");
    setValidFrom(new Date(tc.valid_from));
    setValidTo(tc.valid_to ? new Date(tc.valid_to) : undefined);
    setOpen(true);
  }

  async function handleSave() {
    try {
      if (editingId) {
        await updateTaxCode.mutateAsync({
          codeId: editingId,
          payload: {
            name,
            rate_pct: ratePct,
            gl_account_id: glAccountId ? Number(glAccountId) : null,
            clear_gl_account: !glAccountId,
            valid_to: validTo ? validTo.toISOString().slice(0, 10) : null,
          },
        });
        toast.show({ title: t("taxCodeUpdated"), tone: "success" });
      } else {
        await createTaxCode.mutateAsync({
          code,
          name,
          nature,
          rate_pct: ratePct,
          gl_account_id: glAccountId ? Number(glAccountId) : null,
          valid_from: validFrom.toISOString().slice(0, 10),
          valid_to: validTo ? validTo.toISOString().slice(0, 10) : null,
        });
        toast.show({ title: t("taxCodeCreated"), tone: "success" });
      }
      setOpen(false);
    } catch (err) {
      showApiError(err, t("taxCodeSaveFailed"));
    }
  }

  async function handleToggleActive(tc: TaxCode) {
    try {
      await updateTaxCode.mutateAsync({
        codeId: tc.id,
        payload: { is_active: !tc.is_active },
      });
      toast.show({
        title: tc.code,
        description: tc.is_active ? "Deactivated" : "Activated",
        tone: "success",
      });
    } catch (err) {
      showApiError(err, t("taxCodeUpdateFailed"));
    }
  }

  function formatNatureLabel(natureVal: string): string {
    switch (natureVal) {
      case "output":
        return t("outputVat");
      case "input":
        return t("inputVat");
      case "exempt":
        return t("exempt");
      case "zero_rated":
        return t("zeroRated");
      default:
        return natureVal;
    }
  }

  return (
    <div className="flex min-h-screen flex-col" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]" aria-label={tCommon("back")}>
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{t("taxTypes")}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">{t("taxesSubtitle")}</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="primary" onClick={startCreate} className="gap-1.5 text-xs">
            <Plus className="size-3.5" /> {t("newTaxCode")}
          </Button>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-5xl space-y-6">
          <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 space-y-4">
            <div className="flex items-center gap-2">
              <Percent className="size-4 text-[var(--vinea-brand)]" />
              <h2 className="font-display text-base font-semibold">{t("taxCodes")}</h2>
            </div>

            <Table>
              <THead>
                <TR>
                  <TH className="w-32">{t("code")}</TH>
                  <TH>{t("taxNameAndNature")}</TH>
                  <TH className="w-24 text-right">{t("rate")}</TH>
                  <TH>{t("glAccount")}</TH>
                  <TH className="w-48">{t("validityWindow")}</TH>
                  <TH className="w-36 text-right">{t("status")}</TH>
                </TR>
              </THead>
              <TBody>
                {(taxCodesQuery.data ?? []).map((tc) => {
                  const acc = tc.gl_account_id ? accountById.get(tc.gl_account_id) : undefined;
                  const rateNum = Number(tc.rate_pct);
                  return (
                    <TR key={tc.id}>
                      <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">{tc.code}</TD>
                      <TD>
                        <p className="font-medium text-xs text-[var(--vinea-ink)]">{tc.name}</p>
                        <p className="text-[11px] text-[var(--vinea-ink-subtle)]">{formatNatureLabel(tc.nature)}</p>
                      </TD>
                      <TD className="text-right font-mono text-xs font-semibold">{t("percentValue", { value: rateNum })}</TD>
                      <TD className="text-xs text-[var(--vinea-ink)]">
                        {acc ? dotted(acc.code, acc.name) : t("emptyValue")}
                      </TD>
                      <TD className="text-xs text-[var(--vinea-ink-muted)]">
                        {formatDate(tc.valid_from)} {tc.valid_to ? `– ${formatDate(tc.valid_to)}` : "– Indefinite"}
                      </TD>
                      <TD className="text-right">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => startEdit(tc)}
                            aria-label={t("editLabel", { name: tc.code })}
                            className="p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)] rounded"
                          >
                            <Edit2 className="size-3.5" />
                          </button>
                          <button type="button" onClick={() => handleToggleActive(tc)}>
                            <StatusChip tone={tc.is_active ? "success" : "neutral"}>
                              {tc.is_active ? "Active" : "Inactive"}
                            </StatusChip>
                          </button>
                        </div>
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          </div>
        </div>
      </main>

      {/* Modal Dialog */}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title={editingId ? "Edit Tax Code" : t("newTaxCode")}>
          <div className="space-y-3 pt-2">
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("code")}>
                <Input value={code} onChange={(e) => setCode(e.target.value)} disabled={!!editingId} placeholder={t("taxCodePlaceholder")} />
              </Field>
              <Field label={t("ratePercent")}>
                <Input value={ratePct} onChange={(e) => setRatePct(e.target.value)} type="number" step="any" placeholder={t("taxRatePlaceholder")} />
              </Field>
            </div>
            <Field label={t("name")}>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("taxNamePlaceholder")} />
            </Field>
            {!editingId && (
              <Field label={t("nature")}>
                <select
                  value={nature}
                  onChange={(e) => setNature(e.target.value)}
                  className="h-10 w-full rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] px-3 text-sm text-[var(--vinea-ink)]"
                >
                  <option value="output">{t("outputVat")}</option>
                  <option value="input">{t("inputVat")}</option>
                  <option value="exempt">{t("exempt")}</option>
                  <option value="zero_rated">{t("zeroRated")}</option>
                </select>
              </Field>
            )}
            <Field label={t("glAccount")}>
              <Combobox
                options={toOptions(postableAccounts, (a) => `${a.code} · ${a.name}`)}
                value={glAccountId}
                onValueChange={setGlAccountId}
                placeholder={t("chooseGlPostingAccount")}
              />
            </Field>
            {!editingId && (
              <Field label={t("validFrom")}>
                <DatePicker value={validFrom} onValueChange={setValidFrom} />
              </Field>
            )}

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setOpen(false)}>{t("cancel")}</Button>
              <Button variant="primary" disabled={!name || !ratePct} onClick={handleSave}>
                {t("save")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
