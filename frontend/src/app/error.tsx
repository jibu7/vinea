"use client";

import { useEffect } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const t = useTranslations("errors");

  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-6 text-center">
      <h1 className="font-display text-2xl font-semibold">{t("pageTitle")}</h1>
      <p className="max-w-md text-sm text-[var(--vinea-ink-muted)]">{t("pageBody")}</p>
      <Button variant="primary" onClick={reset}>{t("tryAgain")}</Button>
    </div>
  );
}
