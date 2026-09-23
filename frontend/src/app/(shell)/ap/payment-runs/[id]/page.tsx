import { PaymentRunDetailScreen } from "@/features/banking/payment-run-detail-screen";

export default async function PaymentRunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PaymentRunDetailScreen runId={Number(id)} />;
}
