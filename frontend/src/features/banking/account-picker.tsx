"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Combobox } from "@/design/components/combobox";
import { Field } from "@/design/components/input";
import { useCurrencies } from "@/features/gl/hooks";
import { byId } from "@/features/gl/lookups";
import { BankAccountKind } from "@/lib/api-enums";
import { dotted, formatMoney } from "@/lib/format";
import { useBankAccounts } from "./hooks";
import type { BankAccount } from "./types";

/**
 * The bank accounts a statement or a reconciliation can belong to — **`bank` kind only**. A
 * cash account has no statement and no reconciliation (decision 5, `reconciliation_needs_bank`):
 * a till is counted, not reconciled, and offering it here would be offering the refusal.
 *
 * `requested` is the `?account=` the page was opened with; without one the first bank account
 * is chosen, which on every tenant is the seeded `1120`.
 */
export function useBankAccountChoice(requested: number | null) {
  const accounts = useBankAccounts();
  const banks = useMemo(
    () => (accounts.data ?? []).filter((row) => row.kind === BankAccountKind.BANK),
    [accounts.data],
  );
  const selected = banks.find((row) => row.id === requested) ?? banks[0] ?? null;
  return { accounts, banks, selected };
}

/** Money in the account's **own** currency — a statement is in it, and so is every reconciled
 * amount. A USD account's `495.00` is `$ 495.00`, never a base-currency figure. */
export function useAccountMoney(account: BankAccount | null) {
  const tc = useTranslations("banking.common");
  const currencies = useCurrencies();
  const currency = account ? byId(currencies.data).get(account.currency_id) : undefined;
  const like = currency
    ? { code: currency.code, symbol: currency.symbol, decimalPlaces: currency.decimal_places }
    : null;
  return {
    currency,
    money: (value: string | number | null | undefined) =>
      value === null || value === undefined || like === null ? tc("emptyValue") : formatMoney(Number(value), like),
  };
}

/** The account filter both listings carry. Choosing one puts it in the URL, so a link into
 * a statement and back lands on the same account. */
export function BankAccountFilter({
  banks,
  selected,
  basePath,
}: {
  banks: BankAccount[];
  selected: BankAccount | null;
  basePath: string;
}) {
  const t = useTranslations("banking.common");
  const router = useRouter();
  return (
    <Field label={t("bankAccount")} className="w-72">
      <Combobox
        options={banks.map((row) => ({ value: String(row.id), label: dotted(row.code, row.name) }))}
        value={selected ? String(selected.id) : ""}
        onValueChange={(value) => router.push(`${basePath}?account=${value}`)}
        placeholder={t("chooseBankAccount")}
      />
    </Field>
  );
}
