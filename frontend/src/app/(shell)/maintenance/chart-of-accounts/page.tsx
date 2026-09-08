"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Folder,
  FileText,
  Plus,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import { Button } from "@/design/components/button";
import { Dialog, DialogContent, DialogTrigger } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { Combobox } from "@/design/components/combobox";
import { StatusChip } from "@/design/components/status-chip";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useToast } from "@/design/components/toast";
import { useAccounts, useCreateAccount, useUpdateAccount } from "@/features/gl/hooks";
import { toOptions } from "@/features/gl/lookups";
import { controlTypeLabel, type GLAccount } from "@/features/gl/types";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

interface AccountNode {
  account: GLAccount;
  children: AccountNode[];
}

function buildTree(accounts: GLAccount[]): AccountNode[] {
  const map = new Map<number, AccountNode>();
  for (const acc of accounts) {
    map.set(acc.id, { account: acc, children: [] });
  }

  const roots: AccountNode[] = [];
  for (const acc of accounts) {
    const node = map.get(acc.id)!;
    if (acc.parent_id && map.has(acc.parent_id)) {
      map.get(acc.parent_id)!.children.push(node);
    } else {
      roots.push(node);
    }
  }

  // Sort by code
  const sortNodes = (nodes: AccountNode[]) => {
    nodes.sort((a, b) => a.account.code.localeCompare(b.account.code));
    for (const n of nodes) sortNodes(n.children);
  };
  sortNodes(roots);
  return roots;
}

