"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Boxes, Edit2, Plus, ScanBarcode } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Drawer, DrawerContent } from "@/design/components/drawer";
import { Field, Input } from "@/design/components/input";
import { MaintenanceCard, MaintenancePage } from "@/design/components/maintenance-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/design/components/tabs";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import { useAccounts, useCurrencies, useTaxCodes } from "@/features/gl/hooks";
import {
  useCreateBarcode,
  useCreateItem,
  useItemBarcodes,
  useItems,
  useUomCategories,
  useUpdateBarcode,
  useUpdateItem,
} from "@/features/inventory/hooks";
import type { Barcode, Item } from "@/features/inventory/types";
import { ControlType, ItemType } from "@/lib/api-enums";
import { dotted, formatMoney, trimDecimalString, type CurrencyLike } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

const ITEM_TYPES: readonly ItemType[] = [ItemType.STOCK, ItemType.SERVICE, ItemType.NON_STOCK];

/**
 * The company's base currency, as `formatMoney` wants it.
 *
 * A selling price arrives as the raw `NUMERIC(20,6)` string the column holds — "8500.000000".
 * Printing that is the P4 defect class exactly: a screen that renders perfectly and shows an
 * RWF price with six decimal places it does not have. Money renders through `formatMoney`
 * with the currency's own `decimal_places`, here as everywhere else.
 */
function useBaseCurrency(): CurrencyLike {
  const currencies = useCurrencies();
  const base = (currencies.data ?? []).find((c) => c.is_base);
  return {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };
}


/**
 * The item catalogue (P5 decision 8).
 *
 * Two things make this more than a code-and-name master. The **unit of measure** decides what
 * a quantity means: an item's base unit is the unit its moves are counted in, and every other
 * unit in the same category converts to it. The **barcodes** are how a scan becomes a
 * quantity — a code can stand for a single unit or a whole pack, which is why each one
 * carries a unit and a pack quantity of its own.
 *
 * Only stock items ever carry a quantity. Service and non-stock items are here because they
 * go on documents; they never reach `stock_moves`.
 */
