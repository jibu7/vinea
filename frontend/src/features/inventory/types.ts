/**
 * Inventory API types (P5 step 6) — the shapes `app/schemas/inventory.py` serialises.
 *
 * Money and quantities cross the wire as **strings**, never numbers: the backend's amounts are
 * `NUMERIC(20,6)` and factors `NUMERIC(20,10)`, and IEEE doubles cannot hold either without
 * rounding them. Anything that has to be arithmetic on this side is done on the string, and
 * anything that is only displayed stays a string all the way to `formatMoney`.
 */

export type ItemType = "stock" | "service" | "non_stock";
export type NegativeStockPolicy = "block" | "allow";

export interface Page<T> {
  items: T[];
  next_cursor: number | null;
}

// --- Units of measure -------------------------------------------------------------------

export interface Uom {
  id: number;
  category_id: number;
  code: string;
  name: string;
  /** How many base units one of these is. `"1.0000000000"` for the base unit itself. */
  factor_to_base: string;
  decimal_places: number;
  is_base: boolean;
  is_active: boolean;
}

export interface UomCategory {
  id: number;
  code: string;
  name: string;
  is_active: boolean;
}

export interface UomCategoryWithUnits extends UomCategory {
  uoms: Uom[];
}

/** A category always arrives with its base unit — a category with no base has no arithmetic. */
export interface UomCategoryCreatePayload {
  code: string;
  name: string;
  base_uom_code: string;
  base_uom_name: string;
  base_uom_decimal_places?: number;
}

export interface UomCategoryUpdatePayload {
  name?: string;
  is_active?: boolean;
}

export interface UomCreatePayload {
  category_id: number;
  code: string;
  name: string;
  factor_to_base: string;
  decimal_places?: number;
}

export interface UomUpdatePayload {
  name?: string;
  factor_to_base?: string;
  decimal_places?: number;
  is_active?: boolean;
}

// --- Items ------------------------------------------------------------------------------

export interface Item {
  id: number;
  code: string;
  name: string;
  description: string | null;
  item_type: ItemType;
  uom_category_id: number;
  base_uom_id: number;
  inventory_account_id: number | null;
  cogs_account_id: number | null;
  sales_account_id: number | null;
  default_sales_tax_code_id: number | null;
  default_purchase_tax_code_id: number | null;
  selling_price: string;
  price_includes_tax: boolean;
  is_active: boolean;
}

export interface ItemCreatePayload {
  code: string;
  name: string;
  uom_category_id: number;
  base_uom_id: number;
  item_type?: ItemType;
  description?: string | null;
  inventory_account_id?: number | null;
  cogs_account_id?: number | null;
  sales_account_id?: number | null;
  default_sales_tax_code_id?: number | null;
  default_purchase_tax_code_id?: number | null;
  selling_price?: string;
  price_includes_tax?: boolean;
}

/**
 * The `clear_*` flags exist because `null` in a PATCH body is indistinguishable from "field
 * omitted" once it round-trips through JSON: sending `inventory_account_id: null` means
 * "leave it alone", and `clear_inventory_account: true` means "unset it".
 */
export interface ItemUpdatePayload {
  code?: string;
  name?: string;
  description?: string | null;
  item_type?: ItemType;
  uom_category_id?: number;
  base_uom_id?: number;
  inventory_account_id?: number | null;
  clear_inventory_account?: boolean;
  cogs_account_id?: number | null;
  clear_cogs_account?: boolean;
  sales_account_id?: number | null;
  clear_sales_account?: boolean;
  default_sales_tax_code_id?: number | null;
  clear_sales_tax_code?: boolean;
  default_purchase_tax_code_id?: number | null;
  clear_purchase_tax_code?: boolean;
  selling_price?: string;
  price_includes_tax?: boolean;
  is_active?: boolean;
}

export interface ItemAuditRecord {
  id: number;
  at: string;
  action: string;
  actor_email: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

// --- Barcodes ---------------------------------------------------------------------------

export interface Barcode {
  id: number;
  item_id: number;
  barcode: string;
  uom_id: number;
  pack_quantity: string;
  is_active: boolean;
}

/** The company-wide listing: the same row with the item and unit it stands for resolved. */
export interface BarcodeListing {
  id: number;
  barcode: string;
  item_id: number;
  item_code: string;
  item_name: string;
  uom_id: number;
  uom_code: string;
  uom_name: string;
  pack_quantity: string;
  is_active: boolean;
}

export interface BarcodeCreatePayload {
  barcode: string;
  uom_id: number;
  pack_quantity?: string;
}

export interface BarcodeUpdatePayload {
  uom_id?: number;
  pack_quantity?: string;
  is_active?: boolean;
}

// --- Warehouses -------------------------------------------------------------------------

export interface Warehouse {
  id: number;
  code: string;
  name: string;
  branch_id: number;
  is_default: boolean;
  /** The one system location per company (decision 6). Never offered as a destination. */
  is_in_transit: boolean;
  is_active: boolean;
}

export interface WarehouseCreatePayload {
  code: string;
  name: string;
  branch_id: number;
  is_default?: boolean;
}

export interface WarehouseUpdatePayload {
  name?: string;
  branch_id?: number;
  is_default?: boolean;
  is_active?: boolean;
}

// --- Defaults ---------------------------------------------------------------------------

export interface InventoryDefaults {
  inventory_account_id: number | null;
  inventory_in_transit_account_id: number | null;
  inventory_adjustment_account_id: number | null;
  stock_count_variance_account_id: number | null;
  cogs_account_id: number | null;
  negative_stock_policy: NegativeStockPolicy;
  default_warehouse_id: number | null;
}

export interface InventoryDefaultsPayload {
  inventory_account_id?: number | null;
  inventory_in_transit_account_id?: number | null;
  inventory_adjustment_account_id?: number | null;
  stock_count_variance_account_id?: number | null;
  cogs_account_id?: number | null;
  negative_stock_policy?: NegativeStockPolicy;
  default_warehouse_id?: number | null;
}
