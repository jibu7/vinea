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
import { ROLE_COPY, partnerCode, type PartnerRole } from "@/features/subledger/types";
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
  const t = useTranslations("maintenance");
  const searchParams = useSearchParams();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();

  const initialRole: PartnerRole = searchParams.get("role") === "ap" ? "ap" : "ar";
  const [role, setRole] = useState<PartnerRole>(initialRole);
  const [selectedId, setSelectedId] = useState("");
  const [newCode, setNewCode] = useState("");

  const copy = ROLE_COPY[role];
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
        title: `${copy.partner} renamed`,
        description: `${existing} → ${partnerCode(updated, role)}`,
        tone: "success",
      });
      history.refetch();
    } catch (err) {
      showApiError(err, `Couldn't rename ${copy.partner.toLowerCase()}`);
    }
  }

  const changed = newCode.trim() !== "" && newCode !== (current ? partnerCode(current, role) : "");

  return (
    <MaintenancePage
      title={t("renamePartnerCode")}
      description="Change a code while every document, allocation and open item keeps its history"
      backHref={role === "ar" ? "/maintenance/customers" : "/maintenance/suppliers"}
    >
      <MaintenanceCard icon={<Tag className="size-4" />} title="Select a code to rename">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-[12rem_1fr]">
          <Field label="Role">
            <Select
              options={[
                { value: "ar", label: "Customer" },
                { value: "ap", label: "Supplier" },
              ]}
              value={role}
              onValueChange={(v) => switchRole(v as PartnerRole)}
            />
          </Field>
          <Field label={copy.partner}>
            <Combobox
              options={(partners.data ?? []).map((p) => ({
                value: String(p.id),
                label: `${partnerCode(p, role)} · ${p.name}`,
              }))}
              value={selectedId}
              onValueChange={setSelectedId}
              placeholder={`Choose ${copy.partner.toLowerCase()}…`}
            />
          </Field>
        </div>

        {current && (
          <div className="space-y-4 rounded-[var(--radius-control)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-sunken)]/50 p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs uppercase tracking-wider text-[var(--vinea-ink-subtle)]">
                  {copy.partner}
                </p>
                <p className="text-base font-semibold text-[var(--vinea-ink)]">{current.name}</p>
              </div>
              <div className="flex gap-2">
                {current.is_customer && current.is_supplier && (
                  <StatusChip tone="info">Both roles</StatusChip>
                )}
                <StatusChip tone={current.is_active ? "success" : "neutral"}>
                  {current.is_active ? "Active" : "Inactive"}
                </StatusChip>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label={`Current ${copy.code.toLowerCase()}`}>
                <Input
                  value={partnerCode(current, role) ?? ""}
                  disabled
                  className="bg-[var(--vinea-surface-sunken)] font-mono"
                />
              </Field>
              <Field label={`New ${copy.code.toLowerCase()}`}>
                <Input
                  value={newCode}
                  onChange={(e) => setNewCode(e.target.value)}
                  className="font-mono"
                  placeholder={role === "ar" ? "CUST100" : "SUPP100"}
                />
              </Field>
            </div>

            <div className="flex items-center justify-between gap-4">
              <p className="flex items-center gap-2 text-xs text-[var(--vinea-ink-muted)]">
                <ShieldAlert className="size-4 shrink-0 text-[var(--vinea-warning)]" />
                Documents, allocations and journal lines stay tied to this partner; only the
                visible code changes. The other role&rsquo;s code is untouched.
              </p>
              <Button
                variant="primary"
                disabled={!changed || !canEdit || updatePartner.isPending}
                onClick={handleRename}
              >
                {updatePartner.isPending ? "Saving…" : "Rename"}
              </Button>
            </div>
          </div>
        )}
      </MaintenanceCard>

      {current && (
        <MaintenanceCard icon={<History className="size-4" />} title="Rename history">
          {history.isLoading ? (
            <p className="py-4 text-center text-xs text-[var(--vinea-ink-subtle)]">Loading history…</p>
          ) : (history.data ?? []).length === 0 ? (
            <p className="text-xs text-[var(--vinea-ink-subtle)]">
              No change events recorded for this {copy.partner.toLowerCase()}.
            </p>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH className="w-36">Timestamp</TH>
                  <TH className="w-40">Event</TH>
                  <TH>Details</TH>
                  <TH className="w-48 text-right">Changed by</TH>
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
                            {String((record.after as { name?: string } | null)?.name ?? "—")}
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
        </MaintenanceCard>
      )}
    </MaintenancePage>
  );
}

export default function RenamePartnerCodePage() {
  return (
    <Suspense
      fallback={<div className="flex min-h-screen items-center justify-center text-sm">Loading…</div>}
    >
      <RenamePartnerCodeView />
    </Suspense>
  );
}
