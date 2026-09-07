"use client";

import { useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft, Sparkles } from "lucide-react";
import { Button } from "@/design/components/button";
import { Field, Input } from "@/design/components/input";
import { DatePicker } from "@/design/components/date-picker";
import { StatusChip } from "@/design/components/status-chip";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { Dialog, DialogTrigger, DialogContent } from "@/design/components/dialog";
import { Money } from "@/design/components/money";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useJournalEntry, useReverseEntry } from "@/features/gl/hooks";
import { useGLLookups, byId } from "@/features/gl/lookups";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useToast } from "@/design/components/toast";
import { newDraftId } from "@/lib/drafts";
import { formatDate } from "@/lib/format";

export default function EntryViewPage() {
  const params = useParams<{ id: string }>();
  const entryId = Number(params.id);
  const t = useTranslations("gl");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const { data: entry, isLoading } = useJournalEntry(entryId);
  const { accounts, branches, projects, currencies } = useGLLookups();
  const accountById = byId(accounts.data);
  const branchById = byId(branches.data);
  const projectById = byId(projects.data);
  const currencyById = byId(currencies.data);
  const baseCurrency = useMemo(() => currencies.data?.find((c) => c.is_base), [currencies.data]);

  const [reverseOpen, setReverseOpen] = useState(false);
  const [reverseDate, setReverseDate] = useState<Date>(new Date());
  const [reason, setReason] = useState("");
  const [reverseError, setReverseError] = useState<string | null>(null);
  const [reverseKey] = useState(() => newDraftId());
  const reverseEntry = useReverseEntry();

  if (isLoading || !entry) {
    return <div className="flex min-h-screen items-center justify-center text-sm text-[var(--vinea-ink-subtle)]">Loading…</div>;
  }

  const isCashbook = entry.doc_type === "CB";
  const title = isCashbook ? t("cashbookBatch") : t("journalBatch");
  const totalDebit = entry.lines.filter((l) => !l.is_rounding_line && Number(l.base_amount) > 0).reduce((s, l) => s + Number(l.base_amount), 0);
  const totalCredit = entry.lines.filter((l) => !l.is_rounding_line && Number(l.base_amount) < 0).reduce((s, l) => s - Number(l.base_amount), 0);

  async function handleReverse() {
    setReverseError(null);
    try {
      const reversal = await reverseEntry.mutateAsync({
        entryId,
        payload: { entry_date: reverseDate.toISOString().slice(0, 10), reason },
        idempotencyKey: reverseKey,
      });
      setReverseOpen(false);
      toast.show({ title: "Entry reversed", description: t("viewingReversal"), tone: "success" });
      router.push(`/gl/entries/${reversal.id}`);
    } catch (err) {
      const fieldErrors = (err as { fieldErrors?: Record<string, string[]>; message?: string }).fieldErrors;
      const message = (err as { message?: string }).message;
      if (fieldErrors && Object.keys(fieldErrors).length > 0) {
        setReverseError(Object.values(fieldErrors)[0][0]);
      } else if (message) {
        setReverseError(message);
      } else {
        showApiError(err, "Couldn't reverse entry");
      }
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
            <h1 className="font-display text-lg font-semibold">{title}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">{entry.number} · {formatDate(entry.entry_date)}</p>
          </div>
          <StatusChip tone="success">{t("posted")}</StatusChip>
        </div>
        <ThemeToggle />
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-5xl space-y-6">
          {entry.reverses_entry_id && (
            <div className="rounded-[var(--radius-control)] border border-[var(--vinea-info)] bg-[var(--vinea-info-soft)] px-4 py-2 text-sm text-[var(--vinea-info)]">
              {t("reversalOf", { number: entry.reverses_entry_id })} —{" "}
              <Link href={`/gl/entries/${entry.reverses_entry_id}`} className="underline">
                view original
              </Link>
              {entry.reversal_reason && <span> · {entry.reversal_reason}</span>}
            </div>
          )}

          <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-5">
            <p className="text-sm text-[var(--vinea-ink-muted)]">{entry.description}</p>
          </div>

          <Table>
            <THead>
              <TR>
                <TH>Account</TH>
                <TH>Branch</TH>
                <TH>Project</TH>
                <TH className="text-right">Debit</TH>
                <TH className="text-right">Credit</TH>
              </TR>
            </THead>
            <TBody>
              {entry.lines.map((line) => {
                const account = accountById.get(line.gl_account_id);
                const branch = branchById.get(line.branch_id);
                const project = line.project_id ? projectById.get(line.project_id) : undefined;
                const currency = currencyById.get(line.currency_id) ?? baseCurrency;
                const base = Number(line.base_amount);
                return (
                  <TR key={line.id} className={line.is_rounding_line ? "bg-[var(--vinea-warning-soft)]/40" : undefined}>
                    <TD>
                      {account ? `${account.code} · ${account.name}` : line.gl_account_id}
                      {line.is_rounding_line && (
                        <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-[var(--vinea-warning-soft)] px-2 py-0.5 text-[10px] font-medium text-[var(--vinea-warning)]">
                          <Sparkles className="size-3" /> {t("roundingLine")}
                        </span>
                      )}
                      {line.description && <p className="text-xs text-[var(--vinea-ink-muted)]">{line.description}</p>}
                    </TD>
                    <TD>{branch?.code ?? "—"}</TD>
                    <TD>{project?.code ?? "—"}</TD>
                    <TD className="text-right">
                      {base > 0 && currency && <Money amount={base} currency={{ code: currency.code, decimalPlaces: currency.decimal_places, symbol: currency.symbol }} />}
                    </TD>
                    <TD className="text-right">
                      {base < 0 && currency && <Money amount={-base} currency={{ code: currency.code, decimalPlaces: currency.decimal_places, symbol: currency.symbol }} />}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </div>
      </main>

      <footer className="sticky bottom-0 border-t border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3 shadow-[var(--elevation-2)]">
        <div className="mx-auto flex max-w-5xl items-center justify-between">
          <div className="flex gap-8 text-sm">
            <div>
              <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("totalDebit")}</p>
              {baseCurrency && <Money amount={totalDebit} currency={{ code: baseCurrency.code, decimalPlaces: baseCurrency.decimal_places, symbol: baseCurrency.symbol }} className="font-semibold" />}
            </div>
            <div>
              <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("totalCredit")}</p>
              {baseCurrency && <Money amount={totalCredit} currency={{ code: baseCurrency.code, decimalPlaces: baseCurrency.decimal_places, symbol: baseCurrency.symbol }} className="font-semibold" />}
            </div>
          </div>
          <Dialog open={reverseOpen} onOpenChange={setReverseOpen}>
            <DialogTrigger asChild>
              <Button variant="danger">{t("reverse")}</Button>
            </DialogTrigger>
            <DialogContent title={t("reverseTitle")} description={t("reverseDescription")}>
              {reverseError && (
                <p className="mb-3 rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]">
                  {reverseError}
                </p>
              )}
              <div className="space-y-3">
                <Field label={t("date")}>
                  <DatePicker value={reverseDate} onValueChange={setReverseDate} />
                </Field>
                <Field label={t("reason")}>
                  <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Duplicate posting" />
                </Field>
              </div>
              <div className="mt-4 flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setReverseOpen(false)}>{t("cancel")}</Button>
                <Button variant="danger" disabled={!reason || reverseEntry.isPending} onClick={handleReverse}>
                  {t("confirmReverse")}
                </Button>
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </footer>
    </div>
  );
}
