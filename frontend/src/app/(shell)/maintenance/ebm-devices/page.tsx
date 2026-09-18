"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { KeyRound, Plus, RefreshCw, Router, ShieldAlert } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { QueryState } from "@/design/components/query-state";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import {
  useFiscalDevices,
  useInitializeFiscalDevice,
  useRegisterFiscalDevice,
  useSuspendFiscalDevice,
  useSyncFiscalCodes,
} from "@/features/fiscal/hooks";
import type { FiscalDevice } from "@/features/fiscal/types";
import { useBranches } from "@/features/gl/hooks";
import { FiscalDeviceStatus, FiscalEnvironment, FiscalProfile } from "@/lib/api-enums";
import { dotted, formatDate } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

/**
 * **EBM devices** — Maintenance → Tax, after Tax types.
 *
 * One device per branch, and the branch is the thing: `bhf_id` is the authority's own branch
 * identifier (the head office is `00`) and every receipt counter is per device, so two
 * devices on one shop would each hold their own gapless run of the same numbers. The service
 * refuses the second with `fiscal_device_exists`; this screen offers only branches that have
 * none, so the refusal is something an operator reads rather than something they hit.
 *
 * **Initialize is the only way in.** There is no Activate button and there is not meant to
 * be: activation is what happens when the authority has been told about the device and what
 * it sent back has been stored, and `activate()` refuses a device with no `sdc_id`. A
 * suspended device is brought back by **re-initializing** it — the decision taken at step 1 —
 * which is also how its keys are reissued. A separate Activate would be a button that could
 * only ever fail on the case it was there for.
 *
 * **No key is rendered, because no key arrives.** `cmc_key`, `intrl_key` and `sign_key` are
 * the only secrets this phase holds and `DeviceRead` has no field for one; what the screen
 * shows is `has_keys` — *held* or *not held* — which is the question an operator actually
 * asks. The redaction is proven over the model in `tests/fiscal/test_key_redaction.py`, so it
 * survives a second endpoint learning to serialise a device.
 */
