"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/cn";
import { navIntents, type NavIntent, type NavItem } from "@/design/nav-tree";


function groupByModule(items: NavItem[]): Array<[string, NavItem[]]> {
  const order: string[] = [];
  const byModule = new Map<string, NavItem[]>();
  for (const item of items) {
    if (!byModule.has(item.module)) {
      byModule.set(item.module, []);
      order.push(item.module);
    }
    byModule.get(item.module)!.push(item);
  }
  return order.map((module) => [module, byModule.get(module)!]);
}

function IntentNode({ intent, permissions }: { intent: NavIntent; permissions: Set<string> }) {
  const [open, setOpen] = useState(intent.label === "Maintenance" || intent.label === "Transactions");
  const visibleItems = intent.items.filter((item) => item.phase || !item.permission || permissions.has(item.permission));
  if (visibleItems.length === 0) return null;

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 rounded-[var(--radius-control)] px-2 py-1.5 text-sm font-medium text-[var(--vinea-ink)] hover:bg-[var(--vinea-surface-sunken)]"
      >
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        {intent.label}
      </button>
      {open && (
        <div className="ml-2 mt-0.5 space-y-2 border-l border-[var(--vinea-border)] pl-2">
          {groupByModule(visibleItems).map(([module, items]) => (
            <div key={module}>
              <p className="px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
                {module}
              </p>
              <ul className="space-y-0.5">
                {items.map((item) => {
                  const disabled = !!item.phase;
                  const itemClass = cn(
                    "block flex-1 truncate rounded-[var(--radius-control)] px-2 py-1 text-sm",
                    disabled
                      ? "cursor-not-allowed text-[var(--vinea-ink-subtle)]"
                      : "cursor-pointer text-[var(--vinea-ink-muted)] hover:bg-[var(--vinea-surface-sunken)] hover:text-[var(--vinea-ink)]",
                  );
                  return (
                    <li key={item.label} className="flex items-center justify-between gap-2">
                      {!disabled && item.href ? (
                        <Link href={item.href} className={itemClass}>
                          {item.label}
                        </Link>
                      ) : (
                        <span className={itemClass}>{item.label}</span>
                      )}
                      {item.phase && (
                        <span className="mr-2 rounded-full bg-[var(--vinea-surface-sunken)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--vinea-ink-subtle)]">
                          {item.phase}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Intent-first sidebar tree — Maintenance/Transactions/Enquiries/Reports per Appendix C. */
export function SidebarNav({ permissions }: { permissions: Set<string> }) {
  return (
    <nav className="space-y-1">
      {navIntents.map((intent) => (
        <IntentNode key={intent.label} intent={intent} permissions={permissions} />
      ))}
    </nav>
  );
}

