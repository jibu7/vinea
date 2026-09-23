import { ReconciliationWorkspace } from "@/features/banking/reconciliation-workspace";

export default async function BankReconciliationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ReconciliationWorkspace reconciliationId={Number(id)} />;
}
