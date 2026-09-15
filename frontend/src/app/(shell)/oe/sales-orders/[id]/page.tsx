import { SalesOrderScreen } from "@/features/order-entry/sales-order-screen";

export default async function SalesOrderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <SalesOrderScreen orderId={Number(id)} />;
}
