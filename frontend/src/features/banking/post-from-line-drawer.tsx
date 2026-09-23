"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Drawer, DrawerContent } from "@/design/components/drawer";
import { Field, Input } from "@/design/components/input";
import { LineGrid, emptyLineGridRow, type LineErrors, type LineGridRow } from "@/design/components/line-grid";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/design/components/tabs";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { byId, toOptions, useGLLookups } from "@/features/gl/lookups";
import { usePartners } from "@/features/subledger/hooks";
import { ControlType } from "@/lib/api-enums";
import { newDraftId } from "@/lib/drafts";
import { dotted, formatDate } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useAccountMoney } from "./account-picker";
import { usePostCashbookFromLine, usePostSettlementFromLine, usePrefill } from "./hooks";
import type { BankAccount, StatementLine } from "./types";

type Tab = "cashbook" | "settlement";

/**
 * What *Post from line* may do for this user, per tab — **two permissions each** (decision 11):
 * working the match is `bank:reconcile`, and writing the ledger is the posting's own —
 * `gl:journal_post` for a cashbook entry, `ar:transactions_post` for a receipt on a credit line,
 * `ap:transactions_post` for a payment on a debit line. The workspace's button is drawn only
 * when at least one tab is open, so the button reflects both.
 */
export function usePostFromLinePermissions(line: Pick<StatementLine, "amount"> | null) {
  const has = useHasPermission();
  const credit = line !== null && Number(line.amount) > 0;
  const reconcile = has("bank:reconcile");
  const cashbook = reconcile && has("gl:journal_post");
  const settlement = reconcile && has(credit ? "ar:transactions_post" : "ap:transactions_post");
  return { cashbook, settlement, any: cashbook || settlement, role: credit ? ("ar" as const) : ("ap" as const) };
}

/**
 * **Post from line** — how the ledger catches up with the bank (decision 4).
 *
 * The statement shows a movement the ledger lacks; somebody posts it, through the kernel, and the
 * posting and its `posted_from_statement` match are written **in one transaction** — a posted
 * line is never left unmatched, and a refused posting leaves no match. Two drawers in one:
 *
 * * **Cashbook entry** — the P3 cashbook batch's own grid, one row. The date, the amount and the
 *   description come off the statement line; the account and tax code come off the first
 *   `bank_rules` row whose pattern the description contains, and where no rule matches, off the
 *   Banking defaults — `bank_charges_account_id` for a debit, `bank_interest_account_id` for a
 *   credit. A rule **suggests**: nothing here posts until *Post* is pressed. The amount cell is
 *   fixed, because the server posts the line's own amount and an edit would be discarded.
 * * **Receipt / payment** — a P4 settlement to the partner chosen, **unallocated**: which invoices
 *   it pays is the allocation screen's decision, not a bank statement's.
 *
 * Each post carries a draft UUID as `Idempotency-Key`, minted when the drawer opens.
 */
