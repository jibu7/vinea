import { StatementDetailScreen } from "@/features/banking/statement-detail-screen";

export default async function BankStatementPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <StatementDetailScreen statementId={Number(id)} />;
}
