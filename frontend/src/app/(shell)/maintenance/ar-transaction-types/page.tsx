"use client";

import { useTranslations } from "next-intl";
import { TransactionTypesScreen } from "@/features/gl/transaction-types-screen";

export default function ArTransactionTypesPage() {
  const t = useTranslations("maintenance");
  return (
    <TransactionTypesScreen
      module="ar"
      title={t("arTransactionTypes")}
      description={t("arTransactionTypesSubtitle")}
      codePlaceholder={t("arTransactionTypeCodePlaceholder")}
      namePlaceholder={t("arTransactionTypeNamePlaceholder")}
    />
  );
}
