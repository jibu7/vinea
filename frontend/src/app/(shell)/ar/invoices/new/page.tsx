"use client";

import { DocumentScreen } from "@/features/subledger/document-screen";
import { documentScreen } from "@/features/subledger/document-kinds";

export default function ArInvoicePage() {
  return <DocumentScreen spec={documentScreen("invoice")} />;
}
