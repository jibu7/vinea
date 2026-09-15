import { OrderWorkspace } from "@/features/order-entry/order-workspace";

export default async function EditSalesOrderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <OrderWorkspace role="sales" orderId={Number(id)} />;
}
