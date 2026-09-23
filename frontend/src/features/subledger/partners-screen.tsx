"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Edit2, Plus, Search, Users } from "lucide-react";
import { Button } from "@/design/components/button";
import { QueryState } from "@/design/components/query-state";
import { Combobox } from "@/design/components/combobox";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Drawer, DrawerContent } from "@/design/components/drawer";
import { Field, Input } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/design/components/tabs";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import { useFiscalDevices, useVerifyTin } from "@/features/fiscal/hooks";
import { useCurrencies } from "@/features/gl/hooks";
import { FiscalDeviceStatus } from "@/lib/api-enums";
import { byId, toOptions } from "@/features/gl/lookups";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useCreatePartner, usePartners, useUpdatePartner } from "./hooks";
import { PartnerContactsPanel } from "./partner-contacts-panel";
import { PartnerSettingsForm } from "./partner-settings-form";
import { partnerCode, type Partner, type PartnerRole } from "./types";

interface DetailsForm {
  name: string;
  code: string;
  tin: string;
  email: string;
  phone: string;
  notes: string;
  currencyId: string;
  bankName: string;
  bankAccountNumber: string;
  bankAccountHolder: string;
}

const BLANK: DetailsForm = {
  name: "",
  code: "",
  tin: "",
  email: "",
  phone: "",
  notes: "",
  currencyId: "",
  bankName: "",
  bankAccountNumber: "",
  bankAccountHolder: "",
};

/**
 * **Verify TIN** — the authority's opinion of a taxpayer number, beside the field that holds
 * it.
 *
 * A button rather than a live check, and that is the point: the lookup is a `GET` that stores
 * nothing, and what it returns is an answer *at a moment* rather than a fact about the
 * partner. Firing it on every keystroke would ask RRA about `1`, `10`, `100`… and firing it
 * on blur would cache an answer that is not a property of the row.
 *
 * `found: false` is an **answer**, not a failure — the authority's own "no such taxpayer"
 * (884) arrives that way, and it is the one a clerk most needs to see before invoicing.
 *
 * The lookup goes through a device, because it is the device the authority answers. A company
 * with no active device is told so rather than shown a button that can only 422.
 */
function VerifyTin({ tin }: { tin: string }) {
  // `tTin`, not `t`: the screen below declares its own `t` on `arap.partners`, and two aliases
  // of the same name in one file is exactly what `i18n-coverage.test.ts` cannot tell apart.
  const tTin = useTranslations("fiscal.tin");
  const devices = useFiscalDevices();
  const verify = useVerifyTin();
  const [result, setResult] = useState<{ found: boolean; text: string } | null>(null);

  const device = (devices.data ?? []).find((d) => d.status === FiscalDeviceStatus.ACTIVE);
  const trimmed = tin.trim();

  async function handleVerify() {
    if (!device) return;
    setResult(null);
    try {
      const lookup = await verify.mutateAsync({ deviceId: device.id, tin: trimmed });
      setResult({
        found: lookup.found,
        text: lookup.found
          ? lookup.status
            ? tTin("found", { name: lookup.name ?? trimmed, status: lookup.status })
            : tTin("foundNoStatus", { name: lookup.name ?? trimmed })
          : tTin("notFound"),
      });
    } catch {
      // The authority's own refusal text is already carried through by the API error; what
      // this line says is the thing the operator can act on — try again, or ring them.
      setResult({ found: false, text: tTin("failed") });
    }
  }

  if (!device) {
    return <p className="pt-1 text-[11px] text-[var(--vinea-ink-subtle)]">{tTin("noDevice")}</p>;
  }
  return (
    <div className="flex flex-wrap items-center gap-2 pt-1">
      <Button
        variant="ghost"
        onClick={handleVerify}
        disabled={!trimmed || verify.isPending}
        className="text-xs"
      >
        {verify.isPending ? tTin("verifying") : tTin("verify")}
      </Button>
      {result ? (
        <StatusChip tone={result.found ? "success" : "warning"}>{result.text}</StatusChip>
      ) : null}
    </div>
  );
}

/**
 * Customers and Suppliers are one screen: `partners` is one table with `is_customer` /
 * `is_supplier` flags, so the role only decides which code column is in play, which settings
 * row is edited and which `arap.role.*` message block supplies the copy. A partner that is
 * already the other role keeps that role untouched — nothing here ever clears the other code.
 */
