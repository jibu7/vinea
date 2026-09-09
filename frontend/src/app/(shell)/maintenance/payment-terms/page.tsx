"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { CalendarClock, Edit2, Plus } from "lucide-react";
import { Button } from "@/design/components/button";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import {
  useCreatePaymentTerms,
  usePaymentTerms,
  useUpdatePaymentTerms,
} from "@/features/subledger/hooks";
import {
  DUE_BASES,
  DUE_BASIS_MESSAGE,
  DUE_SUMMARY_MESSAGE,
  type DueBasis,
  type PaymentTerms,
  type PaymentTermsPayload,
} from "@/features/subledger/types";
import { trimDecimalString } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

const BLANK: PaymentTermsPayload = {
  code: "",
  name: "",
  due_basis: "days_from_document_date",
  due_days: 30,
  due_day_of_month: null,
  discount_percent: "0",
  discount_days: 0,
};

export default function PaymentTermsPage() {
  const t = useTranslations("arap.paymentTerms");
  const tc = useTranslations("arap.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();
  const canEdit = hasPermission("ar:setup_manage") || hasPermission("ap:setup_manage");

  const [includeInactive, setIncludeInactive] = useState(false);
  const terms = usePaymentTerms({ includeInactive });
  const createTerms = useCreatePaymentTerms();
  const updateTerms = useUpdatePaymentTerms();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<PaymentTerms | null>(null);
  const [form, setForm] = useState<PaymentTermsPayload>(BLANK);

  function startCreate() {
    setEditing(null);
    setForm(BLANK);
    setOpen(true);
  }

  function startEdit(row: PaymentTerms) {
    setEditing(row);
    setForm({
      code: row.code,
      name: row.name,
      due_basis: row.due_basis,
      due_days: row.due_days,
      due_day_of_month: row.due_day_of_month,
      discount_percent: trimDecimalString(row.discount_percent),
      discount_days: row.discount_days,
      is_active: row.is_active,
    });
    setOpen(true);
  }

  async function handleSave() {
    try {
      if (editing) {
        await updateTerms.mutateAsync({ termsId: editing.id, payload: form });
        toast.show({ title: t("updated"), tone: "success" });
      } else {
        await createTerms.mutateAsync(form);
        toast.show({ title: t("created"), tone: "success" });
      }
      setOpen(false);
    } catch (err) {
      showApiError(err, t("saveFailed"));
    }
  }

  // PUT replaces the row, so a status flip resends everything the form holds.
  async function toggleActive(row: PaymentTerms) {
    try {
      await updateTerms.mutateAsync({
        termsId: row.id,
        payload: {
          code: row.code,
          name: row.name,
          due_basis: row.due_basis,
          due_days: row.due_days,
          due_day_of_month: row.due_day_of_month,
          discount_percent: row.discount_percent,
          discount_days: row.discount_days,
          is_active: !row.is_active,
        },
      });
      toast.show({
        title: row.code,
        description: row.is_active ? tc("deactivated") : tc("activated"),
        tone: "success",
      });
    } catch (err) {
      showApiError(err, t("updateFailed"));
    }
  }

  const needsDayOfMonth = form.due_basis === "fixed_day_of_month";

  /** Reads the row back the way a clerk would say it out loud, in the active locale. */
  function describeDue(row: PaymentTerms): string {
    return t(DUE_SUMMARY_MESSAGE[row.due_basis], {
      days: row.due_days,
      day: row.due_day_of_month ?? tc("emptyValue"),
    });
  }

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      actions={
        <Button variant="primary" onClick={startCreate} disabled={!canEdit} className="gap-1.5 text-xs">
          <Plus className="size-3.5" /> {t("new")}
        </Button>
      }
    >
      <MaintenanceCard
        icon={<CalendarClock className="size-4" />}
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
        {(terms.data ?? []).length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {terms.isLoading ? tc("loading") : t("empty")}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-24">{tc("code")}</TH>
                <TH>{tc("name")}</TH>
                <TH>{t("due")}</TH>
                <TH className="w-40">{t("discount")}</TH>
                <TH className="w-32 text-right">{tc("status")}</TH>
              </TR>
            </THead>
            <TBody>
              {(terms.data ?? []).map((row) => (
                <TR key={row.id}>
                  <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">{row.code}</TD>
                  <TD className="text-xs font-medium text-[var(--vinea-ink)]">{row.name}</TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">{describeDue(row)}</TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {Number(row.discount_percent) > 0
                      ? t("discountSummary", {
                          percent: Number(row.discount_percent),
                          days: row.discount_days,
                        })
                      : t("noDiscount")}
                  </TD>
                  <TD className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => startEdit(row)}
                        aria-label={tc("editLabel", { name: row.name })}
                        className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                      >
                        <Edit2 className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleActive(row)}
                        disabled={!canEdit}
                        aria-label={tc(row.is_active ? "deactivateLabel" : "activateLabel", {
                          name: row.name,
                        })}
                      >
                        <StatusChip tone={row.is_active ? "success" : "neutral"}>
                          {row.is_active ? tc("active") : tc("inactive")}
                        </StatusChip>
                      </button>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </MaintenanceCard>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title={editing ? t("editTitle", { name: editing.name }) : t("new")}>
          <div className="space-y-3 pt-2">
            <div className="grid grid-cols-2 gap-3">
              <Field label={tc("code")}>
                <Input
                  value={form.code}
                  onChange={(e) => setForm({ ...form, code: e.target.value })}
                  disabled={!!editing}
                  className="font-mono"
                  placeholder={t("codePlaceholder")}
                />
              </Field>
              <Field label={tc("name")}>
                <Input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder={t("namePlaceholder")}
                />
              </Field>
            </div>

            <Field label={t("dueBasis")}>
              <Select
                options={DUE_BASES.map((value) => ({
                  value,
                  label: t(DUE_BASIS_MESSAGE[value]),
                }))}
                value={form.due_basis}
                onValueChange={(v) =>
                  setForm({
                    ...form,
                    due_basis: v as DueBasis,
                    due_day_of_month: v === "fixed_day_of_month" ? (form.due_day_of_month ?? 1) : null,
                  })
                }
              />
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label={t("dueDays")}>
                <Input
                  type="number"
                  min={0}
                  max={365}
                  value={form.due_days}
                  disabled={needsDayOfMonth}
                  onChange={(e) => setForm({ ...form, due_days: Number(e.target.value) })}
                  className="text-right font-mono tabular-nums"
                />
              </Field>
              <Field label={t("dayOfMonth")}>
                <Input
                  type="number"
                  min={1}
                  max={31}
                  value={form.due_day_of_month ?? ""}
                  disabled={!needsDayOfMonth}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      due_day_of_month: e.target.value ? Number(e.target.value) : null,
                    })
                  }
                  className="text-right font-mono tabular-nums"
                />
              </Field>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <Field label={t("discountPercent")}>
                <Input
                  inputMode="decimal"
                  value={form.discount_percent}
                  onChange={(e) => setForm({ ...form, discount_percent: e.target.value })}
                  className="text-right font-mono tabular-nums"
                />
              </Field>
              <Field label={t("discountDays")}>
                <Input
                  type="number"
                  min={0}
                  max={365}
                  value={form.discount_days}
                  onChange={(e) => setForm({ ...form, discount_days: Number(e.target.value) })}
                  className="text-right font-mono tabular-nums"
                />
              </Field>
            </div>

            <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("discountNote")}</p>

            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button variant="primary" disabled={!form.code || !form.name || !canEdit} onClick={handleSave}>
                {tc("save")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </MaintenancePage>
  );
}
