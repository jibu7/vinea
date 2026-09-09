"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Edit2, Plus, Star } from "lucide-react";
import { Button } from "@/design/components/button";
import { Field, Input } from "@/design/components/input";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useCreateContact, usePartnerContacts, useUpdateContact } from "./hooks";
import type { ContactPayload, PartnerContact, PartnerRole } from "./types";

const BLANK: ContactPayload = { name: "", role: "", email: "", phone: "", notes: "", is_primary: false };

/** The thin CRM from §B.2: names, roles, contact details and notes — nothing more. */
export function PartnerContactsPanel({
  role,
  partnerId,
  canEdit,
}: {
  role: PartnerRole;
  partnerId: number;
  canEdit: boolean;
}) {
  const t = useTranslations("arap.contacts");
  const tc = useTranslations("arap.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const contacts = usePartnerContacts(role, partnerId);
  const createContact = useCreateContact(role);
  const updateContact = useUpdateContact(role);

  const [editing, setEditing] = useState<PartnerContact | null>(null);
  const [form, setForm] = useState<ContactPayload>(BLANK);
  const [formOpen, setFormOpen] = useState(false);

  function startCreate() {
    setEditing(null);
    setForm(BLANK);
    setFormOpen(true);
  }

  function startEdit(contact: PartnerContact) {
    setEditing(contact);
    setForm({
      name: contact.name,
      role: contact.role ?? "",
      email: contact.email ?? "",
      phone: contact.phone ?? "",
      notes: contact.notes ?? "",
      is_primary: contact.is_primary,
    });
    setFormOpen(true);
  }

  async function handleSave() {
    try {
      if (editing) {
        await updateContact.mutateAsync({ contactId: editing.id, payload: form });
      } else {
        await createContact.mutateAsync({ partnerId, payload: form });
      }
      toast.show({ title: editing ? t("updated") : t("added"), tone: "success" });
      setFormOpen(false);
    } catch (err) {
      showApiError(err, t("saveFailed"));
    }
  }

  async function toggleActive(contact: PartnerContact) {
    try {
      await updateContact.mutateAsync({
        contactId: contact.id,
        payload: { name: contact.name, is_active: !contact.is_active },
      });
    } catch (err) {
      showApiError(err, t("updateFailed"));
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button variant="secondary" onClick={startCreate} disabled={!canEdit} className="gap-1.5 text-xs">
          <Plus className="size-3.5" /> {t("add")}
        </Button>
      </div>

      {(contacts.data ?? []).length === 0 ? (
        <p className="py-6 text-center text-xs text-[var(--vinea-ink-subtle)]">{t("empty")}</p>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>{tc("name")}</TH>
              <TH className="w-28">{t("role")}</TH>
              <TH>{tc("email")}</TH>
              <TH className="w-28">{tc("phone")}</TH>
              <TH className="w-28 text-right">{tc("status")}</TH>
            </TR>
          </THead>
          <TBody>
            {(contacts.data ?? []).map((contact) => (
              <TR key={contact.id}>
                <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                  <span className="inline-flex items-center gap-1.5">
                    {contact.is_primary && (
                      <Star
                        className="size-3 fill-[var(--vinea-brand)] text-[var(--vinea-brand)]"
                        aria-label={t("primaryLabel")}
                      />
                    )}
                    {contact.name}
                  </span>
                </TD>
                <TD className="text-xs text-[var(--vinea-ink-muted)]">
                  {contact.role ?? tc("emptyValue")}
                </TD>
                <TD className="text-xs text-[var(--vinea-ink-muted)]">
                  {contact.email ?? tc("emptyValue")}
                </TD>
                <TD className="text-xs text-[var(--vinea-ink-muted)]">
                  {contact.phone ?? tc("emptyValue")}
                </TD>
                <TD className="text-right">
                  <div className="flex items-center justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => startEdit(contact)}
                      aria-label={tc("editLabel", { name: contact.name })}
                      className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                    >
                      <Edit2 className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => toggleActive(contact)}
                      disabled={!canEdit}
                      aria-label={tc(contact.is_active ? "deactivateLabel" : "activateLabel", {
                        name: contact.name,
                      })}
                    >
                      <StatusChip tone={contact.is_active ? "success" : "neutral"}>
                        {contact.is_active ? tc("active") : tc("inactive")}
                      </StatusChip>
                    </button>
                  </div>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}

      {formOpen && (
        <div className="space-y-3 rounded-[var(--radius-control)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-sunken)]/50 p-4">
          <p className="text-xs font-semibold text-[var(--vinea-ink)]">
            {editing ? t("editContact", { name: editing.name }) : t("newContact")}
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label={tc("name")}>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder={t("namePlaceholder")}
              />
            </Field>
            <Field label={t("role")}>
              <Input
                value={form.role ?? ""}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
                placeholder={t("rolePlaceholder")}
              />
            </Field>
            <Field label={tc("email")}>
              <Input
                type="email"
                value={form.email ?? ""}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </Field>
            <Field label={tc("phone")}>
              <Input
                value={form.phone ?? ""}
                onChange={(e) => setForm({ ...form, phone: e.target.value })}
              />
            </Field>
          </div>
          <Field label={tc("notes")}>
            <Input
              value={form.notes ?? ""}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
            />
          </Field>
          <label className="flex items-center gap-2 text-xs text-[var(--vinea-ink-muted)]">
            <input
              type="checkbox"
              checked={form.is_primary ?? false}
              onChange={(e) => setForm({ ...form, is_primary: e.target.checked })}
              className="size-3.5"
            />
            {t("isPrimary")}
          </label>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setFormOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button variant="primary" disabled={!form.name || !canEdit} onClick={handleSave}>
              {t("saveContact")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
