"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { ArrowRight, History, ShieldAlert, Tag } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Field, Input } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import { useItemHistory, useItems, useUpdateItem } from "@/features/inventory/hooks";
import { dotted, formatDate } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

function codeOf(record: Record<string, unknown> | null): string | undefined {
  const value = record?.code;
  return typeof value === "string" ? value : undefined;
}

/**
 * The inventory twin of "Rename account" and "Rename customer code": the code changes, the
 * item id does not, so every stock move, document line and journal line keeps pointing at the
 * same item. The audit trail hangs off `item_id`, which is why the history below survives the
 * rename rather than being orphaned by it.
 *
 * Separated from the Items screen because it needs its own permission — `inv:item_rename`
 * rather than `inv:setup_manage`. The code is what every enquiry and report reads by, so
 * changing it is a different act from editing a description.
 */
export default function RenameItemCodePage() {
  const t = useTranslations("inventory.rename");
  const tc = useTranslations("inventory.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canRename = useHasPermission()("inv:item_rename");

  const [selectedId, setSelectedId] = useState("");
  const [newCode, setNewCode] = useState("");

  const items = useItems({ includeInactive: true });
  const updateItem = useUpdateItem();

  const itemId = selectedId ? Number(selectedId) : null;
  const current = items.data?.find((item) => item.id === itemId) ?? null;
  const history = useItemHistory(itemId);

  useEffect(() => {
    setNewCode(current?.code ?? "");
  }, [current]);

  async function handleRename() {
    if (!current || !newCode || newCode === current.code) return;
    const existing = current.code;
    try {
      const updated = await updateItem.mutateAsync({
        itemId: current.id,
        payload: { code: newCode },
      });
      toast.show({
        title: t("renamed"),
        description: `${existing} → ${updated.code}`,
        tone: "success",
      });
      history.refetch();
    } catch (err) {
      showApiError(err, t("renameFailed"));
    }
  }

  const changed = newCode.trim() !== "" && newCode !== current?.code;

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      backHref="/maintenance/inventory-items"
    >
      <MaintenanceCard icon={<Tag className="size-4" />} title={t("selectTitle")}>
        <Field label={t("item")}>
          <Combobox
            options={(items.data ?? []).map((item) => ({
              value: String(item.id),
              label: dotted(item.code, item.name),
            }))}
            value={selectedId}
            onValueChange={setSelectedId}
            placeholder={t("choose")}
          />
        </Field>

        {current && (
          <div className="space-y-4 rounded-[var(--radius-control)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-sunken)]/50 p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs uppercase tracking-wider text-[var(--vinea-ink-subtle)]">
                  {t("item")}
                </p>
                <p className="text-base font-semibold text-[var(--vinea-ink)]">{current.name}</p>
              </div>
              <StatusChip tone={current.is_active ? "success" : "neutral"}>
                {current.is_active ? tc("active") : tc("inactive")}
              </StatusChip>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label={t("currentCode")}>
                <Input
                  value={current.code}
                  disabled
                  className="bg-[var(--vinea-surface-sunken)] font-mono"
                />
              </Field>
              <Field label={t("newCode")}>
                <Input
                  value={newCode}
                  onChange={(e) => setNewCode(e.target.value)}
                  className="font-mono"
                  placeholder={t("newCodePlaceholder")}
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
                disabled={!changed || !canRename || updateItem.isPending}
                onClick={handleRename}
              >
                {updateItem.isPending ? tc("saving") : t("rename")}
              </Button>
            </div>
          </div>
        )}
      </MaintenanceCard>

      {current && (
        <MaintenanceCard icon={<History className="size-4" />} title={t("historyTitle")}>
          {history.isLoading ? (
            <p className="py-4 text-center text-xs text-[var(--vinea-ink-subtle)]">
              {t("loadingHistory")}
            </p>
          ) : (history.data ?? []).length === 0 ? (
            <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("noHistory")}</p>
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
                  const before = codeOf(record.before);
                  const after = codeOf(record.after);
                  const isRename = before !== undefined && after !== undefined && before !== after;
                  return (
                    <TR key={record.id}>
                      <TD className="text-xs text-[var(--vinea-ink-muted)]">
                        {formatDate(record.at)}
                      </TD>
                      <TD>
                        <StatusChip tone={isRename ? "warning" : "neutral"}>
                          {record.action}
                        </StatusChip>
                      </TD>
                      <TD className="text-xs">
                        {isRename ? (
                          <span className="inline-flex items-center gap-1.5 font-mono">
                            <span className="text-[var(--vinea-ink-subtle)] line-through">
                              {before}
                            </span>
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
