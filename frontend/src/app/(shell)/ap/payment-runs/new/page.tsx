import { NewPaymentRunScreen } from "@/features/banking/new-payment-run-screen";

export default async function NewPaymentRunPage({
  searchParams,
}: {
  searchParams: Promise<{ account?: string }>;
}) {
  const { account } = await searchParams;
  return <NewPaymentRunScreen requestedAccountId={account ? Number(account) : null} />;
}
