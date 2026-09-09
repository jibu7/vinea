"use client";

import { useMemo, useState } from "react";
import { Edit2, Layers, Plus } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import { useAccounts, useCreateTransactionType, useTransactionTypes, useUpdateTransactionType } from "./hooks";
import { byId, toOptions } from "./lookups";
import type { TransactionType } from "./types";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

/**
 * `gl_transaction_types` is one table with a `module` discriminator, so GL, AR and AP get
 * this one screen filtered by module rather than three near-identical pages. Editing rights
 * follow the module: the owning module's setup permission, or the GL one.
 */
export function TransactionTypesScreen({
  module,
  title,
  description,
  codePlaceholder,
  namePlaceholder,
}: {
  module: "gl" | "ar" | "ap";
  title: string;
  description: string;
  codePlaceholder: string;
  namePlaceholder: string;
}) {
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();
  const canEdit = hasPermission("gl:setup_manage") || hasPermission(`${module}:setup_manage`);

  const txTypesQuery = useTransactionTypes(module);
  const accountsQuery = useAccounts();
  const accountById = byId(accountsQuery.data);
  const postableAccounts = useMemo(
    () => (accountsQuery.data ?? []).filter((a) => a.is_postable),
    [accountsQuery.data],
  );

  const createTxType = useCreateTransactionType();
  const updateTxType = useUpdateTransactionType();

  const [open, setOpen] = useState(false);
  const [editingType, setEditingType] = useState<TransactionType | null>(null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [defaultAccountId, setDefaultAccountId] = useState("");

  function startCreate() {
    setEditingType(null);
    setCode("");
    setName("");
    setDefaultAccountId("");
    setOpen(true);
  }

  function startEdit(item: TransactionType) {
    setEditingType(item);
    setCode(item.code);
    setName(item.name);
    setDefaultAccountId(item.default_gl_account_id ? String(item.default_gl_account_id) : "");
    setOpen(true);
  }

  async function handleSave() {
    try {
      if (editingType) {
        await updateTxType.mutateAsync({
          typeId: editingType.id,
          payload: {
            name,
            default_gl_account_id: defaultAccountId ? Number(defaultAccountId) : null,
          },
        });
        toast.show({ title: "Transaction type updated", tone: "success" });
      } else {
        await createTxType.mutateAsync({
          module,
          code,
          name,
          default_gl_account_id: defaultAccountId ? Number(defaultAccountId) : null,
        });
        toast.show({ title: "Transaction type created", tone: "success" });
      }
      setOpen(false);
    } catch (err) {
      showApiError(err, "Couldn't save transaction type");
    }
  }

  async function handleToggleActive(item: TransactionType) {
    try {
      await updateTxType.mutateAsync({
        typeId: item.id,
        payload: { is_active: !item.is_active },
      });
      toast.show({
        title: item.code,
        description: item.is_active ? "Deactivated" : "Activated",
        tone: "success",
      });
    } catch (err) {
      showApiError(err, "Couldn't update transaction type");
    }
  }

  return (
    <MaintenancePage
      title={title}
      description={description}
      actions={
        <Button variant="primary" onClick={startCreate} disabled={!canEdit} className="gap-1.5 text-xs">
          <Plus className="size-3.5" /> New transaction type
        </Button>
      }
    >
      <MaintenanceCard icon={<Layers className="size-4" />} title={title}>
        {(txTypesQuery.data ?? []).length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {txTypesQuery.isLoading ? "Loading…" : "No transaction types for this module yet."}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-20">Module</TH>
                <TH className="w-28">Code</TH>
                <TH>Name</TH>
                <TH>Default GL Account</TH>
                <TH className="w-32 text-right">Status</TH>
              </TR>
            </THead>
            <TBody>
              {(txTypesQuery.data ?? []).map((item) => {
                const defaultAcc = item.default_gl_account_id
                  ? accountById.get(item.default_gl_account_id)
                  : undefined;
                return (
                  <TR key={item.id}>
                    <TD className="font-mono text-xs uppercase text-[var(--vinea-ink-subtle)]">
                      {item.module}
                    </TD>
                    <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">{item.code}</TD>
                    <TD className="text-xs font-medium text-[var(--vinea-ink)]">{item.name}</TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {defaultAcc ? `${defaultAcc.code} · ${defaultAcc.name}` : "—"}
                    </TD>
                    <TD className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => startEdit(item)}
                          aria-label={`Edit ${item.name}`}
                          className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                        >
                          <Edit2 className="size-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => handleToggleActive(item)}
                          disabled={!canEdit}
                          aria-label={`${item.is_active ? "Deactivate" : "Activate"} ${item.name}`}
                        >
                          <StatusChip tone={item.is_active ? "success" : "neutral"}>
                            {item.is_active ? "Active" : "Inactive"}
                          </StatusChip>
                        </button>
                      </div>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        )}
      </MaintenanceCard>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title={editingType ? "Edit transaction type" : "New transaction type"}>
          <div className="space-y-3 pt-2">
            <Field label="Code">
              <Input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                disabled={!!editingType}
                className="font-mono"
                placeholder={codePlaceholder}
              />
            </Field>
            <Field label="Name">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={namePlaceholder}
              />
            </Field>
            <Field label="Default GL account">
              <Combobox
                options={toOptions(postableAccounts, (a) => `${a.code} · ${a.name}`)}
                value={defaultAccountId}
                onValueChange={setDefaultAccountId}
                placeholder="Choose default GL account…"
              />
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
