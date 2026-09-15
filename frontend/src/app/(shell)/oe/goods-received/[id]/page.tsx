import { GrnScreen } from "@/features/order-entry/grn-screen";

export default async function GrnPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <GrnScreen grnId={Number(id)} />;
}
