import { PartnerDocumentDetailScreen } from "@/features/subledger/document-detail-screen";

export default async function ARDocumentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PartnerDocumentDetailScreen role="ar" documentId={Number(id)} />;
}
