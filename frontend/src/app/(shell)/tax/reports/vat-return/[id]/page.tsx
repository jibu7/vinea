import { VatReturnReport } from "@/features/tax/reports/vat-return-report";

/** The drill target for a `VATR-` journal entry: `sources.py` resolves the entry to its return
 * and `documentHref` turns that into this route, so an accountant who opened the settlement
 * entry from the trial balance lands on the return it settled rather than on a picker. */
export default async function VatReturnByIdPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <VatReturnReport returnId={Number(id)} />;
}
