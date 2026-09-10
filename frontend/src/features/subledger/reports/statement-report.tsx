"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Download, FileText } from "lucide-react";
import { Button } from "@/design/components/button";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Field } from "@/design/components/input";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { useCompanyDetails } from "@/features/gl/hooks";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useJob, usePartners, useQueueStatement } from "../hooks";
import { partnerCode, type PartnerRole } from "../types";

const API_BASE = `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/v1`;

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Statements: queue → poll → download. There is deliberately no synchronous PDF endpoint —
 * a statement run over a few hundred partners is a job, and pretending otherwise would tie a
 * request thread to WeasyPrint for as long as it takes.
 *
 * The download is a plain link to the artifact endpoint rather than a fetch-and-blob, so the
 * browser handles the file and the cookie goes with it.
 */
export function StatementReport({ role }: { role: PartnerRole }) {
  const t = useTranslations("reports");
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const [asOf, setAsOf] = useState(today);
  const [variant, setVariant] = useState("open_item");
  const [selected, setSelected] = useState<number[]>([]);
  const [jobId, setJobId] = useState<number | null>(null);

  const partners = usePartners(role);
  const company = useCompanyDetails();
  const queueStatement = useQueueStatement(role);
  const job = useJob(jobId);

  function toggle(partnerId: number) {
    setSelected((current) =>
      current.includes(partnerId)
        ? current.filter((id) => id !== partnerId)
        : [...current, partnerId],
    );
  }

  async function handleQueue() {
    if (selected.length === 0) {
      toast.show({ title: t("stmtSelectPartners"), tone: "neutral" });
      return;
    }
    try {
      const queued = await queueStatement.mutateAsync({
        partner_ids: selected,
        as_of: asOf,
        variant,
      });
      setJobId(queued.id);
    } catch (err) {
      showApiError(err, t("stmtFailed"));
    }
  }

  const status = job.data?.status;

  return (
    <ReportPage
      title={t(role === "ar" ? "stmtTitleAr" : "stmtTitleAp")}
      subtitle={t("stmtSubtitle")}
      companyName={company.data?.name}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-4">
          <Field label={t("asOfDate")}>
            <IsoDatePicker value={asOf} onValueChange={setAsOf} />
          </Field>
          <Field label={t("stmtVariant")}>
            <Select
              options={[
                { value: "open_item", label: t("stmtOpenItem") },
                { value: "activity", label: t("stmtActivity") },
              ]}
              value={variant}
              onValueChange={setVariant}
            />
          </Field>
          <div className="flex items-end">
            <Button
              variant="primary"
              onClick={handleQueue}
              disabled={queueStatement.isPending}
              className="gap-1.5 text-xs"
            >
              <FileText className="size-3.5" /> {t("stmtQueue")}
            </Button>
          </div>
        </div>
      }
    >
      {jobId !== null && (
        <ReportPanel>
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <StatusChip
                tone={
                  status === "succeeded"
                    ? "success"
                    : status === "failed"
                      ? "danger"
                      : "info"
                }
              >
                {status === "succeeded"
                  ? t("stmtReady")
                  : status === "failed"
                    ? t("stmtFailed")
                    : status === "running"
                      ? t("stmtRunning")
                      : t("stmtQueued")}
              </StatusChip>
              <span className="text-xs text-[var(--vinea-ink-muted)]">
                {t("stmtJob", { id: jobId })}
              </span>
              {job.data?.error && (
                <span className="text-xs text-[var(--vinea-danger)]">{job.data.error}</span>
              )}
            </div>
            {status === "succeeded" && (
              <a
                href={`${API_BASE}/subledger/jobs/${jobId}/artifact`}
                data-testid="statement-download"
                className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] bg-[var(--vinea-brand)] px-3 py-1.5 text-xs font-medium text-[var(--vinea-on-brand)]"
              >
                <Download className="size-3.5" /> {t("stmtDownload")}
              </a>
            )}
          </div>
        </ReportPanel>
      )}

      <ReportPanel>
        {(partners.data ?? []).length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {partners.isLoading ? t("loading") : t("noRows")}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-10" />
                <TH className="w-32">{t("code")}</TH>
                <TH>{t("name")}</TH>
              </TR>
            </THead>
            <TBody>
              {(partners.data ?? []).map((partner) => (
                <TR key={partner.id}>
                  <TD>
                    <input
                      type="checkbox"
                      checked={selected.includes(partner.id)}
                      onChange={() => toggle(partner.id)}
                      aria-label={partner.name}
                      className="size-3.5"
                    />
                  </TD>
                  <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                    {partnerCode(partner, role)}
                  </TD>
                  <TD className="text-xs">{partner.name}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </ReportPanel>
    </ReportPage>
  );
}
