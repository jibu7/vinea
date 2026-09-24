import { CashbooksReport, type CashbookMode } from "@/features/banking/cashbooks-report";

/** Mode, account and range in the URL: the enquiry and the summary both link into a detail. */
export default async function CashbooksPage({
  searchParams,
}: {
  searchParams: Promise<{ mode?: string; account?: string; from?: string; to?: string }>;
}) {
  const { mode, account, from, to } = await searchParams;
  return (
    <CashbooksReport
      mode={(mode === "summary" ? "summary" : "detail") satisfies CashbookMode}
      requestedAccountId={account ? Number(account) : null}
      dateFrom={from ?? null}
      dateTo={to ?? null}
    />
  );
}
