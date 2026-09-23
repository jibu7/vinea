"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Edit2, Landmark, Plus, ShieldCheck } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Drawer, DrawerContent } from "@/design/components/drawer";
import { Field, Input } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { QueryState } from "@/design/components/query-state";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/design/components/tabs";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { useAccounts, useCurrencies } from "@/features/gl/hooks";
import { byId } from "@/features/gl/lookups";
import type { Currency } from "@/features/gl/types";
import { AccountClass, BankAccountKind } from "@/lib/api-enums";
import { dotted, formatDate, formatMoney } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { BankRulesPanel } from "./bank-rules-panel";
import {
  useBankAccounts,
  useRegisterBankAccount,
  useUnregisteredBankAccounts,
  useUpdateBankAccount,
} from "./hooks";
import { StatementFormatEditor } from "./statement-format-editor";
import type { BankAccount } from "./types";

/** The bank's own details: the half of the master a person keys, the same on create and edit. */
interface DetailsForm {
  code: string;
  name: string;
  currencyId: string;
  bankName: string;
  accountNumber: string;
  accountHolder: string;
  bankBranch: string;
  swiftBic: string;
}

const BLANK_DETAILS: DetailsForm = {
  code: "",
  name: "",
  currencyId: "",
  bankName: "",
  accountNumber: "",
  accountHolder: "",
  bankBranch: "",
  swiftBic: "",
};

interface NewForm extends DetailsForm {
  kind: BankAccountKind;
  glCode: string;
  glName: string;
  parentId: string;
}

function detailsOf(account: BankAccount): DetailsForm {
  return {
    code: account.code,
    name: account.name,
    currencyId: String(account.currency_id),
    bankName: account.bank_name ?? "",
    accountNumber: account.account_number ?? "",
    accountHolder: account.account_holder ?? "",
    bankBranch: account.bank_branch ?? "",
    swiftBic: account.swift_bic ?? "",
  };
}

/** Text fields as the wire wants them: blank is `null`, so clearing a field clears it. */
function detailsPayload(form: DetailsForm) {
  const text = (value: string) => (value.trim() === "" ? null : value.trim());
  return {
    code: form.code.trim() || null,
    name: form.name.trim() || null,
    currency_id: form.currencyId ? Number(form.currencyId) : null,
    bank_name: text(form.bankName),
    account_number: text(form.accountNumber),
    account_holder: text(form.accountHolder),
    bank_branch: text(form.bankBranch),
    swift_bic: text(form.swiftBic),
  };
}

/**
 * **Bank accounts** — Maintenance → General Ledger, after Defaults (P8 step 6, Appendix C.1.13).
 *
 * A bank account is a **master over a flagged GL account**, never a second balance for it
 * (decision 2). The ledger still holds every figure; what this screen keeps is the three facts
 * the chart of accounts cannot: which currency the account is held in, what the bank calls it,
 * and how its statement exports are laid out. The *last reconciled* columns are the cache of
 * the latest locked reconciliation — a stored row's date and statement balance, verified by
 * `assert_bank_invariants` clause 7 — and not the account's balance.
 *
 * **New** makes the pair in one call: the GL account (an asset, postable, flagged by the kind —
 * none of which is asked, because none of it is a choice) and its master, in the currency
 * chosen. It needs `gl:setup_manage` as well as `bank:setup_manage`, because it writes a chart
 * row. **Register** is the other door: a bank/cash control account that has no master. The
 * hook on `POST /gl/accounts` and the step-1 back-fill cover every path the product has, so
 * that list is empty on a healthy tenant — and says so, rather than hiding.
 *
 * **The currency locks once the account has lines.** Every base amount on the account was
 * frozen at posting under the currency it had (ADR-06); re-denominating it would change what
 * each line "shows on the statement" without touching a posted row. The picker is disabled with
 * the reason beside it, and `bank_account_has_lines` from the server lands on the same field.
 */
