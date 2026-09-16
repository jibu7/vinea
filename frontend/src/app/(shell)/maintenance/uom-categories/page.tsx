"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Edit2, Plus, Ruler } from "lucide-react";
import { Button } from "@/design/components/button";
import { QueryState } from "@/design/components/query-state";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import {
  useCreateUom,
  useCreateUomCategory,
  useUomCategories,
  useUpdateUom,
  useUpdateUomCategory,
} from "@/features/inventory/hooks";
import type { Uom, UomCategoryWithUnits } from "@/features/inventory/types";
import { dotted, trimDecimalString } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

/**
 * Unit-of-measure categories and the units inside them (P5 decision 8).
 *
 * A category is a family of units that convert to one another, and the conversion is the
 * whole point: `factor_to_base` is how a case of six becomes six on a stock move. The base
 * unit is created with its category and is always 1 — it has no factor to edit, because it is
 * what every other factor is measured against.
 */
export default function UomCategoriesPage() {
  const t = useTranslations("inventory.uomCategories");
  const tc = useTranslations("inventory.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canEdit = useHasPermission()("inv:setup_manage");

  const [includeInactive, setIncludeInactive] = useState(false);
  const categories = useUomCategories({ includeInactive });
  const createCategory = useCreateUomCategory();
  const updateCategory = useUpdateUomCategory();
  const createUom = useCreateUom();
  const updateUom = useUpdateUom();

  const [categoryOpen, setCategoryOpen] = useState(false);
  const [editingCategory, setEditingCategory] = useState<UomCategoryWithUnits | null>(null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [baseCode, setBaseCode] = useState("");
  const [baseName, setBaseName] = useState("");

  const [uomOpen, setUomOpen] = useState(false);
  const [uomCategory, setUomCategory] = useState<UomCategoryWithUnits | null>(null);
  const [editingUom, setEditingUom] = useState<Uom | null>(null);
  const [uomCode, setUomCode] = useState("");
  const [uomName, setUomName] = useState("");
  const [factor, setFactor] = useState("");
  const [decimals, setDecimals] = useState("0");

  function startCreateCategory() {
    setEditingCategory(null);
    setCode("");
    setName("");
    setBaseCode("");
    setBaseName("");
    setCategoryOpen(true);
  }

  function startEditCategory(category: UomCategoryWithUnits) {
    setEditingCategory(category);
    setCode(category.code);
    setName(category.name);
    setCategoryOpen(true);
  }

  function startCreateUom(category: UomCategoryWithUnits) {
    setUomCategory(category);
    setEditingUom(null);
    setUomCode("");
    setUomName("");
    setFactor("");
    setDecimals("0");
    setUomOpen(true);
  }

  function startEditUom(category: UomCategoryWithUnits, uom: Uom) {
    setUomCategory(category);
    setEditingUom(uom);
    setUomCode(uom.code);
    setUomName(uom.name);
    setFactor(trimDecimalString(uom.factor_to_base));
    setDecimals(String(uom.decimal_places));
    setUomOpen(true);
  }

  async function saveCategory() {
    try {
      if (editingCategory) {
        await updateCategory.mutateAsync({ categoryId: editingCategory.id, payload: { name } });
        toast.show({ title: t("updated"), tone: "success" });
      } else {
        await createCategory.mutateAsync({
          code,
          name,
          base_uom_code: baseCode,
          base_uom_name: baseName,
        });
        toast.show({ title: t("created"), tone: "success" });
      }
      setCategoryOpen(false);
    } catch (err) {
      showApiError(err, t("saveFailed"));
    }
  }

  async function saveUom() {
    if (!uomCategory) return;
    try {
      if (editingUom) {
        await updateUom.mutateAsync({
          uomId: editingUom.id,
          payload: {
            name: uomName,
            // The base unit's factor is fixed at 1 and the service refuses to move it; send
            // only what a non-base unit can actually change.
            ...(editingUom.is_base ? {} : { factor_to_base: factor }),
            decimal_places: Number(decimals),
          },
        });
        toast.show({ title: t("uomUpdated"), tone: "success" });
      } else {
        await createUom.mutateAsync({
          category_id: uomCategory.id,
          code: uomCode,
          name: uomName,
          factor_to_base: factor,
          decimal_places: Number(decimals),
        });
        toast.show({ title: t("uomCreated"), tone: "success" });
      }
      setUomOpen(false);
    } catch (err) {
      showApiError(err, t("uomSaveFailed"));
    }
  }

  async function toggleCategory(category: UomCategoryWithUnits) {
    try {
      await updateCategory.mutateAsync({
        categoryId: category.id,
        payload: { is_active: !category.is_active },
      });
      toast.show({
        title: category.code,
        description: category.is_active ? tc("deactivated") : tc("activated"),
        tone: "success",
      });
    } catch (err) {
      showApiError(err, t("updateFailed"));
    }
  }

  const rows = categories.data ?? [];

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      actions={
        <Button
          variant="primary"
          onClick={startCreateCategory}
          disabled={!canEdit}
          className="gap-1.5 text-xs"
        >
          <Plus className="size-3.5" /> {t("new")}
        </Button>
      }
    >
      {rows.length === 0 ? (
        <MaintenanceCard icon={<Ruler className="size-4" />} title={t("title")}>
          <QueryState query={categories} isEmpty empty={t("empty")} testId="query" />
        </MaintenanceCard>
      ) : (
        rows.map((category) => (
          <MaintenanceCard
            key={category.id}
            icon={<Ruler className="size-4" />}
            title={dotted(category.code, category.name)}
            actions={
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => startEditCategory(category)}
                  aria-label={tc("editLabel", { name: category.name })}
                  className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                >
                  <Edit2 className="size-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => toggleCategory(category)}
                  disabled={!canEdit}
                  aria-label={tc(category.is_active ? "deactivateLabel" : "activateLabel", {
                    name: category.name,
                  })}
                >
                  <StatusChip tone={category.is_active ? "success" : "neutral"}>
                    {category.is_active ? tc("active") : tc("inactive")}
                  </StatusChip>
                </button>
                <Button
                  variant="ghost"
                  onClick={() => startCreateUom(category)}
                  disabled={!canEdit}
                  className="gap-1.5 text-xs"
                >
                  <Plus className="size-3.5" /> {t("newUom")}
                </Button>
              </div>
            }
          >
            <Table>
              <THead>
                <TR>
                  <TH className="w-28">{tc("code")}</TH>
                  <TH>{tc("name")}</TH>
                  <TH className="w-40 text-right">{t("factorToBase")}</TH>
                  <TH className="w-24 text-right">{t("decimalPlaces")}</TH>
                  <TH className="w-32 text-right">{tc("status")}</TH>
                </TR>
              </THead>
              <TBody>
                {category.uoms.map((uom) => (
                  <TR key={uom.id}>
                    <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                      {uom.code}
                    </TD>
                    <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                      <span className="inline-flex items-center gap-2">
                        {uom.name}
                        {uom.is_base ? <StatusChip tone="info">{t("isBase")}</StatusChip> : null}
                      </span>
                    </TD>
                    <TD className="text-right font-mono text-xs text-[var(--vinea-ink)]">
                      {trimDecimalString(uom.factor_to_base)}
                    </TD>
                    <TD className="text-right font-mono text-xs text-[var(--vinea-ink-muted)]">
                      {uom.decimal_places}
                    </TD>
                    <TD className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => startEditUom(category, uom)}
                          aria-label={tc("editLabel", { name: uom.name })}
                          className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                        >
                          <Edit2 className="size-3.5" />
                        </button>
                        <StatusChip tone={uom.is_active ? "success" : "neutral"}>
                          {uom.is_active ? tc("active") : tc("inactive")}
                        </StatusChip>
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </MaintenanceCard>
        ))
      )}
      <p className="px-1 text-xs text-[var(--vinea-ink-subtle)]">{t("baseNote")}</p>

      <Dialog open={categoryOpen} onOpenChange={setCategoryOpen}>
        <DialogContent
          title={editingCategory ? t("editTitle", { name: editingCategory.name }) : t("newTitle")}
        >
          <div className="space-y-3 pt-2">
            <Field label={tc("code")}>
              <Input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                disabled={!!editingCategory}
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
            {editingCategory ? null : (
              <div className="grid grid-cols-2 gap-3">
                <Field label={t("baseUomCode")}>
                  <Input
                    value={baseCode}
                    onChange={(e) => setBaseCode(e.target.value)}
                    className="font-mono"
                    placeholder={t("baseUomCodePlaceholder")}
                  />
                </Field>
                <Field label={t("baseUomName")}>
                  <Input
                    value={baseName}
                    onChange={(e) => setBaseName(e.target.value)}
                    placeholder={t("baseUomNamePlaceholder")}
                  />
                </Field>
              </div>
            )}
            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setCategoryOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button
                variant="primary"
                disabled={
                  !code || !name || !canEdit || (!editingCategory && (!baseCode || !baseName))
                }
                onClick={saveCategory}
              >
                {tc("save")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={uomOpen} onOpenChange={setUomOpen}>
        <DialogContent
          title={
            editingUom
              ? t("editUomTitle", { name: editingUom.name })
              : t("newUomTitle", { category: uomCategory?.name ?? "" })
          }
        >
          <div className="space-y-3 pt-2">
            <Field label={tc("code")}>
              <Input
                value={uomCode}
                onChange={(e) => setUomCode(e.target.value)}
                disabled={!!editingUom}
                className="font-mono"
                placeholder={t("uomCodePlaceholder")}
              />
            </Field>
            <Field label={tc("name")}>
              <Input
                value={uomName}
                onChange={(e) => setUomName(e.target.value)}
                placeholder={t("uomNamePlaceholder")}
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("factorToBase")}>
                <Input
                  value={factor}
                  onChange={(e) => setFactor(e.target.value)}
                  disabled={!!editingUom?.is_base}
                  inputMode="decimal"
                  className="font-mono"
                  placeholder={t("factorPlaceholder")}
                />
              </Field>
              <Field label={t("decimalPlaces")}>
                <Input
                  value={decimals}
                  onChange={(e) => setDecimals(e.target.value)}
                  inputMode="numeric"
                  className="font-mono"
                />
              </Field>
            </div>
            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setUomOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button
                variant="primary"
                disabled={!uomCode || !uomName || !canEdit || (!editingUom && !factor)}
                onClick={saveUom}
              >
                {tc("save")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </MaintenancePage>
  );
}
