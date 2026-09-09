"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Coins, Wand2 } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import {
  DocumentWorkspaceShell,
  useDocumentShortcuts,
} from "@/design/components/document-workspace";
import { Field, Input } from "@/design/components/input";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useMe } from "@/features/auth/hooks";
import { useAccounts, useCurrencies } from "@/features/gl/hooks";
import { byId } from "@/features/gl/lookups";
import { newDraftId } from "@/lib/drafts";
import { formatDate, formatMoney } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import {
  useAllocationPreview,
  useAutoAllocate,
  usePartnerEnquiry,
  usePartners,
  usePostAllocation,
} from "./hooks";
import {
  partnerCode,
  type AllocationPairPayload,
  type AllocationPayload,
  type AllocationPreview,
  type OpenItem,
  type PartnerRole,
} from "./types";

/** One editable row per open debit: how much of it this allocation settles, and any
 * settlement discount taken with it. */
interface PairEntry {
  amount: string;
  discount: string;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * The allocation screen: open debits and open credits side by side, allocate or auto-allocate,
 * a discount column, and a preview of exactly what will post.
 *
 * The preview is not computed here. It comes from `POST /allocations/preview`, which runs the
 * allocation service's own `prepare()` — the function `allocate()` then posts — and commits
 * nothing. A preview that re-derived these numbers in the browser could disagree with what the
 * ledger writes; a preview that *is* the posting, minus the commit, cannot.
 */
export function AllocationScreen({ role }: { role: PartnerRole }) {
  const t = useTranslations("arap.allocation");
  const tr = useTranslations(`arap.role.${role}`);
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const { data: me } = useMe();

  const [partnerId, setPartnerId] = useState("");
  const [allocationDate, setAllocationDate] = useState(today);
  const [description, setDescription] = useState("");
  const [creditId, setCreditId] = useState<number | null>(null);
  const [entries, setEntries] = useState<Record<number, PairEntry>>({});
  const [preview, setPreview] = useState<AllocationPreview | null>(null);
  const [stale, setStale] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState<string>(newDraftId);

  const partners = usePartners(role);
  const accounts = useAccounts();
  const accountById = byId(accounts.data);
  const currencies = useCurrencies();
  const currencyById = byId(currencies.data);
  const enquiry = usePartnerEnquiry(role, partnerId ? Number(partnerId) : null, allocationDate);
  const previewAllocation = useAllocationPreview(role);
  const autoAllocate = useAutoAllocate(role);
  const postAllocation = usePostAllocation(role);

  const baseCurrency = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: baseCurrency?.code ?? "",
    decimalPlaces: baseCurrency?.decimal_places ?? 0,
    symbol: baseCurrency?.symbol ?? null,
  };

  const openItems = enquiry.data?.open_items ?? [];
  // `direction` is the document's side of the control account: debits are what the partner
  // owes, credits are what has been paid or credited against it.
  const debits = useMemo(() => openItems.filter((item) => item.direction > 0), [openItems]);
  const credits = useMemo(() => openItems.filter((item) => item.direction < 0), [openItems]);

  function currencyLikeFor(item: OpenItem) {
    const currency = currencyById.get(item.currency_id);
    return {
      code: currency?.code ?? "",
      decimalPlaces: currency?.decimal_places ?? 0,
      symbol: currency?.symbol ?? null,
    };
  }

  // Any change to what would be allocated invalidates the preview: it must never show numbers
  // for a different set of pairs than the ones about to post.
  function invalidate() {
    setStale(preview !== null);
  }

  useEffect(() => {
    setEntries({});
    setCreditId(null);
    setPreview(null);
    setStale(false);
  }, [partnerId, allocationDate]);

  const pairs: AllocationPairPayload[] = useMemo(() => {
    if (creditId === null) return [];
    return Object.entries(entries)
      .filter(([, entry]) => Number(entry.amount) > 0)
      .map(([debitId, entry]) => ({
        debit_document_id: Number(debitId),
        credit_document_id: creditId,
        amount: entry.amount,
        ...(Number(entry.discount) > 0 ? { discount_amount: entry.discount } : {}),
      }));
  }, [entries, creditId]);

  const payload: AllocationPayload = {
    partner_id: Number(partnerId),
    allocation_date: allocationDate,
    description: description || null,
    pairs,
  };

  async function handlePreview() {
    if (!pairs.length) return;
    setBanner(null);
    try {
      setPreview(await previewAllocation.mutateAsync(payload));
      setStale(false);
    } catch (err) {
      setPreview(null);
      if (isApiError(err)) setBanner(err.message);
      else showApiError(err, t("previewFailed"));
    }
  }

