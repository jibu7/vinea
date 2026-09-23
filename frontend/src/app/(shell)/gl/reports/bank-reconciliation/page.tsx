import { ReconciliationReportScreen } from "@/features/banking/reconciliation-report";

/** `?reconciliation=` opens one directly — the enquiry and Cashbooks summary link here that
 * way; `?account=` opens that account's latest. */
export default async function BankReconciliationReportPage({
  searchParams,
}: {
  searchParams: Promise<{ account?: string; reconciliation?: string }>;
}) {
  const { account, reconciliation } = await searchParams;
  return (
    <ReconciliationReportScreen
      requestedAccountId={account ? Number(account) : null}
      requestedReconciliationId={reconciliation ? Number(reconciliation) : null}
    />
  );
}
