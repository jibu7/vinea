"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft, Briefcase, Edit2, Plus } from "lucide-react";
import { Button } from "@/design/components/button";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { StatusChip } from "@/design/components/status-chip";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useToast } from "@/design/components/toast";
import { useCreateProject, useProjects, useUpdateProject } from "@/features/gl/hooks";
import type { Project } from "@/features/gl/types";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

export default function ProjectsPage() {
  const t = useTranslations("maintenance");
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const projectsQuery = useProjects();
  const createProject = useCreateProject();
  const updateProject = useUpdateProject();

  const [open, setOpen] = useState(false);
  const [editingProject, setEditingProject] = useState<Project | null>(null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");

  function startCreate() {
    setEditingProject(null);
    setCode("");
    setName("");
    setOpen(true);
  }

  function startEdit(p: Project) {
    setEditingProject(p);
    setCode(p.code);
    setName(p.name);
    setOpen(true);
  }

  async function handleSave() {
    try {
      if (editingProject) {
        await updateProject.mutateAsync({
          projectId: editingProject.id,
          payload: { code, name },
        });
        toast.show({ title: "Project updated", tone: "success" });
      } else {
        await createProject.mutateAsync({ code, name });
        toast.show({ title: "Project created", tone: "success" });
      }
      setOpen(false);
    } catch (err) {
      showApiError(err, "Couldn't save project");
    }
  }

  async function handleToggleActive(p: Project) {
    try {
      await updateProject.mutateAsync({
        projectId: p.id,
        payload: { is_active: !p.is_active },
      });
      toast.show({
        title: p.code,
        description: p.is_active ? "Deactivated" : "Activated",
        tone: "success",
      });
    } catch (err) {
      showApiError(err, "Couldn't update project");
    }
  }

  return (
    <div className="flex min-h-screen flex-col" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]" aria-label="Back">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{t("projects")}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">
              Cross-module job costing and analytics dimension (ADR-04 / D8)
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="primary" onClick={startCreate} className="gap-1.5 text-xs">
            <Plus className="size-3.5" /> {t("newProject")}
          </Button>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-4xl space-y-6">
          <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 space-y-4">
            <div className="flex items-center gap-2">
              <Briefcase className="size-4 text-[var(--vinea-brand)]" />
              <h2 className="font-display text-base font-semibold">Costing Projects</h2>
            </div>

            <Table>
              <THead>
                <TR>
                  <TH className="w-32">Project Code</TH>
                  <TH>Project Name</TH>
                  <TH className="w-32 text-right">Status</TH>
                </TR>
              </THead>
              <TBody>
                {(projectsQuery.data ?? []).map((p) => (
                  <TR key={p.id}>
                    <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">{p.code}</TD>
                    <TD className="font-medium text-xs text-[var(--vinea-ink)]">{p.name}</TD>
                    <TD className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => startEdit(p)}
                          aria-label={`Edit ${p.name}`}
                          className="p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)] rounded"
                        >
                          <Edit2 className="size-3.5" />
                        </button>
                        <button type="button" onClick={() => handleToggleActive(p)}>
                          <StatusChip tone={p.is_active ? "success" : "neutral"}>
                            {p.is_active ? "Active" : "Inactive"}
                          </StatusChip>
                        </button>
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>
        </div>
      </main>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title={editingProject ? "Edit Project" : t("newProject")}>
          <div className="space-y-3 pt-2">
            <Field label={t("code")}>
              <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder="PRJ-01" />
            </Field>
            <Field label={t("name")}>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Vineyard Expansion" />
            </Field>

            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
              <Button variant="primary" disabled={!code || !name} onClick={handleSave}>
                {t("save")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
