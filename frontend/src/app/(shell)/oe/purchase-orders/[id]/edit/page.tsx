import { OrderWorkspace } from "@/features/order-entry/order-workspace";

export default async function EditPurchaseOrderPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <OrderWorkspace role="purchase" orderId={Number(id)} />;
}
