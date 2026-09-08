"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft, Edit2, Layers, Plus } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { StatusChip } from "@/design/components/status-chip";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useToast } from "@/design/components/toast";
import {
  useAccounts,
  useCreateTransactionType,
  useTransactionTypes,
  useUpdateTransactionType,
} from "@/features/gl/hooks";
import { byId, toOptions } from "@/features/gl/lookups";
import type { TransactionType } from "@/features/gl/types";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

export default function TransactionTypesPage() {
  const t = useTranslations("maintenance");
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const txTypesQuery = useTransactionTypes("gl");
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
          module: "gl",
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
    <div className="flex min-h-screen flex-col" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]" aria-label="Back">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{t("transactionTypes")}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">
              Determination chain rules for General Ledger (ADR-05)
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="primary" onClick={startCreate} className="gap-1.5 text-xs">
            <Plus className="size-3.5" /> {t("newTransactionType")}
          </Button>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-4xl space-y-6">
          <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 space-y-4">
            <div className="flex items-center gap-2">
              <Layers className="size-4 text-[var(--vinea-brand)]" />
              <h2 className="font-display text-base font-semibold">{t("transactionTypesTitle")}</h2>
            </div>

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
                      <TD className="text-xs font-mono uppercase text-[var(--vinea-ink-subtle)]">{item.module}</TD>
                      <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">{item.code}</TD>
                      <TD className="font-medium text-xs text-[var(--vinea-ink)]">{item.name}</TD>
                      <TD className="text-xs text-[var(--vinea-ink-muted)]">
                        {defaultAcc ? `${defaultAcc.code} · ${defaultAcc.name}` : "—"}
                      </TD>
                      <TD className="text-right">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => startEdit(item)}
                            aria-label={`Edit ${item.name}`}
                            className="p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)] rounded"
                          >
                            <Edit2 className="size-3.5" />
                          </button>
                          <button type="button" onClick={() => handleToggleActive(item)}>
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
          </div>
        </div>
      </main>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title={editingType ? "Edit Transaction Type" : t("newTransactionType")}>
          <div className="space-y-3 pt-2">
            <Field label={t("code")}>
              <Input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                disabled={!!editingType}
                placeholder="EXP_PAYROLL"
              />
            </Field>
            <Field label={t("name")}>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Payroll Expense Posting" />
            </Field>
            <Field label={t("defaultAccount")}>
              <Combobox
                options={toOptions(postableAccounts, (a) => `${a.code} · ${a.name}`)}
                value={defaultAccountId}
                onValueChange={setDefaultAccountId}
                placeholder="Choose default GL account…"
              />
            </Field>

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
