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
import {
  AGEING_BASIS_LABELS,
  type AgeingBasis,
  type AgeingBucketInput,
  type AgeingBucketSet,
} from "@/features/subledger/types";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

const SEED: AgeingBucketInput[] = [
  { label: "Current", from_days: 0, to_days: 30 },
  { label: "31 - 60", from_days: 31, to_days: 60 },
  { label: "61 - 90", from_days: 61, to_days: 90 },
  { label: "91 - 120", from_days: 91, to_days: 120 },
  { label: "120+", from_days: 121, to_days: null },
];

/**
 * Buckets must start at 0, be contiguous and end open — the same rule the service enforces.
 * The editor owns `from_days` (each row starts one day after the previous row's end) and the
 * last row's `to_days` is always null, so an invalid set cannot be typed in the first place.
 */
function normalise(buckets: AgeingBucketInput[]): AgeingBucketInput[] {
  return buckets.map((bucket, index) => ({
    label: bucket.label,
    from_days: index === 0 ? 0 : (buckets[index - 1].to_days ?? 0) + 1,
    to_days: index === buckets.length - 1 ? null : (bucket.to_days ?? 0),
  }));
}

function describe(bucket: { from_days: number; to_days: number | null }): string {
  return bucket.to_days === null ? `${bucket.from_days}+ days` : `${bucket.from_days}–${bucket.to_days} days`;
}

