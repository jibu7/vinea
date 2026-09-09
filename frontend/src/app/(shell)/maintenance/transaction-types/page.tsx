"use client";

import { useTranslations } from "next-intl";
import { TransactionTypesScreen } from "@/features/gl/transaction-types-screen";

export default function TransactionTypesPage() {
  const t = useTranslations("maintenance");
  return (
    <TransactionTypesScreen
      module="gl"
      title={t("transactionTypes")}
      description="Determination chain rules for General Ledger (ADR-05)"
      codePlaceholder="EXP_PAYROLL"
      namePlaceholder="Payroll Expense Posting"
    />
  );
}
