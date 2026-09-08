"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft, Sliders } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Field } from "@/design/components/input";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useToast } from "@/design/components/toast";
import { useAccounts, useGLSettings, useUpdateGLSettings } from "@/features/gl/hooks";
import { toOptions } from "@/features/gl/lookups";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

export default function DefaultsPage() {
  const t = useTranslations("maintenance");
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const settingsQuery = useGLSettings();
  const updateSettings = useUpdateGLSettings();
  const accountsQuery = useAccounts();

  const [retainedEarningsId, setRetainedEarningsId] = useState("");
  const [roundingDiffId, setRoundingDiffId] = useState("");

  const postableAccounts = useMemo(
    () => (accountsQuery.data ?? []).filter((a) => a.is_postable && !a.is_control),
    [accountsQuery.data],
  );

  useEffect(() => {
    if (settingsQuery.data) {
      setRetainedEarningsId(
        settingsQuery.data.retained_earnings_account_id
          ? String(settingsQuery.data.retained_earnings_account_id)
          : "",
      );
      setRoundingDiffId(
        settingsQuery.data.rounding_difference_account_id
          ? String(settingsQuery.data.rounding_difference_account_id)
          : "",
      );
    }
  }, [settingsQuery.data]);

  async function handleSave() {
    try {
      await updateSettings.mutateAsync({
        retained_earnings_account_id: retainedEarningsId ? Number(retainedEarningsId) : null,
        rounding_difference_account_id: roundingDiffId ? Number(roundingDiffId) : null,
      });
      toast.show({ title: t("saved"), tone: "success" });
    } catch (err) {
      showApiError(err, "Couldn't save GL defaults");
    }
  }

  return (
    <div className="flex min-h-screen flex-col" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{t("defaults")}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">
              General Ledger default accounts for automated system entries
            </p>
          </div>
        </div>
        <ThemeToggle />
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-2xl space-y-6">
          <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 space-y-5">
            <div className="flex items-center gap-2">
              <Sliders className="size-4 text-[var(--vinea-brand)]" />
              <h2 className="font-display text-base font-semibold">{t("generalSettings")}</h2>
            </div>

            <Field label={t("retainedEarningsAccount")}>
              <Combobox
                options={toOptions(postableAccounts, (a) => `${a.code} · ${a.name}`)}
                value={retainedEarningsId}
                onValueChange={setRetainedEarningsId}
                placeholder="Choose retained earnings account…"
              />
            </Field>

            <Field label={t("roundingDifferenceAccount")}>
              <Combobox
                options={toOptions(postableAccounts, (a) => `${a.code} · ${a.name}`)}
                value={roundingDiffId}
                onValueChange={setRoundingDiffId}
                placeholder="Choose rounding difference account…"
              />
            </Field>

            <div className="flex justify-end pt-3">
              <Button variant="primary" disabled={updateSettings.isPending} onClick={handleSave}>
                {updateSettings.isPending ? t("saving") : t("save")}
              </Button>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
