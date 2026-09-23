"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Edit2, Plus } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError } from "@/features/auth/hooks";
import { useAccounts, useTaxCodes } from "@/features/gl/hooks";
import { byId } from "@/features/gl/lookups";
import { dotted } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useBankRules, useCreateBankRule, useUpdateBankRule } from "./hooks";
import type { BankAccount, BankRule } from "./types";

interface RuleForm {
  pattern: string;
  glAccountId: string;
  taxCodeId: string;
  description: string;
  priority: string;
}

const BLANK: RuleForm = { pattern: "", glAccountId: "", taxCodeId: "", description: "", priority: "100" };

/**
 * The **rules** tab: what makes the *Post from line* drawer open prefilled for a line that
 * comes every month — the account fee to `6700`, the interest to `4300`.
 *
 * **A rule never posts** (decision 4). It fills a drawer in; a person reads it and presses Post.
 * So the list says what each rule would *suggest*, in priority order — the lowest number is
 * tried first — and nothing on this tab moves money.
 *
 * The account picker offers **postable, active, non-control** accounts only. The engine is what
 * actually stops a rule aiming at a control account (`control_account_direct_posting`, at the
 * moment somebody presses Post), and a picker that offered one would be offering a suggestion
 * that could only ever be refused.
 */
export function BankRulesPanel({ account, canManage }: { account: BankAccount; canManage: boolean }) {
  const t = useTranslations("banking.rules");
  const tc = useTranslations("banking.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const rules = useBankRules(account.id);
  const accounts = useAccounts();
  const taxCodes = useTaxCodes();
  const createRule = useCreateBankRule();
  const updateRule = useUpdateBankRule();

  const [editing, setEditing] = useState<BankRule | "new" | null>(null);
  const [form, setForm] = useState<RuleForm>(BLANK);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});

  const accountById = byId(accounts.data);
  const taxById = byId(taxCodes.data);
  const usable = useMemo(
    () => (accounts.data ?? []).filter((a) => a.is_postable && a.is_active && !a.is_control),
    [accounts.data],
  );
  const rows = [...(rules.data ?? [])].sort((a, b) => a.priority - b.priority || a.id - b.id);

  function open(rule: BankRule | "new") {
    setFieldErrors({});
    setEditing(rule);
    setForm(
      rule === "new"
        ? BLANK
        : {
            pattern: rule.pattern,
            glAccountId: rule.gl_account_id ? String(rule.gl_account_id) : "",
            taxCodeId: rule.tax_code_id ? String(rule.tax_code_id) : "",
            description: rule.description ?? "",
            priority: String(rule.priority),
          },
    );
  }

  function payload(isActive?: boolean) {
    return {
      pattern: form.pattern.trim(),
      gl_account_id: form.glAccountId ? Number(form.glAccountId) : null,
      tax_code_id: form.taxCodeId ? Number(form.taxCodeId) : null,
      description: form.description.trim() || null,
      priority: Number(form.priority || 100),
      ...(isActive === undefined ? {} : { is_active: isActive }),
    };
  }

  async function handleSave() {
    setFieldErrors({});
    try {
      if (editing === "new") {
        await createRule.mutateAsync({ bankAccountId: account.id, payload: payload() });
        toast.show({ title: t("created"), description: form.pattern, tone: "success" });
      } else if (editing) {
        await updateRule.mutateAsync({ ruleId: editing.id, payload: payload() });
        toast.show({ title: t("saved"), description: form.pattern, tone: "success" });
      }
      setEditing(null);
    } catch (err) {
      if (isApiError(err)) setFieldErrors(err.fieldErrors);
      showApiError(err, t("saveFailed"));
    }
  }

  async function toggleActive(rule: BankRule) {
    try {
      await updateRule.mutateAsync({
        ruleId: rule.id,
        payload: {
          pattern: rule.pattern,
          gl_account_id: rule.gl_account_id,
          tax_code_id: rule.tax_code_id,
          description: rule.description,
          priority: rule.priority,
          is_active: !rule.is_active,
        },
      });
    } catch (err) {
      showApiError(err, t("saveFailed"));
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs text-[var(--vinea-ink-muted)]">{t("note")}</p>
        <Button
          variant="secondary"
          onClick={() => open("new")}
          disabled={!canManage}
          className="shrink-0 gap-1.5 text-xs"
        >
          <Plus className="size-3.5" /> {t("newRule")}
        </Button>
      </div>

      {rows.length === 0 ? (
        <QueryState query={rules} isEmpty empty={t("empty")} testId="rules" />
      ) : (
        <Table>
          <THead>
            <TR>
              <TH className="w-16 text-right">{t("priority")}</TH>
              <TH>{t("pattern")}</TH>
              <TH>{t("suggests")}</TH>
              <TH className="w-24 text-right">{tc("status")}</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((rule) => {
              const gl = rule.gl_account_id ? accountById.get(rule.gl_account_id) : undefined;
              const tax = rule.tax_code_id ? taxById.get(rule.tax_code_id) : undefined;
              return (
                <TR key={rule.id}>
                  <TD className="text-right font-mono text-xs">{rule.priority}</TD>
                  <TD className="font-mono text-xs font-semibold text-[var(--vinea-ink)]">
                    {rule.pattern}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink)]">
                    {gl ? dotted(gl.code, gl.name) : tc("emptyValue")}
                    {tax ? (
                      <span className="ml-2 text-[var(--vinea-ink-subtle)]">{tax.code}</span>
                    ) : null}
                    {rule.description ? (
                      <p className="text-[11px] text-[var(--vinea-ink-subtle)]">{rule.description}</p>
                    ) : null}
                  </TD>
                  <TD className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => open(rule)}
                        disabled={!canManage}
                        aria-label={t("editLabel", { pattern: rule.pattern })}
                        className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                      >
                        <Edit2 className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleActive(rule)}
                        disabled={!canManage}
                        aria-label={t(rule.is_active ? "deactivateLabel" : "activateLabel", {
                          pattern: rule.pattern,
                        })}
                      >
                        <StatusChip tone={rule.is_active ? "success" : "neutral"}>
                          {rule.is_active ? tc("active") : tc("inactive")}
                        </StatusChip>
                      </button>
                    </div>
                  </TD>
                </TR>
              );
            })}
          </TBody>
        </Table>
      )}

      <Dialog open={editing !== null} onOpenChange={(isOpen) => !isOpen && setEditing(null)}>
        <DialogContent title={editing === "new" ? t("newRule") : t("editRule")}>
          <div className="space-y-3 pt-2">
            <div className="grid grid-cols-3 gap-3">
              <Field label={t("pattern")} error={fieldErrors.pattern?.[0]} className="col-span-2">
                <Input
                  value={form.pattern}
                  onChange={(e) => setForm({ ...form, pattern: e.target.value })}
                  placeholder={t("patternPlaceholder")}
                  className="font-mono"
                />
              </Field>
              <Field label={t("priority")} error={fieldErrors.priority?.[0]}>
                <Input
                  type="number"
                  min={0}
                  value={form.priority}
                  onChange={(e) => setForm({ ...form, priority: e.target.value })}
                />
              </Field>
            </div>
            <Field label={t("counterAccount")} error={fieldErrors.gl_account_id?.[0]}>
              <Combobox
                options={usable.map((a) => ({ value: String(a.id), label: dotted(a.code, a.name) }))}
                value={form.glAccountId}
                onValueChange={(value) => setForm({ ...form, glAccountId: value })}
                placeholder={t("chooseAccount")}
              />
            </Field>
            <Field label={t("taxCode")} error={fieldErrors.tax_code_id?.[0]}>
              <Combobox
                options={[
                  { value: "", label: tc("emptyValue") },
                  ...(taxCodes.data ?? [])
                    .filter((code) => code.is_active)
                    .map((code) => ({ value: String(code.id), label: dotted(code.code, code.name) })),
                ]}
                value={form.taxCodeId}
                onValueChange={(value) => setForm({ ...form, taxCodeId: value })}
                placeholder={t("noTax")}
              />
            </Field>
            <Field label={t("lineDescription")} error={fieldErrors.description?.[0]}>
              <Input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </Field>
            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setEditing(null)}>
                {tc("cancel")}
              </Button>
              <Button
                variant="primary"
                disabled={
                  !form.pattern.trim() || !canManage || createRule.isPending || updateRule.isPending
                }
                onClick={handleSave}
              >
                {tc("save")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
