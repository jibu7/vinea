"use client";

import { useTranslations } from "next-intl";
import { TransactionTypesScreen } from "@/features/gl/transaction-types-screen";

export default function ApTransactionTypesPage() {
  const t = useTranslations("maintenance");
  return (
    <TransactionTypesScreen
      module="ap"
      title={t("apTransactionTypes")}
      description={t("apTransactionTypesSubtitle")}
      codePlaceholder={t("apTransactionTypeCodePlaceholder")}
      namePlaceholder={t("apTransactionTypeNamePlaceholder")}
    />
  );
}
