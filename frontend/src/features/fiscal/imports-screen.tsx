"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Download } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import { useCompanyDetails } from "@/features/gl/hooks";
import { useItems } from "@/features/inventory/hooks";
import { FiscalImportStatus } from "@/lib/api-enums";
import { newDraftId } from "@/lib/drafts";
import { dotted, formatDate, formatQuantity } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import {
  useApproveImportDeclaration,
  useFetchImports,
  useFiscalDevices,
  useImportDeclarations,
  useRejectImportDeclaration,
} from "./hooks";
import type { ImportDeclaration } from "./types";

const STATUS_TONE: Record<string, "neutral" | "success" | "danger"> = {
  [FiscalImportStatus.PENDING]: "neutral",
  [FiscalImportStatus.APPROVED]: "success",
  [FiscalImportStatus.REJECTED]: "danger",
};

/**
 * **Import declarations** — Transactions → Tax.
 *
 * What customs told RRA this taxpayer brought into the country, line by line, waiting to be
 * matched against what the warehouse actually received.
 *
 * **Approving moves nothing and posts nothing**, and that is worth saying on the screen as
 * well as here: the goods reached the ledger through a goods receipt, and acknowledging the
 * declaration a second time would double them. What approval *is* is a compliance
 * acknowledgment — RRA asks which item code a declared line became, and this is where a person
 * answers.
 *
 * The item picker is therefore the whole content of the decision, which is why Approve is
 * disabled until one is chosen rather than sending a null and being refused.
 */