export default function EbmDevicesPage() {
  const t = useTranslations("fiscal.devices");
  const tc = useTranslations("fiscal.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canManage = useHasPermission()("fiscal:setup_manage");

  const devices = useFiscalDevices();
  const branches = useBranches();
  const registerDevice = useRegisterFiscalDevice();
  const initializeDevice = useInitializeFiscalDevice();
  const suspendDevice = useSuspendFiscalDevice();
  const syncCodes = useSyncFiscalCodes();

  const [registerOpen, setRegisterOpen] = useState(false);
  const [branchId, setBranchId] = useState("");
  const [profile, setProfile] = useState<FiscalProfile>(FiscalProfile.VSDC);
  const [environment, setEnvironment] = useState<FiscalEnvironment>(FiscalEnvironment.TEST);
  const [baseUrl, setBaseUrl] = useState("");
  const [serial, setSerial] = useState("");
  const [bhfId, setBhfId] = useState("00");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});

  const [suspending, setSuspending] = useState<FiscalDevice | null>(null);
  const [reason, setReason] = useState("");
  /** Which row is mid-call, so its own button spins rather than all of them. */
  const [busyId, setBusyId] = useState<number | null>(null);

  const branchRows = branches.data ?? [];
  const branchById = useMemo(
    () => new Map(branchRows.map((branch) => [branch.id, branch])),
    [branchRows],
  );

  /** Only branches with no device. The service refuses a second one and this is the same rule
   * said where the operator is choosing, rather than after they have chosen. */
  const availableBranches = useMemo(() => {
    const taken = new Set((devices.data ?? []).map((device) => device.branch_id));
    return branchRows.filter((branch) => branch.is_active && !taken.has(branch.id));
  }, [branchRows, devices.data]);

  function branchLabel(id: number): string {
    const branch = branchById.get(id);
    return branch ? dotted(branch.code, branch.name) : String(id);
  }

  function statusTone(status: FiscalDevice["status"]): "success" | "warning" | "neutral" {
    if (status === FiscalDeviceStatus.ACTIVE) return "success";
    if (status === FiscalDeviceStatus.SUSPENDED) return "warning";
    return "neutral";
  }

  function startRegister() {
    setBranchId("");
    setProfile(FiscalProfile.VSDC);
    setEnvironment(FiscalEnvironment.TEST);
    setBaseUrl("");
    setSerial("");
    setBhfId("00");
    setFieldErrors({});
    setRegisterOpen(true);
  }

  async function handleRegister() {
    setFieldErrors({});
    try {
      await registerDevice.mutateAsync({
        branch_id: Number(branchId),
        profile,
        environment,
        base_url: baseUrl.trim(),
        dvc_srl_no: serial.trim(),
        bhf_id: bhfId.trim(),
      });
      toast.show({ title: t("registered"), tone: "success" });
      setRegisterOpen(false);
    } catch (err) {
      // Inline on the field the backend names — `branch_id`, not "something was wrong".
      if (isApiError(err)) setFieldErrors(err.fieldErrors);
      showApiError(err, t("registerFailed"));
    }
  }

  async function handleInitialize(device: FiscalDevice) {
    setBusyId(device.id);
    try {
      const updated = await initializeDevice.mutateAsync({
        deviceId: device.id,
        // A fresh key per press: the header is there so a double-click while the authority is
        // slow replays one call, not so two deliberate presses become one.
        idempotencyKey: crypto.randomUUID(),
      });
      toast.show({
        title: t("initialized"),
        description: updated.sdc_id ?? undefined,
        tone: "success",
      });
    } catch (err) {
      showApiError(err, t("initializeFailed"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleSuspend() {
    if (!suspending) return;
    setBusyId(suspending.id);
    try {
      await suspendDevice.mutateAsync({ deviceId: suspending.id, reason: reason.trim() });
      toast.show({ title: t("suspended"), tone: "success" });
      setSuspending(null);
      setReason("");
    } catch (err) {
      showApiError(err, t("suspendFailed"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleSync(device: FiscalDevice) {
    setBusyId(device.id);
    try {
      const result = await syncCodes.mutateAsync(device.id);
      toast.show({
        title: t("synced"),
        description: t("syncedRows", { codes: result.codes.rows, classes: result.classes.rows }),
        tone: "success",
      });
    } catch (err) {
      showApiError(err, t("syncFailed"));
    } finally {
      setBusyId(null);
    }
  }

  const rows = devices.data ?? [];

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      actions={
        <Button
          variant="primary"
          onClick={startRegister}
          disabled={!canManage || availableBranches.length === 0}
          className="gap-1.5 text-xs"
        >
          <Plus className="size-3.5" /> {t("register")}
        </Button>
      }
    >
      <MaintenanceCard icon={<Router className="size-4" />} title={t("cardTitle")}>
        {rows.length === 0 ? (
          <QueryState query={devices} isEmpty empty={t("empty")} testId="devices" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-44">{t("branch")}</TH>
                <TH className="w-28">{t("profile")}</TH>
                <TH className="w-28">{t("environment")}</TH>
                <TH>{t("identity")}</TH>
                <TH className="w-36">{t("lastSuccess")}</TH>
                <TH className="w-64 text-right">{tc("status")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((device) => (
                <TR key={device.id}>
                  <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                    {branchLabel(device.branch_id)}
                    <p className="font-mono text-[11px] text-[var(--vinea-ink-subtle)]">
                      {t("bhfId", { value: device.bhf_id })}
                    </p>
                  </TD>
                  <TD className="text-xs uppercase text-[var(--vinea-ink)]">{device.profile}</TD>
                  <TD className="text-xs text-[var(--vinea-ink)]">
                    {t(`environmentLabel.${device.environment}`)}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink)]">
                    <p className="font-mono">{device.sdc_id ?? tc("emptyValue")}</p>
                    <p className="font-mono text-[11px] text-[var(--vinea-ink-subtle)]">
                      {t("mrcNo", { value: device.mrc_no ?? tc("emptyValue") })}
                    </p>
                    <p className="font-mono text-[11px] text-[var(--vinea-ink-subtle)]">
                      {t("serial", { value: device.dvc_srl_no })}
                    </p>
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {device.last_success_at ? (
                      formatDate(device.last_success_at)
                    ) : (
                      <StatusChip tone="warning">{t("offline")}</StatusChip>
                    )}
                    {device.last_error ? (
                      <p
                        className="text-[11px] text-[var(--vinea-danger)]"
                        title={device.last_error}
                      >
                        {device.last_error}
                      </p>
                    ) : null}
                  </TD>
                  <TD className="text-right">
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      <StatusChip tone={device.has_keys ? "info" : "neutral"}>
                        <KeyRound className="size-3" />
                        {device.has_keys ? t("keysHeld") : t("keysAbsent")}
                      </StatusChip>
                      <StatusChip tone={statusTone(device.status)}>
                        {t(`statusLabel.${device.status}`)}
                      </StatusChip>
                      <Button
                        variant="ghost"
                        onClick={() => handleInitialize(device)}
                        disabled={!canManage || busyId === device.id}
                        className="text-xs"
                      >
                        {device.status === FiscalDeviceStatus.SUSPENDED
                          ? t("reinitialize")
                          : t("initialize")}
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={() => handleSync(device)}
                        disabled={
                          !canManage ||
                          busyId === device.id ||
                          device.status !== FiscalDeviceStatus.ACTIVE
                        }
                        className="gap-1.5 text-xs"
                      >
                        <RefreshCw className="size-3.5" /> {t("syncCodes")}
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={() => {
                          setSuspending(device);
                          setReason("");
                        }}
                        disabled={
                          !canManage || device.status !== FiscalDeviceStatus.ACTIVE
                        }
                        className="text-xs"
                      >
                        {t("suspend")}
                      </Button>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
        <p className="flex items-start gap-2 pt-3 text-xs text-[var(--vinea-ink-subtle)]">
          <ShieldAlert className="size-4 shrink-0 text-[var(--vinea-warning)]" />
          {t("keysNote")}
        </p>
        <p className="pt-2 text-xs text-[var(--vinea-ink-subtle)]">{t("activationNote")}</p>
      </MaintenanceCard>

      <Dialog open={registerOpen} onOpenChange={setRegisterOpen}>
        <DialogContent title={t("registerTitle")}>
          <div className="space-y-3 pt-2">
            <Field label={t("branch")} error={fieldErrors.branch_id?.[0]}>
              <Combobox
                options={availableBranches.map((branch) => ({
                  value: String(branch.id),
                  label: dotted(branch.code, branch.name),
                }))}
                value={branchId}
                onValueChange={setBranchId}
                placeholder={t("chooseBranch")}
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("profile")}>
                <Select
                  options={[
                    { value: FiscalProfile.VSDC, label: t("profileLabel.vsdc") },
                    { value: FiscalProfile.OSDC, label: t("profileLabel.osdc") },
                  ]}
                  value={profile}
                  onValueChange={(value) => setProfile(value as FiscalProfile)}
                />
              </Field>
              <Field label={t("environment")}>
                <Select
                  options={[
                    { value: FiscalEnvironment.TEST, label: t("environmentLabel.test") },
                    {
                      value: FiscalEnvironment.PRODUCTION,
                      label: t("environmentLabel.production"),
                    },
                  ]}
                  value={environment}
                  onValueChange={(value) => setEnvironment(value as FiscalEnvironment)}
                />
              </Field>
            </div>
            <Field label={t("baseUrl")} error={fieldErrors.base_url?.[0]}>
              <Input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                className="font-mono"
                placeholder={t("baseUrlPlaceholder")}
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("deviceSerial")} error={fieldErrors.dvc_srl_no?.[0]}>
                <Input
                  value={serial}
                  onChange={(e) => setSerial(e.target.value)}
                  className="font-mono"
                  placeholder={t("deviceSerialPlaceholder")}
                />
              </Field>
              <Field label={t("bhfIdLabel")} error={fieldErrors.bhf_id?.[0]}>
                <Input
                  value={bhfId}
                  onChange={(e) => setBhfId(e.target.value)}
                  className="font-mono"
                  maxLength={2}
                />
              </Field>
            </div>
            <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("registerNote")}</p>
            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setRegisterOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button
                variant="primary"
                disabled={
                  !branchId || !baseUrl || !serial || !canManage || registerDevice.isPending
                }
                onClick={handleRegister}
              >
                {tc("create")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog
        open={suspending !== null}
        onOpenChange={(open) => {
          if (!open) setSuspending(null);
        }}
      >
        <DialogContent title={t("suspendTitle")}>
          <div className="space-y-3 pt-2">
            <p className="text-xs text-[var(--vinea-ink-muted)]">{t("suspendNote")}</p>
            <Field label={t("suspendReason")}>
              <Input value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setSuspending(null)}>
                {tc("cancel")}
              </Button>
              <Button
                variant="primary"
                disabled={!reason.trim() || !canManage || suspendDevice.isPending}
                onClick={handleSuspend}
              >
                {t("suspend")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </MaintenancePage>
  );
}
