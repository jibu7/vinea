import Link from "next/link";
import { LayoutGrid, Building2, Command as CommandIcon, Sun, ArrowUpRight, ArrowDownRight, FileText, Wallet, ShieldCheck } from "lucide-react";
import { Button } from "@/design/components/button";
import { StatusChip } from "@/design/components/status-chip";
import { Money } from "@/design/components/money";
import { ModuleNav } from "@/design/components/module-nav";
import { RWF } from "@/lib/format";

const kpis = [
  { label: "Cash position", amount: 4250000, delta: "+3.2%", up: true },
  { label: "Receivables", amount: 1820000, delta: "-1.1%", up: false },
  { label: "Payables", amount: 940000, delta: "+0.6%", up: true },
  { label: "Net income (MTD)", amount: 612000, delta: "+8.4%", up: true },
];

const activity = [
  { doc: "JE-2026-00042", desc: "Cash sale — invoice 1042", amount: 150000, status: "posted" as const },
  { doc: "CB-2026-00118", desc: "Bank charges — September", amount: -12500, status: "posted" as const },
  { doc: "JE-2026-00043", desc: "Payroll accrual", amount: 2400000, status: "draft" as const },
  { doc: "CB-2026-00119", desc: "MTN MoMo settlement", amount: 380000, status: "posted" as const },
];

export default function DashboardPrototype() {
  return (
    <div className="flex min-h-screen" data-density="airy">
      <aside className="hidden w-64 shrink-0 overflow-y-auto border-r border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 lg:block">
        <div className="mb-6 flex items-center gap-2 px-2">
          <div className="flex size-8 items-center justify-center rounded-[var(--radius-control)] bg-[var(--vinea-brand)] text-white">
            <LayoutGrid className="size-4" />
          </div>
          <span className="font-display text-lg font-semibold">Vinea</span>
        </div>

        {/* Administration sits apart from the module tree — mirrors Sage's top-bar placement */}
        <div className="mb-3 border-b border-[var(--vinea-border)] pb-3">
          <span className="flex items-center gap-1.5 rounded-[var(--radius-control)] px-2 py-1.5 text-sm font-medium text-[var(--vinea-ink)] hover:bg-[var(--vinea-surface-sunken)]">
            <ShieldCheck className="size-3.5 text-[var(--vinea-ink-subtle)]" />
            Administration
          </span>
          <ul className="ml-5 mt-0.5">
            <li className="cursor-pointer rounded-[var(--radius-control)] px-2 py-1 text-sm text-[var(--vinea-ink-muted)] hover:bg-[var(--vinea-surface-sunken)] hover:text-[var(--vinea-ink)]">
              Users &amp; memberships
            </li>
          </ul>
        </div>

        <ModuleNav />
      </aside>

      <div className="flex-1">
        <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
          <button className="flex items-center gap-2 rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] px-3 py-1.5 text-sm text-[var(--vinea-ink-muted)]">
            <Building2 className="size-4" />
            Rugari Wines Ltd
          </button>
          <div className="flex items-center gap-2">
            <button className="flex items-center gap-2 rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] px-3 py-1.5 text-sm text-[var(--vinea-ink-subtle)]">
              <CommandIcon className="size-3.5" /> Search
              <kbd className="rounded border border-[var(--vinea-border)] px-1 text-[10px]">Ctrl K</kbd>
            </button>
            <Button variant="ghost" size="sm" aria-label="Toggle theme"><Sun className="size-4" /></Button>
          </div>
        </header>

        <main className="mx-auto max-w-6xl px-6 py-8">
          <div className="mb-8">
            <h1 className="font-display text-3xl font-semibold">Good morning, Aline</h1>
            <p className="mt-1 text-[var(--vinea-ink-muted)]">Here&apos;s how Rugari Wines is tracking this period.</p>
          </div>

          <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {kpis.map((kpi) => (
              <div key={kpi.label} className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-5 shadow-[var(--elevation-1)]">
                <p className="text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">{kpi.label}</p>
                <p className="mt-2 font-display text-2xl font-semibold">
                  <Money amount={kpi.amount} currency={RWF} />
                </p>
                <p className={`mt-1 flex items-center gap-1 text-xs ${kpi.up ? "text-[var(--vinea-success)]" : "text-[var(--vinea-danger)]"}`}>
                  {kpi.up ? <ArrowUpRight className="size-3.5" /> : <ArrowDownRight className="size-3.5" />}
                  {kpi.delta} vs last month
                </p>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <div className="lg:col-span-2 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)]">
              <div className="border-b border-[var(--vinea-border)] px-5 py-3">
                <h2 className="font-display text-lg font-medium">Recent activity</h2>
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
                        {a.status === "posted" ? "Posted" : "Draft"}
                      </StatusChip>
                    </div>
                  </li>
                ))}
              </ul>
            </div>

            <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-5">
              <h2 className="mb-4 font-display text-lg font-medium">Quick actions</h2>
              <div className="space-y-2">
                <Link href="/design/prototypes/workspace">
                  <Button variant="secondary" className="w-full justify-start"><Wallet className="size-4" /> New journal entry</Button>
                </Link>
                <Button variant="secondary" className="w-full justify-start"><Wallet className="size-4" /> New cashbook entry</Button>
                <Button variant="ghost" className="w-full justify-start">View trial balance</Button>
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