export function BankAccountsScreen() {
  const t = useTranslations("banking.accounts");
  const tc = useTranslations("banking.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();
  const canManage = hasPermission("bank:setup_manage");
  const canCreate = canManage && hasPermission("gl:setup_manage");
  const canPreview = hasPermission("bank:statement_import");

  const [includeInactive, setIncludeInactive] = useState(false);
  const accounts = useBankAccounts({ includeInactive });
  const unregistered = useUnregisteredBankAccounts(canManage);
  const glAccounts = useAccounts();
  const currencies = useCurrencies();
  const register = useRegisterBankAccount();
  const update = useUpdateBankAccount();

  const currencyById = byId(currencies.data);
  const glById = byId(glAccounts.data);
  const baseCurrency = (currencies.data ?? []).find((c) => c.is_base);
  const currencyOptions = (currencies.data ?? [])
    .filter((c) => c.is_active)
    .map((c) => ({ value: String(c.id), label: dotted(c.code, c.name) }));
  /** Where a new bank account hangs: a header asset account. `1100 Cash and Bank` in the Rwanda
   * pack, preselected when it is there. */
  const parentOptions = useMemo(
    () =>
      (glAccounts.data ?? [])
        .filter((a) => !a.is_postable && a.class === AccountClass.ASSET && a.is_active)
        .map((a) => ({ value: String(a.id), label: dotted(a.code, a.name) })),
    [glAccounts.data],
  );

  const [newOpen, setNewOpen] = useState(false);
  const [newForm, setNewForm] = useState<NewForm>({
    ...BLANK_DETAILS,
    kind: BankAccountKind.BANK,
    glCode: "",
    glName: "",
    parentId: "",
  });
  const [newErrors, setNewErrors] = useState<Record<string, string[]>>({});

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [details, setDetails] = useState<DetailsForm>(BLANK_DETAILS);
  const [detailErrors, setDetailErrors] = useState<Record<string, string[]>>({});

  const rows = accounts.data ?? [];
  const selected = rows.find((row) => row.id === selectedId) ?? null;

  /** Opening an account loads its details once; a refetch of the same account (after a save,
   * or a rule added on another tab) must not wipe an edit in progress. */
  function openAccount(account: BankAccount) {
    setDetailErrors({});
    setDetails(detailsOf(account));
    setSelectedId(account.id);
  }

  function currencyLike(currency: Currency | undefined) {
    return currency
      ? { code: currency.code, symbol: currency.symbol, decimalPlaces: currency.decimal_places }
      : null;
  }

  function startNew() {
    const defaultParent = (glAccounts.data ?? []).find((a) => a.code === "1100" && !a.is_postable);
    setNewErrors({});
    setNewForm({
      ...BLANK_DETAILS,
      currencyId: baseCurrency ? String(baseCurrency.id) : "",
      kind: BankAccountKind.BANK,
      glCode: "",
      glName: "",
      parentId: defaultParent ? String(defaultParent.id) : "",
    });
    setNewOpen(true);
  }

  async function handleCreate() {
    setNewErrors({});
    const detailsPart = detailsPayload(newForm);
    try {
      const created = await register.mutateAsync({
        new_account: {
          code: newForm.glCode.trim(),
          name: newForm.glName.trim(),
          kind: newForm.kind,
          parent_id: newForm.parentId ? Number(newForm.parentId) : null,
        },
        ...detailsPart,
        // The master's code and name default to the GL account's, which is what the hook
        // would have given it; a blank here means "the same".
        code: detailsPart.code ?? newForm.glCode.trim(),
        name: detailsPart.name ?? newForm.glName.trim(),
      });
      toast.show({ title: t("created"), description: dotted(created.code, created.name), tone: "success" });
      setNewOpen(false);
      openAccount(created);
    } catch (err) {
      if (isApiError(err)) setNewErrors(err.fieldErrors);
      showApiError(err, t("createFailed"));
    }
  }

  async function handleRegister(glAccountId: number) {
    try {
      const created = await register.mutateAsync({ gl_account_id: glAccountId });
      toast.show({ title: t("registered"), description: dotted(created.code, created.name), tone: "success" });
      openAccount(created);
    } catch (err) {
      showApiError(err, t("registerFailed"));
    }
  }

  async function handleSaveDetails() {
    if (!selected) return;
    setDetailErrors({});
    const payload = detailsPayload(details);
    try {
      await update.mutateAsync({
        id: selected.id,
        payload: {
          ...payload,
          // Sent only when it moved: an unchanged currency on an account with lines is not an
          // edit, and must not be refused as one.
          currency_id:
            payload.currency_id !== null && payload.currency_id !== selected.currency_id
              ? payload.currency_id
              : undefined,
        },
      });
      toast.show({ title: t("saved"), tone: "success" });
    } catch (err) {
      if (isApiError(err)) setDetailErrors(err.fieldErrors);
      showApiError(err, t("saveFailed"));
    }
  }

  async function toggleActive(account: BankAccount) {
    try {
      await update.mutateAsync({ id: account.id, payload: { is_active: !account.is_active } });
    } catch (err) {
      showApiError(err, t("saveFailed"));
    }
  }

  function detailFields(
    form: DetailsForm,
    setForm: (next: DetailsForm) => void,
    errors: Record<string, string[]>,
    currencyLocked: boolean,
  ) {
    return (
      <>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("bankName")} error={errors.bank_name?.[0]}>
            <Input value={form.bankName} onChange={(e) => setForm({ ...form, bankName: e.target.value })} />
          </Field>
          <Field label={t("accountNumber")} error={errors.account_number?.[0]}>
            <Input
              value={form.accountNumber}
              onChange={(e) => setForm({ ...form, accountNumber: e.target.value })}
              className="font-mono"
            />
          </Field>
        </div>
        <Field label={t("accountHolder")} error={errors.account_holder?.[0]}>
          <Input
            value={form.accountHolder}
            onChange={(e) => setForm({ ...form, accountHolder: e.target.value })}
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("bankBranch")} error={errors.bank_branch?.[0]}>
            <Input value={form.bankBranch} onChange={(e) => setForm({ ...form, bankBranch: e.target.value })} />
          </Field>
          <Field label={t("swiftBic")} error={errors.swift_bic?.[0]}>
            <Input
              value={form.swiftBic}
              onChange={(e) => setForm({ ...form, swiftBic: e.target.value })}
              className="font-mono"
            />
          </Field>
        </div>
        <Field
          label={t("heldIn")}
          error={errors.currency_id?.[0] ?? (currencyLocked ? t("currencyLocked") : undefined)}
        >
          {currencyLocked ? (
            // Not a disabled combobox: a disabled control reads as "not yet", and this is
            // "never again" — the account's lines were valued under this currency.
            <p
              className="flex h-10 items-center rounded-[var(--radius-control)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-sunken)] px-3 text-sm text-[var(--vinea-ink-muted)]"
              data-testid="currency-locked"
            >
              {(() => {
                const currency = currencyById.get(Number(form.currencyId));
                return currency ? dotted(currency.code, currency.name) : tc("emptyValue");
              })()}
            </p>
          ) : (
            <Combobox
              options={currencyOptions}
              value={form.currencyId}
              onValueChange={(value) => setForm({ ...form, currencyId: value })}
              placeholder={t("chooseCurrency")}
            />
          )}
        </Field>
      </>
    );
  }

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      actions={
        <Button variant="primary" onClick={startNew} disabled={!canCreate} className="gap-1.5 text-xs">
          <Plus className="size-3.5" /> {t("new")}
        </Button>
      }
    >
      <MaintenanceCard
        icon={<Landmark className="size-4" />}
        title={t("cardTitle")}
        actions={
          <label className="flex items-center gap-1.5 text-xs text-[var(--vinea-ink-muted)]">
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(e) => setIncludeInactive(e.target.checked)}
              className="size-3.5"
            />
            {t("showInactive")}
          </label>
        }
      >
        {rows.length === 0 ? (
          <QueryState query={accounts} isEmpty empty={t("empty")} testId="bank-accounts" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-24">{t("code")}</TH>
                <TH>{t("name")}</TH>
                <TH className="w-44">{t("glAccount")}</TH>
                <TH className="w-16">{t("kind")}</TH>
                <TH className="w-16">{t("currency")}</TH>
                <TH>{t("bank")}</TH>
                <TH className="w-40">{t("lastReconciled")}</TH>
                <TH className="w-28 text-right">{tc("status")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((account) => {
                const gl = glById.get(account.gl_account_id);
                const currency = currencyById.get(account.currency_id);
                const like = currencyLike(currency);
                return (
                  <TR key={account.id} data-bank-account={account.code}>
                    <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                      {account.code}
                    </TD>
                    <TD className="text-xs font-medium text-[var(--vinea-ink)]">{account.name}</TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {gl ? dotted(gl.code, gl.name) : tc("emptyValue")}
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink)]">{t(`kindLabel.${account.kind}`)}</TD>
                    <TD className="font-mono text-xs text-[var(--vinea-ink)]">
                      {currency?.code ?? tc("emptyValue")}
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink)]">
                      {account.bank_name ?? tc("emptyValue")}
                      {account.account_number ? (
                        <p className="whitespace-nowrap font-mono text-[11px] text-[var(--vinea-ink-subtle)]">
                          {account.account_number}
                        </p>
                      ) : null}
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {account.last_reconciled_at ? (
                        <>
                          {formatDate(account.last_reconciled_at)}
                          {account.last_reconciled_balance !== null && like ? (
                            <p className="font-mono tabular-nums text-[var(--vinea-ink)]">
                              {formatMoney(Number(account.last_reconciled_balance), like)}
                            </p>
                          ) : null}
                        </>
                      ) : account.kind === BankAccountKind.CASH ? (
                        t("cashNotReconciled")
                      ) : (
                        t("neverReconciled")
                      )}
                    </TD>
                    <TD className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => openAccount(account)}
                          aria-label={t("editLabel", { name: account.name })}
                          className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                        >
                          <Edit2 className="size-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => toggleActive(account)}
                          disabled={!canManage}
                          aria-label={t(account.is_active ? "deactivateLabel" : "activateLabel", {
                            name: account.name,
                          })}
                        >
                          <StatusChip tone={account.is_active ? "success" : "neutral"}>
                            {account.is_active ? tc("active") : tc("inactive")}
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
        <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("masterNote")}</p>
      </MaintenanceCard>

      {canManage ? (
        <MaintenanceCard icon={<ShieldCheck className="size-4" />} title={t("unregisteredTitle")}>
          {(unregistered.data ?? []).length === 0 ? (
            <QueryState
              query={unregistered}
              isEmpty
              empty={t("unregisteredEmpty")}
              testId="unregistered"
            />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH className="w-24">{t("glCode")}</TH>
                  <TH>{t("glName")}</TH>
                  <TH className="w-32 text-right">{t("actions")}</TH>
                </TR>
              </THead>
              <TBody>
                {(unregistered.data ?? []).map((row) => (
                  <TR key={row.id}>
                    <TD className="font-mono text-xs font-semibold">{row.code}</TD>
                    <TD className="text-xs">{row.name}</TD>
                    <TD className="text-right">
                      <Button
                        variant="ghost"
                        className="text-xs"
                        disabled={register.isPending}
                        onClick={() => handleRegister(row.id)}
                      >
                        {t("register")}
                      </Button>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </MaintenanceCard>
      ) : null}

      <Dialog open={newOpen} onOpenChange={setNewOpen}>
        <DialogContent title={t("newTitle")}>
          <div className="space-y-3 pt-2">
            <p className="text-xs text-[var(--vinea-ink-muted)]">{t("newNote")}</p>
            <div className="grid grid-cols-3 gap-3">
              <Field label={t("kind")}>
                <Select
                  options={[
                    { value: BankAccountKind.BANK, label: t("kindLabel.bank") },
                    { value: BankAccountKind.CASH, label: t("kindLabel.cash") },
                  ]}
                  value={newForm.kind}
                  onValueChange={(value) => setNewForm({ ...newForm, kind: value as BankAccountKind })}
                />
              </Field>
              <Field label={t("glCode")} error={newErrors.code?.[0]}>
                <Input
                  value={newForm.glCode}
                  onChange={(e) => setNewForm({ ...newForm, glCode: e.target.value })}
                  className="font-mono"
                  placeholder={t("glCodePlaceholder")}
                />
              </Field>
              <Field label={t("parentAccount")} error={newErrors.parent_id?.[0]}>
                <Combobox
                  options={parentOptions}
                  value={newForm.parentId}
                  onValueChange={(value) => setNewForm({ ...newForm, parentId: value })}
                  placeholder={t("noParent")}
                />
              </Field>
            </div>
            <Field label={t("glName")} error={newErrors.name?.[0]}>
              <Input
                value={newForm.glName}
                onChange={(e) => setNewForm({ ...newForm, glName: e.target.value })}
                placeholder={t("glNamePlaceholder")}
              />
            </Field>
            {detailFields(
              newForm,
              (next) => setNewForm({ ...newForm, ...next }),
              newErrors,
              false,
            )}
            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setNewOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button
                variant="primary"
                disabled={!newForm.glCode.trim() || !newForm.glName.trim() || !canCreate || register.isPending}
                onClick={handleCreate}
              >
                {tc("create")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Drawer open={selected !== null} onOpenChange={(open) => !open && setSelectedId(null)}>
        {selected ? (
          <DrawerContent
            title={dotted(selected.code, selected.name)}
            description={t("drawerDescription", {
              gl: (() => {
                const gl = glById.get(selected.gl_account_id);
                return gl ? dotted(gl.code, gl.name) : String(selected.gl_account_id);
              })(),
              kind: t(`kindLabel.${selected.kind}`),
            })}
            className="max-w-3xl overflow-y-auto"
          >
            <Tabs defaultValue="details">
              <TabsList className="mb-4">
                <TabsTrigger value="details">{t("detailsTab")}</TabsTrigger>
                {selected.kind === BankAccountKind.BANK ? (
                  <>
                    <TabsTrigger value="format">{t("formatTab")}</TabsTrigger>
                    <TabsTrigger value="rules">{t("rulesTab")}</TabsTrigger>
                  </>
                ) : null}
              </TabsList>

              <TabsContent value="details" className="space-y-3">
                <div className="grid grid-cols-3 gap-3">
                  <Field label={t("code")} error={detailErrors.code?.[0]}>
                    <Input
                      value={details.code}
                      onChange={(e) => setDetails({ ...details, code: e.target.value })}
                      className="font-mono"
                    />
                  </Field>
                  <Field label={t("name")} error={detailErrors.name?.[0]} className="col-span-2">
                    <Input value={details.name} onChange={(e) => setDetails({ ...details, name: e.target.value })} />
                  </Field>
                </div>
                {detailFields(details, setDetails, detailErrors, selected.has_lines)}
                {selected.kind === BankAccountKind.CASH ? (
                  <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("cashNote")}</p>
                ) : null}
                <div className="flex justify-end pt-2">
                  <Button
                    variant="primary"
                    disabled={!canManage || update.isPending}
                    onClick={handleSaveDetails}
                  >
                    {update.isPending ? tc("saving") : t("saveDetails")}
                  </Button>
                </div>
              </TabsContent>

              {selected.kind === BankAccountKind.BANK ? (
                <>
                  <TabsContent value="format">
                    <StatementFormatEditor
                      account={selected}
                      currency={currencyById.get(selected.currency_id)}
                      canManage={canManage}
                      canPreview={canPreview}
                    />
                  </TabsContent>
                  <TabsContent value="rules">
                    <BankRulesPanel account={selected} canManage={canManage} />
                  </TabsContent>
                </>
              ) : null}
            </Tabs>
          </DrawerContent>
        ) : null}
      </Drawer>
    </MaintenancePage>
  );
}