  async function handleAutoAllocate() {
    if (!partnerId) return;
    setBanner(null);
    try {
      const suggestion = await autoAllocate.mutateAsync({
        partner_id: Number(partnerId),
        allocation_date: allocationDate,
        credit_document_id: creditId,
      });
      if (!suggestion.lines.length) {
        toast.show({ title: t("nothingToAllocate"), tone: "neutral" });
        return;
      }
      // Adopt the server's own oldest-first pairing, so what is previewed and posted is what
      // it proposed rather than a client re-reading of it.
      setCreditId(suggestion.lines[0].credit_document_id);
      setEntries(
        Object.fromEntries(
          suggestion.lines.map((line) => [
            line.debit_document_id,
            { amount: line.amount, discount: line.discount_amount },
          ]),
        ),
      );
      setPreview(suggestion);
      setStale(false);
    } catch (err) {
      if (isApiError(err)) setBanner(err.message);
      else showApiError(err, t("autoFailed"));
    }
  }

  async function handlePost() {
    if (!pairs.length) return;
    setBanner(null);
    try {
      const allocation = await postAllocation.mutateAsync({ payload, idempotencyKey });
      toast.show({ title: t("posted", { number: allocation.number }), tone: "success" });
      setEntries({});
      setCreditId(null);
      setPreview(null);
      setIdempotencyKey(newDraftId());
    } catch (err) {
      if (isApiError(err)) setBanner(err.message);
      else showApiError(err, t("postFailed"));
    }
  }

  const canPost = pairs.length > 0 && preview !== null && !stale && !postAllocation.isPending;

  useDocumentShortcuts({ onPost: handlePost, onCancel: () => setEntries({}), canPost });

  const allocatedTotal = pairs.reduce((sum, pair) => sum + Number(pair.amount), 0);
  const selectedCredit = credits.find((item) => item.document_id === creditId);
  const creditRemaining = selectedCredit
    ? Number(selectedCredit.open_amount) - allocatedTotal
    : 0;