export function PostFromLineDrawer({
  line,
  account,
  onClose,
}: {
  line: StatementLine | null;
  account: BankAccount;
  onClose: () => void;
}) {
  const t = useTranslations("banking.postFromLine");
  const tc = useTranslations("banking.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const permitted = usePostFromLinePermissions(line);
  const { money } = useAccountMoney(account);
  const { accounts, branches, projects, taxCodes } = useGLLookups();
  const prefill = usePrefill(line?.id ?? null);
  const partners = usePartners(permitted.role);
  const postCashbook = usePostCashbookFromLine();
  const postSettlement = usePostSettlementFromLine();

  const [tab, setTab] = useState<Tab>("cashbook");
  const [entryDate, setEntryDate] = useState("");
  const [reference, setReference] = useState("");
  const [rows, setRows] = useState<LineGridRow[]>([emptyLineGridRow()]);
  const [lineErrors, setLineErrors] = useState<LineErrors>({});
  const [partnerId, setPartnerId] = useState("");
  const [documentDate, setDocumentDate] = useState("");
  const [settlementReference, setSettlementReference] = useState("");
  const [settlementDescription, setSettlementDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [cashbookKey, setCashbookKey] = useState(() => newDraftId());
  const [settlementKey, setSettlementKey] = useState(() => newDraftId());

  // Filled once per line, when its prefill arrives. A refetch of the same prefill must not wipe
  // what the person has changed since.
  const [filledFor, setFilledFor] = useState<number | null>(null);
  useEffect(() => {
    if (!line || !prefill.data || filledFor === line.id) return;
    const mainBranch = (branches.data ?? []).find((b) => b.is_main);
    const magnitude = String(Math.abs(Number(line.amount)));
    setEntryDate(line.value_date);
    setReference(line.reference ?? "");
    setRows([
      emptyLineGridRow({
        accountId: prefill.data.gl_account_id ? String(prefill.data.gl_account_id) : "",
        description: prefill.data.description,
        amount: magnitude,
        taxCodeId: prefill.data.tax_code_id ? String(prefill.data.tax_code_id) : "",
        branchId: mainBranch ? String(mainBranch.id) : "",
      }),
    ]);
    setLineErrors({});
    const rulePartner =
      prefill.data.partner_id !== null && prefill.data.partner_type === permitted.role
        ? String(prefill.data.partner_id)
        : "";
    setPartnerId(rulePartner);
    setDocumentDate(line.value_date);
    setSettlementReference(line.reference ?? "");
    setSettlementDescription(line.description);
    setError(null);
    setCashbookKey(newDraftId());
    setSettlementKey(newDraftId());
    setTab(!permitted.cashbook || (rulePartner && permitted.settlement) ? "settlement" : "cashbook");
    setFilledFor(line.id);
  }, [line, prefill.data, filledFor, branches.data, permitted.role, permitted.cashbook, permitted.settlement]);

  const glById = byId(accounts.data);
  const ruleNote = useMemo(() => {
    if (!prefill.data) return null;
    const target = prefill.data.gl_account_id ? glById.get(prefill.data.gl_account_id) : undefined;
    const name = target ? dotted(target.code, target.name) : tc("emptyValue");
    return prefill.data.rule_id !== null ? t("prefilledByRule", { account: name }) : t("prefilledByDefault", { account: name });
  }, [prefill.data, glById, t, tc]);

  if (!line) return null;
  const credit = Number(line.amount) > 0;
  const row = rows[0];

  async function handleCashbook() {
    if (!line || !row?.accountId) return;
    setError(null);
    setLineErrors({});
    try {
      const posted = await postCashbook.mutateAsync({
        statementLineId: line.id,
        payload: {
          gl_account_id: Number(row.accountId),
          tax_code_id: row.taxCodeId ? Number(row.taxCodeId) : null,
          description: row.description.trim() || null,
          reference: reference.trim() || null,
          entry_date: entryDate || null,
          branch_id: row.branchId ? Number(row.branchId) : null,
          project_id: row.projectId ? Number(row.projectId) : null,
        },
        idempotencyKey: cashbookKey,
      });
      toast.show({ title: t("posted", { number: posted.entry_number }), tone: "success" });
      onClose();
    } catch (err) {
      if (isApiError(err)) {
        const next: LineErrors = {};
        for (const [key, messages] of Object.entries(err.fieldErrors ?? {})) {
          const match = key.match(/^lines\.(\d+)\.(.+)$/);
          const field = match ? match[2] : key;
          if (["gl_account_id", "tax_code_id", "description", "branch_id", "project_id"].includes(field)) {
            next[0] = { ...(next[0] ?? {}), [field]: messages.join(", ") };
          }
        }
        setLineErrors(next);
        setError(err.message);
      } else showApiError(err, t("postFailed"));
    }
  }

  async function handleSettlement() {
    if (!line || !partnerId) return;
    setError(null);
    try {
      const posted = await postSettlement.mutateAsync({
        statementLineId: line.id,
        payload: {
          partner_id: Number(partnerId),
          description: settlementDescription.trim() || null,
          reference: settlementReference.trim() || null,
          document_date: documentDate || null,
        },
        idempotencyKey: settlementKey,
      });
      toast.show({
        title: t("posted", { number: posted.document_number ?? posted.entry_number }),
        tone: "success",
      });
      onClose();
    } catch (err) {
      if (isApiError(err)) setError(err.message);
      else showApiError(err, t("postFailed"));
    }
  }

  const partnerOptions = (partners.data ?? [])
    .filter((partner) => (credit ? partner.is_customer : partner.is_supplier))
    .map((partner) => ({
      value: String(partner.id),
      label: dotted(credit ? partner.customer_code : partner.supplier_code, partner.name),
    }));

  return (
    <Drawer open onOpenChange={(open) => !open && onClose()}>
      <DrawerContent
        title={t("title")}
        description={t("description", {
          date: formatDate(line.value_date),
          text: line.description,
          amount: money(line.amount),
        })}
        className="max-w-4xl overflow-y-auto"
      >
        <div className="space-y-4" data-testid="post-from-line">
          <dl className="grid grid-cols-3 gap-3 text-xs">
            <div>
              <dt className="text-[var(--vinea-ink-subtle)]">{t("lineDate")}</dt>
              <dd className="text-[var(--vinea-ink)]">{formatDate(line.value_date)}</dd>
            </div>
            <div>
              <dt className="text-[var(--vinea-ink-subtle)]">{t("lineDirection")}</dt>
              <dd className="text-[var(--vinea-ink)]">{credit ? t("moneyIn") : t("moneyOut")}</dd>
            </div>
            <div>
              <dt className="text-[var(--vinea-ink-subtle)]">{t("lineAmount")}</dt>
              <dd className="font-mono tabular-nums text-[var(--vinea-ink)]" data-testid="post-from-line-amount">
                {money(line.amount)}
              </dd>
            </div>
          </dl>

          <Tabs value={tab} onValueChange={(value) => setTab(value as Tab)}>
            <TabsList className="mb-4">
              <TabsTrigger value="cashbook">{t("cashbookTab")}</TabsTrigger>
              <TabsTrigger value="settlement">{credit ? t("receiptTab") : t("paymentTab")}</TabsTrigger>
            </TabsList>

            <TabsContent value="cashbook" className="space-y-4">
              {!permitted.cashbook ? (
                <p className="text-xs text-[var(--vinea-ink-muted)]" data-testid="cashbook-not-permitted">
                  {t("cashbookNotPermitted")}
                </p>
              ) : (
                <>
                  {ruleNote ? (
                    <p className="text-xs text-[var(--vinea-ink-muted)]" data-testid="prefill-note">
                      {ruleNote}
                    </p>
                  ) : null}
                  <div className="grid grid-cols-3 gap-3">
                    <Field label={t("entryDate")}>
                      <IsoDatePicker value={entryDate} onValueChange={setEntryDate} />
                    </Field>
                    <Field label={t("kind")}>
                      <p className="flex h-10 items-center text-sm text-[var(--vinea-ink)]">
                        {credit ? t("receipt") : t("payment")}
                      </p>
                    </Field>
                    <Field label={t("reference")}>
                      <Input value={reference} onChange={(e) => setReference(e.target.value)} />
                    </Field>
                  </div>
                  <LineGrid
                    mode="cashbook"
                    rows={rows}
                    onRowsChange={setRows}
                    errors={lineErrors}
                    maxRows={1}
                    fixedAmount
                    accountOptions={toOptions(
                      (accounts.data ?? []).filter(
                        (a) =>
                          a.is_postable && a.control_type !== ControlType.BANK && a.control_type !== ControlType.CASH,
                      ),
                      (a) => `${a.code} · ${a.name}`,
                    )}
                    branchOptions={toOptions(branches.data, (b) => `${b.code} · ${b.name}`)}
                    projectOptions={toOptions(projects.data, (p) => `${p.code} · ${p.name}`)}
                    taxCodeOptions={toOptions(taxCodes.data, (tc2) => `${tc2.code} (${tc2.rate_pct}%)`)}
                  />
                  <div className="flex justify-end">
                    <Button
                      variant="primary"
                      disabled={!row?.accountId || postCashbook.isPending}
                      onClick={handleCashbook}
                    >
                      {postCashbook.isPending ? t("posting") : t("postCashbook")}
                    </Button>
                  </div>
                </>
              )}
            </TabsContent>

            <TabsContent value="settlement" className="space-y-4">
              {!permitted.settlement ? (
                <p className="text-xs text-[var(--vinea-ink-muted)]" data-testid="settlement-not-permitted">
                  {credit ? t("receiptNotPermitted") : t("paymentNotPermitted")}
                </p>
              ) : (
                <>
                  <p className="text-xs text-[var(--vinea-ink-muted)]">{t("unallocatedNote")}</p>
                  <div className="grid grid-cols-2 gap-3">
                    <Field label={credit ? t("customer") : t("supplier")}>
                      <Combobox
                        options={partnerOptions}
                        value={partnerId}
                        onValueChange={setPartnerId}
                        placeholder={credit ? t("chooseCustomer") : t("chooseSupplier")}
                      />
                    </Field>
                    <Field label={t("documentDate")}>
                      <IsoDatePicker value={documentDate} onValueChange={setDocumentDate} />
                    </Field>
                    <Field label={t("settlementReference")}>
                      <Input value={settlementReference} onChange={(e) => setSettlementReference(e.target.value)} />
                    </Field>
                    <Field label={t("settlementDescription")}>
                      <Input
                        value={settlementDescription}
                        onChange={(e) => setSettlementDescription(e.target.value)}
                      />
                    </Field>
                  </div>
                  <div className="flex justify-end">
                    <Button
                      variant="primary"
                      disabled={!partnerId || postSettlement.isPending}
                      onClick={handleSettlement}
                    >
                      {postSettlement.isPending ? t("posting") : credit ? t("postReceipt") : t("postPayment")}
                    </Button>
                  </div>
                </>
              )}
            </TabsContent>
          </Tabs>

          {error ? (
            <p
              data-testid="post-from-line-error"
              className="rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
            >
              {error}
            </p>
          ) : null}
        </div>
      </DrawerContent>
    </Drawer>
  );
}