export function ImportDeclarationsScreen() {
  const t = useTranslations("fiscal.imports");
  const tc = useTranslations("fiscal.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canManage = useHasPermission()("fiscal:queue_manage");

  const [status, setStatus] = useState<string>(FiscalImportStatus.PENDING);
  const [approving, setApproving] = useState<ImportDeclaration | null>(null);
  const [rejecting, setRejecting] = useState<ImportDeclaration | null>(null);
  const [itemId, setItemId] = useState("");
  const [note, setNote] = useState("");

  const company = useCompanyDetails();
  const devices = useFiscalDevices();
  const declarations = useImportDeclarations({ status: status || undefined });
  const fetchImports = useFetchImports();
  const approve = useApproveImportDeclaration();
  const reject = useRejectImportDeclaration();
  const items = useItems();

  const itemOptions = useMemo(
    () =>
      (items.data ?? []).map((item) => ({
        value: String(item.id),
        label: dotted(item.code, item.name),
      })),
    [items.data],
  );
  const itemById = useMemo(
    () => new Map((items.data ?? []).map((item) => [item.id, item])),
    [items.data],
  );

  const activeDevice = (devices.data ?? []).find((device) => device.sdc_id !== null);
  const rows = declarations.data ?? [];

  async function handleFetch() {
    if (!activeDevice) return;
    try {
      const result = await fetchImports.mutateAsync(activeDevice.id);
      toast.show({ title: t("fetched", { rows: result.rows }), tone: "success" });
    } catch (err) {
      showApiError(err, t("fetchFailed"));
    }
  }

  async function handleApprove() {
    if (!approving || !itemId) return;
    try {
      await approve.mutateAsync({
        declarationId: approving.id,
        itemId: Number(itemId),
        note: note.trim() || null,
        idempotencyKey: newDraftId(),
      });
      setApproving(null);
      setItemId("");
      setNote("");
      toast.show({ title: t("approved"), tone: "success" });
    } catch (err) {
      showApiError(err, t("approveFailed"));
    }
  }

  async function handleReject() {
    if (!rejecting) return;
    try {
      await reject.mutateAsync({
        declarationId: rejecting.id,
        note: note.trim() || null,
        idempotencyKey: newDraftId(),
      });
      setRejecting(null);
      setNote("");
      toast.show({ title: t("rejected"), tone: "success" });
    } catch (err) {
      showApiError(err, t("rejectFailed"));
    }
  }

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      filters={
        <div className="flex flex-wrap items-end gap-3">
          <Field label={tc("status")} className="w-56">
            <Combobox
              options={[
                { value: "", label: t("allStatuses") },
                ...Object.values(FiscalImportStatus).map((value) => ({
                  value,
                  label: t(`statusLabel.${value}`),
                })),
              ]}
              value={status}
              onValueChange={setStatus}
              placeholder={t("allStatuses")}
            />
          </Field>
          <Button
            variant="secondary"
            disabled={!canManage || !activeDevice || fetchImports.isPending}
            onClick={handleFetch}
            data-testid="fetch-imports"
            className="gap-1.5 text-xs"
          >
            <Download className="size-3.5" /> {t("fetch")}
          </Button>
        </div>
      }
    >
      <ReportPanel>
        <p className="pb-3 text-xs text-[var(--vinea-ink-subtle)]">{t("acknowledgmentNote")}</p>
        {rows.length === 0 ? (
          <QueryState query={declarations} isEmpty empty={t("empty")} testId="imports" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-32">{t("declaration")}</TH>
                <TH className="w-28">{t("declarationDate")}</TH>
                <TH className="w-28">{t("hsCode")}</TH>
                <TH>{t("declaredItem")}</TH>
                <TH className="w-24 text-right">{t("quantity")}</TH>
                <TH>{t("supplier")}</TH>
                <TH className="w-40">{t("vineaItem")}</TH>
                <TH className="w-28">{tc("status")}</TH>
                <TH className="w-36 print:hidden" />
              </TR>
            </THead>
            <TBody>
              {rows.map((row) => (
                <TR key={row.id}>
                  <TD className="font-mono text-xs">{dotted(row.dcl_no, String(row.item_seq))}</TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {row.dcl_de ? formatDate(row.dcl_de) : tc("emptyValue")}
                  </TD>
                  <TD className="font-mono text-xs">{row.hs_cd ?? tc("emptyValue")}</TD>
                  <TD className="text-xs">{row.item_nm ?? tc("emptyValue")}</TD>
                  <TD
                    className="text-right font-mono text-xs tabular-nums"
                    data-testid={`import-qty-${row.id}`}
                  >
                    {row.qty === null ? tc("emptyValue") : formatQuantity(Number(row.qty), 2)}
                  </TD>
                  <TD className="text-xs">{row.spplr_nm ?? tc("emptyValue")}</TD>
                  <TD className="text-xs">
                    {row.item_id === null
                      ? tc("emptyValue")
                      : (itemById.get(row.item_id)?.code ?? String(row.item_id))}
                  </TD>
                  <TD>
                    <StatusChip tone={STATUS_TONE[row.status] ?? "neutral"}>
                      <span data-testid={`import-status-${row.id}`}>
                        {t(`statusLabel.${row.status}`)}
                      </span>
                    </StatusChip>
                  </TD>
                  <TD className="print:hidden">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        disabled={!canManage || row.status !== FiscalImportStatus.PENDING}
                        data-testid={`approve-${row.id}`}
                        onClick={() => {
                          setItemId(row.item_id ? String(row.item_id) : "");
                          setNote("");
                          setApproving(row);
                        }}
                      >
                        {t("approve")}
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={!canManage || row.status !== FiscalImportStatus.PENDING}
                        data-testid={`reject-${row.id}`}
                        onClick={() => {
                          setNote("");
                          setRejecting(row);
                        }}
                      >
                        {t("reject")}
                      </Button>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </ReportPanel>

      <Dialog open={approving !== null} onOpenChange={(open) => !open && setApproving(null)}>
        <DialogContent title={t("approveTitle")} description={t("approveDescription")}>
          <div className="space-y-3">
            <Field label={t("vineaItem")}>
              <Combobox
                options={itemOptions}
                value={itemId}
                onValueChange={setItemId}
                placeholder={t("chooseItem")}
              />
            </Field>
            <Field label={t("note")}>
              <Input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                data-testid="import-note"
                placeholder={t("notePlaceholder")}
              />
            </Field>
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setApproving(null)}>
              {tc("cancel")}
            </Button>
            <Button
              variant="primary"
              disabled={!itemId || approve.isPending}
              data-testid="confirm-approve"
              onClick={handleApprove}
            >
              {t("confirmApprove")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={rejecting !== null} onOpenChange={(open) => !open && setRejecting(null)}>
        <DialogContent title={t("rejectTitle")} description={t("rejectDescription")}>
          <Field label={t("note")}>
            <Input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              data-testid="reject-note"
              placeholder={t("notePlaceholder")}
            />
          </Field>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setRejecting(null)}>
              {tc("cancel")}
            </Button>
            <Button
              variant="danger"
              disabled={reject.isPending}
              data-testid="confirm-reject"
              onClick={handleReject}
            >
              {t("confirmReject")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </ReportPage>
  );
}
