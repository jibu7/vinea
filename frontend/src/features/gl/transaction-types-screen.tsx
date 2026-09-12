"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
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
import type { InventoryTransactionKind, TransactionType } from "./types";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

/**
 * `gl_transaction_types` is one table with a `module` discriminator, so GL, AR, AP and
 * Inventory get this one screen filtered by module rather than four near-identical pages.
 * Editing rights follow the module: the owning module's setup permission, or the GL one.
 *
 * Inventory is the one module whose types carry a **kind** (P5 decision 9): what the type
 * does to stock. A user adding "Damaged" or "Samples" is adding another `adjustment_out`
 * with its own contra account, and the kind is what tells the posting engine which of those
 * it is — so for `module="inv"` the screen shows the column and requires the field on create.
 * Passing `kinds` is what turns that on; every other module leaves it undefined and the
 * screen is exactly what it was.
 */
export function TransactionTypesScreen({
  module,
  title,
  description,
  codePlaceholder,
  namePlaceholder,
  kinds,
}: {
  module: "gl" | "ar" | "ap" | "inv";
  title: string;
  description: string;
  codePlaceholder: string;
  namePlaceholder: string;
  /** Ordered `[value, label]` pairs. Present only for modules whose types have a kind. */
  kinds?: ReadonlyArray<readonly [InventoryTransactionKind, string]>;
}) {
  const t = useTranslations("maintenance");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();
  const canEdit = hasPermission("gl:setup_manage") || hasPermission(`${module}:setup_manage`);

  const txTypesQuery = useTransactionTypes(module);
  const accountsQuery = useAccounts();
  const accountById = byId(accountsQuery.data);
  // `_assert_usable_default` in the kernel requires postable *and* active, so an inactive
  // account in this list could never be saved.
  const postableAccounts = useMemo(
    () => (accountsQuery.data ?? []).filter((a) => a.is_postable && a.is_active),
    [accountsQuery.data],
  );

  const createTxType = useCreateTransactionType();
  const updateTxType = useUpdateTransactionType();

  const [open, setOpen] = useState(false);
  const [editingType, setEditingType] = useState<TransactionType | null>(null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [defaultAccountId, setDefaultAccountId] = useState("");
  const [kind, setKind] = useState("");
  const kindLabels = useMemo(() => new Map(kinds ?? []), [kinds]);

  function startCreate() {
    setEditingType(null);
    setCode("");
    setName("");
    setDefaultAccountId("");
    setKind("");
    setOpen(true);
  }

  function startEdit(item: TransactionType) {
    setEditingType(item);
    setCode(item.code);
    setName(item.name);
    setDefaultAccountId(item.default_gl_account_id ? String(item.default_gl_account_id) : "");
    setKind(item.kind ?? "");
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
        toast.show({ title: t("transactionTypeUpdated"), tone: "success" });
      } else {
        await createTxType.mutateAsync({
          module,
          code,
          name,
          // The kind is fixed at creation and never editable: changing what an existing type
          // does to stock would silently re-interpret every document already posted with it.
          ...(kinds ? { kind: kind as InventoryTransactionKind } : {}),
          default_gl_account_id: defaultAccountId ? Number(defaultAccountId) : null,
        });
        toast.show({ title: t("transactionTypeCreated"), tone: "success" });
      }
      setOpen(false);
    } catch (err) {
      showApiError(err, t("transactionTypeSaveFailed"));
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
        description: item.is_active ? t("deactivated") : t("activated"),
        tone: "success",
      });
    } catch (err) {
      showApiError(err, t("transactionTypeUpdateFailed"));
    }
  }

  return (
    <MaintenancePage
      title={title}
      description={description}
      actions={
        <Button variant="primary" onClick={startCreate} disabled={!canEdit} className="gap-1.5 text-xs">
          <Plus className="size-3.5" /> {t("newTransactionType")}
        </Button>
      }
    >
      <MaintenanceCard icon={<Layers className="size-4" />} title={title}>
        {(txTypesQuery.data ?? []).length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {txTypesQuery.isLoading ? t("loading") : t("transactionTypesEmpty")}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-20">{t("transactionTypeModule")}</TH>
                <TH className="w-28">{t("code")}</TH>
                <TH>{t("name")}</TH>
                {kinds ? <TH className="w-36">{t("transactionTypeKind")}</TH> : null}
                <TH>{t("defaultAccount")}</TH>
                <TH className="w-32 text-right">{t("transactionTypeStatus")}</TH>
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
                    {kinds ? (
                      <TD className="text-xs text-[var(--vinea-ink-muted)]">
                        {item.kind ? (kindLabels.get(item.kind) ?? item.kind) : t("emptyValue")}
                      </TD>
                    ) : null}
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {defaultAcc ? `${defaultAcc.code} · ${defaultAcc.name}` : t("emptyValue")}
                    </TD>
                    <TD className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => startEdit(item)}
                          aria-label={t("editLabel", { name: item.name })}
                          className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                        >
                          <Edit2 className="size-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => handleToggleActive(item)}
                          disabled={!canEdit}
                          aria-label={t(item.is_active ? "deactivateLabel" : "activateLabel", {
                            name: item.name,
                          })}
                        >
                          <StatusChip tone={item.is_active ? "success" : "neutral"}>
                            {item.is_active ? t("active") : t("inactive")}
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
        <DialogContent
          title={editingType ? t("editTransactionTypeTitle") : t("newTransactionTypeTitle")}
        >
          <div className="space-y-3 pt-2">
            <Field label={t("code")}>
              <Input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                disabled={!!editingType}
                className="font-mono"
                placeholder={codePlaceholder}
              />
            </Field>
            <Field label={t("name")}>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={namePlaceholder}
              />
            </Field>
            {kinds ? (
              <Field label={t("transactionTypeKind")}>
                {editingType ? (
                  // Read-only rather than a disabled control, because it is not a field that
                  // happens to be locked — after creation the kind is simply not editable,
                  // and a greyed-out picker invites the question of how to ungrey it.
                  <p className="px-1 py-1.5 text-xs text-[var(--vinea-ink-muted)]">
                    {editingType.kind
                      ? (kindLabels.get(editingType.kind) ?? editingType.kind)
                      : t("emptyValue")}
                  </p>
                ) : (
                  <Combobox
                    options={kinds.map(([value, label]) => ({ value, label }))}
                    value={kind}
                    onValueChange={setKind}
                    placeholder={t("chooseTransactionTypeKind")}
                  />
                )}
              </Field>
            ) : null}
            <Field label={t("defaultAccount")}>
              <Combobox
                options={toOptions(postableAccounts, (a) => `${a.code} · ${a.name}`)}
                value={defaultAccountId}
                onValueChange={setDefaultAccountId}
                placeholder={t("chooseDefaultAccount")}
              />
            </Field>

            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                {t("cancel")}
              </Button>
              <Button
                variant="primary"
                disabled={!code || !name || !canEdit || (!!kinds && !editingType && !kind)}
                onClick={handleSave}
              >
                {t("save")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </MaintenancePage>
  );
}
