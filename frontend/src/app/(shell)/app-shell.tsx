"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Building2,
  Command as CommandIcon,
  LayoutGrid,
  LogOut,
  ShieldCheck,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { SidebarNav, navIntents } from "@/design/components/module-nav";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { Button } from "@/design/components/button";
import { CommandPalette, type CommandPaletteItem } from "@/design/components/command-palette";
import { useToast } from "@/design/components/toast";
import { useLogout, useSwitchCompany } from "@/features/auth/hooks";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import type { MeResponse } from "@/features/auth/types";

export function AppShell({ me, children }: { me: MeResponse; children: React.ReactNode }) {
  const t = useTranslations("shell");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const logout = useLogout();
  const switchCompany = useSwitchCompany();
  const [companyMenuOpen, setCompanyMenuOpen] = useState(false);
  const permissions = useMemo(() => new Set(me.permissions), [me.permissions]);

  async function handleSwitchCompany(companyId: number) {
    setCompanyMenuOpen(false);
    if (companyId === me.company?.id) return;
    try {
      await switchCompany.mutateAsync(companyId);
      router.refresh();
    } catch (err) {
      showApiError(err, "Couldn't switch company");
    }
  }

  async function handleLogout() {
    await logout.mutateAsync();
    router.push("/login");
  }

  // Ctrl+K must reach every screen, listed by intent — same data source as the sidebar tree.
  const screenItems: CommandPaletteItem[] = navIntents.flatMap((intent) =>
    intent.items
      .filter((item) => item.phase || !item.permission || permissions.has(item.permission))
      .map((item) => ({
        id: `${intent.label}-${item.module}-${item.label}`,
        label: item.label,
        group: intent.label,
        onSelect: () => {
          if (item.href) return router.push(item.href);
          if (item.phase) return toast.show({ title: item.label, description: `Landing in ${item.phase}`, tone: "neutral" });
          toast.show({ title: item.label, description: "Coming in a later step", tone: "neutral" });
        },
      })),
  );

  const paletteItems: CommandPaletteItem[] = [
    {
      id: "toggle-theme",
      label: "Toggle theme",
      group: "Preferences",
      onSelect: () => document.documentElement.setAttribute(
        "data-theme",
        document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark",
      ),
    },
    ...me.memberships
      .filter((m) => m.company_id !== me.company?.id)
      .map((m) => ({
        id: `switch-${m.company_id}`,
        label: `Switch to ${m.company_name}`,
        group: "Preferences",
        onSelect: () => handleSwitchCompany(m.company_id),
      })),
    { id: "sign-out", label: "Sign out", group: "Preferences", onSelect: handleLogout },
    ...(permissions.has("users:read")
      ? [
          {
            id: "admin-users",
            label: t("usersAndMemberships"),
            group: "Administration",
            onSelect: () => router.push("/administration/users"),
          },
        ]
      : []),
    ...screenItems,
  ];

  return (
    <div className="flex min-h-screen" data-density="airy">
      <CommandPalette items={paletteItems} />

      <aside className="hidden w-64 shrink-0 overflow-y-auto border-r border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 lg:block print:hidden">
        <div className="mb-6 flex items-center gap-2 px-2">
          <div className="flex size-8 items-center justify-center rounded-[var(--radius-control)] bg-[var(--vinea-brand)] text-[var(--vinea-on-brand)]">
            <LayoutGrid className="size-4" />
          </div>
          <span className="font-display text-lg font-semibold">Vinea</span>
        </div>

        {permissions.has("users:read") && (
          <div className="mb-3 border-b border-[var(--vinea-border)] pb-3">
            <span className="flex items-center gap-1.5 rounded-[var(--radius-control)] px-2 py-1.5 text-sm font-medium text-[var(--vinea-ink)] hover:bg-[var(--vinea-surface-sunken)]">
              <ShieldCheck className="size-3.5 text-[var(--vinea-ink-subtle)]" />
              {t("administration")}
            </span>
            <ul className="ml-5 mt-0.5">
              <li>
                <Link
                  href="/administration/users"
                  className="block cursor-pointer rounded-[var(--radius-control)] px-2 py-1 text-sm text-[var(--vinea-ink-muted)] hover:bg-[var(--vinea-surface-sunken)] hover:text-[var(--vinea-ink)]"
                >
                  {t("usersAndMemberships")}
                </Link>
              </li>
            </ul>
          </div>
        )}

        <span className="mb-1 flex items-center gap-1.5 rounded-[var(--radius-control)] px-2 py-1.5 text-sm font-medium text-[var(--vinea-ink)] hover:bg-[var(--vinea-surface-sunken)]">
          <LayoutGrid className="size-3.5 text-[var(--vinea-ink-subtle)]" />
          {t("myDesktop")}
        </span>

        <SidebarNav permissions={permissions} />
      </aside>

      <div className="flex-1">
        <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3 print:hidden">
          <div className="relative">
            <button
              onClick={() => setCompanyMenuOpen((o) => !o)}
              className="flex items-center gap-2 rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] px-3 py-1.5 text-sm text-[var(--vinea-ink-muted)]"
            >
              <Building2 className="size-4" />
              {me.company?.name ?? "Select company"}
            </button>
            {companyMenuOpen && (
              <div className="absolute left-0 top-full z-40 mt-1 w-64 rounded-[var(--radius-control)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-1 shadow-[var(--elevation-2)]">
                {me.memberships.map((m) => (
                  <button
                    key={m.id}
                    onClick={() => handleSwitchCompany(m.company_id)}
                    className="flex w-full items-center justify-between rounded-[calc(var(--radius-control)-2px)] px-2 py-1.5 text-left text-sm hover:bg-[var(--vinea-surface-sunken)]"
                  >
                    {m.company_name}
                    {m.company_id === me.company?.id && <span className="text-[var(--vinea-brand)]">✓</span>}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button className="flex items-center gap-2 rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] px-3 py-1.5 text-sm text-[var(--vinea-ink-subtle)]">
              <CommandIcon className="size-3.5" /> {t("search")}
              <kbd className="rounded border border-[var(--vinea-border)] px-1 text-[10px]">Ctrl K</kbd>
            </button>
            <ThemeToggle />
            <Button variant="ghost" size="sm" onClick={handleLogout} aria-label="Sign out">
              <LogOut className="size-4" />
            </Button>
          </div>
        </header>

        <main>{children}</main>
      </div>
    </div>
  );
}
