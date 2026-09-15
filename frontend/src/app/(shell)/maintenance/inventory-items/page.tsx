"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Boxes, Edit2, Package, Plus, ScanBarcode, Trash2 } from "lucide-react";
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
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { useAccounts, useCurrencies, useTaxCodes } from "@/features/gl/hooks";
import {
  useCreateBarcode,
  useCreateItem,
  useItemBarcodes,
  useItems,
  useKitComponents,
  useSaveKitComponents,
  useUomCategories,
  useUpdateBarcode,
  useUpdateItem,
} from "@/features/inventory/hooks";
import type { Barcode, Item, UomCategoryWithUnits } from "@/features/inventory/types";
import { ControlType, ItemType } from "@/lib/api-enums";
import {
  dotted,
  formatMoney,
  formatQuantity,
  trimDecimalString,
  type CurrencyLike,
} from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

/** Every type an item can be created as. `KIT` joins them at P6: a kit is a sellable
 * assembly with a price of its own that never has a stock move — it explodes into its
 * components at line entry, and those are what move (decision 8). */
const ITEM_TYPES: readonly ItemType[] = [
  ItemType.STOCK,
  ItemType.SERVICE,
  ItemType.NON_STOCK,
  ItemType.KIT,
];

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
  const tk = useTranslations("inventory.kits");
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
  const [purchaseAccountId, setPurchaseAccountId] = useState("");
  const [weightPerBaseUnit, setWeightPerBaseUnit] = useState("");
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
    setPurchaseAccountId("");
    setWeightPerBaseUnit("");
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
    setPurchaseAccountId(item.purchase_account_id ? String(item.purchase_account_id) : "");
    setWeightPerBaseUnit(
      item.weight_per_base_unit ? trimDecimalString(item.weight_per_base_unit) : "",
    );
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
            ...(purchaseAccountId
              ? { purchase_account_id: Number(purchaseAccountId) }
              : { clear_purchase_account: true }),
            ...(weightPerBaseUnit
              ? { weight_per_base_unit: weightPerBaseUnit }
              : { clear_weight_per_base_unit: true }),
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
          purchase_account_id: purchaseAccountId ? Number(purchaseAccountId) : null,
          weight_per_base_unit: weightPerBaseUnit || null,
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
                {/* Only a kit has one. Every other item type would show an empty section
                    and an endpoint that refuses the save with `not_a_kit`. */}
                {selected.item_type === ItemType.KIT ? (
                  <TabsTrigger value="kit">{tk("tabTitle")}</TabsTrigger>
                ) : null}
              </TabsList>
              <TabsContent value="details">
                <ItemDetails item={selected} uomName={uomName} />
              </TabsContent>
              <TabsContent value="barcodes">
                <BarcodePanel item={selected} canEdit={canEdit} />
              </TabsContent>
              {selected.item_type === ItemType.KIT ? (
                <TabsContent value="kit">
                  <KitComponentsPanel
                    kit={selected}
                    canEdit={canEdit}
                    categories={categories.data ?? []}
                  />
                </TabsContent>
              ) : null}
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
              {/* Where an AP line for a service or non-stock item lands. A stock item's AP
                  line goes to the GRN accrual instead, so this is left unset on one. */}
              <Field label={t("purchaseAccount")}>
                <Combobox
                  options={[
                    { value: "", label: t("chooseAccount") },
                    ...ordinaryAccounts.map((a) => ({
                      value: String(a.id),
                      label: dotted(a.code, a.name),
                    })),
                  ]}
                  value={purchaseAccountId}
                  onValueChange={setPurchaseAccountId}
                  placeholder={t("chooseAccount")}
                />
              </Field>
              <Field label={t("weightPerBaseUnit")}>
                <Input
                  value={weightPerBaseUnit}
                  onChange={(e) => setWeightPerBaseUnit(e.target.value)}
                  inputMode="decimal"
                  className="text-right font-mono"
                />
              </Field>
            </div>
            <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("weightNote")}</p>
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

