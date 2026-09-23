import { PaymentRunsScreen } from "@/features/banking/payment-runs-screen";

/** `?account=` picks the bank account, as on Bank statements, so a link back from a run lands on
 * the account it was paid from. Read here rather than with `useSearchParams`, which would need a
 * Suspense boundary above it. */
export default async function PaymentRunsPage({
  searchParams,
}: {
  searchParams: Promise<{ account?: string }>;
}) {
  const { account } = await searchParams;
  return <PaymentRunsScreen requestedAccountId={account ? Number(account) : null} />;
}
