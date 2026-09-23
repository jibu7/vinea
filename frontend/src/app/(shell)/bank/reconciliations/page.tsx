import { ReconciliationsScreen } from "@/features/banking/reconciliations-screen";

/** `?account=` as on Bank statements: the workspace's back link returns to its own account. */
export default async function BankReconciliationsPage({
  searchParams,
}: {
  searchParams: Promise<{ account?: string }>;
}) {
  const { account } = await searchParams;
  return <ReconciliationsScreen requestedAccountId={account ? Number(account) : null} />;
}
