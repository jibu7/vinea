"use client";

import { useTranslations } from "next-intl";
import { TransactionTypesScreen } from "@/features/gl/transaction-types-screen";

export default function TransactionTypesPage() {
  const t = useTranslations("maintenance");
  return (
    <TransactionTypesScreen
      module="gl"
      title={t("transactionTypes")}
      description={t("glTransactionTypesSubtitle")}
      codePlaceholder={t("glTransactionTypeCodePlaceholder")}
      namePlaceholder={t("glTransactionTypeNamePlaceholder")}
    />
  );
}
