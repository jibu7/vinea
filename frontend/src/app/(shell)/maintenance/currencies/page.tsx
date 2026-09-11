"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft, Coins, Plus, TrendingUp } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { DatePicker } from "@/design/components/date-picker";
import { Dialog, DialogContent, DialogTrigger } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { StatusChip } from "@/design/components/status-chip";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useToast } from "@/design/components/toast";
import {
  useCreateCurrency,
  useCreateExchangeRate,
  useCurrencies,
  useExchangeRates,
  useUpdateCurrency,
} from "@/features/gl/hooks";
import { byId, toOptions } from "@/features/gl/lookups";
import type { Currency } from "@/features/gl/types";
import { dotted, formatDate } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

export default function CurrenciesPage() {
  const t = useTranslations("maintenance");
  const tc = useTranslations("common");
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const currenciesQuery = useCurrencies();
  const createCurrency = useCreateCurrency();
  const updateCurrency = useUpdateCurrency();

  const [selectedCurrencyFilter, setSelectedCurrencyFilter] = useState<string>("");
  const ratesQuery = useExchangeRates(selectedCurrencyFilter ? Number(selectedCurrencyFilter) : undefined);
  const createRate = useCreateExchangeRate();

  // New Currency modal
  const [currOpen, setCurrOpen] = useState(false);
  const [currCode, setCurrCode] = useState("");
  const [currName, setCurrName] = useState("");
  const [currSymbol, setCurrSymbol] = useState("");
  const [currDecimals, setCurrDecimals] = useState("2");

  // New Rate modal
  const [rateOpen, setRateOpen] = useState(false);
  const [rateCurrencyId, setRateCurrencyId] = useState("");
  const [rateValidFrom, setRateValidFrom] = useState<Date>(new Date());
  const [rateValue, setRateValue] = useState("");

  const currencyById = byId(currenciesQuery.data);
  const nonBaseCurrencies = useMemo(
    () => (currenciesQuery.data ?? []).filter((c) => !c.is_base),
    [currenciesQuery.data],
  );

  async function handleCreateCurrency() {
    try {
      await createCurrency.mutateAsync({
        code: currCode.toUpperCase(),
        name: currName,
        symbol: currSymbol || null,
        decimal_places: Number(currDecimals),
      });
      setCurrOpen(false);
      setCurrCode("");
      setCurrName("");
      setCurrSymbol("");
      toast.show({ title: t("currencyCreated"), tone: "success" });
    } catch (err) {
      showApiError(err, t("currencyCreateFailed"));
    }
  }

  async function handleCreateRate() {
    try {
      await createRate.mutateAsync({
        currency_id: Number(rateCurrencyId),
        valid_from: rateValidFrom.toISOString().slice(0, 10),
        rate: rateValue,
      });
      setRateOpen(false);
      setRateValue("");
      toast.show({ title: t("exchangeRateRecorded"), tone: "success" });
    } catch (err) {
      showApiError(err, t("exchangeRateSaveFailed"));
    }
  }

  async function handleToggleCurrencyActive(curr: Currency) {
    try {
      await updateCurrency.mutateAsync({
        currencyId: curr.id,
        payload: { is_active: !curr.is_active },
      });
      toast.show({
        title: curr.code,
        description: curr.is_active ? "Deactivated" : "Activated",
        tone: "success",
      });
    } catch (err) {
      showApiError(err, t("currencyUpdateFailed"));
    }
  }

  return (
    <div className="flex min-h-screen flex-col" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]" aria-label={tc("back")}>
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{t("foreignCurrency")}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">{t("currenciesSubtitle")}</p>
          </div>
        </div>
        <ThemeToggle />
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-5xl space-y-6">
          {/* Currencies Section */}
          <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Coins className="size-4 text-[var(--vinea-brand)]" />
                <h2 className="font-display text-base font-semibold">{t("currencies")}</h2>
              </div>

              <Dialog open={currOpen} onOpenChange={setCurrOpen}>
                <DialogTrigger asChild>
                  <Button variant="primary" className="gap-1.5 text-xs">
                    <Plus className="size-3.5" /> {t("newCurrency")}
                  </Button>
                </DialogTrigger>
                <DialogContent title={t("newCurrency")}>
                  <div className="space-y-3 pt-2">
                    <div className="grid grid-cols-2 gap-3">
                      <Field label={t("code")}>
                        <Input value={currCode} onChange={(e) => setCurrCode(e.target.value)} maxLength={3} placeholder={t("currencyCodePlaceholder")} />
                      </Field>
                      <Field label={t("symbol")}>
                        <Input value={currSymbol} onChange={(e) => setCurrSymbol(e.target.value)} placeholder={t("currencySymbolPlaceholder")} />
                      </Field>
                    </div>
                    <Field label={t("name")}>
                      <Input value={currName} onChange={(e) => setCurrName(e.target.value)} placeholder={t("currencyNamePlaceholder")} />
                    </Field>
                    <Field label={t("decimals")}>
                      <Input value={currDecimals} onChange={(e) => setCurrDecimals(e.target.value)} type="number" min={0} max={6} />
                    </Field>

                    <div className="flex justify-end gap-2 pt-2">
                      <Button variant="ghost" onClick={() => setCurrOpen(false)}>{t("cancel")}</Button>
                      <Button variant="primary" disabled={!currCode || !currName || createCurrency.isPending} onClick={handleCreateCurrency}>
                        {createCurrency.isPending ? t("saving") : t("save")}
                      </Button>
                    </div>
                  </div>
                </DialogContent>
              </Dialog>
            </div>

            <Table>
              <THead>
                <TR>
                  <TH className="w-24">{t("code")}</TH>
                  <TH>{t("currencyName")}</TH>
                  <TH className="w-24">{t("symbol")}</TH>
                  <TH className="w-28 text-center">{t("decimals")}</TH>
                  <TH className="w-28">{t("currencyType")}</TH>
                  <TH className="w-28 text-right">{t("status")}</TH>
                </TR>
              </THead>
              <TBody>
                {(currenciesQuery.data ?? []).map((c) => (
                  <TR key={c.id}>
                    <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">{c.code}</TD>
                    <TD className="font-medium text-xs text-[var(--vinea-ink)]">{c.name}</TD>
                    <TD className="font-mono text-xs">{c.symbol ?? t("emptyValue")}</TD>
                    <TD className="text-center font-mono text-xs">{c.decimal_places}</TD>
                    <TD>
                      {c.is_base ? (
                        <StatusChip tone="success">{t("isBase")}</StatusChip>
                      ) : (
                        <StatusChip tone="neutral">{t("foreign")}</StatusChip>
                      )}
                    </TD>
                    <TD className="text-right">
                      {!c.is_base ? (
                        <button type="button" onClick={() => handleToggleCurrencyActive(c)}>
                          <StatusChip tone={c.is_active ? "success" : "neutral"}>
                            {c.is_active ? t("active") : t("inactive")}
                          </StatusChip>
                        </button>
                      ) : (
                        <StatusChip tone="success">{t("active")}</StatusChip>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>

          {/* Exchange Rates Section */}
          <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-2">
                <TrendingUp className="size-4 text-[var(--vinea-brand)]" />
                <div>
                  <h2 className="font-display text-base font-semibold">{t("exchangeRates")}</h2>
                  <p className="text-xs text-[var(--vinea-ink-muted)]">{t("exchangeRatesSubtitle")}</p>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <select
                  value={selectedCurrencyFilter}
                  onChange={(e) => setSelectedCurrencyFilter(e.target.value)}
                  aria-label={t("filterRatesAria")}
                  className="h-9 rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] px-2.5 text-xs text-[var(--vinea-ink)]"
                >
                  <option value="">{t("allForeignCurrencies")}</option>
                  {nonBaseCurrencies.map((c) => (
                    <option key={c.id} value={c.id}>{dotted(c.code, c.name)}</option>
                  ))}
                </select>

                <Dialog open={rateOpen} onOpenChange={setRateOpen}>
                  <DialogTrigger asChild>
                    <Button variant="primary" className="gap-1.5 text-xs">
                      <Plus className="size-3.5" /> {t("newRate")}
                    </Button>
                  </DialogTrigger>
                  <DialogContent title={t("newRate")}>
                    <div className="space-y-3 pt-2">
                      <Field label={t("currencies")}>
                        <Combobox
                          options={toOptions(nonBaseCurrencies, (c) => `${c.code} · ${c.name}`)}
                          value={rateCurrencyId}
                          onValueChange={setRateCurrencyId}
                          placeholder={t("selectForeignCurrency")}
                        />
                      </Field>
                      <Field label={t("validFrom")}>
                        <DatePicker value={rateValidFrom} onValueChange={setRateValidFrom} />
                      </Field>
                      <Field label={t("rate")}>
                        <Input
                          value={rateValue}
                          onChange={(e) => setRateValue(e.target.value)}
                          placeholder={t("ratePlaceholder")}
                          type="number"
                          step="any"
                        />
                      </Field>

                      <div className="flex justify-end gap-2 pt-2">
                        <Button variant="ghost" onClick={() => setRateOpen(false)}>{t("cancel")}</Button>
                        <Button variant="primary" disabled={!rateCurrencyId || !rateValue || createRate.isPending} onClick={handleCreateRate}>
                          {createRate.isPending ? t("saving") : t("save")}
                        </Button>
                      </div>
                    </div>
                  </DialogContent>
                </Dialog>
              </div>
            </div>

            <Table>
              <THead>
                <TR>
                  <TH className="w-32">{t("currency")}</TH>
                  <TH className="w-48">{t("validFrom")}</TH>
                  <TH className="text-right">{t("effectiveRate")}</TH>
                </TR>
              </THead>
              <TBody>
                {(ratesQuery.data ?? []).map((r) => {
                  const curr = currencyById.get(r.currency_id);
                  return (
                    <TR key={`${r.currency_id}-${r.valid_from}`}>
                      <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                        {curr?.code ?? r.currency_id}
                      </TD>
                      <TD className="text-xs text-[var(--vinea-ink)]">
                        {formatDate(r.valid_from)}
                      </TD>
                      <TD className="text-right font-mono text-xs font-semibold">
                        {r.rate}
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          </div>
        </div>
      </main>
    </div>
  );
}
