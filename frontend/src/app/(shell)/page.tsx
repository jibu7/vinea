"use client";

import Link from "next/link";
import { ArrowDownRight, ArrowUpRight, FileText, Wallet } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMe } from "@/features/auth/hooks";
import { Button } from "@/design/components/button";
import { StatusChip } from "@/design/components/status-chip";
import { Money } from "@/design/components/money";
import { RWF } from "@/lib/format";

const kpis = [
  { key: "cashPosition", amount: 4250000, delta: "+3.2%", up: true },
  { key: "receivables", amount: 1820000, delta: "-1.1%", up: false },
  { key: "payables", amount: 940000, delta: "+0.6%", up: true },
  { key: "netIncome", amount: 612000, delta: "+8.4%", up: true },
] as const;

/* Mock rows standing in for the GL enquiry aggregate (P3 step 6/7). Doc numbers and
 * descriptions are server payload, so they are data here rather than catalogue copy. */
const activity = [
  { doc: "JE-2026-00042", desc: "Cash sale — invoice 1042", amount: 150000, status: "posted" as const },
  { doc: "CB-2026-00118", desc: "Bank charges — September", amount: -12500, status: "posted" as const },
  { doc: "JE-2026-00043", desc: "Payroll accrual", amount: 2400000, status: "draft" as const },
];

export default function DashboardPage() {
  const t = useTranslations("dashboard");
  const tStatus = useTranslations("status");
  const { data: me } = useMe();
  const firstName = me?.full_name.split(" ")[0] ?? "";

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <div className="mb-8">
        <h1 className="font-display text-3xl font-semibold">
          {firstName ? t("welcomeNamed", { name: firstName }) : t("welcome")}
        </h1>
        <p className="mt-1 text-[var(--vinea-ink-muted)]">
          {t("subtitle", { company: me?.company?.name ?? t("yourCompany") })}
        </p>
      </div>

      {/* KPIs and recent activity are still mock data — no GL enquiry aggregate endpoint yet (P3 step 6/7). */}
      <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {kpis.map((kpi) => (
          <div key={kpi.key} className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-5 shadow-[var(--elevation-1)]">
            <p className="text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">{t(`kpi.${kpi.key}`)}</p>
            <p className="mt-2 font-display text-2xl font-semibold">
              <Money amount={kpi.amount} currency={RWF} />
            </p>
            <p className={`mt-1 flex items-center gap-1 text-xs ${kpi.up ? "text-[var(--vinea-success)]" : "text-[var(--vinea-danger)]"}`}>
              {kpi.up ? <ArrowUpRight className="size-3.5" /> : <ArrowDownRight className="size-3.5" />}
              {kpi.delta} {t("vsLastMonth")}
            </p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)]">
          <div className="border-b border-[var(--vinea-border)] px-5 py-3">
            <h2 className="font-display text-lg font-medium">{t("recentActivity")}</h2>
          </div>
          <ul className="divide-y divide-[var(--vinea-border)]">
            {activity.map((a) => (
              <li key={a.doc} className="flex items-center justify-between px-5 py-3">
                <div className="flex items-center gap-3">
                  <div className="flex size-8 items-center justify-center rounded-full bg-[var(--vinea-surface-sunken)]">
                    <FileText className="size-4 text-[var(--vinea-ink-subtle)]" />
                  </div>
                  <div>
                    <p className="text-sm font-medium">{a.doc}</p>
                    <p className="text-xs text-[var(--vinea-ink-muted)]">{a.desc}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <Money amount={a.amount} currency={RWF} className="text-sm" />
                  <StatusChip tone={a.status === "posted" ? "success" : "neutral"}>
                    {a.status === "posted" ? tStatus("posted") : tStatus("draft")}
                  </StatusChip>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-5">
          <h2 className="mb-4 font-display text-lg font-medium">{t("quickActions")}</h2>
          <div className="space-y-2">
            <Link href="/gl/journal-batches/new">
              <Button variant="secondary" className="w-full justify-start"><Wallet className="size-4" /> {t("newJournal")}</Button>
            </Link>
            <Link href="/gl/cashbook-batches/new">
              <Button variant="secondary" className="w-full justify-start"><Wallet className="size-4" /> {t("newCashbook")}</Button>
            </Link>
            <Button variant="ghost" className="w-full justify-start" disabled>{t("viewTrialBalance")}</Button>
          </div>
        </div>
      </div>
    </div>
  );
}
