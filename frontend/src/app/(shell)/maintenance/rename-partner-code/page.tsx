"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { ArrowRight, History, ShieldAlert, Tag } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Field, Input } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import { usePartnerHistory, usePartners, useUpdatePartner } from "@/features/subledger/hooks";
import { partnerCode, type PartnerRole } from "@/features/subledger/types";
import { formatDate } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

function codeOf(record: Record<string, unknown> | null, role: PartnerRole): string | undefined {
  const key = role === "ar" ? "customer_code" : "supplier_code";
  const value = record?.[key];
  return typeof value === "string" ? value : undefined;
}

/**
 * The AR/AP twin of "Rename account": the code changes, the partner id does not, so every
 * document, allocation and journal line keeps pointing at the same partner. The audit trail
 * hangs off `partner_id`, which is why the history below survives the rename.
 */
function RenamePartnerCodeView() {
  const t = useTranslations("arap.rename");
  const tc = useTranslations("arap.common");
  const tp = useTranslations("arap.partners");
  const searchParams = useSearchParams();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();

  const initialRole: PartnerRole = searchParams.get("role") === "ap" ? "ap" : "ar";
  const [role, setRole] = useState<PartnerRole>(initialRole);
  const [selectedId, setSelectedId] = useState("");
  const [newCode, setNewCode] = useState("");

  const tr = useTranslations(`arap.role.${role}`);
  const canEdit = hasPermission(`${role}:setup_manage`);

  const partners = usePartners(role, { includeInactive: true });
  const updatePartner = useUpdatePartner(role);

  const partnerId = selectedId ? Number(selectedId) : null;
  const current = partners.data?.find((p) => p.id === partnerId) ?? null;
  const history = usePartnerHistory(role, partnerId);

  useEffect(() => {
    setNewCode(current ? (partnerCode(current, role) ?? "") : "");
  }, [current, role]);

  // The two roles are separate lists; switching drops whatever was picked in the other one.
  function switchRole(next: PartnerRole) {
    setRole(next);
    setSelectedId("");
    setNewCode("");
  }

  async function handleRename() {
    if (!current) return;
    const existing = partnerCode(current, role);
    if (!newCode || newCode === existing) return;
    try {
      const updated = await updatePartner.mutateAsync({
        partnerId: current.id,
        payload: role === "ar" ? { customer_code: newCode } : { supplier_code: newCode },
      });
      toast.show({
        title: tr("renamed"),
        description: `${existing} → ${partnerCode(updated, role)}`,
        tone: "success",
      });
      history.refetch();
    } catch (err) {
      showApiError(err, tr("renameFailed"));
    }
  }

  const changed = newCode.trim() !== "" && newCode !== (current ? partnerCode(current, role) : "");

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      backHref={role === "ar" ? "/maintenance/customers" : "/maintenance/suppliers"}
    >
      <MaintenanceCard icon={<Tag className="size-4" />} title={t("selectTitle")}>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-[12rem_1fr]">
          <Field label={t("roleLabel")}>
            <Select
              options={[
                { value: "ar", label: t("roleAr") },
                { value: "ap", label: t("roleAp") },
              ]}
              value={role}
              onValueChange={(v) => switchRole(v as PartnerRole)}
            />
          </Field>
          <Field label={tr("partner")}>
            <Combobox
              options={(partners.data ?? []).map((p) => ({
                value: String(p.id),
                label: `${partnerCode(p, role)} · ${p.name}`,
              }))}
              value={selectedId}
              onValueChange={setSelectedId}
              placeholder={tr("choose")}
            />
          </Field>
        </div>

        {current && (
          <div className="space-y-4 rounded-[var(--radius-control)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-sunken)]/50 p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs uppercase tracking-wider text-[var(--vinea-ink-subtle)]">
                  {tr("partner")}
                </p>
                <p className="text-base font-semibold text-[var(--vinea-ink)]">{current.name}</p>
              </div>
              <div className="flex gap-2">
                {current.is_customer && current.is_supplier && (
                  <StatusChip tone="info">{tp("bothRoles")}</StatusChip>
                )}
                <StatusChip tone={current.is_active ? "success" : "neutral"}>
                  {current.is_active ? tc("active") : tc("inactive")}
                </StatusChip>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label={tr("currentCode")}>
                <Input
                  value={partnerCode(current, role) ?? ""}
                  disabled
                  className="bg-[var(--vinea-surface-sunken)] font-mono"
                />
              </Field>
              <Field label={tr("newCode")}>
                <Input
                  value={newCode}
                  onChange={(e) => setNewCode(e.target.value)}
                  className="font-mono"
                  placeholder={tr("newCodePlaceholder")}
                />
              </Field>
            </div>

            <div className="flex items-center justify-between gap-4">
              <p className="flex items-center gap-2 text-xs text-[var(--vinea-ink-muted)]">
                <ShieldAlert className="size-4 shrink-0 text-[var(--vinea-warning)]" />
                {t("safetyNote")}
              </p>
              <Button
                variant="primary"
                disabled={!changed || !canEdit || updatePartner.isPending}
                onClick={handleRename}
              >
                {updatePartner.isPending ? tc("saving") : t("rename")}
              </Button>
            </div>
          </div>
        )}
      </MaintenanceCard>

      {current && (
        <MaintenanceCard icon={<History className="size-4" />} title={t("historyTitle")}>
          {history.isLoading ? (
            <p className="py-4 text-center text-xs text-[var(--vinea-ink-subtle)]">{t("loadingHistory")}</p>
          ) : (history.data ?? []).length === 0 ? (
            <p className="text-xs text-[var(--vinea-ink-subtle)]">{tr("noHistory")}</p>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH className="w-36">{t("timestamp")}</TH>
                  <TH className="w-40">{t("event")}</TH>
                  <TH>{t("details")}</TH>
                  <TH className="w-48 text-right">{t("changedBy")}</TH>
                </TR>
              </THead>
              <TBody>
                {(history.data ?? []).map((record) => {
                  const before = codeOf(record.before, role);
                  const after = codeOf(record.after, role);
                  const isRename = record.action === "partner.renamed" && before !== after;
                  return (
                    <TR key={record.id}>
                      <TD className="text-xs text-[var(--vinea-ink-muted)]">{formatDate(record.at)}</TD>
                      <TD>
                        <StatusChip tone={isRename ? "warning" : "neutral"}>{record.action}</StatusChip>
                      </TD>
                      <TD className="text-xs">
                        {isRename && before && after ? (
                          <span className="inline-flex items-center gap-1.5 font-mono">
                            <span className="text-[var(--vinea-ink-subtle)] line-through">{before}</span>
                            <ArrowRight className="size-3 text-[var(--vinea-ink-muted)]" />
                            <span className="font-bold text-[var(--vinea-brand)]">{after}</span>
                          </span>
                        ) : (
                          <span className="text-[var(--vinea-ink-muted)]">
                            {String(
                              (record.after as { name?: string } | null)?.name ?? tc("emptyValue"),
                            )}
                          </span>
                        )}
                      </TD>
                      <TD className="text-right text-xs text-[var(--vinea-ink-muted)]">
                        {record.actor_email ?? t("system")}
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
        </MaintenanceCard>
      )}
    </MaintenancePage>
  );
}

export default function RenamePartnerCodePage() {
  const tc = useTranslations("arap.common");
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center text-sm">{tc("loading")}</div>
      }
    >
      <RenamePartnerCodeView />
    </Suspense>
  );
}
