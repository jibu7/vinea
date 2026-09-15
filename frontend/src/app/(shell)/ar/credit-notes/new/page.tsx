"use client";

import { Suspense } from "react";
import { useTranslations } from "next-intl";
import { DocumentScreen } from "@/features/subledger/document-screen";
import { documentScreen } from "@/features/subledger/document-kinds";

/** The screen reads `useSearchParams` — a document may be prepared from an order or a
 * receipt named there (P6 decision 7) — and that needs a Suspense boundary above it. */
export default function ArCreditNotePage() {
  const tc = useTranslations("common");
  return (
    <Suspense
      fallback={
        <p className="p-10 text-center text-xs text-[var(--vinea-ink-subtle)]">{tc("loading")}</p>
      }
    >
      <DocumentScreen spec={documentScreen("credit-note")} />
    </Suspense>
  );
}
