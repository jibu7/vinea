"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Edit2, Layers3, Plus, Trash2 } from "lucide-react";
import { Button } from "@/design/components/button";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import {
  useAgeingBucketSets,
  useCreateAgeingBucketSet,
  useUpdateAgeingBucketSet,
} from "@/features/subledger/hooks";
import { appendBucket, normaliseBuckets, removeBucket } from "@/features/subledger/ageing-buckets";
import {
  AGEING_BASES,
  AGEING_BASIS_MESSAGE,
  type AgeingBasis,
  type AgeingBucketInput,
  type AgeingBucketSet,
} from "@/features/subledger/types";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

/** The 30/60/90/120+ starting point a new set opens on. Only the first bucket carries a
 * word; the rest are numeric ranges, which read the same in any locale. The label is user
 * data from here on — the operator renames it freely — so it is seeded, not translated on
 * every render. */
function seedBuckets(currentLabel: string): AgeingBucketInput[] {
  return [
    { label: currentLabel, from_days: 0, to_days: 30 },
    { label: "31 - 60", from_days: 31, to_days: 60 },
    { label: "61 - 90", from_days: 61, to_days: 90 },
    { label: "91 - 120", from_days: 91, to_days: 120 },
    { label: "120+", from_days: 121, to_days: null },
  ];
}

