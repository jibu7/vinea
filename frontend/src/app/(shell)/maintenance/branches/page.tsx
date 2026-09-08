"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft, Edit2, GitBranch, Plus } from "lucide-react";
import { Button } from "@/design/components/button";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { StatusChip } from "@/design/components/status-chip";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useToast } from "@/design/components/toast";
import { useBranches, useCreateBranch, useUpdateBranch } from "@/features/gl/hooks";
import type { Branch } from "@/features/gl/types";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

export default function BranchesPage() {
  const t = useTranslations("maintenance");
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const branchesQuery = useBranches();
  const createBranch = useCreateBranch();
  const updateBranch = useUpdateBranch();

  const [open, setOpen] = useState(false);
  const [editingBranch, setEditingBranch] = useState<Branch | null>(null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [isMain, setIsMain] = useState(false);

  function startCreate() {
    setEditingBranch(null);
    setCode("");
    setName("");
    setIsMain(false);
    setOpen(true);
  }

  function startEdit(b: Branch) {
    setEditingBranch(b);
    setCode(b.code);
    setName(b.name);
    setIsMain(b.is_main);
    setOpen(true);
  }

  async function handleSave() {
    try {
      if (editingBranch) {
        await updateBranch.mutateAsync({
          branchId: editingBranch.id,
          payload: { name, is_main: isMain },
        });
        toast.show({ title: "Branch updated", tone: "success" });
      } else {
        await createBranch.mutateAsync({ code, name, is_main: isMain });
        toast.show({ title: "Branch created", tone: "success" });
      }
      setOpen(false);
    } catch (err) {
      showApiError(err, "Couldn't save branch");
    }
  }

  async function handleToggleActive(b: Branch) {
    try {
      await updateBranch.mutateAsync({
        branchId: b.id,
        payload: { is_active: !b.is_active },
      });
      toast.show({
        title: b.code,
        description: b.is_active ? "Deactivated" : "Activated",
        tone: "success",
      });
    } catch (err) {
      showApiError(err, "Couldn't update branch");
    }
  }

  return (
    <div className="flex min-h-screen flex-col" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{t("branches")}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">
              Operational locations and costing centers
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="primary" onClick={startCreate} className="gap-1.5 text-xs">
            <Plus className="size-3.5" /> {t("newBranch")}
          </Button>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-4xl space-y-6">
          <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 space-y-4">
            <div className="flex items-center gap-2">
              <GitBranch className="size-4 text-[var(--vinea-brand)]" />
              <h2 className="font-display text-base font-semibold">{t("branchesTitle")}</h2>
            </div>

            <Table>
              <THead>
                <TR>
                  <TH className="w-24">Code</TH>
                  <TH>Branch Name</TH>
                  <TH className="w-32">Type</TH>
                  <TH className="w-32 text-right">Status</TH>
                </TR>
              </THead>
              <TBody>
                {(branchesQuery.data ?? []).map((b) => (
                  <TR key={b.id}>
                    <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">{b.code}</TD>
                    <TD className="font-medium text-xs text-[var(--vinea-ink)]">{b.name}</TD>
                    <TD>
                      {b.is_main ? (
                        <StatusChip tone="warning">{t("isMainBranch")}</StatusChip>
                      ) : (
                        <StatusChip tone="neutral">Branch</StatusChip>
                      )}
                    </TD>
                    <TD className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => startEdit(b)}
                          className="p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)] rounded"
                        >
                          <Edit2 className="size-3.5" />
                        </button>
                        {!b.is_main ? (
                          <button type="button" onClick={() => handleToggleActive(b)}>
                            <StatusChip tone={b.is_active ? "success" : "neutral"}>
                              {b.is_active ? "Active" : "Inactive"}
                            </StatusChip>
                          </button>
                        ) : (
                          <StatusChip tone="success">Active</StatusChip>
                        )}
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>
        </div>
      </main>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title={editingBranch ? "Edit Branch" : t("newBranch")}>
          <div className="space-y-3 pt-2">
            <Field label={t("code")}>
              <Input value={code} onChange={(e) => setCode(e.target.value)} disabled={!!editingBranch} placeholder="MUS" />
            </Field>
            <Field label={t("name")}>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Musanze Branch" />
            </Field>
            <div className="pt-2">
              <label className="flex items-center gap-2 text-sm font-medium cursor-pointer">
                <input
                  type="checkbox"
                  checked={isMain}
                  onChange={(e) => setIsMain(e.target.checked)}
                  className="size-4 accent-[var(--vinea-brand)]"
                />
                {t("isMainBranch")}
              </label>
            </div>

            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
              <Button variant="primary" disabled={!code || !name} onClick={handleSave}>
                {t("save")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
