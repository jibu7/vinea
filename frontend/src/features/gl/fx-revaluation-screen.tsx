"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ExternalLink } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { FxRevaluationRole, FxRevaluationStatus } from "@/lib/api-enums";
import { newDraftId } from "@/lib/drafts";
import { formatDate, formatMoney, todayIso } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import {
  useCompanyDetails,
  useCurrencies,
  useFxRevaluation,
  useFxRevaluationPreview,
  useFxRevaluations,
  usePostFxRevaluation,
  useReverseFxRevaluation,
} from "./hooks";
import type { FxRevaluationLine } from "./types";

/**
 * A document line is keyed and labelled by its document; a **bank line** (P8 decision 8) has no
 * document — `document_id` is null on it — and is keyed and labelled by the bank account, whose
 * code sits where the document number does and whose name sits where the partner does.
 */
function lineKey(line: FxRevaluationLine): string {
  return line.document_id !== null ? `document-${line.document_id}` : `bank-${line.bank_account_id}`;
}

function lineLabel(line: FxRevaluationLine): string {
  return (line.document_number ?? line.bank_account_code) ?? "";
}

function lineName(line: FxRevaluationLine): string {
  return (line.partner_name ?? line.bank_account_name) ?? "";
}

/** The last day of the month `today` falls in. The run is refused on any other date
 * (`fx_revaluation_not_period_end`), so offering one is offering the answer. */
function monthEndOf(today: string): string {
  const [year, month] = today.split("-").map(Number);
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return `${year}-${String(month).padStart(2, "0")}-${String(last).padStart(2, "0")}`;
}

/**
 * **FX revaluation** — Transactions → General Ledger.
 *
 * What the open foreign-currency partner documents are worth at a date, and the entry that says
 * so.
 *
 * **It never touches a control account.** `1200` and `2100` are subledger-only (P4's VN007) and
 * their balance is Σ open items at *booking* rates — the invariant the whole subledger rests
 * on. So the revaluation moves `1290 AR Revaluation` / `2190 AP Revaluation` against the
 * unrealized gain and loss accounts, and the control accounts are where they were. The preview
 * shows the per-document difference the entry is built from, so the figure is auditable before
 * it is posted rather than after.
 *
 * **Two entries, one transaction.** The run posts at the revaluation date *and* its mirror the
 * following day, so the balance sheet at the date carries the revaluation and the next period
 * does not. Realized FX at allocation stays P4's and is untouched by this.
 *
 * **Bank and cash accounts are a scope of the same run** (P8 decision 8): the `bank` role
 * revalues every foreign-currency bank account's balance, and `all` is the month-end press —
 * customers, suppliers and bank together. A bank line has no document and no partner, so it is
 * keyed by the account and shows the account's code and name in their place; its gain or loss
 * goes to `1130 Bank Revaluation`, never to the bank account, whose lines are the statement's.
 *
 * The three refusals live on the Post button, not here: the period must be open, the date must
 * be a period end, and a run for that (role, date) must not already stand — reverse it first.
 */
/**
 * `openId` pre-opens one row's detail, and it exists for the **drill**.
 *
 * `sources.py` resolves a ``FXR-`` journal entry to its run, and the entry page's
 * "reverse via the module's document" link has to land where Reverse actually is — which is
 * here, not on the report. The report answers "what did we revalue over this range"; this screen
 * owns the document and its actions (P5 step 9's kernel rule: a module-owned entry reverses
 * through its module's document, never from the general ledger).
 */
