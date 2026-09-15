import { LandedCostScreen } from "@/features/order-entry/landed-cost-screen";

export default async function LandedCostPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <LandedCostScreen documentId={Number(id)} />;
}
