"use client";

import { useTranslations } from "next-intl";
import { TransactionTypesScreen } from "@/features/gl/transaction-types-screen";

export default function ApTransactionTypesPage() {
  const t = useTranslations("maintenance");
  return (
    <TransactionTypesScreen
      module="ap"
      title={t("apTransactionTypes")}
      description="Determination-chain defaults for supplier invoices, debit notes and payments"
      codePlaceholder="SINV"
      namePlaceholder="Supplier invoice"
    />
  );
}
