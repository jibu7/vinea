import { BankAccountEnquiryScreen } from "@/features/banking/bank-account-enquiry-screen";

/** `?account=` and `?as_of=` in the URL, so a link into the workspace and back lands on the
 * same account at the same date. */
export default async function BankAccountEnquiryPage({
  searchParams,
}: {
  searchParams: Promise<{ account?: string; as_of?: string }>;
}) {
  const { account, as_of: asOf } = await searchParams;
  return (
    <BankAccountEnquiryScreen
      requestedAccountId={account ? Number(account) : null}
      requestedAsOf={asOf ?? null}
    />
  );
}