export default function AgeingBucketSetsPage() {
  const t = useTranslations("arap.bucketSets");
  const tc = useTranslations("arap.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();
  const canEdit = hasPermission("ar:setup_manage") || hasPermission("ap:setup_manage");

  const [includeInactive, setIncludeInactive] = useState(false);
  const sets = useAgeingBucketSets({ includeInactive });
  const createSet = useCreateAgeingBucketSet();
  const updateSet = useUpdateAgeingBucketSet();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<AgeingBucketSet | null>(null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [basis, setBasis] = useState<AgeingBasis>("due_date");
  const [isDefault, setIsDefault] = useState(false);
  const [buckets, setBuckets] = useState<AgeingBucketInput[]>(() => seedBuckets("Current"));

  function describeRange(bucket: { from_days: number; to_days: number | null }): string {
    return bucket.to_days === null
      ? t("rangeOpen", { from: bucket.from_days })
      : t("rangeClosed", { from: bucket.from_days, to: bucket.to_days });
  }

  function startCreate() {
    setEditing(null);
    setCode("");
    setName("");
    setBasis("due_date");
    setIsDefault(false);
    setBuckets(seedBuckets(t("seedCurrent")));
    setOpen(true);
  }

  function startEdit(row: AgeingBucketSet) {
    setEditing(row);
    setCode(row.code);
    setName(row.name);
    setBasis(row.basis);
    setIsDefault(row.is_default);
    setBuckets(
      row.buckets.map((b) => ({ label: b.label, from_days: b.from_days, to_days: b.to_days })),
    );
    setOpen(true);
  }

  function patchBucket(index: number, patch: Partial<AgeingBucketInput>) {
    setBuckets((current) =>
      normaliseBuckets(current.map((bucket, i) => (i === index ? { ...bucket, ...patch } : bucket))),
    );
  }

  function addBucket() {
    setBuckets((current) => appendBucket(current, (boundary) => `${boundary}+`));
  }

  function dropBucket(index: number) {
    setBuckets((current) => removeBucket(current, index));
  }

  async function handleSave() {
    const payload = normaliseBuckets(buckets);
    try {
      if (editing) {
        await updateSet.mutateAsync({
          setId: editing.id,
          payload: { name, basis, is_default: isDefault, buckets: payload },
        });
        toast.show({ title: t("updated"), tone: "success" });
      } else {
        await createSet.mutateAsync({ code, name, basis, is_default: isDefault, buckets: payload });
        toast.show({ title: t("created"), tone: "success" });
      }
      setOpen(false);
    } catch (err) {
      showApiError(err, t("saveFailed"));
    }
  }

  async function toggleActive(row: AgeingBucketSet) {
    try {
      await updateSet.mutateAsync({ setId: row.id, payload: { is_active: !row.is_active } });
      toast.show({
        title: row.code,
        description: row.is_active ? tc("deactivated") : tc("activated"),
        tone: "success",
      });
    } catch (err) {
      showApiError(err, t("updateFailed"));
    }
  }

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      actions={
        <Button variant="primary" onClick={startCreate} disabled={!canEdit} className="gap-1.5 text-xs">
          <Plus className="size-3.5" /> {t("new")}
        </Button>
      }
    >
      <MaintenanceCard
        icon={<Layers3 className="size-4" />}
        title={t("cardTitle")}
        actions={
          <label className="flex items-center gap-1.5 text-xs text-[var(--vinea-ink-muted)]">
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(e) => setIncludeInactive(e.target.checked)}
              className="size-3.5"
            />
            {tc("showInactive")}
          </label>
        }
      >
        {(sets.data ?? []).length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {sets.isLoading ? tc("loading") : t("empty")}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-24">{tc("code")}</TH>
                <TH className="w-40">{tc("name")}</TH>
                <TH className="w-28">{t("basis")}</TH>
                <TH>{t("buckets")}</TH>
                <TH className="w-32 text-right">{tc("status")}</TH>
              </TR>
            </THead>
            <TBody>
              {(sets.data ?? []).map((row) => (
                <TR key={row.id}>
                  <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">{row.code}</TD>
                  <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                    <span className="inline-flex items-center gap-2">
                      {row.name}
                      {row.is_default && <StatusChip tone="info">{tc("default")}</StatusChip>}
                    </span>
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {t(AGEING_BASIS_MESSAGE[row.basis])}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {row.buckets
                      .map((b) => t("bucketEntry", { label: b.label, range: describeRange(b) }))
                      .join(" · ")}
                  </TD>
                  <TD className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => startEdit(row)}
                        aria-label={tc("editLabel", { name: row.name })}
                        className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                      >
                        <Edit2 className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleActive(row)}
                        disabled={!canEdit}
                        aria-label={tc(row.is_active ? "deactivateLabel" : "activateLabel", {
                          name: row.name,
                        })}
                      >
                        <StatusChip tone={row.is_active ? "success" : "neutral"}>
                          {row.is_active ? tc("active") : tc("inactive")}
                        </StatusChip>
                      </button>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </MaintenanceCard>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          title={editing ? t("editTitle", { name: editing.name }) : t("newTitle")}
          className="max-w-lg"
        >
          <div className="space-y-3 pt-2">
            <div className="grid grid-cols-2 gap-3">
              <Field label={tc("code")}>
                <Input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  disabled={!!editing}
                  className="font-mono"
                  placeholder={t("codePlaceholder")}
                />
              </Field>
              <Field label={tc("name")}>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t("namePlaceholder")}
                />
              </Field>
            </div>

            <Field label={t("basisLabel")}>
              <Select
                options={AGEING_BASES.map((value) => ({
                  value,
                  label: t(AGEING_BASIS_MESSAGE[value]),
                }))}
                value={basis}
                onValueChange={(v) => setBasis(v as AgeingBasis)}
              />
            </Field>

            <label className="flex items-center gap-2 text-xs text-[var(--vinea-ink-muted)]">
              <input
                type="checkbox"
                checked={isDefault}
                onChange={(e) => setIsDefault(e.target.checked)}
                className="size-3.5"
              />
              {t("isDefault")}
            </label>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium text-[var(--vinea-ink-muted)]">{t("buckets")}</p>
                <Button variant="ghost" onClick={addBucket} className="gap-1 text-xs">
                  <Plus className="size-3" /> {t("addBucket")}
                </Button>
              </div>
              <div className="space-y-2">
                {buckets.map((bucket, index) => {
                  const isLast = index === buckets.length - 1;
                  return (
                    <div key={index} className="flex items-end gap-2">
                      <Field label={index === 0 ? t("bucketLabel") : ""} className="flex-1">
                        <Input
                          value={bucket.label}
                          aria-label={t("bucketLabelAria", { index: index + 1 })}
                          onChange={(e) => patchBucket(index, { label: e.target.value })}
                        />
                      </Field>
                      {/* Derived, never entered: each bucket starts the day after the
                          previous one ends (see ageing-buckets.ts). Rendered as text rather
                          than a disabled input, which reads as "editable, just not now". */}
                      <div className="w-20">
                        {index === 0 && (
                          <p className="mb-1.5 text-xs font-medium text-[var(--vinea-ink-muted)]">
                            {t("from")}
                          </p>
                        )}
                        <p
                          data-testid={`bucket-from-${index}`}
                          aria-label={t("bucketFromAria", { index: index + 1 })}
                          className="flex h-8 items-center justify-end rounded-[var(--radius-control)] bg-[var(--vinea-surface-sunken)] px-2 font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]"
                        >
                          {bucket.from_days}
                        </p>
                      </div>
                      <Field label={index === 0 ? t("to") : ""} className="w-20">
                        <Input
                          type="number"
                          min={bucket.from_days}
                          value={isLast ? "" : (bucket.to_days ?? "")}
                          aria-label={t("bucketToAria", { index: index + 1 })}
                          disabled={isLast}
                          placeholder={isLast ? t("openEnded") : ""}
                          onChange={(e) =>
                            patchBucket(index, {
                              to_days: e.target.value ? Number(e.target.value) : null,
                            })
                          }
                          className="text-right font-mono tabular-nums"
                        />
                      </Field>
                      <button
                        type="button"
                        onClick={() => dropBucket(index)}
                        disabled={buckets.length <= 1}
                        aria-label={t("removeBucketAria", { index: index + 1 })}
                        className="mb-1 rounded p-1.5 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-danger)] disabled:opacity-40"
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    </div>
                  );
                })}
              </div>
              <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("contiguityNote")}</p>
            </div>

            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button variant="primary" disabled={!code || !name || !canEdit} onClick={handleSave}>
                {tc("save")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </MaintenancePage>
  );
}
