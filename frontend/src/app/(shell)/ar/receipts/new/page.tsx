"use client";

import { DocumentScreen } from "@/features/subledger/document-screen";
import { documentScreen } from "@/features/subledger/document-kinds";

export default function ArReceiptPage() {
  return <DocumentScreen spec={documentScreen("receipt")} />;
}