export default function InventoryItemsPage() {
  const t = useTranslations("inventory.items");
  const tb = useTranslations("inventory.barcodes");
  const tc = useTranslations("inventory.common");
  const tt = useTranslations("inventory.itemTypes");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canEdit = useHasPermission()("inv:setup_manage");

  const [search, setSearch] = useState("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const items = useItems({ search, includeInactive });
  const baseCurrency = useBaseCurrency();
  const categories = useUomCategories();
  const accounts = useAccounts();
  const taxCodes = useTaxCodes();
  const createItem = useCreateItem();
  const updateItem = useUpdateItem();

  const [selected, setSelected] = useState<Item | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Item | null>(null);

  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [itemType, setItemType] = useState<ItemType>(ItemType.STOCK);
  const [categoryId, setCategoryId] = useState("");
  const [baseUomId, setBaseUomId] = useState("");
  const [sellingPrice, setSellingPrice] = useState("0");
  const [priceIncludesTax, setPriceIncludesTax] = useState(false);
  const [inventoryAccountId, setInventoryAccountId] = useState("");
  const [salesAccountId, setSalesAccountId] = useState("");
  const [cogsAccountId, setCogsAccountId] = useState("");
  const [salesTaxCodeId, setSalesTaxCodeId] = useState("");
  const [purchaseTaxCodeId, setPurchaseTaxCodeId] = useState("");

  const usableAccounts = useMemo(
    () => (accounts.data ?? []).filter((a) => a.is_postable && a.is_active),
    [accounts.data],
  );
  const inventoryControl = useMemo(
    () => usableAccounts.filter((a) => a.control_type === ControlType.INVENTORY),
    [usableAccounts],
  );
  const ordinaryAccounts = useMemo(
    () => usableAccounts.filter((a) => !a.is_control),
    [usableAccounts],
  );
  const categoryUoms = useMemo(() => {
    const category = (categories.data ?? []).find((c) => String(c.id) === categoryId);
    return category?.uoms.filter((u) => u.is_active) ?? [];
  }, [categories.data, categoryId]);

  // Switching category invalidates whatever base unit was picked from the old one — the
  // service refuses a base unit from another category, so offering it would only earn a 409.
  useEffect(() => {
    if (baseUomId && !categoryUoms.some((u) => String(u.id) === baseUomId)) setBaseUomId("");
  }, [categoryUoms, baseUomId]);

  const uomName = (id: number) => {
    for (const category of categories.data ?? []) {
      const uom = category.uoms.find((u) => u.id === id);
      if (uom) return uom.code;
    }
    return tc("emptyValue");
  };

  function startCreate() {
    setEditing(null);
    setCode("");
    setName("");
    setDescription("");
    setItemType(ItemType.STOCK);
    setCategoryId("");
    setBaseUomId("");
    setSellingPrice("0");
    setPriceIncludesTax(false);
    setInventoryAccountId("");
    setSalesAccountId("");
    setCogsAccountId("");
    setSalesTaxCodeId("");
    setPurchaseTaxCodeId("");
    setOpen(true);
  }

  function startEdit(item: Item) {
    setEditing(item);
    setCode(item.code);
    setName(item.name);
    setDescription(item.description ?? "");
    setItemType(item.item_type);
    setCategoryId(String(item.uom_category_id));
    setBaseUomId(String(item.base_uom_id));
    setSellingPrice(trimDecimalString(item.selling_price));
    setPriceIncludesTax(item.price_includes_tax);
    setInventoryAccountId(item.inventory_account_id ? String(item.inventory_account_id) : "");
    setSalesAccountId(item.sales_account_id ? String(item.sales_account_id) : "");
    setCogsAccountId(item.cogs_account_id ? String(item.cogs_account_id) : "");
    setSalesTaxCodeId(
      item.default_sales_tax_code_id ? String(item.default_sales_tax_code_id) : "",
    );
    setPurchaseTaxCodeId(
      item.default_purchase_tax_code_id ? String(item.default_purchase_tax_code_id) : "",
    );
    setOpen(true);
  }

  async function handleSave() {
    try {
      if (editing) {
        // `clear_*` rather than `null`: a PATCH body cannot tell "unset this" from "leave it
        // alone" with null alone, which is what those flags exist for.
        await updateItem.mutateAsync({
          itemId: editing.id,
          payload: {
            code,
            name,
            description: description || null,
            selling_price: sellingPrice,
            price_includes_tax: priceIncludesTax,
            ...(inventoryAccountId
              ? { inventory_account_id: Number(inventoryAccountId) }
              : { clear_inventory_account: true }),
            ...(salesAccountId
              ? { sales_account_id: Number(salesAccountId) }
              : { clear_sales_account: true }),
            ...(cogsAccountId
              ? { cogs_account_id: Number(cogsAccountId) }
              : { clear_cogs_account: true }),
            ...(salesTaxCodeId
              ? { default_sales_tax_code_id: Number(salesTaxCodeId) }
              : { clear_sales_tax_code: true }),
            ...(purchaseTaxCodeId
              ? { default_purchase_tax_code_id: Number(purchaseTaxCodeId) }
              : { clear_purchase_tax_code: true }),
          },
        });
        toast.show({ title: t("updated"), tone: "success" });
      } else {
        const created = await createItem.mutateAsync({
          code,
          name,
          description: description || null,
          item_type: itemType,
          uom_category_id: Number(categoryId),
          base_uom_id: Number(baseUomId),
          selling_price: sellingPrice,
          price_includes_tax: priceIncludesTax,
          inventory_account_id: inventoryAccountId ? Number(inventoryAccountId) : null,
          sales_account_id: salesAccountId ? Number(salesAccountId) : null,
          cogs_account_id: cogsAccountId ? Number(cogsAccountId) : null,
          default_sales_tax_code_id: salesTaxCodeId ? Number(salesTaxCodeId) : null,
          default_purchase_tax_code_id: purchaseTaxCodeId ? Number(purchaseTaxCodeId) : null,
        });
        toast.show({ title: t("created"), tone: "success" });
        // Straight into the drawer, so the barcodes tab is one click from creating the item
        // rather than a hunt back through the list.
        setSelected(created);
      }
      setOpen(false);
    } catch (err) {
      showApiError(err, t("saveFailed"));
    }
  }

  async function toggleActive(item: Item) {
    try {
      await updateItem.mutateAsync({ itemId: item.id, payload: { is_active: !item.is_active } });
      toast.show({
        title: item.code,
        description: item.is_active ? tc("deactivated") : tc("activated"),
        tone: "success",
      });
    } catch (err) {
      showApiError(err, t("updateFailed"));
    }
  }

  const rows = items.data ?? [];

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      actions={
        <Button
          variant="primary"
          onClick={startCreate}
          disabled={!canEdit}
          className="gap-1.5 text-xs"
        >
          <Plus className="size-3.5" /> {t("new")}
        </Button>
      }
    >
      <MaintenanceCard
        icon={<Boxes className="size-4" />}
        title={t("title")}
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
        <div className="pb-3">
          <Field label={tc("search")}>
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("searchPlaceholder")}
            />
          </Field>
        </div>

        {rows.length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {items.isLoading ? tc("loading") : t("empty")}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-32">{tc("code")}</TH>
                <TH>{tc("name")}</TH>
                <TH className="w-28">{t("itemType")}</TH>
                <TH className="w-24">{t("baseUom")}</TH>
                <TH className="w-32 text-right">{t("sellingPrice")}</TH>
                <TH className="w-32 text-right">{tc("status")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((item) => (
                <TR key={item.id}>
                  <TD>
                    <button
                      type="button"
                      onClick={() => setSelected(item)}
                      className="font-mono text-xs font-semibold text-[var(--vinea-brand)] hover:underline"
                    >
                      {item.code}
                    </button>
                  </TD>
                  <TD className="text-xs font-medium text-[var(--vinea-ink)]">{item.name}</TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">{tt(item.item_type)}</TD>
                  <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                    {uomName(item.base_uom_id)}
                  </TD>
                  <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]">
                    {formatMoney(Number(item.selling_price), baseCurrency, { showCode: false })}
                  </TD>
                  <TD className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => startEdit(item)}
                        aria-label={tc("editLabel", { name: item.name })}
                        className="rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
                      >
                        <Edit2 className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleActive(item)}
                        disabled={!canEdit}
                        aria-label={tc(item.is_active ? "deactivateLabel" : "activateLabel", {
                          name: item.name,
                        })}
                      >
                        <StatusChip tone={item.is_active ? "success" : "neutral"}>
                          {item.is_active ? tc("active") : tc("inactive")}
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

      <Drawer open={selected !== null} onOpenChange={(isOpen) => !isOpen && setSelected(null)}>
        {selected && (
          <DrawerContent
            title={selected.name}
            description={dotted(tc("code"), selected.code)}
          >
            <Tabs defaultValue="details">
              <TabsList>
                <TabsTrigger value="details">{t("tabDetails")}</TabsTrigger>
                <TabsTrigger value="barcodes">{t("tabBarcodes")}</TabsTrigger>
              </TabsList>
              <TabsContent value="details">
                <ItemDetails item={selected} uomName={uomName} />
              </TabsContent>
              <TabsContent value="barcodes">
                <BarcodePanel item={selected} canEdit={canEdit} />
              </TabsContent>
            </Tabs>
          </DrawerContent>
        )}
      </Drawer>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title={editing ? t("editTitle", { name: editing.name }) : t("newTitle")}>
          <div className="space-y-3 pt-2">
            <div className="grid grid-cols-2 gap-3">
              <Field label={tc("code")}>
                <Input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  className="font-mono"
                  placeholder={t("codePlaceholder")}
                />
              </Field>
              <Field label={t("itemType")}>
                {editing ? (
                  <p className="px-1 py-1.5 text-xs text-[var(--vinea-ink-muted)]">
                    {tt(editing.item_type)}
                  </p>
                ) : (
                  <Select
                    options={ITEM_TYPES.map((value) => ({ value, label: tt(value) }))}
                    value={itemType}
                    onValueChange={(value) => setItemType(value as ItemType)}
                  />
                )}
              </Field>
            </div>
            <Field label={tc("name")}>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("namePlaceholder")}
              />
            </Field>
            <Field label={t("description")}>
              <Input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t("descriptionPlaceholder")}
              />
            </Field>

            {editing ? null : (
              <div className="grid grid-cols-2 gap-3">
                <Field label={t("uomCategory")}>
                  <Combobox
                    options={(categories.data ?? []).map((category) => ({
                      value: String(category.id),
                      label: dotted(category.code, category.name),
                    }))}
                    value={categoryId}
                    onValueChange={setCategoryId}
                    placeholder={t("chooseCategory")}
                  />
                </Field>
                <Field label={t("baseUom")}>
                  <Combobox
                    options={categoryUoms.map((uom) => ({
                      value: String(uom.id),
                      label: dotted(uom.code, uom.name),
                    }))}
                    value={baseUomId}
                    onValueChange={setBaseUomId}
                    placeholder={t("chooseBaseUom")}
                  />
                </Field>
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <Field label={t("sellingPrice")}>
                <Input
                  value={sellingPrice}
                  onChange={(e) => setSellingPrice(e.target.value)}
                  inputMode="decimal"
                  className="text-right font-mono"
                />
              </Field>
              <label className="flex items-end gap-2 pb-2 text-xs text-[var(--vinea-ink-muted)]">
                <input
                  type="checkbox"
                  checked={priceIncludesTax}
                  onChange={(e) => setPriceIncludesTax(e.target.checked)}
                  className="size-3.5"
                />
                {t("priceIncludesTax")}
              </label>
            </div>

            <Field label={t("inventoryAccount")}>
              <Combobox
                options={[
                  { value: "", label: t("chooseAccount") },
                  ...inventoryControl.map((a) => ({
                    value: String(a.id),
                    label: dotted(a.code, a.name),
                  })),
                ]}
                value={inventoryAccountId}
                onValueChange={setInventoryAccountId}
                placeholder={t("chooseAccount")}
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("salesAccount")}>
                <Combobox
                  options={[
                    { value: "", label: t("chooseAccount") },
                    ...ordinaryAccounts.map((a) => ({
                      value: String(a.id),
                      label: dotted(a.code, a.name),
                    })),
                  ]}
                  value={salesAccountId}
                  onValueChange={setSalesAccountId}
                  placeholder={t("chooseAccount")}
                />
              </Field>
              <Field label={t("cogsAccount")}>
                <Combobox
                  options={[
                    { value: "", label: t("chooseAccount") },
                    ...ordinaryAccounts.map((a) => ({
                      value: String(a.id),
                      label: dotted(a.code, a.name),
                    })),
                  ]}
                  value={cogsAccountId}
                  onValueChange={setCogsAccountId}
                  placeholder={t("chooseAccount")}
                />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("salesTaxCode")}>
                <Combobox
                  options={[
                    { value: "", label: t("chooseTaxCode") },
                    ...(taxCodes.data ?? []).map((tax) => ({
                      value: String(tax.id),
                      label: dotted(tax.code, tax.name),
                    })),
                  ]}
                  value={salesTaxCodeId}
                  onValueChange={setSalesTaxCodeId}
                  placeholder={t("chooseTaxCode")}
                />
              </Field>
              <Field label={t("purchaseTaxCode")}>
                <Combobox
                  options={[
                    { value: "", label: t("chooseTaxCode") },
                    ...(taxCodes.data ?? []).map((tax) => ({
                      value: String(tax.id),
                      label: dotted(tax.code, tax.name),
                    })),
                  ]}
                  value={purchaseTaxCodeId}
                  onValueChange={setPurchaseTaxCodeId}
                  placeholder={t("chooseTaxCode")}
                />
              </Field>
            </div>

            <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("accountsNote")}</p>
            {editing ? (
              <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("lockedNote")}</p>
            ) : null}

            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button
                variant="primary"
                disabled={
                  !code || !name || !canEdit || (!editing && (!categoryId || !baseUomId))
                }
                onClick={handleSave}
              >
                {editing ? tc("save") : tc("create")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </MaintenancePage>
  );
}