  return (
    <DocumentWorkspaceShell
      backHref="/"
      title={t(role === "ar" ? "titleAr" : "titleAp")}
      subtitle={t("subtitle")}
      errorBanner={banner}
      footer={
        <div className="flex items-center justify-between gap-6">
          <div className="flex items-center gap-6 text-sm">
            <span className="text-[var(--vinea-ink-muted)]">
              {t("totalAllocated")}{" "}
              <span className="font-mono tabular-nums text-[var(--vinea-ink)]">
                {formatMoney(allocatedTotal, selectedCredit ? currencyLikeFor(selectedCredit) : baseLike)}
              </span>
            </span>
            {preview && (
              <>
                <span className="text-[var(--vinea-ink-muted)]">
                  {t("totalDiscount")}{" "}
                  <span className="font-mono tabular-nums text-[var(--vinea-ink)]">
                    {formatMoney(Number(preview.total_discount), baseLike)}
                  </span>
                </span>
                <span className="text-[var(--vinea-ink-muted)]">
                  {t("totalFx")}{" "}
                  <span className="font-mono tabular-nums text-[var(--vinea-ink)]">
                    {formatMoney(Number(preview.total_fx_base), baseLike)}
                  </span>
                </span>
              </>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              onClick={handlePreview}
              disabled={!pairs.length || previewAllocation.isPending}
            >
              {t("preview")}
            </Button>
            <Button variant="primary" disabled={!canPost} onClick={handlePost}>
              {postAllocation.isPending ? t("posting") : t("post")}
            </Button>
          </div>
        </div>
      }
    >
      <section className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-4">
        <Field label={t("partner")}>
          <Combobox
            options={(partners.data ?? []).map((p) => ({
              value: String(p.id),
              label: `${partnerCode(p, role)} · ${p.name}`,
            }))}
            value={partnerId}
            onValueChange={setPartnerId}
            placeholder={t("choosePartner")}
          />
        </Field>
        <Field label={t("allocationDate")}>
          <IsoDatePicker value={allocationDate} onValueChange={setAllocationDate} />
        </Field>
        <Field label={t("description")} className="sm:col-span-2">
          <Input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t("descriptionPlaceholder")}
          />
        </Field>
      </section>

      {!partnerId ? (
        <p className="py-10 text-center text-sm text-[var(--vinea-ink-subtle)]">
          {t("selectPartner")}
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* Credits first: pick what is being applied, then say where it goes. */}
          <section className="space-y-2 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4">
            <div className="flex items-baseline justify-between">
              <h2 className="font-display text-sm font-semibold">{t("credits")}</h2>
              <p className="text-xs text-[var(--vinea-ink-subtle)]">
                {t(role === "ar" ? "creditsHintAr" : "creditsHintAp")}
              </p>
            </div>
            {credits.length === 0 ? (
              <p className="py-6 text-center text-xs text-[var(--vinea-ink-subtle)]">
                {t("noOpenItems")}
              </p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH className="w-32">{t("number")}</TH>
                    <TH className="w-24">{t("date")}</TH>
                    <TH className="text-right">{t("open")}</TH>
                  </TR>
                </THead>
                <TBody>
                  {credits.map((item) => (
                    <TR
                      key={item.document_id}
                      className={
                        item.document_id === creditId ? "bg-[var(--vinea-brand-soft)]/40" : undefined
                      }
                    >
                      <TD>
                        <button
                          type="button"
                          aria-label={t("selectCreditAria", { number: item.number })}
                          onClick={() => {
                            setCreditId(item.document_id);
                            invalidate();
                          }}
                          className="font-mono text-xs font-semibold text-[var(--vinea-brand)] underline"
                        >
                          {item.number}
                        </button>
                      </TD>
                      <TD className="text-xs text-[var(--vinea-ink-muted)]">
                        {formatDate(item.document_date)}
                      </TD>
                      <TD className="text-right font-mono text-xs tabular-nums">
                        {formatMoney(Math.abs(Number(item.open_amount)), currencyLikeFor(item))}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
            {selectedCredit && (
              <p className="text-right text-xs text-[var(--vinea-ink-muted)]">
                {t("creditRemaining", {
                  amount: formatMoney(Math.abs(creditRemaining), currencyLikeFor(selectedCredit)),
                })}
              </p>
            )}
          </section>

          <section className="space-y-2 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4">
            <div className="flex items-baseline justify-between">
              <h2 className="font-display text-sm font-semibold">{t("debits")}</h2>
              <Button
                variant="ghost"
                onClick={handleAutoAllocate}
                disabled={autoAllocate.isPending}
                className="gap-1.5 text-xs"
              >
                <Wand2 className="size-3.5" /> {t("autoAllocate")}
              </Button>
            </div>
            {debits.length === 0 ? (
              <p className="py-6 text-center text-xs text-[var(--vinea-ink-subtle)]">
                {t("noOpenItems")}
              </p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH className="w-28">{t("number")}</TH>
                    <TH className="w-20">{t("due")}</TH>
                    <TH className="text-right">{t("open")}</TH>
                    <TH className="w-28 text-right">{t("allocate")}</TH>
                    <TH className="w-24 text-right">{t("discount")}</TH>
                  </TR>
                </THead>
                <TBody>
                  {debits.map((item) => {
                    const entry = entries[item.document_id] ?? { amount: "", discount: "" };
                    return (
                      <TR key={item.document_id}>
                        <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                          {item.number}
                        </TD>
                        <TD className="text-xs text-[var(--vinea-ink-muted)]">
                          {item.due_date ? formatDate(item.due_date) : null}
                          {item.days_overdue > 0 && (
                            <StatusChip tone="warning">{String(item.days_overdue)}</StatusChip>
                          )}
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums">
                          {formatMoney(Number(item.open_amount), currencyLikeFor(item))}
                        </TD>
                        <TD>
                          <Input
                            value={entry.amount}
                            inputMode="decimal"
                            aria-label={t("allocateAria", { number: item.number })}
                            onChange={(e) => {
                              setEntries((current) => ({
                                ...current,
                                [item.document_id]: { ...entry, amount: e.target.value },
                              }));
                              invalidate();
                            }}
                            className="text-right font-mono tabular-nums"
                          />
                        </TD>
                        <TD>
                          <Input
                            value={entry.discount}
                            inputMode="decimal"
                            aria-label={t("discountAria", { number: item.number })}
                            onChange={(e) => {
                              setEntries((current) => ({
                                ...current,
                                [item.document_id]: { ...entry, discount: e.target.value },
                              }));
                              invalidate();
                            }}
                            className="text-right font-mono tabular-nums"
                          />
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            )}
          </section>
        </div>
      )}

      {/* The preview panel: the postings, exactly as the service computed them. */}
      <section
        data-testid="allocation-preview"
        className="space-y-2 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4"
      >
        <div className="flex items-baseline justify-between">
          <h2 className="flex items-center gap-2 font-display text-sm font-semibold">
            <Coins className="size-4 text-[var(--vinea-brand)]" />
            {t("previewTitle")}
          </h2>
          <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("previewFromServer")}</p>
        </div>
        {stale && (
          <p className="text-xs text-[var(--vinea-warning)]">{t("previewStale")}</p>
        )}
        {!preview ? (
          <p className="py-6 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {t("previewEmpty")}
          </p>
        ) : preview.postings.length === 0 ? (
          // A same-currency allocation with no discount moves no money: it closes open items
          // and writes no journal entry. Saying so is not the same as saying nothing yet.
          <p
            data-testid="allocation-preview-empty"
            className="py-6 text-center text-xs text-[var(--vinea-ink-muted)]"
          >
            {t("previewNoPostings")}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-40">{t("account")}</TH>
                <TH>{t("narrative")}</TH>
                <TH className="w-40 text-right">{t("baseAmount")}</TH>
              </TR>
            </THead>
            <TBody>
              {preview.postings.map((posting, index) => (
                <TR key={`${posting.gl_account_id}-${index}`}>
                  <TD className="font-mono text-xs text-[var(--vinea-ink)]">
                    {(() => {
                      const account = accountById.get(posting.gl_account_id);
                      return account ? `${account.code} · ${account.name}` : posting.gl_account_id;
                    })()}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">{posting.description}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {formatMoney(Number(posting.base_amount), baseLike)}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </section>
    </DocumentWorkspaceShell>
  );
}


