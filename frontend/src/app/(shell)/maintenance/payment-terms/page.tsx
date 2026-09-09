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
  DUE_BASIS_LABELS,
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

/** Reads the row back the way a clerk would say it out loud. */
function describeDue(terms: PaymentTerms): string {
  switch (terms.due_basis) {
    case "days_from_document_date":
      return `${terms.due_days} days from document date`;
    case "days_from_end_of_month":
      return `${terms.due_days} days from end of month`;
    case "fixed_day_of_month":
      return `Day ${terms.due_day_of_month ?? "—"} of the following month`;
  }
}

export default function PaymentTermsPage() {
  const t = useTranslations("maintenance");
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
        toast.show({ title: "Payment terms updated", tone: "success" });
      } else {
        await createTerms.mutateAsync(form);
        toast.show({ title: "Payment terms created", tone: "success" });
      }
      setOpen(false);
    } catch (err) {
      showApiError(err, "Couldn't save payment terms");
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
        description: row.is_active ? "Deactivated" : "Activated",
        tone: "success",
      });
    } catch (err) {
      showApiError(err, "Couldn't update payment terms");
    }
  }

  const needsDayOfMonth = form.due_basis === "fixed_day_of_month";

  return (
    <MaintenancePage
      title={t("paymentTerms")}
      description="Due-date basis and the settlement discount, which is taken at allocation — not at invoice time"
      actions={
        <Button variant="primary" onClick={startCreate} disabled={!canEdit} className="gap-1.5 text-xs">
          <Plus className="size-3.5" /> New payment terms
        </Button>
      }
    >
      <MaintenanceCard
        icon={<CalendarClock className="size-4" />}
        title="Payment terms"
        actions={
          <label className="flex items-center gap-1.5 text-xs text-[var(--vinea-ink-muted)]">
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(e) => setIncludeInactive(e.target.checked)}
              className="size-3.5"
            />
            Show inactive
          </label>
        }
      >
        {(terms.data ?? []).length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {terms.isLoading ? "Loading…" : "No payment terms yet."}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-24">Code</TH>
                <TH>Name</TH>
                <TH>Due</TH>
                <TH className="w-40">Settlement discount</TH>
                <TH className="w-32 text-right">Status</TH>
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
                      ? `${Number(row.discount_percent)}% within ${row.discount_days} days`
                      : "None"}
                  </TD>
                  <TD className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => startEdit(row)}
                        aria-label={`Edit ${row.name}`}
                        className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                      >
                        <Edit2 className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleActive(row)}
                        disabled={!canEdit}
                        aria-label={`${row.is_active ? "Deactivate" : "Activate"} ${row.name}`}
                      >
                        <StatusChip tone={row.is_active ? "success" : "neutral"}>
                          {row.is_active ? "Active" : "Inactive"}
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
        <DialogContent title={editing ? `Edit ${editing.name}` : "New payment terms"}>
          <div className="space-y-3 pt-2">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Code">
                <Input
                  value={form.code}
                  onChange={(e) => setForm({ ...form, code: e.target.value })}
                  disabled={!!editing}
                  className="font-mono"
                  placeholder="NET30"
                />
              </Field>
              <Field label="Name">
                <Input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="Net 30 days"
                />
              </Field>
            </div>

            <Field label="Due basis">
              <Select
                options={(Object.keys(DUE_BASIS_LABELS) as DueBasis[]).map((value) => ({
                  value,
                  label: DUE_BASIS_LABELS[value],
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
              <Field label="Due days">
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
              <Field label="Day of month">
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
              <Field label="Discount percent">
                <Input
                  inputMode="decimal"
                  value={form.discount_percent}
                  onChange={(e) => setForm({ ...form, discount_percent: e.target.value })}
                  className="text-right font-mono tabular-nums"
                />
              </Field>
              <Field label="Discount days">
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

            <p className="text-xs text-[var(--vinea-ink-subtle)]">
              A discount posts to the settlement-discount account when the allocation happens
              inside the discount window — gross, with no VAT adjustment in this phase.
            </p>

            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={!form.code || !form.name || !canEdit} onClick={handleSave}>
                Save
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </MaintenancePage>
  );
}
