import { VatReturnScreen } from "@/features/tax/vat-return-screen";

/** The drill target for a `VATR-` journal entry. `sources.py` resolves the entry to its return
 * and `documentHref` turns that into this route — the **document** screen, where Reverse lives,
 * because the entry page's link is "reverse via the `tax` document" and landing it on a report
 * with no Reverse button would be a dead end. The report at `/tax/reports/vat-return` stays for
 * ranges. */
export default async function VatReturnByIdPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <VatReturnScreen openId={Number(id)} />;
}
