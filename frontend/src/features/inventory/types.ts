/**
 * Inventory API types (P5 step 6) — the shapes `app/schemas/inventory.py` serialises.
 *
 * Money and quantities cross the wire as **strings**, never numbers: the backend's amounts are
 * `NUMERIC(20,6)` and factors `NUMERIC(20,10)`, and IEEE doubles cannot hold either without
 * rounding them. Anything that has to be arithmetic on this side is done on the string, and
 * anything that is only displayed stays a string all the way to `formatMoney`.
 */

// Re-exported from the generated module rather than re-declared: a second hand-written copy
// of a wire union is the same class of defect as a hand-written literal. Imported as well as
// re-exported, because the interfaces below use them.
import type { ItemType, NegativeStockPolicy } from "@/lib/api-enums";

export type { ItemType, NegativeStockPolicy };

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

// --- Stock documents (adjustments and journal batches, P5 step 7) -------------------------

/**
 * One keyed line. `quantity` is a **magnitude**, never signed — the direction is the
 * transaction type's kind. A revaluation moves no quantity and states a signed `value`.
 */
export interface StockDocumentLinePayload {
  item_id: number;
  warehouse_id: number;
  quantity?: string;
  uom_id?: number | null;
  /** Required on an increase, refused on a decrease — an issue is costed at the average. */
  unit_cost?: string | null;
  /** A revaluation's signed amount. */
  value?: string | null;
  transaction_type_id?: number | null;
  contra_account_id?: number | null;
  project_id?: number | null;
  description?: string | null;
}

export interface StockDocumentPayload {
  document_date: string;
  description: string;
  reference?: string | null;
  transaction_type_id?: number | null;
  lines: StockDocumentLinePayload[];
}

export interface StockDocumentLine {
  id: number;
  line_no: number;
  item_id: number;
  warehouse_id: number;
  quantity: string;
  uom_id: number;
  quantity_base: string;
  unit_cost: string | null;
  value: string | null;
  transaction_type_id: number;
  contra_account_id: number | null;
  project_id: number | null;
  description: string | null;
  stock_move_id: number | null;
}

export interface StockDocumentSummary {
  id: number;
  doc_type: string;
  number: string;
  document_date: string;
  description: string;
  reference: string | null;
  status: string;
  /** Null when nothing in the posting carried value — a real and legal outcome. */
  journal_entry_id: number | null;
  reversal_entry_id: number | null;
  reverses_document_id: number | null;
}

export interface StockDocument extends StockDocumentSummary {
  transaction_type_id: number | null;
  lines: StockDocumentLine[];
}

export interface StockDocumentReversePayload {
  reversal_date: string;
  reason: string;
}

// --- Warehouse transfers ------------------------------------------------------------------

export interface TransferLinePayload {
  item_id: number;
  quantity: string;
  uom_id?: number | null;
  description?: string | null;
}

export interface TransferPayload {
  transfer_date: string;
  description: string;
  reference?: string | null;
  from_warehouse_id: number;
  to_warehouse_id: number;
  transaction_type_id?: number | null;
  project_id?: number | null;
  /** "Transfer now": both legs in one transaction. False dispatches only. */
  receive_now: boolean;
  lines: TransferLinePayload[];
}

export interface TransferLine {
  id: number;
  line_no: number;
  item_id: number;
  quantity: string;
  uom_id: number;
  quantity_base: string;
  description: string | null;
  dispatch_out_move_id: number | null;
  dispatch_in_move_id: number | null;
  receive_out_move_id: number | null;
  receive_in_move_id: number | null;
}

export interface TransferSummary {
  id: number;
  number: string;
  transfer_date: string;
  description: string;
  reference: string | null;
  from_warehouse_id: number;
  to_warehouse_id: number;
  status: string;
  dispatch_entry_id: number | null;
  receive_entry_id: number | null;
  dispatch_reversal_entry_id: number | null;
  receive_reversal_entry_id: number | null;
  received_date: string | null;
  undone_date: string | null;
}

