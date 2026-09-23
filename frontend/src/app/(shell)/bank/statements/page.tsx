import { StatementsScreen } from "@/features/banking/statements-screen";

/** `?account=` picks the bank account, so a link back from a statement lands where it left. Read
 * here rather than with `useSearchParams`, which would need a Suspense boundary above it. */
export default async function BankStatementsPage({
  searchParams,
}: {
  searchParams: Promise<{ account?: string }>;
}) {
  const { account } = await searchParams;
  return <StatementsScreen requestedAccountId={account ? Number(account) : null} />;
}
