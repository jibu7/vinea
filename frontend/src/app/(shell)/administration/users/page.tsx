"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft, Mail, Plus, Shield, ShieldAlert, UserCheck, UserX } from "lucide-react";
import { Button } from "@/design/components/button";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { StatusChip } from "@/design/components/status-chip";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useToast } from "@/design/components/toast";
import {
  useActivateMember,
  useCompanyMembers,
  useCompanyRoles,
  useDeactivateMember,
  useInviteMember,
  useUpdateMemberRoles,
} from "@/features/gl/hooks";
import type { CompanyMember } from "@/features/gl/types";
import { formatDate } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

export default function UsersAndMembershipsPage() {
  const t = useTranslations("maintenance");
  const tc = useTranslations("common");
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const membersQuery = useCompanyMembers();
  const rolesQuery = useCompanyRoles();
  const inviteMember = useInviteMember();
  const updateMemberRoles = useUpdateMemberRoles();
  const deactivateMember = useDeactivateMember();
  const activateMember = useActivateMember();

  // Invite modal state
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [selectedRoleIds, setSelectedRoleIds] = useState<number[]>([]);

  // Role Assignment modal state
  const [roleModalMember, setRoleModalMember] = useState<CompanyMember | null>(null);
  const [memberRoleIds, setMemberRoleIds] = useState<number[]>([]);

  function startInvite() {
    setInviteEmail("");
    // Default to Clerk or first non-system role
    const clerk = rolesQuery.data?.find((r) => r.name === "Clerk");
    setSelectedRoleIds(clerk ? [clerk.id] : []);
    setInviteOpen(true);
  }

  function startEditRoles(m: CompanyMember) {
    setRoleModalMember(m);
    setMemberRoleIds(m.roles.map((r) => r.id));
  }

  async function handleSendInvite() {
    if (!inviteEmail.trim()) return;
    try {
      await inviteMember.mutateAsync({
        email: inviteEmail.trim(),
        role_ids: selectedRoleIds,
      });
      setInviteOpen(false);
      setInviteEmail("");
      toast.show({ title: t("invitationSent"), description: inviteEmail, tone: "success" });
    } catch (err) {
      showApiError(err, t("invitationSendFailed"));
    }
  }

  async function handleSaveRoles() {
    if (!roleModalMember) return;
    try {
      await updateMemberRoles.mutateAsync({
        membershipId: roleModalMember.id,
        roleIds: memberRoleIds,
      });
      setRoleModalMember(null);
      toast.show({ title: t("rolesUpdated"), tone: "success" });
    } catch (err) {
      showApiError(err, t("rolesUpdateFailed"));
    }
  }

  async function handleToggleStatus(m: CompanyMember) {
    if (m.is_owner) {
      toast.show({ title: t("cannotDeactivateOwner"), tone: "danger" });
      return;
    }
    try {
      if (m.status === "active") {
        await deactivateMember.mutateAsync(m.id);
        toast.show({ title: t("memberDeactivated"), tone: "neutral" });
      } else {
        await activateMember.mutateAsync(m.id);
        toast.show({ title: t("memberActivated"), tone: "success" });
      }
    } catch (err) {
      showApiError(err, t("statusChangeFailed"));
    }
  }

  return (
    <div className="flex min-h-screen flex-col" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]" aria-label={tc("back")}>
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{t("usersAndMemberships")}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">{t("usersSubtitle")}</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="primary" onClick={startInvite} className="gap-1.5 text-xs">
            <Plus className="size-3.5" /> {t("inviteUser")}
          </Button>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-5xl space-y-6">
          <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 space-y-4">
            <div className="flex items-center gap-2">
              <Shield className="size-4 text-[var(--vinea-brand)]" />
              <h2 className="font-display text-base font-semibold">{t("teamMembersAndInvitations")}</h2>
            </div>

            <Table>
              <THead>
                <TR>
                  <TH>{t("memberEmail")}</TH>
                  <TH className="w-48">{t("roles")}</TH>
                  <TH className="w-32">{t("status")}</TH>
                  <TH className="w-36">{t("joinedInvited")}</TH>
                  <TH className="w-36 text-right">{t("actions")}</TH>
                </TR>
              </THead>
              <TBody>
                {(membersQuery.data ?? []).map((m) => (
                  <TR key={m.id}>
                    <TD>
                      <p className="font-medium text-xs text-[var(--vinea-ink)]">
                        {m.full_name ?? m.email}
                        {m.is_owner && (
                          <span className="ml-2 rounded bg-[var(--vinea-brand-soft)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--vinea-brand)]">
                            {t("owner")}
                          </span>
                        )}
                      </p>
                      {m.full_name && <p className="text-[11px] text-[var(--vinea-ink-muted)]">{m.email}</p>}
                    </TD>
                    <TD>
                      <div className="flex flex-wrap gap-1">
                        {m.roles.map((r) => (
                          <span
                            key={r.id}
                            className="rounded-full bg-[var(--vinea-surface-sunken)] px-2 py-0.5 text-[10px] font-medium text-[var(--vinea-ink-muted)]"
                          >
                            {r.name}
                          </span>
                        ))}
                        {m.roles.length === 0 && <span className="text-xs text-[var(--vinea-ink-subtle)]">{t("emptyValue")}</span>}
                      </div>
                    </TD>
                    <TD>
                      <StatusChip
                        tone={
                          m.status === "active"
                            ? "success"
                            : m.status === "pending"
                              ? "warning"
                              : "neutral"
                        }
                      >
                        {m.status === "active" ? t("active") : m.status === "pending" ? t("pending") : t("suspended")}
                      </StatusChip>
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {m.accepted_at
                        ? formatDate(m.accepted_at)
                        : m.invited_at
                          ? t("invitedOn", { date: formatDate(m.invited_at) })
                          : t("emptyValue")}
                    </TD>
                    <TD className="text-right">
                      {!m.is_owner && (
                        <div className="flex items-center justify-end gap-1.5">
                          <Button
                            variant="ghost"
                            onClick={() => startEditRoles(m)}
                            className="h-7 px-2 text-xs"
                          >
                            {t("roles")}
                          </Button>
                          <Button
                            variant={m.status === "active" ? "ghost" : "secondary"}
                            onClick={() => handleToggleStatus(m)}
                            className="h-7 px-2 text-xs"
                          >
                            {m.status === "active" ? t("deactivate") : t("activate")}
                          </Button>
                        </div>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>
        </div>
      </main>

      {/* Invite Modal */}
      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent title={t("inviteUser")}>
          <div className="space-y-4 pt-2">
            <Field label={t("email")}>
              <Input
                type="email"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder={t("emailPlaceholder")}
              />
            </Field>

            <div>
              <p className="mb-2 text-xs font-medium text-[var(--vinea-ink-muted)]">{t("roles")}</p>
              <div className="space-y-2 rounded-[var(--radius-control)] border border-[var(--vinea-border)] p-3">
                {(rolesQuery.data ?? []).map((r) => {
                  const checked = selectedRoleIds.includes(r.id);
                  return (
                    <label key={r.id} className="flex items-start gap-2 cursor-pointer text-xs">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => {
                          if (e.target.checked) setSelectedRoleIds((prev) => [...prev, r.id]);
                          else setSelectedRoleIds((prev) => prev.filter((id) => id !== r.id));
                        }}
                        className="mt-0.5 size-4 accent-[var(--vinea-brand)]"
                      />
                      <div>
                        <p className="font-medium text-[var(--vinea-ink)]">{r.name}</p>
                        {r.description && <p className="text-[11px] text-[var(--vinea-ink-subtle)]">{r.description}</p>}
                      </div>
                    </label>
                  );
                })}
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setInviteOpen(false)}>{t("cancel")}</Button>
              <Button variant="primary" disabled={!inviteEmail.trim() || inviteMember.isPending} onClick={handleSendInvite}>
                {inviteMember.isPending ? t("saving") : t("sendInvitation")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Edit Roles Modal */}
      <Dialog open={roleModalMember !== null} onOpenChange={(open) => !open && setRoleModalMember(null)}>
        <DialogContent title={t("assignRoles")} description={roleModalMember?.email}>
          <div className="space-y-4 pt-2">
            <div className="space-y-2 rounded-[var(--radius-control)] border border-[var(--vinea-border)] p-3">
              {(rolesQuery.data ?? []).map((r) => {
                const checked = memberRoleIds.includes(r.id);
                return (
                  <label key={r.id} className="flex items-start gap-2 cursor-pointer text-xs">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(e) => {
                        if (e.target.checked) setMemberRoleIds((prev) => [...prev, r.id]);
                        else setMemberRoleIds((prev) => prev.filter((id) => id !== r.id));
                      }}
                      className="mt-0.5 size-4 accent-[var(--vinea-brand)]"
                    />
                    <div>
                      <p className="font-medium text-[var(--vinea-ink)]">{r.name}</p>
                      {r.description && <p className="text-[11px] text-[var(--vinea-ink-subtle)]">{r.description}</p>}
                    </div>
                  </label>
                );
              })}
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setRoleModalMember(null)}>{t("cancel")}</Button>
              <Button variant="primary" disabled={updateMemberRoles.isPending} onClick={handleSaveRoles}>
                {updateMemberRoles.isPending ? t("saving") : t("save")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