function ItemDetails({ item, uomName }: { item: Item; uomName: (id: number) => string }) {
  const t = useTranslations("inventory.items");
  const tc = useTranslations("inventory.common");
  const tt = useTranslations("inventory.itemTypes");
  const baseCurrency = useBaseCurrency();
  const rows: Array<[string, string]> = [
    [t("itemType"), tt(item.item_type)],
    [t("baseUom"), uomName(item.base_uom_id)],
    [t("sellingPrice"), formatMoney(Number(item.selling_price), baseCurrency)],
    [t("description"), item.description ?? tc("emptyValue")],
  ];
  return (
    <dl className="space-y-2 pt-3">
      {rows.map(([label, value]) => (
        <div key={label} className="flex items-baseline justify-between gap-4">
          <dt className="text-xs uppercase tracking-wider text-[var(--vinea-ink-subtle)]">
            {label}
          </dt>
          <dd className="text-xs font-medium text-[var(--vinea-ink)]">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The barcodes of one item — created here, listed company-wide on the Barcodes screen. */
function BarcodePanel({ item, canEdit }: { item: Item; canEdit: boolean }) {
  const tb = useTranslations("inventory.barcodes");
  const tc = useTranslations("inventory.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const barcodes = useItemBarcodes(item.id);
  const categories = useUomCategories();
  const createBarcode = useCreateBarcode();
  const updateBarcode = useUpdateBarcode();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Barcode | null>(null);
  const [value, setValue] = useState("");
  const [uomId, setUomId] = useState("");
  const [pack, setPack] = useState("1");

  // Only units from the item's own category convert to its base — anything else is refused
  // by `_assert_uom_category` server-side.
  const uoms = useMemo(() => {
    const category = (categories.data ?? []).find((c) => c.id === item.uom_category_id);
    return category?.uoms.filter((u) => u.is_active) ?? [];
  }, [categories.data, item.uom_category_id]);

  const uomLabel = (id: number) => {
    const uom = uoms.find((u) => u.id === id);
    return uom ? uom.code : tc("emptyValue");
  };

  function startCreate() {
    setEditing(null);
    setValue("");
    setUomId(String(item.base_uom_id));
    setPack("1");
    setOpen(true);
  }

  function startEdit(barcode: Barcode) {
    setEditing(barcode);
    setValue(barcode.barcode);
    setUomId(String(barcode.uom_id));
    setPack(trimDecimalString(barcode.pack_quantity));
    setOpen(true);
  }

  async function handleSave() {
    try {
      if (editing) {
        await updateBarcode.mutateAsync({
          barcodeId: editing.id,
          payload: { uom_id: Number(uomId), pack_quantity: pack },
        });
        toast.show({ title: tb("updated"), tone: "success" });
      } else {
        await createBarcode.mutateAsync({
          itemId: item.id,
          payload: { barcode: value, uom_id: Number(uomId), pack_quantity: pack },
        });
        toast.show({ title: tb("created"), tone: "success" });
      }
      setOpen(false);
    } catch (err) {
      showApiError(err, tb("saveFailed"));
    }
  }

  const rows = barcodes.data ?? [];

  return (
    <div className="space-y-3 pt-3">
      <div className="flex items-center justify-between">
        <p className="flex items-center gap-1.5 text-xs text-[var(--vinea-ink-muted)]">
          <ScanBarcode className="size-4" />
          {tb("itemTitle")}
        </p>
        <Button
          variant="ghost"
          onClick={startCreate}
          disabled={!canEdit}
          className="gap-1.5 text-xs"
        >
          <Plus className="size-3.5" /> {tb("new")}
        </Button>
      </div>

      {rows.length === 0 ? (
        <p className="py-6 text-center text-xs text-[var(--vinea-ink-subtle)]">
          {barcodes.isLoading ? tc("loading") : tb("empty")}
        </p>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>{tb("barcode")}</TH>
              <TH className="w-24">{tb("uom")}</TH>
              <TH className="w-24 text-right">{tb("packQuantity")}</TH>
              <TH className="w-16 text-right">{tc("status")}</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((barcode) => (
              <TR key={barcode.id}>
                <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                  <button
                    type="button"
                    onClick={() => startEdit(barcode)}
                    aria-label={tc("editLabel", { name: barcode.barcode })}
                    className="hover:underline"
                  >
                    {barcode.barcode}
                  </button>
                </TD>
                <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                  {uomLabel(barcode.uom_id)}
                </TD>
                <TD className="text-right font-mono text-xs text-[var(--vinea-ink)]">
                  {trimDecimalString(barcode.pack_quantity)}
                </TD>
                <TD className="text-right">
                  <StatusChip tone={barcode.is_active ? "success" : "neutral"}>
                    {barcode.is_active ? tc("active") : tc("inactive")}
                  </StatusChip>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
      <p className="text-xs text-[var(--vinea-ink-subtle)]">{tb("uniqueNote")}</p>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          title={editing ? tb("editTitle", { barcode: editing.barcode }) : tb("newTitle")}
        >
          <div className="space-y-3 pt-2">
            <Field label={tb("barcode")}>
              <Input
                value={value}
                onChange={(e) => setValue(e.target.value)}
                disabled={!!editing}
                className="font-mono"
                placeholder={tb("barcodePlaceholder")}
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label={tb("uom")}>
                <Combobox
                  options={uoms.map((uom) => ({
                    value: String(uom.id),
                    label: dotted(uom.code, uom.name),
                  }))}
                  value={uomId}
                  onValueChange={setUomId}
                  placeholder={tb("chooseUom")}
                />
              </Field>
              <Field label={tb("packQuantity")}>
                <Input
                  value={pack}
                  onChange={(e) => setPack(e.target.value)}
                  inputMode="decimal"
                  className="text-right font-mono"
                />
              </Field>
            </div>
            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button variant="primary" disabled={!value || !uomId || !canEdit} onClick={handleSave}>
                {tc("save")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
