"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import {
  ArrowLeft,
  Building,
  Calendar,
  Check,
  Lock,
  Plus,
  RefreshCw,
  Sliders,
  Unlock,
} from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { DatePicker } from "@/design/components/date-picker";
import { Dialog, DialogContent, DialogTrigger } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { StatusChip } from "@/design/components/status-chip";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/design/components/tabs";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useToast } from "@/design/components/toast";
import {
  useAccounts,
  useCloseFiscalYear,
  useClosePeriod,
  useCompanyDetails,
  useCreateFiscalYear,
  useFiscalYears,
  useGLSettings,
  useLockPeriod,
  useOpenPeriod,
  usePeriods,
  useReopenFiscalYear,
  useReopenPeriod,
  useUpdateCompany,
  useUpdateGLSettings,
} from "@/features/gl/hooks";
import { toOptions } from "@/features/gl/lookups";
import { formatDate } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

export default function CompanyDetailsPage() {
  const t = useTranslations("maintenance");
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const companyQuery = useCompanyDetails();
  const updateCompany = useUpdateCompany();

  const accountsQuery = useAccounts();
  const settingsQuery = useGLSettings();
  const updateSettings = useUpdateGLSettings();

  const fiscalYearsQuery = useFiscalYears();
  const createFiscalYear = useCreateFiscalYear();
  const closeFiscalYear = useCloseFiscalYear();
  const reopenFiscalYear = useReopenFiscalYear();

  const [selectedYearId, setSelectedYearId] = useState<number | undefined>(undefined);
  const periodsQuery = usePeriods(selectedYearId);
  const openPeriod = useOpenPeriod();
  const closePeriod = useClosePeriod();
  const lockPeriod = useLockPeriod();
  const reopenPeriod = useReopenPeriod();

  // Company Form State
  const [name, setName] = useState("");
  const [tin, setTin] = useState("");
  const [vatRegistered, setVatRegistered] = useState(false);
  const [fiscalCountry, setFiscalCountry] = useState("RW");

  // Settings State
  const [retainedEarningsId, setRetainedEarningsId] = useState<string>("");
  const [roundingDiffId, setRoundingDiffId] = useState<string>("");

  // New Fiscal Year Modal State
  const [newYearOpen, setNewYearOpen] = useState(false);
  const [yearCode, setYearCode] = useState("");
  const [startDate, setStartDate] = useState<Date>(new Date());
  const [endDate, setEndDate] = useState<Date>(new Date());
  const [periodCount, setPeriodCount] = useState("12");

  // Reopen Dialog State
  const [reopenOpen, setReopenOpen] = useState(false);
  const [reopenTarget, setReopenTarget] = useState<{ type: "year" | "period"; id: number } | null>(null);
  const [reopenReason, setReopenReason] = useState("");

  useEffect(() => {
    if (companyQuery.data) {
      setName(companyQuery.data.name ?? "");
      setTin(companyQuery.data.tin ?? "");
      setVatRegistered(companyQuery.data.vat_registered ?? false);
      setFiscalCountry(companyQuery.data.fiscal_country ?? "RW");
    }
  }, [companyQuery.data]);

  useEffect(() => {
    if (settingsQuery.data) {
      setRetainedEarningsId(settingsQuery.data.retained_earnings_account_id ? String(settingsQuery.data.retained_earnings_account_id) : "");
      setRoundingDiffId(settingsQuery.data.rounding_difference_account_id ? String(settingsQuery.data.rounding_difference_account_id) : "");
    }
  }, [settingsQuery.data]);

  useEffect(() => {
    if (fiscalYearsQuery.data && fiscalYearsQuery.data.length > 0 && selectedYearId === undefined) {
      setSelectedYearId(fiscalYearsQuery.data[0].id);
    }
  }, [fiscalYearsQuery.data, selectedYearId]);

  async function handleSaveCompany() {
    try {
      await updateCompany.mutateAsync({
        name,
        tin: tin || null,
        vat_registered: vatRegistered,
        fiscal_country: fiscalCountry,
      });
      toast.show({ title: t("saved"), tone: "success" });
    } catch (err) {
      showApiError(err, "Couldn't update company details");
    }
  }

  async function handleSaveSettings() {
    try {
      await updateSettings.mutateAsync({
        retained_earnings_account_id: retainedEarningsId ? Number(retainedEarningsId) : null,
        rounding_difference_account_id: roundingDiffId ? Number(roundingDiffId) : null,
      });
      toast.show({ title: t("saved"), tone: "success" });
    } catch (err) {
      showApiError(err, "Couldn't save GL defaults");
    }
  }

  async function handleCreateYear() {
    try {
      await createFiscalYear.mutateAsync({
        code: yearCode,
        start_date: startDate.toISOString().slice(0, 10),
        end_date: endDate.toISOString().slice(0, 10),
        period_count: Number(periodCount),
      });
      setNewYearOpen(false);
      setYearCode("");
      toast.show({ title: "Fiscal year created", tone: "success" });
    } catch (err) {
      showApiError(err, "Couldn't create fiscal year");
    }
  }

  async function handleConfirmReopen() {
    if (!reopenTarget || !reopenReason.trim()) return;
    try {
      if (reopenTarget.type === "year") {
        await reopenFiscalYear.mutateAsync({ yearId: reopenTarget.id, reason: reopenReason });
        toast.show({ title: "Fiscal year reopened", tone: "success" });
      } else {
        await reopenPeriod.mutateAsync({ periodId: reopenTarget.id, reason: reopenReason });
        toast.show({ title: "Period reopened", tone: "success" });
      }
      setReopenOpen(false);
      setReopenReason("");
      setReopenTarget(null);
    } catch (err) {
      showApiError(err, "Couldn't reopen");
    }
  }

  const postableAccounts = (accountsQuery.data ?? []).filter((a) => a.is_postable && !a.is_control);

  return (
    <div className="flex min-h-screen flex-col" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{t("companyDetails")}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">
              {companyQuery.data?.name ?? "Company setup"} · Configuration & fiscal periods
            </p>
          </div>
        </div>
        <ThemeToggle />
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-5xl space-y-6">
          <Tabs defaultValue="company">
            <TabsList>
              <TabsTrigger value="company" className="gap-1.5">
                <Building className="size-3.5" /> {t("companyInfo")}
              </TabsTrigger>
              <TabsTrigger value="periods" className="gap-1.5">
                <Calendar className="size-3.5" /> {t("fiscalYearsAndPeriods")}
              </TabsTrigger>
              <TabsTrigger value="defaults" className="gap-1.5">
                <Sliders className="size-3.5" /> {t("generalSettings")}
              </TabsTrigger>
            </TabsList>

            {/* TAB 1: Company Info */}
            <TabsContent value="company" className="pt-4">
              <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 space-y-4 max-w-2xl">
                <Field label={t("companyName")}>
                  <Input value={name} onChange={(e) => setName(e.target.value)} />
                </Field>
                <div className="grid grid-cols-2 gap-4">
                  <Field label={t("tin")}>
                    <Input value={tin} onChange={(e) => setTin(e.target.value)} placeholder="e.g. 100234567" />
                  </Field>
                  <Field label={t("fiscalCountry")}>
                    <Input value={fiscalCountry} onChange={(e) => setFiscalCountry(e.target.value)} maxLength={2} />
                  </Field>
                </div>
                <div className="pt-2">
                  <label className="flex items-center gap-2 text-sm font-medium cursor-pointer">
                    <input
                      type="checkbox"
                      checked={vatRegistered}
                      onChange={(e) => setVatRegistered(e.target.checked)}
                      className="size-4 accent-[var(--vinea-brand)]"
                    />
                    {t("vatRegistered")}
                  </label>
                </div>

                <div className="pt-4 flex justify-end">
                  <Button variant="primary" disabled={updateCompany.isPending} onClick={handleSaveCompany}>
                    {updateCompany.isPending ? t("saving") : t("save")}
                  </Button>
                </div>
              </div>
            </TabsContent>

            {/* TAB 2: Fiscal Years & Periods */}
            <TabsContent value="periods" className="pt-4 space-y-6">
              {/* Fiscal Years */}
              <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 space-y-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="font-display text-base font-semibold">{t("fiscalYears")}</h2>
                    <p className="text-xs text-[var(--vinea-ink-muted)]">Annual boundaries for ledger closing</p>
                  </div>
                  <Dialog open={newYearOpen} onOpenChange={setNewYearOpen}>
                    <DialogTrigger asChild>
                      <Button variant="secondary" className="gap-1.5 text-xs">
                        <Plus className="size-3.5" /> {t("newFiscalYear")}
                      </Button>
                    </DialogTrigger>
                    <DialogContent title={t("newFiscalYear")}>
                      <div className="space-y-3 pt-2">
                        <Field label="Year code">
                          <Input value={yearCode} onChange={(e) => setYearCode(e.target.value)} placeholder="FY2026" />
                        </Field>
                        <div className="grid grid-cols-2 gap-3">
                          <Field label="Start date">
                            <DatePicker value={startDate} onValueChange={setStartDate} />
                          </Field>
                          <Field label="End date">
                            <DatePicker value={endDate} onValueChange={setEndDate} />
                          </Field>
                        </div>
                        <Field label="Number of periods">
                          <Input value={periodCount} onChange={(e) => setPeriodCount(e.target.value)} type="number" />
                        </Field>
                        <div className="flex justify-end gap-2 pt-2">
                          <Button variant="ghost" onClick={() => setNewYearOpen(false)}>Cancel</Button>
                          <Button variant="primary" disabled={!yearCode || createFiscalYear.isPending} onClick={handleCreateYear}>
                            {createFiscalYear.isPending ? t("saving") : t("save")}
                          </Button>
                        </div>
                      </div>
                    </DialogContent>
                  </Dialog>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {(fiscalYearsQuery.data ?? []).map((fy) => (
                    <div
                      key={fy.id}
                      onClick={() => setSelectedYearId(fy.id)}
                      className={`cursor-pointer rounded-[var(--radius-control)] border p-4 transition-colors ${
                        selectedYearId === fy.id
                          ? "border-[var(--vinea-brand)] bg-[var(--vinea-brand-soft)]/20"
                          : "border-[var(--vinea-border)] bg-[var(--vinea-surface-sunken)]/40 hover:bg-[var(--vinea-surface-sunken)]"
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-sm font-bold text-[var(--vinea-ink)]">{fy.code}</span>
                        <StatusChip tone={fy.status === "open" ? "success" : fy.status === "locked" ? "neutral" : "warning"}>
                          {fy.status}
                        </StatusChip>
                      </div>
                      <p className="mt-1 text-xs text-[var(--vinea-ink-muted)]">
                        {formatDate(fy.start_date)} – {formatDate(fy.end_date)}
                      </p>
                      <div className="mt-3 flex gap-2">
                        {fy.status === "open" ? (
                          <Button
                            variant="secondary"
                            onClick={(e) => {
                              e.stopPropagation();
                              closeFiscalYear.mutate(fy.id);
                            }}
                            className="text-xs h-7 px-2"
                          >
                            <Lock className="size-3 mr-1" /> {t("closeYear")}
                          </Button>
                        ) : (
                          <Button
                            variant="secondary"
                            onClick={(e) => {
                              e.stopPropagation();
                              setReopenTarget({ type: "year", id: fy.id });
                              setReopenOpen(true);
                            }}
                            className="text-xs h-7 px-2"
                          >
                            <Unlock className="size-3 mr-1" /> {t("reopenYear")}
                          </Button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Accounting Periods Timeline */}
              <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 space-y-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="font-display text-base font-semibold">{t("accountingPeriods")}</h2>
                    <p className="text-xs text-[var(--vinea-ink-muted)]">
                      Monthly posting control & audit checkpoints
                    </p>
                  </div>
                </div>

                {periodsQuery.isLoading ? (
                  <div className="p-8 text-center text-xs text-[var(--vinea-ink-subtle)]">Loading periods…</div>
                ) : (
                  <Table>
                    <THead>
                      <TR>
                        <TH className="w-16">#</TH>
                        <TH>Period Name</TH>
                        <TH className="w-48">Date Window</TH>
                        <TH className="w-28">Status</TH>
                        <TH className="w-40 text-right">{t("actions")}</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {(periodsQuery.data ?? []).map((p) => (
                        <TR key={p.id}>
                          <TD className="font-mono text-xs">{p.period_number}</TD>
                          <TD className="font-medium text-xs text-[var(--vinea-ink)]">{p.name}</TD>
                          <TD className="text-xs text-[var(--vinea-ink-muted)]">
                            {formatDate(p.start_date)} – {formatDate(p.end_date)}
                          </TD>
                          <TD>
                            <StatusChip
                              tone={
                                p.status === "open"
                                  ? "success"
                                  : p.status === "closed"
                                    ? "warning"
                                    : p.status === "locked"
                                      ? "neutral"
                                      : "neutral"
                              }
                            >
                              {p.status}
                            </StatusChip>
                          </TD>
                          <TD className="text-right">
                            {p.status === "pending" && (
                              <Button
                                variant="secondary"
                                onClick={() => openPeriod.mutate(p.id)}
                                className="text-xs h-7 px-2"
                              >
                                {t("openPeriod")}
                              </Button>
                            )}
                            {p.status === "open" && (
                              <Button
                                variant="secondary"
                                onClick={() => closePeriod.mutate(p.id)}
                                className="text-xs h-7 px-2"
                              >
                                {t("closePeriod")}
                              </Button>
                            )}
                            {p.status === "closed" && (
                              <div className="flex justify-end gap-1">
                                <Button
                                  variant="secondary"
                                  onClick={() => lockPeriod.mutate(p.id)}
                                  className="text-xs h-7 px-2"
                                >
                                  {t("lockPeriod")}
                                </Button>
                                <Button
                                  variant="ghost"
                                  onClick={() => {
                                    setReopenTarget({ type: "period", id: p.id });
                                    setReopenOpen(true);
                                  }}
                                  className="text-xs h-7 px-2"
                                >
                                  {t("reopenPeriod")}
                                </Button>
                              </div>
                            )}
                            {p.status === "locked" && (
                              <Button
                                variant="ghost"
                                onClick={() => {
                                  setReopenTarget({ type: "period", id: p.id });
                                  setReopenOpen(true);
                                }}
                                className="text-xs h-7 px-2"
                              >
                                {t("reopenPeriod")}
                              </Button>
                            )}
                          </TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                )}
              </div>
            </TabsContent>

            {/* TAB 3: Defaults (gl_settings) */}
            <TabsContent value="defaults" className="pt-4">
              <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-6 space-y-4 max-w-2xl">
                <div>
                  <h2 className="font-display text-base font-semibold">{t("generalSettings")}</h2>
                  <p className="text-xs text-[var(--vinea-ink-muted)]">
                    Default accounts for year-end retained earnings and FX rounding
                  </p>
                </div>

                <Field label={t("retainedEarningsAccount")}>
                  <Combobox
                    options={toOptions(postableAccounts, (a) => `${a.code} · ${a.name}`)}
                    value={retainedEarningsId}
                    onValueChange={setRetainedEarningsId}
                    placeholder="Choose retained earnings account…"
                  />
                </Field>

                <Field label={t("roundingDifferenceAccount")}>
                  <Combobox
                    options={toOptions(postableAccounts, (a) => `${a.code} · ${a.name}`)}
                    value={roundingDiffId}
                    onValueChange={setRoundingDiffId}
                    placeholder="Choose rounding difference account…"
                  />
                </Field>

                <div className="pt-4 flex justify-end">
                  <Button variant="primary" disabled={updateSettings.isPending} onClick={handleSaveSettings}>
                    {updateSettings.isPending ? t("saving") : t("save")}
                  </Button>
                </div>
              </div>
            </TabsContent>
          </Tabs>

          {/* Reopen Reason Dialog */}
          <Dialog open={reopenOpen} onOpenChange={setReopenOpen}>
            <DialogContent title="Reopen Audit Confirmation" description="Reopening a closed fiscal boundary is audited.">
              <div className="space-y-3 pt-2">
                <Field label={t("reason")}>
                  <Input
                    value={reopenReason}
                    onChange={(e) => setReopenReason(e.target.value)}
                    placeholder="e.g. Late supplier invoice adjustments"
                  />
                </Field>
                <div className="flex justify-end gap-2 pt-2">
                  <Button variant="ghost" onClick={() => setReopenOpen(false)}>Cancel</Button>
                  <Button variant="primary" disabled={!reopenReason.trim()} onClick={handleConfirmReconfirm}>
                    Confirm Reopen
                  </Button>
                </div>
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </main>
    </div>
  );

  async function handleConfirmReconfirm() {
    await handleConfirmReopen();
  }
}