export default function AgeingBucketSetsPage() {
  const t = useTranslations("maintenance");
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
  const [buckets, setBuckets] = useState<AgeingBucketInput[]>(SEED);

  function startCreate() {
    setEditing(null);
    setCode("");
    setName("");
    setBasis("due_date");
    setIsDefault(false);
    setBuckets(SEED);
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
      normalise(current.map((bucket, i) => (i === index ? { ...bucket, ...patch } : bucket))),
    );
  }

  function addBucket() {
    setBuckets((current) => {
      const last = current[current.length - 1];
      const boundary = last ? last.from_days + 30 : 30;
      const closed = current.map((bucket, i) =>
        i === current.length - 1 ? { ...bucket, to_days: boundary } : bucket,
      );
      return normalise([...closed, { label: `${boundary + 1}+`, from_days: boundary + 1, to_days: null }]);
    });
  }

  function removeBucket(index: number) {
    setBuckets((current) =>
      current.length <= 1 ? current : normalise(current.filter((_, i) => i !== index)),
    );
  }

  async function handleSave() {
    const payload = normalise(buckets);
    try {
      if (editing) {
        await updateSet.mutateAsync({
          setId: editing.id,
          payload: { name, basis, is_default: isDefault, buckets: payload },
        });
        toast.show({ title: "Bucket set updated", tone: "success" });
      } else {
        await createSet.mutateAsync({ code, name, basis, is_default: isDefault, buckets: payload });
        toast.show({ title: "Bucket set created", tone: "success" });
      }
      setOpen(false);
    } catch (err) {
      showApiError(err, "Couldn't save bucket set");
    }
  }

  async function toggleActive(row: AgeingBucketSet) {
    try {
      await updateSet.mutateAsync({ setId: row.id, payload: { is_active: !row.is_active } });
      toast.show({
        title: row.code,
        description: row.is_active ? "Deactivated" : "Activated",
        tone: "success",
      });
    } catch (err) {
      showApiError(err, "Couldn't update bucket set");
    }
  }

  return (
    <MaintenancePage
      title={t("ageingBucketSets")}
      description="Buckets and the ageing basis used by the age analysis — configurable per company"
      actions={
        <Button variant="primary" onClick={startCreate} disabled={!canEdit} className="gap-1.5 text-xs">
          <Plus className="size-3.5" /> New bucket set
        </Button>
      }
    >
      <MaintenanceCard
        icon={<Layers3 className="size-4" />}
        title="Bucket sets"
        actions={
          <label className="flex items-center gap-1.5 text-xs text-[var(--vinea-ink-muted)]">
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(e) => setIncludeInactive(e.target.checked)}
              className="size-3.5"
            />
            Show inactive
          </label>
        }
      >
        {(sets.data ?? []).length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {sets.isLoading ? "Loading…" : "No bucket sets yet."}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-24">Code</TH>
                <TH className="w-40">Name</TH>
                <TH className="w-28">Basis</TH>
                <TH>Buckets</TH>
                <TH className="w-32 text-right">Status</TH>
              </TR>
            </THead>
            <TBody>
              {(sets.data ?? []).map((row) => (
                <TR key={row.id}>
                  <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">{row.code}</TD>
                  <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                    <span className="inline-flex items-center gap-2">
                      {row.name}
                      {row.is_default && <StatusChip tone="info">Default</StatusChip>}
                    </span>
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {AGEING_BASIS_LABELS[row.basis]}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {row.buckets.map((b) => `${b.label} (${describe(b)})`).join(" · ")}
                  </TD>
                  <TD className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => startEdit(row)}
                        aria-label={`Edit ${row.name}`}
                        className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                      >
                        <Edit2 className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleActive(row)}
                        disabled={!canEdit}
                        aria-label={`${row.is_active ? "Deactivate" : "Activate"} ${row.name}`}
                      >
                        <StatusChip tone={row.is_active ? "success" : "neutral"}>
                          {row.is_active ? "Active" : "Inactive"}
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
          title={editing ? `Edit ${editing.name}` : "New ageing bucket set"}
          className="max-w-lg"
        >
          <div className="space-y-3 pt-2">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Code">
                <Input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  disabled={!!editing}
                  className="font-mono"
                  placeholder="STD"
                />
              </Field>
              <Field label="Name">
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Standard 30/60/90/120+"
                />
              </Field>
            </div>

            <Field label="Ageing basis">
              <Select
                options={(Object.keys(AGEING_BASIS_LABELS) as AgeingBasis[]).map((value) => ({
                  value,
                  label: AGEING_BASIS_LABELS[value],
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
              Company default
            </label>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium text-[var(--vinea-ink-muted)]">Buckets</p>
                <Button variant="ghost" onClick={addBucket} className="gap-1 text-xs">
                  <Plus className="size-3" /> Add bucket
                </Button>
              </div>
              <div className="space-y-2">
                {buckets.map((bucket, index) => {
                  const isLast = index === buckets.length - 1;
                  return (
                    <div key={index} className="flex items-end gap-2">
                      <Field label={index === 0 ? "Label" : ""} className="flex-1">
                        <Input
                          value={bucket.label}
                          aria-label={`Bucket ${index + 1} label`}
                          onChange={(e) => patchBucket(index, { label: e.target.value })}
                        />
                      </Field>
                      <Field label={index === 0 ? "From" : ""} className="w-20">
                        <Input
                          value={bucket.from_days}
                          aria-label={`Bucket ${index + 1} from day`}
                          disabled
                          className="text-right font-mono tabular-nums"
                        />
                      </Field>
                      <Field label={index === 0 ? "To" : ""} className="w-20">
                        <Input
                          type="number"
                          min={bucket.from_days}
                          value={isLast ? "" : (bucket.to_days ?? "")}
                          aria-label={`Bucket ${index + 1} to day`}
                          disabled={isLast}
                          placeholder={isLast ? "open" : ""}
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
                        onClick={() => removeBucket(index)}
                        disabled={buckets.length <= 1}
                        aria-label={`Remove bucket ${index + 1}`}
                        className="mb-1 rounded p-1.5 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-danger)] disabled:opacity-40"
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    </div>
                  );
                })}
              </div>
              <p className="text-xs text-[var(--vinea-ink-subtle)]">
                Buckets start at day 0, run contiguously and the last one stays open-ended.
              </p>
            </div>

            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={!code || !name || !canEdit} onClick={handleSave}>
                Save
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </MaintenancePage>
  );
}