export interface Transfer extends TransferSummary {
  transaction_type_id: number;
  project_id: number | null;
  lines: TransferLine[];
}

// --- Stock counts -------------------------------------------------------------------------

export interface CountSessionPayload {
  warehouse_id: number;
  count_date: string;
  description: string;
  reference?: string | null;
  transaction_type_id?: number | null;
  project_id?: number | null;
  include_items?: number[];
  include_zero_balances?: boolean;
}

export interface CountLine {
  id: number;
  line_no: number;
  item_id: number;
  system_quantity: string;
  counted_quantity: string | null;
  uom_id: number;
  counted_quantity_base: string | null;
  /** `counted - system` in the base unit; null while uncounted. */
  variance: string | null;
  /** The location has been posted to since this line was frozen (decision 7). */
  stale: boolean;
  snapshot_at: string;
  counted_at: string | null;
  note: string | null;
  stock_move_id: number | null;
}

export interface CountSessionSummary {
  id: number;
  number: string;
  warehouse_id: number;
  count_date: string;
  description: string;
  reference: string | null;
  status: string;
  snapshot_at: string;
  document_id: number | null;
  processed_at: string | null;
  cancelled_at: string | null;
}

export interface CountSession extends CountSessionSummary {
  transaction_type_id: number;
  project_id: number | null;
  lines: CountLine[];
}

export interface CountLineEntryPayload {
  /** `null` clears the line back to uncounted — not the same as counting it at zero. */
  counted_quantity: string | null;
  uom_id?: number | null;
  note?: string | null;
}

export interface CountPreviewLine {
  line_id: number;
  item_id: number;
  item_code: string;
  item_name: string;
  system_quantity: string;
  counted_quantity: string | null;
  variance: string | null;
  /** The item's current average — what decision 7 costs both gains and losses at. */
  unit_cost: string;
  value: string;
  counted: boolean;
  stale: boolean;
}

/** The posting before Process, as the allocation screen does it. */
export interface CountPreview {
  session_id: number;
  number: string;
  warehouse_id: number;
  count_date: string;
  total_value: string;
  counted_lines: number;
  uncounted_lines: number;
  variance_lines: number;
  stale_lines: number[];
  can_process: boolean;
  lines: CountPreviewLine[];
}

export interface CountProcessResult {
  session: CountSessionSummary;
  /** Null when every variance was zero: a count that agrees with the books posts nothing. */
  document: StockDocument | null;
}

// --- On hand ------------------------------------------------------------------------------

/** One `stock_balances` row: what a warehouse holds of an item, now. */
export interface OnHandRow {
  item_id: number;
  warehouse_id: number;
  quantity: string;
  value: string;
}

// --- Item enquiry (P5 step 8) ---------------------------------------------------------------

/** What one warehouse holds of the item as at the enquiry's `as_of`. */
export interface EnquiryLocation {
  warehouse_id: number;
  warehouse_code: string;
  warehouse_name: string;
  branch_id: number;
  is_in_transit: boolean;
  quantity: string;
  value: string;
}

/** One move, with every key the screen needs to drill onwards from it. */
export interface EnquiryMove {
  move_id: number;
  move_date: string;
  /** Posting order. Shown beside the date because the two differ for a backdated document,
   * and when they differ it is the only thing that explains the value on the row. */
  sequence_no: number;
  warehouse_id: number;
  warehouse_code: string;
  quantity: string;
  unit_cost: string | null;
  value: string;
  running_quantity: string;
  running_value: string;
  /** Costed at the last positive average because there was no stock to cost it against
   * (decision 5). The review trail, never corrected retroactively. */
  cost_provisional: boolean;
  project_id: number | null;
  journal_entry_id: number | null;
  entry_number: string | null;
  transaction_type_id: number | null;
  transaction_type_code: string | null;
  transaction_type_name: string | null;
  source_doc_type: string | null;
  source_doc_id: number | null;
  source_line_id: number | null;
  reverses_move_id: number | null;
}

