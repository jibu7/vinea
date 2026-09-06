"use client";

import { useTranslations } from "next-intl";
import { useToast } from "@/design/components/toast";
import { ApiError } from "@/lib/api";

/** Maps API error codes (unbalanced_entry, period_closed, …) to i18n'd toasts. */
export function useApiErrorToast() {
  const t = useTranslations("errors");
  const toast = useToast();

  return (err: unknown, title = "Error") => {
    const code = err instanceof ApiError ? err.code : "generic";
    const description = t.has(code) ? t(code) : err instanceof ApiError ? err.message : t("generic");
    toast.show({ title, description, tone: "danger" });
  };
}