export function FxRevaluationScreen({ openId }: { openId?: number } = {}) {
  const t = useTranslations("gl.fxRevaluation");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canRevalue = useHasPermission()("gl:fx_revalue");

  const [revaluationDate, setRevaluationDate] = useState(() => monthEndOf(todayIso()));
  const [role, setRole] = useState<FxRevaluationRole>(FxRevaluationRole.BOTH);
  const [postOpen, setPostOpen] = useState(false);
  const [postError, setPostError] = useState<string | null>(null);
  const [openRunId, setOpenRunId] = useState<number | null>(openId ?? null);
  const [reverseReason, setReverseReason] = useState("");
  const [reverseError, setReverseError] = useState<string | null>(null);

  const company = useCompanyDetails();
  const currencies = useCurrencies();
  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };
  const money = (value: string | number) => formatMoney(Number(value), baseLike, { showCode: false });
  const currencyById = new Map((currencies.data ?? []).map((c) => [c.id, c]));
  /** The open amount in **the line's own currency** — a USD account's balance reads USD 495.00,
   * not the raw `NUMERIC(20,6)` the wire carries. */
  const inCurrency = (value: string, currencyId: number) => {
    const currency = currencyById.get(currencyId);
    return formatMoney(Number(value), {
      code: currency?.code ?? "",
      decimalPlaces: currency?.decimal_places ?? 2,
      symbol: currency?.symbol ?? null,
    });
  };

  const preview = useFxRevaluationPreview(revaluationDate, role);
  const runs = useFxRevaluations();
  const detail = useFxRevaluation(openRunId);
  const post = usePostFxRevaluation();
  const reverse = useReverseFxRevaluation();

  async function handlePost() {
    setPostError(null);
    try {
      const run = await post.mutateAsync({
        revaluationDate,
        role,
        idempotencyKey: newDraftId(),
      });
      setPostOpen(false);
      toast.show({ title: t("posted", { number: run.number }), tone: "success" });
    } catch (err) {
      if (isApiError(err)) setPostError(err.message);
      else showApiError(err, t("postFailed"));
    }
  }

  async function handleReverse() {
    if (openRunId === null) return;
    setReverseError(null);
    try {
      const run = await reverse.mutateAsync({ revaluationId: openRunId, reason: reverseReason });
      setOpenRunId(null);
      setReverseReason("");
      toast.show({ title: t("reversed", { number: run.number }), tone: "success" });
    } catch (err) {
      if (isApiError(err)) setReverseError(err.message);
      else showApiError(err, t("reverseFailed"));
    }
  }

  const lines = preview.data?.lines ?? [];

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      asOfLabel={formatDate(revaluationDate)}
      filters={
        <div className="flex flex-wrap items-end gap-3">
          <Field label={t("revaluationDate")} className="w-44">
            <IsoDatePicker value={revaluationDate} onValueChange={setRevaluationDate} />
          </Field>
          <Field label={t("role")} className="w-44">
            <Combobox
              options={Object.values(FxRevaluationRole).map((value) => ({
                value,
                label: t(`roleLabel.${value}`),
              }))}
              value={role}
              onValueChange={(v) => setRole(v as FxRevaluationRole)}
              placeholder={t("roleLabel.both")}
            />
          </Field>
          <Button
            variant="primary"
            disabled={!canRevalue || lines.length === 0}
            data-testid="post-revaluation"
            onClick={() => {
              setPostError(null);
              setPostOpen(true);
            }}
          >
            {t("post")}
          </Button>
        </div>
      }
    >
      <ReportPanel>
        <div className="flex items-center justify-between pb-2">
          <h2 className="text-xs font-semibold text-[var(--vinea-ink-muted)]">{t("preview")}</h2>
          {preview.data && (
            <p className="text-xs text-[var(--vinea-ink-muted)]">
              {t("totalDifference")}{" "}
              <span
                className="font-mono tabular-nums text-[var(--vinea-ink)]"
                data-testid="revaluation-total"
              >
                {money(preview.data.total_difference)}
              </span>
            </p>
          )}
        </div>
        <p className="pb-3 text-xs text-[var(--vinea-ink-subtle)]">{t("previewNote")}</p>
        {lines.length === 0 ? (
          <QueryState query={preview} isEmpty empty={t("nothingToRevalue")} testId="revaluation" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-32">{t("documentOrAccount")}</TH>
                <TH>{t("partnerOrBank")}</TH>
                <TH className="w-20">{t("currency")}</TH>
                <TH className="w-28 text-right">{t("openAmount")}</TH>
                <TH className="w-28 text-right">{t("bookingRate")}</TH>
                <TH className="w-32 text-right">{t("carryingBase")}</TH>
                <TH className="w-28 text-right">{t("rateAtDate")}</TH>
                <TH className="w-32 text-right">{t("revaluedBase")}</TH>
                <TH className="w-32 text-right">{t("difference")}</TH>
              </TR>
            </THead>
            <TBody>
              {lines.map((line) => (
                <TR key={lineKey(line)} data-revaluation-line={lineLabel(line)}>
                  <TD className="font-mono text-xs font-semibold">{lineLabel(line)}</TD>
                  <TD className="text-xs">{lineName(line)}</TD>
                  <TD className="font-mono text-xs">{line.currency_code}</TD>
                  <TD
                    className="text-right font-mono text-xs tabular-nums whitespace-nowrap"
                    data-testid={`open-${lineLabel(line)}`}
                  >
                    {inCurrency(line.open_amount, line.currency_id)}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                    {line.booking_rate ?? t("emptyValue")}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {money(line.carrying_base)}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                    {line.rate_at_date}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {money(line.revalued_base)}
                  </TD>
                  <TD
                    className="text-right font-mono text-xs tabular-nums"
                    data-testid={`difference-${lineLabel(line)}`}
                  >
                    {money(line.difference)}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </ReportPanel>

      <ReportPanel>
        <h2 className="pb-2 text-xs font-semibold text-[var(--vinea-ink-muted)]">{t("runs")}</h2>
        {(runs.data ?? []).length === 0 ? (
          <QueryState query={runs} isEmpty empty={t("noRuns")} testId="revaluation-runs" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-32">{t("number")}</TH>
                <TH className="w-32">{t("revaluationDate")}</TH>
                <TH className="w-28">{t("role")}</TH>
                <TH className="w-28">{t("entry")}</TH>
                <TH className="w-28">{t("mirror")}</TH>
                <TH className="w-28">{t("status")}</TH>
                <TH className="w-28 print:hidden" />
              </TR>
            </THead>
            <TBody>
              {(runs.data ?? []).map((run) => (
                <TR key={run.id}>
                  <TD className="font-mono text-xs font-semibold">{run.number}</TD>
                  <TD className="text-xs">{formatDate(run.revaluation_date)}</TD>
                  <TD className="text-xs">{t(`roleLabel.${run.role}`)}</TD>
                  <TD className="text-xs">
                    {run.journal_entry_id !== null && (
                      <Link
                        href={`/gl/entries/${run.journal_entry_id}`}
                        className="inline-flex items-center gap-1 font-mono text-xs text-[var(--vinea-brand)] underline"
                      >
                        {t("viewEntry")}
                        <ExternalLink className="size-3" />
                      </Link>
                    )}
                  </TD>
                  <TD className="text-xs">
                    {run.mirror_entry_id !== null && (
                      <Link
                        href={`/gl/entries/${run.mirror_entry_id}`}
                        className="inline-flex items-center gap-1 font-mono text-xs text-[var(--vinea-brand)] underline"
                        data-testid={`mirror-${run.number}`}
                      >
                        {t("viewEntry")}
                        <ExternalLink className="size-3" />
                      </Link>
                    )}
                  </TD>
                  <TD>
                    <StatusChip
                      tone={run.status === FxRevaluationStatus.POSTED ? "success" : "neutral"}
                    >
                      {t(`statusLabel.${run.status}`)}
                    </StatusChip>
                  </TD>
                  <TD className="print:hidden">
                    <Button
                      variant="ghost"
                      data-testid={`open-run-${run.id}`}
                      onClick={() => {
                        setReverseError(null);
                        setReverseReason("");
                        setOpenRunId(run.id);
                      }}
                    >
                      {t("open")}
                    </Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </ReportPanel>

      <Dialog open={postOpen} onOpenChange={setPostOpen}>
        <DialogContent title={t("postTitle")} description={t("postDescription")}>
          {postError && (
            <p
              data-testid="post-error"
              className="mb-3 rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
            >
              {postError}
            </p>
          )}
          <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("postNote")}</p>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setPostOpen(false)}>
              {t("cancel")}
            </Button>
            <Button
              variant="primary"
              disabled={post.isPending}
              data-testid="confirm-post"
              onClick={handlePost}
            >
              {t("confirmPost")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={openRunId !== null} onOpenChange={(open) => !open && setOpenRunId(null)}>
        <DialogContent title={t("detailTitle")} description={t("detailDescription")}>
          {detail.data && (
            <div className="max-h-[60vh] space-y-3 overflow-auto">
              <Table>
                <THead>
                  <TR>
                    <TH className="w-32">{t("documentOrAccount")}</TH>
                    <TH>{t("partnerOrBank")}</TH>
                    <TH className="w-32 text-right">{t("difference")}</TH>
                  </TR>
                </THead>
                <TBody>
                  {detail.data.lines.map((line) => (
                    <TR key={lineKey(line)}>
                      <TD className="font-mono text-xs">{lineLabel(line)}</TD>
                      <TD className="text-xs">{lineName(line)}</TD>
                      <TD
                        className="text-right font-mono text-xs tabular-nums"
                        data-testid="run-difference"
                      >
                        {money(line.difference)}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
              {reverseError && (
                <p
                  data-testid="reverse-error"
                  className="rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
                >
                  {reverseError}
                </p>
              )}
              {detail.data.status === FxRevaluationStatus.POSTED && (
                <Field label={t("reverseReason")}>
                  <Input
                    value={reverseReason}
                    onChange={(e) => setReverseReason(e.target.value)}
                    data-testid="fx-reverse-reason"
                    placeholder={t("reverseReasonPlaceholder")}
                  />
                </Field>
              )}
            </div>
          )}
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpenRunId(null)}>
              {t("close")}
            </Button>
            {detail.data?.status === FxRevaluationStatus.POSTED && (
              <Button
                variant="danger"
                disabled={!canRevalue || reverseReason.length < 3 || reverse.isPending}
                data-testid="confirm-fx-reverse"
                onClick={handleReverse}
              >
                {t("reverse")}
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </ReportPage>
  );
}
