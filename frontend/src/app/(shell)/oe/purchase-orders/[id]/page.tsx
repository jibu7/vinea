import { PurchaseOrderScreen } from "@/features/order-entry/purchase-order-screen";

export default async function PurchaseOrderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PurchaseOrderScreen orderId={Number(id)} />;
}
