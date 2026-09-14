import { PartnerDocumentDetailScreen } from "@/features/subledger/document-detail-screen";

export default async function APDocumentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PartnerDocumentDetailScreen role="ap" documentId={Number(id)} />;
}
