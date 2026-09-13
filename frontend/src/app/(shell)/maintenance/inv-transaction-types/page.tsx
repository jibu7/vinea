"use client";

import { useTranslations } from "next-intl";
import { TransactionTypesScreen } from "@/features/gl/transaction-types-screen";
import { InventoryTransactionKind } from "@/lib/api-enums";

/**
 * The same screen GL, AR and AP use, filtered to `module="inv"` — one table, one
 * discriminator. Inventory is the only module that passes `kinds`, because it is the only one
 * whose types say what they do to stock (P5 decision 9): a company adding "Damaged" is adding
 * another `adjustment_out` with its own contra account.
 */
const KIND_ORDER: readonly InventoryTransactionKind[] = [
  InventoryTransactionKind.ADJUSTMENT_IN,
  InventoryTransactionKind.ADJUSTMENT_OUT,
  InventoryTransactionKind.REVALUATION,
  InventoryTransactionKind.TRANSFER,
  InventoryTransactionKind.COUNT_VARIANCE,
  InventoryTransactionKind.OPENING_BALANCE,
];

export default function InventoryTransactionTypesPage() {
  const t = useTranslations("maintenance");
  const tk = useTranslations("inventory.kinds");
  return (
    <TransactionTypesScreen
      module="inv"
      title={t("invTransactionTypes")}
      description={t("invTransactionTypesSubtitle")}
      codePlaceholder={t("invTransactionTypeCodePlaceholder")}
      namePlaceholder={t("invTransactionTypeNamePlaceholder")}
      kinds={KIND_ORDER.map((kind) => [kind, tk(kind)] as const)}
    />
  );
}
