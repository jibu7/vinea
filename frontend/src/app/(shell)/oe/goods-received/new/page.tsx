"use client";

import { Suspense } from "react";
import { useTranslations } from "next-intl";
import { GrnNewScreen } from "@/features/order-entry/grn-new-screen";

/** `useSearchParams` needs a Suspense boundary above it — the receipt may be prepared from a
 * purchase order named in the query string. */
export default function NewGrnPage() {
  const tc = useTranslations("orderEntry.common");
  return (
    <Suspense
      fallback={
        <p className="p-10 text-center text-xs text-[var(--vinea-ink-subtle)]">{tc("loading")}</p>
      }
    >
      <GrnNewScreen />
    </Suspense>
  );
}