export interface ItemEnquiry {
  item_id: number;
  item_code: string;
  item_name: string;
  base_uom_id: number;
  as_of: string;
  date_from: string | null;
  warehouse_id: number | null;
  provisional_only: boolean;
  locations: EnquiryLocation[];
  /** Item-wide as at `as_of`, across every location — a warehouse filter narrows the rows,
   * never the average. */
  total_quantity: string;
  total_value: string;
  average_cost: string;
  opening_quantity: string;
  opening_value: string;
  moves: EnquiryMove[];
  next_cursor: number | null;
}

// --- Reports (P5 step 8) ---------------------------------------------------------------------

export interface MovementRow {
  item_id: number;
  item_code: string;
  item_name: string;
  warehouse_id: number;
  warehouse_code: string;
  warehouse_name: string;
  branch_id: number;
  is_in_transit: boolean;
  opening_quantity: string;
  opening_value: string;
  quantity_in: string;
  value_in: string;
  quantity_out: string;
  value_out: string;
  closing_quantity: string;
  closing_value: string;
}

export interface MovementReport {
  date_from: string;
  date_to: string;
  rows: MovementRow[];
  next_cursor: number | null;
  /** Over the whole filtered set, not over this page. */
  opening_value: string;
  value_in: string;
  value_out: string;
  closing_value: string;
}

export interface TransactionRow {
  move_id: number;
  move_date: string;
  sequence_no: number;
  item_id: number;
  item_code: string;
  item_name: string;
  warehouse_id: number;
  warehouse_code: string;
  branch_id: number;
  quantity: string;
  unit_cost: string | null;
  value: string;
  cost_provisional: boolean;
  project_id: number | null;
  transaction_type_id: number | null;
  journal_entry_id: number | null;
  entry_number: string | null;
  source_doc_type: string | null;
  source_doc_id: number | null;
  source_line_id: number | null;
}

export interface TransactionReport {
  date_from: string;
  date_to: string;
  rows: TransactionRow[];
  next_cursor: number | null;
  total_quantity: string;
  total_value: string;
  move_count: number;
}

export interface ValuationRow {
  item_id: number;
  item_code: string;
  item_name: string;
  warehouse_id: number;
  warehouse_code: string;
  warehouse_name: string;
  branch_id: number;
  is_in_transit: boolean;
  quantity: string;
  value: string;
  /** Value / quantity **as at `as_of`**; null at zero quantity. Not the cost anything was
   * posted at — a backdated receipt makes the two differ — so it is never labelled "cost". */
  average_as_at: string | null;
  gl_account_id: number | null;
}

export interface ValuationItemTotal {
  item_id: number;
  item_code: string;
  item_name: string;
  quantity: string;
  value: string;
}

export interface ValuationWarehouseTotal {
  warehouse_id: number;
  value: string;
}

/** What the inventory account should read on `as_of` — the report states its own tie to the
 * GL, so the screen shows the two side by side instead of taking it on trust. */
export interface ValuationAccountTotal {
  gl_account_id: number | null;
  code: string | null;
  name: string | null;
  value: string;
}

export interface ValuationReport {
  as_of: string;
  include_zero: boolean;
  rows: ValuationRow[];
  item_totals: ValuationItemTotal[];
  warehouse_totals: ValuationWarehouseTotal[];
  account_totals: ValuationAccountTotal[];
  next_cursor: number | null;
  total_value: string;
}

export interface CountVarianceRow {
  line_id: number;
  line_no: number;
  item_id: number;
  item_code: string;
  item_name: string;
  system_quantity: string;
  counted_quantity: string | null;
  variance: string | null;
  stale: boolean;
  stock_move_id: number | null;
}

export interface CountReportRow {
  session_id: number;
  number: string;
  warehouse_id: number;
  warehouse_code: string;
  warehouse_name: string;
  branch_id: number;
  count_date: string;
  description: string;
  status: string;
  snapshot_at: string;
  document_id: number | null;
  document_number: string | null;
  journal_entry_id: number | null;
  line_count: number;
  counted_count: number;
  variance_count: number;
  lines: CountVarianceRow[];
}

export interface CountReport {
  rows: CountReportRow[];
  next_cursor: number | null;
}
