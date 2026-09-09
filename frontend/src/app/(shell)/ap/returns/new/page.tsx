"use client";

import { DocumentScreen } from "@/features/subledger/document-screen";
import { documentScreen } from "@/features/subledger/document-kinds";

export default function ApReturnToSupplierPage() {
  return <DocumentScreen spec={documentScreen("return-to-supplier")} />;
}
