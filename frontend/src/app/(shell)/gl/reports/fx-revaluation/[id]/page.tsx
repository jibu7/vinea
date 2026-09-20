import { FxRevaluationReport } from "@/features/gl/reports/fx-revaluation-report";

/** The drill target for an `FXR-` journal entry — the run entry, its next-day mirror, the
 * counter-entry that reversed it or that counter's own mirror. All four resolve to this run
 * through `sources.py` and `_p7_document`, so whichever of them somebody opened from the trial
 * balance lands here rather than on a picker. */
export default async function FxRevaluationByIdPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <FxRevaluationReport revaluationId={Number(id)} />;
}
