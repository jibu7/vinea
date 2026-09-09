"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { BadgeCheck, Edit2, Plus } from "lucide-react";
import { Button } from "@/design/components/button";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import { useCreateSalesRep, useSalesReps, useUpdateSalesRep } from "@/features/subledger/hooks";
import type { SalesRep } from "@/features/subledger/types";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

export default function SalesRepsPage() {
  const t = useTranslations("maintenance");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canEdit = useHasPermission()("ar:setup_manage");

  const [includeInactive, setIncludeInactive] = useState(false);
  const reps = useSalesReps({ includeInactive });
  const createRep = useCreateSalesRep();
  const updateRep = useUpdateSalesRep();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<SalesRep | null>(null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");

  function startCreate() {
    setEditing(null);
    setCode("");
    setName("");
    setEmail("");
    setOpen(true);
  }

  function startEdit(rep: SalesRep) {
    setEditing(rep);
    setCode(rep.code);
    setName(rep.name);
    setEmail(rep.email ?? "");
    setOpen(true);
  }

  async function handleSave() {
    try {
      if (editing) {
        await updateRep.mutateAsync({ repId: editing.id, payload: { name, email: email || null } });
        toast.show({ title: "Sales representative updated", tone: "success" });
      } else {
        await createRep.mutateAsync({ code, name, email: email || null });
        toast.show({ title: "Sales representative created", tone: "success" });
      }
      setOpen(false);
    } catch (err) {
      showApiError(err, "Couldn't save sales representative");
    }
  }

  async function toggleActive(rep: SalesRep) {
    try {
      await updateRep.mutateAsync({ repId: rep.id, payload: { is_active: !rep.is_active } });
      toast.show({
        title: rep.code,
        description: rep.is_active ? "Deactivated" : "Activated",
        tone: "success",
      });
    } catch (err) {
      showApiError(err, "Couldn't update sales representative");
    }
  }

  return (
    <MaintenancePage
      title={t("salesReps")}
      description="Attributed on customer settings and carried onto AR documents"
      actions={
        <Button variant="primary" onClick={startCreate} disabled={!canEdit} className="gap-1.5 text-xs">
          <Plus className="size-3.5" /> New sales rep
        </Button>
      }
    >
      <MaintenanceCard
        icon={<BadgeCheck className="size-4" />}
        title="Sales representatives"
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
        {(reps.data ?? []).length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {reps.isLoading ? "Loading…" : "No sales representatives yet."}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-28">Code</TH>
                <TH>Name</TH>
                <TH>Email</TH>
                <TH className="w-32 text-right">Status</TH>
              </TR>
            </THead>
            <TBody>
              {(reps.data ?? []).map((rep) => (
                <TR key={rep.id}>
                  <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">{rep.code}</TD>
                  <TD className="text-xs font-medium text-[var(--vinea-ink)]">{rep.name}</TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">{rep.email ?? "—"}</TD>
                  <TD className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => startEdit(rep)}
                        aria-label={`Edit ${rep.name}`}
                        className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                      >
                        <Edit2 className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleActive(rep)}
                        disabled={!canEdit}
                        aria-label={`${rep.is_active ? "Deactivate" : "Activate"} ${rep.name}`}
                      >
                        <StatusChip tone={rep.is_active ? "success" : "neutral"}>
                          {rep.is_active ? "Active" : "Inactive"}
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
        <DialogContent title={editing ? `Edit ${editing.name}` : "New sales representative"}>
          <div className="space-y-3 pt-2">
            <Field label="Code">
              <Input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                disabled={!!editing}
                className="font-mono"
                placeholder="REP01"
              />
            </Field>
            <Field label="Name">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Claudine Ineza" />
            </Field>
            <Field label="Email">
              <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={!code || !name || !canEdit} onClick={handleSave}>
                Save
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </MaintenancePage>
  );
}
