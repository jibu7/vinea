import { InventoryDocumentScreen } from "@/features/inventory/document-screen";

export default async function InventoryDocumentPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <InventoryDocumentScreen documentId={Number(id)} />;
}