/**
 * A kit's definition — the components one kit explodes into (P6 decision 8).
 *
 * **Saved whole, never row by row.** `PUT /inventory/items/{id}/kit-components` replaces the
 * definition in one request because a kit is only meaningful as a set: "2 × bottle + 1 × box"
 * is one fact, and a screen saving it a row at a time would leave the kit briefly wrong
 * between two requests and permanently wrong if the second failed. So the table below is a
 * *reading* of the saved definition and the editor is a separate mode over a local draft,
 * rather than a grid that writes as it is typed.
 *
 * Editing changes what the **next** order line explodes into and restates nothing: orders
 * already taken keep the component lines they were keyed with, which is what lets Breakup
 * edit one order without the catalogue moving under it.
 */
function KitComponentsPanel({
  kit,
  canEdit,
  categories,
}: {
  kit: Item;
  canEdit: boolean;
  categories: UomCategoryWithUnits[];
}) {
  const tk = useTranslations("inventory.kits");
  const tc = useTranslations("inventory.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();

  const components = useKitComponents(kit.id);
  const items = useItems({});
  const saveComponents = useSaveKitComponents();

  const [editing, setEditing] = useState(false);
  const [rows, setRows] = useState<Array<{ itemId: string; quantity: string }>>([]);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});

  /** `components.2.component_item_id` → row 2's item cell. The service names the row and the
   * field it refused — a duplicate component, a nested kit, a quantity of zero — and the
   * message belongs on that cell rather than in a toast that leaves the operator hunting
   * through five rows for the one that is wrong. */
  const rowErrors = useMemo(() => {
    const out: Record<number, Record<string, string>> = {};
    for (const [key, messages] of Object.entries(fieldErrors)) {
      const match = /^components\.(\d+)\.(.+)$/.exec(key);
      if (match) {
        out[Number(match[1])] = { ...out[Number(match[1])], [match[2]]: messages[0] };
      }
    }
    return out;
  }, [fieldErrors]);

  const byId = useMemo(
    () => new Map((items.data ?? []).map((item) => [item.id, item])),
    [items.data],
  );

  /** A component is any active item that is not itself a kit and not this kit — kits do not
   * nest (`nested_kit`) and a kit cannot contain itself (`kit_is_its_own_component`). */
  const choices = useMemo(
    () =>
      (items.data ?? []).filter(
        (item) => item.is_active && item.item_type !== ItemType.KIT && item.id !== kit.id,
      ),
    [items.data, kit.id],
  );

  /** The unit a component's quantity is counted in, with the decimal places the *unit*
   * declares — EA is 0, KG is 3. Never a hard-coded scale: the raw column holds
   * "2.000000" and that is not what anyone should be shown. */
  function uom(itemId: number): { code: string; decimalPlaces: number } {
    const item = byId.get(itemId);
    for (const category of categories) {
      const found = category.uoms.find((u) => u.id === item?.base_uom_id);
      if (found) return { code: found.code, decimalPlaces: found.decimal_places };
    }
    return { code: tc("emptyValue"), decimalPlaces: 0 };
  }

  function itemLabel(itemId: number): string {
    const item = byId.get(itemId);
    return item ? dotted(item.code, item.name) : String(itemId);
  }

  function startEditing() {
    setFieldErrors({});
    setRows(
      (components.data ?? []).map((row) => ({
        itemId: String(row.component_item_id),
        quantity: trimDecimalString(row.quantity_per_kit),
      })),
    );
    setEditing(true);
  }

  async function handleSave() {
    setFieldErrors({});
    try {
      await saveComponents.mutateAsync({
        itemId: kit.id,
        payload: {
          components: rows
            .filter((row) => row.itemId && row.quantity)
            .map((row) => ({
              component_item_id: Number(row.itemId),
              quantity_per_kit: row.quantity,
            })),
        },
      });
      toast.show({ title: tk("saved"), tone: "success" });
      setEditing(false);
    } catch (err) {
      if (isApiError(err)) setFieldErrors(err.fieldErrors);
      showApiError(err, tk("saveFailed"));
    }
  }

  const saved = components.data ?? [];

  return (
    <div className="space-y-3 pt-3">
      <div className="flex items-center justify-between">
        <p className="flex items-center gap-1.5 text-xs text-[var(--vinea-ink-muted)]">
          <Package className="size-4" />
          {tk("sectionTitle")}
        </p>
        {editing ? (
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={() => setEditing(false)} className="text-xs">
              {tc("cancel")}
            </Button>
            <Button
              variant="primary"
              onClick={handleSave}
              disabled={!canEdit || saveComponents.isPending}
              className="text-xs"
            >
              {saveComponents.isPending ? tc("saving") : tc("save")}
            </Button>
          </div>
        ) : (
          <Button
            variant="ghost"
            onClick={startEditing}
            disabled={!canEdit}
            className="gap-1.5 text-xs"
          >
            <Edit2 className="size-3.5" /> {tk("editDefinition")}
          </Button>
        )}
      </div>

      {editing ? (
        <div className="space-y-2">
          {rows.map((row, index) => (
            <div key={index} className="flex items-end gap-2">
              <Field
                label={tk("component")}
                error={rowErrors[index]?.component_item_id}
                className="flex-1"
              >
                <Combobox
                  options={choices.map((item) => ({
                    value: String(item.id),
                    label: dotted(item.code, item.name),
                  }))}
                  value={row.itemId}
                  onValueChange={(value) =>
                    setRows((prev) =>
                      prev.map((r, i) => (i === index ? { ...r, itemId: value } : r)),
                    )
                  }
                  placeholder={tk("chooseComponent")}
                />
              </Field>
              <Field
                label={tk("quantityPerKit")}
                error={rowErrors[index]?.quantity_per_kit}
                className="w-28"
              >
                <Input
                  value={row.quantity}
                  onChange={(e) =>
                    setRows((prev) =>
                      prev.map((r, i) =>
                        i === index ? { ...r, quantity: e.target.value } : r,
                      ),
                    )
                  }
                  inputMode="decimal"
                  className="text-right font-mono"
                />
              </Field>
              <button
                type="button"
                onClick={() => setRows((prev) => prev.filter((_, i) => i !== index))}
                aria-label={tk("removeRow", {
                  name: row.itemId ? itemLabel(Number(row.itemId)) : String(index + 1),
                })}
                className="mb-2 rounded p-1 text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-danger)]"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))}
          <Button
            variant="ghost"
            onClick={() => setRows((prev) => [...prev, { itemId: "", quantity: "1" }])}
            className="gap-1.5 text-xs"
          >
            <Plus className="size-3.5" /> {tk("addRow")}
          </Button>
        </div>
      ) : saved.length === 0 ? (
        <p className="py-6 text-center text-xs text-[var(--vinea-ink-subtle)]">
          {components.isLoading ? tc("loading") : tk("empty")}
        </p>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>{tk("component")}</TH>
              <TH className="w-20">{tk("baseUom")}</TH>
              <TH className="w-24 text-right">{tk("quantityPerKit")}</TH>
            </TR>
          </THead>
          <TBody>
            {saved.map((row) => (
              <TR key={row.id}>
                <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                  {itemLabel(row.component_item_id)}
                </TD>
                <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                  {uom(row.component_item_id).code}
                </TD>
                <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]">
                  {formatQuantity(
                    Number(row.quantity_per_kit),
                    uom(row.component_item_id).decimalPlaces,
                  )}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}

      {!editing && saved.length > 0 ? (
        <p className="text-xs text-[var(--vinea-ink-subtle)]">
          {tk("explodesTo", { count: saved.length })}
        </p>
      ) : null}
      <p className="text-xs text-[var(--vinea-ink-subtle)]">{tk("note")}</p>
    </div>
  );
}
