import { FxRevaluationScreen } from "@/features/gl/fx-revaluation-screen";

/** The drill target for an `FXR-` journal entry — the run, its next-day mirror, the counter-entry
 * that reversed it, or that counter's own mirror; all four resolve to this run. The **document**
 * screen rather than the report, because this is where Reverse is. The report at
 * `/gl/reports/fx-revaluation` stays for reading a posted run's lines. */
export default async function FxRevaluationByIdPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <FxRevaluationScreen openId={Number(id)} />;
}