export function PartnersScreen({ role }: { role: PartnerRole }) {
  const t = useTranslations("arap.partners");
  const tc = useTranslations("arap.common");
  const tr = useTranslations(`arap.role.${role}`);
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();
  const canEdit = hasPermission(`${role}:setup_manage`);

  const [search, setSearch] = useState("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState<DetailsForm>(BLANK);
  const [selected, setSelected] = useState<Partner | null>(null);
  const [detailsForm, setDetailsForm] = useState<DetailsForm>(BLANK);

  const partners = usePartners(role, { includeInactive });
  const currencies = useCurrencies();
  const currencyById = byId(currencies.data);
  const createPartner = useCreatePartner(role);
  const updatePartner = useUpdatePartner(role);

  // Searching client-side keeps the list responsive; the API's `search` covers the same
  // three fields when the list outgrows a single page.
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const list = partners.data ?? [];
    if (!needle) return list;
    return list.filter((p) =>
      [p.name, partnerCode(p, role), p.tin, p.email]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle)),
    );
  }, [partners.data, search, role]);

  const codeField = role === "ar" ? "customer_code" : "supplier_code";
  const currencyOptions = [
    { value: "", label: t("baseCurrency") },
    ...toOptions(currencies.data ?? [], (c) => `${c.code} · ${c.name}`),
  ];

  function openDetail(partner: Partner) {
    setSelected(partner);
    setDetailsForm({
      name: partner.name,
      code: partnerCode(partner, role) ?? "",
      tin: partner.tin ?? "",
      email: partner.email ?? "",
      phone: partner.phone ?? "",
      notes: partner.notes ?? "",
      currencyId: partner.currency_id ? String(partner.currency_id) : "",
      bankName: partner.bank_name ?? "",
      bankAccountNumber: partner.bank_account_number ?? "",
      bankAccountHolder: partner.bank_account_holder ?? "",
    });
  }

  async function handleCreate() {
    try {
      const created = await createPartner.mutateAsync({
        name: form.name,
        [codeField]: form.code,
        tin: form.tin || null,
        email: form.email || null,
        phone: form.phone || null,
        notes: form.notes || null,
        currency_id: form.currencyId ? Number(form.currencyId) : null,
      });
      toast.show({ title: tr("created"), description: created.name, tone: "success" });
      setCreateOpen(false);
      setForm(BLANK);
      openDetail(created);
    } catch (err) {
      showApiError(err, tr("createFailed"));
    }
  }

  async function handleSaveDetails() {
    if (!selected) return;
    try {
      const updated = await updatePartner.mutateAsync({
        partnerId: selected.id,
        payload: {
          name: detailsForm.name,
          [codeField]: detailsForm.code,
          tin: detailsForm.tin,
          email: detailsForm.email,
          phone: detailsForm.phone,
          notes: detailsForm.notes,
          ...(detailsForm.currencyId
            ? { currency_id: Number(detailsForm.currencyId) }
            : { clear_currency: true }),
          // A supplier's bank details are one fact and are sent as one: the flag with the three
          // values replaces all three, so a field blanked here is blank on the next read.
          ...(role === "ap"
            ? {
                clear_bank_details: true,
                bank_name: detailsForm.bankName.trim() || null,
                bank_account_number: detailsForm.bankAccountNumber.trim() || null,
                bank_account_holder: detailsForm.bankAccountHolder.trim() || null,
              }
            : {}),
        },
      });
      setSelected(updated);
      toast.show({ title: tr("saved"), tone: "success" });
    } catch (err) {
      showApiError(err, t("saveFailed"));
    }
  }

  async function toggleActive(partner: Partner) {
    try {
      await updatePartner.mutateAsync({
        partnerId: partner.id,
        payload: { is_active: !partner.is_active },
      });
      toast.show({
        title: partner.name,
        description: partner.is_active ? tc("deactivated") : tc("activated"),
        tone: "success",
      });
    } catch (err) {
      showApiError(err, tr("updateFailed"));
    }
  }

  return (
    <MaintenancePage
      title={tr("partners")}
      description={tr("subtitle")}
      actions={
        <Button
          variant="primary"
          onClick={() => {
            setForm(BLANK);
            setCreateOpen(true);
          }}
          disabled={!canEdit}
          className="gap-1.5 text-xs"
        >
          <Plus className="size-3.5" /> {tr("newPartner")}
        </Button>
      }
    >
      <MaintenanceCard
        icon={<Users className="size-4" />}
        title={tr("partners")}
        actions={
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs text-[var(--vinea-ink-muted)]">
              <input
                type="checkbox"
                checked={includeInactive}
                onChange={(e) => setIncludeInactive(e.target.checked)}
                className="size-3.5"
              />
              {tc("showInactive")}
            </label>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-[var(--vinea-ink-subtle)]" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                aria-label={tr("searchLabel")}
                placeholder={t("searchPlaceholder")}
                // `[data-density=dense]` re-sets px-2 on Input, so the icon gutter needs the
                // same variant or the placeholder runs under the magnifier.
                className="w-56 pl-7 [[data-density=dense]_&]:pl-7"
              />
            </div>
          </div>
        }
      >
        {rows.length === 0 ? (
          <QueryState query={partners} isEmpty empty={tr("noneMatch")} testId="query" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-32">{tr("code")}</TH>
                <TH>{tc("name")}</TH>
                <TH className="w-28">{t("tin")}</TH>
                <TH>{tc("email")}</TH>
                <TH className="w-24">{t("currency")}</TH>
                <TH className="w-32 text-right">{tc("status")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((partner) => {
                const currency = partner.currency_id ? currencyById.get(partner.currency_id) : undefined;
                return (
                  <TR key={partner.id}>
                    <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                      {partnerCode(partner, role)}
                    </TD>
                    <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                      <span className="inline-flex items-center gap-2">
                        {partner.name}
                        {partner.is_customer && partner.is_supplier && (
                          <StatusChip tone="info">{t("bothRoles")}</StatusChip>
                        )}
                      </span>
                    </TD>
                    <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                      {partner.tin ?? tc("emptyValue")}
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {partner.email ?? tc("emptyValue")}
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {currency?.code ?? t("baseCurrency")}
                    </TD>
                    <TD className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => openDetail(partner)}
                          aria-label={tc("editLabel", { name: partner.name })}
                          className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                        >
                          <Edit2 className="size-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => toggleActive(partner)}
                          disabled={!canEdit}
                          aria-label={tc(partner.is_active ? "deactivateLabel" : "activateLabel", {
                            name: partner.name,
                          })}
                        >
                          <StatusChip tone={partner.is_active ? "success" : "neutral"}>
                            {partner.is_active ? tc("active") : tc("inactive")}
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
        <p className="text-xs text-[var(--vinea-ink-subtle)]">
          {t.rich("renameHint", {
            link: () => (
              <Link
                href={`/maintenance/rename-partner-code?role=${role}`}
                className="text-[var(--vinea-brand)] underline"
              >
                {tr("renameLink")}
              </Link>
            ),
          })}
        </p>
      </MaintenanceCard>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent title={tr("newPartner")}>
          <div className="space-y-3 pt-2">
            <Field label={tr("code")}>
              <Input
                value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value })}
                className="font-mono"
                placeholder={tr("codePlaceholder")}
              />
            </Field>
            <Field label={tc("name")}>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder={t("namePlaceholder")}
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("tin")}>
                <Input
                  value={form.tin}
                  onChange={(e) => setForm({ ...form, tin: e.target.value })}
                  className="font-mono"
                />
                <VerifyTin tin={form.tin} />
              </Field>
              <Field label={t("currency")}>
                <Combobox
                  options={currencyOptions}
                  value={form.currencyId}
                  onValueChange={(v) => setForm({ ...form, currencyId: v })}
                  placeholder={t("baseCurrency")}
                />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label={tc("email")}>
                <Input
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                />
              </Field>
              <Field label={tc("phone")}>
                <Input
                  value={form.phone}
                  onChange={(e) => setForm({ ...form, phone: e.target.value })}
                />
              </Field>
            </div>
            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setCreateOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button
                variant="primary"
                disabled={!form.code || !form.name || createPartner.isPending}
                onClick={handleCreate}
              >
                {tc("create")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Drawer open={selected !== null} onOpenChange={(open) => !open && setSelected(null)}>
        {selected && (
          <DrawerContent
            title={selected.name}
            description={`${tr("code")} ${partnerCode(selected, role) ?? tc("emptyValue")}`}
          >
            <Tabs defaultValue="details">
              <TabsList className="mb-4">
                <TabsTrigger value="details">{t("detailsTab")}</TabsTrigger>
                <TabsTrigger value="settings">{tr("settingsTab")}</TabsTrigger>
                <TabsTrigger value="contacts">{t("contactsTab")}</TabsTrigger>
              </TabsList>

              <TabsContent value="details" className="space-y-3">
                <Field label={tr("code")}>
                  <Input
                    value={detailsForm.code}
                    onChange={(e) => setDetailsForm({ ...detailsForm, code: e.target.value })}
                    className="font-mono"
                  />
                </Field>
                <Field label={tc("name")}>
                  <Input
                    value={detailsForm.name}
                    onChange={(e) => setDetailsForm({ ...detailsForm, name: e.target.value })}
                  />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label={t("tin")}>
                    <Input
                      value={detailsForm.tin}
                      onChange={(e) => setDetailsForm({ ...detailsForm, tin: e.target.value })}
                      className="font-mono"
                    />
                    <VerifyTin tin={detailsForm.tin} />
                  </Field>
                  <Field label={t("currency")}>
                    <Combobox
                      options={currencyOptions}
                      value={detailsForm.currencyId}
                      onValueChange={(v) => setDetailsForm({ ...detailsForm, currencyId: v })}
                      placeholder={t("baseCurrency")}
                    />
                  </Field>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <Field label={tc("email")}>
                    <Input
                      type="email"
                      value={detailsForm.email}
                      onChange={(e) => setDetailsForm({ ...detailsForm, email: e.target.value })}
                    />
                  </Field>
                  <Field label={tc("phone")}>
                    <Input
                      value={detailsForm.phone}
                      onChange={(e) => setDetailsForm({ ...detailsForm, phone: e.target.value })}
                    />
                  </Field>
                </div>
                <Field label={tc("notes")}>
                  <Input
                    value={detailsForm.notes}
                    onChange={(e) => setDetailsForm({ ...detailsForm, notes: e.target.value })}
                  />
                </Field>
                {role === "ap" ? (
                  // P8 step 6. What a payment run's instruction file prints for this supplier —
                  // beneficiary, bank, account number. A supplier with none is still paid; the
                  // run warns `bank_details_missing` and the file carries the row with the
                  // fields empty, because the accountant has to know whom to key by hand.
                  <section
                    aria-labelledby="supplier-bank-details"
                    className="space-y-3 border-t border-[var(--vinea-border)] pt-3"
                  >
                    <h3
                      id="supplier-bank-details"
                      className="text-sm font-semibold text-[var(--vinea-ink)]"
                    >
                      {t("bankDetails")}
                    </h3>
                    <div className="grid grid-cols-2 gap-3">
                      <Field label={t("supplierBankName")}>
                        <Input
                          value={detailsForm.bankName}
                          onChange={(e) =>
                            setDetailsForm({ ...detailsForm, bankName: e.target.value })
                          }
                        />
                      </Field>
                      <Field label={t("supplierBankAccountNumber")}>
                        <Input
                          value={detailsForm.bankAccountNumber}
                          onChange={(e) =>
                            setDetailsForm({ ...detailsForm, bankAccountNumber: e.target.value })
                          }
                          className="font-mono"
                        />
                      </Field>
                    </div>
                    <Field label={t("supplierBankAccountHolder")}>
                      <Input
                        value={detailsForm.bankAccountHolder}
                        onChange={(e) =>
                          setDetailsForm({ ...detailsForm, bankAccountHolder: e.target.value })
                        }
                      />
                    </Field>
                    <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("bankDetailsNote")}</p>
                  </section>
                ) : null}
                <div className="flex justify-end pt-2">
                  <Button
                    variant="primary"
                    disabled={!canEdit || updatePartner.isPending}
                    onClick={handleSaveDetails}
                  >
                    {updatePartner.isPending ? tc("saving") : t("saveDetails")}
                  </Button>
                </div>
              </TabsContent>

              <TabsContent value="settings">
                <PartnerSettingsForm role={role} partnerId={selected.id} canEdit={canEdit} />
              </TabsContent>

              <TabsContent value="contacts">
                <PartnerContactsPanel role={role} partnerId={selected.id} canEdit={canEdit} />
              </TabsContent>
            </Tabs>
          </DrawerContent>
        )}
      </Drawer>
    </MaintenancePage>
  );
}