export default function ChartOfAccountsPage() {
  const t = useTranslations("maintenance");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const { data: accounts, isLoading } = useAccounts();
  const createAccount = useCreateAccount();
  const updateAccount = useUpdateAccount();

  const [query, setQuery] = useState("");
  const [selectedClass, setSelectedClass] = useState<string>("all");
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const [nodeErrors, setNodeErrors] = useState<Record<number, string>>({});

  // New account dialog state
  const [newOpen, setNewOpen] = useState(false);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [accountClass, setAccountClass] = useState<string>("expense");
  const [parentId, setParentId] = useState<string>("");
  const [isPostable, setIsPostable] = useState(true);
  const [isControl, setIsControl] = useState(false);
  const [controlType, setControlType] = useState<string>("");

  const filteredAccounts = useMemo(() => {
    return (accounts ?? []).filter((acc) => {
      if (selectedClass !== "all" && acc.class !== selectedClass) return false;
      if (query.trim()) {
        const q = query.toLowerCase();
        return acc.code.toLowerCase().includes(q) || acc.name.toLowerCase().includes(q);
      }
      return true;
    });
  }, [accounts, selectedClass, query]);

  const tree = useMemo(() => buildTree(filteredAccounts), [filteredAccounts]);

  function toggleCollapse(id: number) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleCreateAccount() {
    try {
      await createAccount.mutateAsync({
        code,
        name,
        class_: accountClass,
        parent_id: parentId ? Number(parentId) : null,
        is_postable: isPostable,
        is_control: isControl,
        control_type: isControl && controlType ? controlType : null,
      });
      setNewOpen(false);
      setCode("");
      setName("");
      setParentId("");
      toast.show({ title: t("newAccount"), description: `${code} · ${name}`, tone: "success" });
    } catch (err) {
      showApiError(err, "Couldn't create account");
    }
  }

  async function handleToggleActive(acc: GLAccount) {
    setNodeErrors((prev) => {
      const next = { ...prev };
      delete next[acc.id];
      return next;
    });
    try {
      await updateAccount.mutateAsync({
        accountId: acc.id,
        payload: { is_active: !acc.is_active },
      });
      toast.show({
        title: acc.name,
        description: acc.is_active ? "Deactivated" : "Activated",
        tone: "success",
      });
    } catch (err) {
      const apiErr = err as { code?: string; message?: string };
      let msg = apiErr.message || "Couldn't update account";
      if (apiErr.code === "account_in_use") {
        msg = "Referenced by GL settings; cannot be deactivated";
      } else if (apiErr.code === "control_account_cannot_be_deactivated") {
        msg = "Control accounts cannot be deactivated";
      } else if (apiErr.code === "account_has_non_zero_balance") {
        msg = "Account has a non-zero balance; cannot be deactivated";
      }
      setNodeErrors((prev) => ({ ...prev, [acc.id]: msg }));
      showApiError(err, "Couldn't update account");
    }
  }

  function renderNode(node: AccountNode, depth = 0) {
    const acc = node.account;
    const hasChildren = node.children.length > 0;
    const isNodeCollapsed = collapsed.has(acc.id);

    return (
      <div key={acc.id} className="group">
        <div
          data-account-code={acc.code}
          className={`flex items-center justify-between border-b border-[var(--vinea-border)] px-4 py-2 hover:bg-[var(--vinea-surface-sunken)]/60 ${
            !acc.is_active ? "opacity-60" : ""
          }`}
          style={{ paddingLeft: `${depth * 20 + 16}px` }}
        >
          <div className="flex items-center gap-2.5 min-w-0">
            {hasChildren ? (
              <button
                type="button"
                onClick={() => toggleCollapse(acc.id)}
                className="rounded p-0.5 text-[var(--vinea-ink-subtle)] hover:bg-[var(--vinea-surface-sunken)] hover:text-[var(--vinea-ink)]"
              >
                {isNodeCollapsed ? (
                  <ChevronRight className="size-4" />
                ) : (
                  <ChevronDown className="size-4" />
                )}
              </button>
            ) : (
              <span className="w-5" />
            )}

            {acc.is_postable ? (
              <FileText className="size-4 text-[var(--vinea-brand)] shrink-0" />
            ) : (
              <Folder className="size-4 text-[var(--vinea-warning)] shrink-0" />
            )}

            <span className="font-mono text-xs font-semibold text-[var(--vinea-ink)]">
              {acc.code}
            </span>
            <span className="truncate text-sm font-medium text-[var(--vinea-ink)]">
              {acc.name}
            </span>

            <span className="rounded-full bg-[var(--vinea-surface-sunken)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--vinea-ink-subtle)]">
              {acc.class}
            </span>

            {acc.is_control && (
              <span className="rounded bg-[var(--vinea-info-soft)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--vinea-info)]">
                Control: {controlTypeLabel(acc.control_type)}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            <Link
              href={`/maintenance/rename-account?accountId=${acc.id}`}
              className="text-xs text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-brand)] hover:underline"
            >
              {t("rename")}
            </Link>
            <button
              type="button"
              onClick={() => handleToggleActive(acc)}
              className="ml-2 text-xs font-medium"
            >
              <StatusChip tone={acc.is_active ? "success" : "neutral"}>
                {acc.is_active ? "Active" : "Inactive"}
              </StatusChip>
            </button>
          </div>
        </div>

        {nodeErrors[acc.id] && (
          <div
            className="flex items-center gap-2 border-b border-[var(--vinea-border)] bg-[var(--vinea-danger-soft)]/60 px-4 py-1.5 text-xs text-[var(--vinea-danger)] font-medium"
            style={{ paddingLeft: `${depth * 20 + 36}px` }}
          >
            <span>• {nodeErrors[acc.id]}</span>
          </div>
        )}

        {hasChildren && !isNodeCollapsed && (
          <div>{node.children.map((child) => renderNode(child, depth + 1))}</div>
        )}
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{t("chartOfAccounts")}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">
              Tree hierarchy by parent account · {filteredAccounts.length} of {accounts?.length ?? 0} accounts
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Dialog open={newOpen} onOpenChange={setNewOpen}>
            <DialogTrigger asChild>
              <Button variant="primary" className="gap-1.5 text-xs">
                <Plus className="size-3.5" /> {t("newAccount")}
              </Button>
            </DialogTrigger>
            <DialogContent title={t("newAccount")}>
              <div className="space-y-3 pt-2">
                <div className="grid grid-cols-2 gap-3">
                  <Field label={t("code")}>
                    <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder="6150" />
                  </Field>
                  <Field label={t("accountClass")}>
                    <select
                      value={accountClass}
                      onChange={(e) => setAccountClass(e.target.value)}
                      className="h-10 w-full rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] px-3 text-sm"
                    >
                      <option value="asset">Asset</option>
                      <option value="liability">Liability</option>
                      <option value="equity">Equity</option>
                      <option value="income">Income</option>
                      <option value="expense">Expense</option>
                    </select>
                  </Field>
                </div>
                <Field label={t("name")}>
                  <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Staff Welfare" />
                </Field>
                <Field label={t("parentAccount")}>
                  <Combobox
                    options={toOptions(
                      (accounts ?? []).filter((a) => !a.is_postable),
                      (a) => `${a.code} · ${a.name}`,
                    )}
                    value={parentId}
                    onValueChange={setParentId}
                    placeholder="None (Root level)"
                  />
                </Field>
                <div className="flex items-center gap-6 pt-1 text-sm">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={isPostable}
                      onChange={(e) => setIsPostable(e.target.checked)}
                      className="size-4 accent-[var(--vinea-brand)]"
                    />
                    {t("isPostable")}
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={isControl}
                      onChange={(e) => setIsControl(e.target.checked)}
                      className="size-4 accent-[var(--vinea-brand)]"
                    />
                    {t("isControl")}
                  </label>
                </div>
                {isControl && (
                  <Field label={t("controlType")}>
                    <select
                      value={controlType}
                      onChange={(e) => setControlType(e.target.value)}
                      className="h-10 w-full rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] px-3 text-sm"
                    >
                      <option value="">Select control type…</option>
                      <option value="bank">Bank</option>
                      <option value="cash">Cash</option>
                      <option value="ar">Accounts Receivable (AR)</option>
                      <option value="ap">Accounts Payable (AP)</option>
                      <option value="inventory">Inventory</option>
                    </select>
                  </Field>
                )}
                <div className="mt-4 flex justify-end gap-2">
                  <Button variant="ghost" onClick={() => setNewOpen(false)}>
                    Cancel
                  </Button>
                  <Button
                    variant="primary"
                    disabled={!code || !name || createAccount.isPending}
                    onClick={handleCreateAccount}
                  >
                    {createAccount.isPending ? t("saving") : t("save")}
                  </Button>
                </div>
              </div>
            </DialogContent>
          </Dialog>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-5xl space-y-4">
          {/* Filters Bar */}
          <div className="flex flex-wrap items-center justify-between gap-4 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4">
            <div className="relative flex-1 min-w-64">
              <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--vinea-ink-subtle)]" />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter accounts by code or name…"
                className="h-10 w-full rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] pl-9 pr-3 text-sm text-[var(--vinea-ink)]"
              />
            </div>
            <div className="flex gap-1">
              {["all", "asset", "liability", "equity", "income", "expense"].map((cls) => (
                <button
                  key={cls}
                  type="button"
                  onClick={() => setSelectedClass(cls)}
                  className={`rounded-[var(--radius-control)] px-3 py-1.5 text-xs font-medium capitalize transition-colors ${
                    selectedClass === cls
                      ? "bg-[var(--vinea-brand)] text-white"
                      : "bg-[var(--vinea-surface-sunken)] text-[var(--vinea-ink-muted)] hover:text-[var(--vinea-ink)]"
                  }`}
                >
                  {cls}
                </button>
              ))}
            </div>
          </div>

          {/* Tree View */}
          {isLoading ? (
            <div className="flex h-48 items-center justify-center text-sm text-[var(--vinea-ink-subtle)]">
              Loading Chart of Accounts…
            </div>
          ) : tree.length === 0 ? (
            <div className="rounded-[var(--radius-card)] border border-dashed border-[var(--vinea-border)] p-12 text-center text-sm text-[var(--vinea-ink-subtle)]">
              No accounts match the current filter.
            </div>
          ) : (
            <div className="overflow-hidden rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)]">
              {tree.map((node) => renderNode(node, 0))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
