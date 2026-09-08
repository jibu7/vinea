"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { ArrowLeft, ArrowRight, History, ShieldAlert } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Field, Input } from "@/design/components/input";
import { StatusChip } from "@/design/components/status-chip";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useToast } from "@/design/components/toast";
import { useAccountHistory, useAccounts, useUpdateAccount } from "@/features/gl/hooks";
import { toOptions } from "@/features/gl/lookups";
import { formatDate } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

function RenameAccountView() {
  const t = useTranslations("maintenance");
  const searchParams = useSearchParams();
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const initialAccountId = searchParams.get("accountId") ?? "";
  const [selectedAccountId, setSelectedAccountId] = useState(initialAccountId);
  const [newCode, setNewCode] = useState("");

  const accounts = useAccounts();
  const updateAccount = useUpdateAccount();

  const accountIdNum = selectedAccountId ? Number(selectedAccountId) : null;
  const currentAccount = accounts.data?.find((a) => a.id === accountIdNum);
  const history = useAccountHistory(accountIdNum);

  useEffect(() => {
    if (currentAccount) {
      setNewCode(currentAccount.code);
    } else {
      setNewCode("");
    }
  }, [currentAccount]);

  async function handleRename() {
    if (!currentAccount || !newCode || newCode === currentAccount.code) return;
    try {
      const updated = await updateAccount.mutateAsync({
        accountId: currentAccount.id,
        payload: { code: newCode },
      });
      toast.show({
        title: "Account renamed",
        description: `Code changed from ${currentAccount.code} to ${updated.code}`,
        tone: "success",
      });
      history.refetch();
    } catch (err) {
      showApiError(err, "Couldn't rename account");
    }
  }

  const isRenamed = newCode.trim() !== "" && newCode !== currentAccount?.code;

  return (
    <div className="flex min-h-screen flex-col" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/maintenance/chart-of-accounts" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{t("renameAccount")}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">
              Update an account code while keeping its full ledger history and linkages intact
            </p>
          </div>
        </div>
        <ThemeToggle />
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-4xl space-y-6">
          {/* Account Selection and Rename Card */}
          <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 space-y-6">
            <Field label="Select account to rename">
              <Combobox
                options={toOptions(accounts.data ?? [], (a) => `${a.code} · ${a.name}`)}
                value={selectedAccountId}
                onValueChange={setSelectedAccountId}
                placeholder="Choose account…"
              />
            </Field>

            {currentAccount && (
              <div className="rounded-[var(--radius-control)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-sunken)]/50 p-4 space-y-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs uppercase tracking-wider text-[var(--vinea-ink-subtle)]">Account</p>
                    <p className="text-base font-semibold text-[var(--vinea-ink)]">{currentAccount.name}</p>
                  </div>
                  <div className="flex gap-2">
                    <StatusChip tone="neutral">{currentAccount.class}</StatusChip>
                    <StatusChip tone={currentAccount.is_postable ? "success" : "warning"}>
                      {currentAccount.is_postable ? "Postable" : "Header"}
                    </StatusChip>
                  </div>
                </div>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 pt-2">
                  <Field label={t("currentCode")}>
                    <Input value={currentAccount.code} disabled className="font-mono bg-[var(--vinea-surface-sunken)]" />
                  </Field>

                  <Field label={t("newCode")}>
                    <Input
                      value={newCode}
                      onChange={(e) => setNewCode(e.target.value)}
                      placeholder="e.g. 6150"
                      className="font-mono"
                    />
                  </Field>
                </div>

                <div className="flex items-center justify-between pt-2">
                  <div className="flex items-center gap-2 text-xs text-[var(--vinea-ink-muted)]">
                    <ShieldAlert className="size-4 text-[var(--vinea-warning)]" />
                    <span>Existing journal lines stay tied to this account; only the visible code is updated.</span>
                  </div>

                  <Button
                    variant="primary"
                    disabled={!isRenamed || updateAccount.isPending}
                    onClick={handleRename}
                    className="gap-1.5"
                  >
                    {updateAccount.isPending ? t("saving") : t("rename")}
                  </Button>
                </div>
              </div>
            )}
          </div>

          {/* Audit History Notes */}
          {currentAccount && (
            <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 space-y-4">
              <div className="flex items-center gap-2">
                <History className="size-4 text-[var(--vinea-brand)]" />
                <h2 className="font-display text-sm font-semibold">{t("renameHistory")}</h2>
              </div>

              {history.isLoading ? (
                <div className="p-4 text-center text-xs text-[var(--vinea-ink-subtle)]">Loading history…</div>
              ) : !history.data || history.data.length === 0 ? (
                <p className="text-xs text-[var(--vinea-ink-subtle)]">No change events recorded for this account.</p>
              ) : (
                <Table>
                  <THead>
                    <TR>
                      <TH className="w-36">Timestamp</TH>
                      <TH className="w-40">Event</TH>
                      <TH>Details</TH>
                      <TH className="w-48 text-right">Changed By</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {history.data.map((record) => {
                      const beforeCode = (record.before as { code?: string } | null)?.code;
                      const afterCode = (record.after as { code?: string } | null)?.code;
                      const isRename = record.action === "gl_account.renamed";
                      const isRecon = record.action === "gl_account.audit_reconciliation";
                      const note = (record.after as { note?: string } | null)?.note;

                      return (
                        <TR key={record.id}>
                          <TD className="text-xs text-[var(--vinea-ink-muted)]">
                            {formatDate(record.at)}
                          </TD>
                          <TD>
                            <StatusChip tone={isRename ? "warning" : isRecon ? "info" : "neutral"}>
                              {record.action}
                            </StatusChip>
                          </TD>
                          <TD className="text-xs">
                            {isRename && beforeCode && afterCode ? (
                              <span className="inline-flex items-center gap-1.5 font-mono">
                                <span className="line-through text-[var(--vinea-ink-subtle)]">{beforeCode}</span>
                                <ArrowRight className="size-3 text-[var(--vinea-ink-muted)]" />
                                <span className="font-bold text-[var(--vinea-brand)]">{afterCode}</span>
                              </span>
                            ) : isRecon && afterCode ? (
                              <span className="text-[var(--vinea-ink)]">
                                Reconciled to <strong className="font-mono">{afterCode}</strong>
                                {note ? ` · ${note}` : ""}
                              </span>
                            ) : (
                              <span className="text-[var(--vinea-ink-muted)]">
                                {JSON.stringify(record.after ?? {})}
                              </span>
                            )}
                          </TD>
                          <TD className="text-right text-xs text-[var(--vinea-ink-muted)]">
                            {record.actor_email ?? "System"}
                          </TD>
                        </TR>
                      );
                    })}
                  </TBody>
                </Table>
              )}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

export default function RenameAccountPage() {
  return (
    <Suspense fallback={<div className="flex min-h-screen items-center justify-center text-sm">Loading…</div>}>
      <RenameAccountView />
    </Suspense>
  );
}
