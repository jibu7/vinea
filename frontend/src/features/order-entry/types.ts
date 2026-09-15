/**
 * Order-entry wire types (P6).
 *
 * Orders, receipts, landed costs and the documents the flows prepare — each shape written
 * against the screen that renders it, which is the only way to know a field is real rather
 * than assumed.
 *
 * Enum-valued fields import from `@/lib/api-enums` and are re-exported here, so a screen
 * holding an `OrderDefaults` never has to reach past this module for the policy union.
 */
import type {
  BackorderPolicy,
  GrnStatus,
  LandedCostBasis,
  LandedCostStatus,
  PurchaseOrderStatus,
  SalesOrderStatus,
  TaxMode,
} from "@/lib/api-enums";

export type {
  BackorderPolicy,
  GrnStatus,
  LandedCostBasis,
  LandedCostStatus,
  PurchaseOrderStatus,
  SalesOrderStatus,
};

export interface OrderDefaults {
  grn_accrual_account_id: number | null;
  purchase_price_variance_account_id: number | null;
  landed_cost_clearing_account_id: number | null;
  backorder_policy: BackorderPolicy;
  /** Read-only on this screen. Sales and purchase orders default their warehouse from it,
   * but it is the *inventory* default and the Inventory defaults screen owns writing it —
   * `update_order_defaults` refuses it with `unknown_gl_setting`. */
  default_warehouse_id: number | null;
}

/**
 * A PUT body. Every key is optional and only those present are written, so the screen sends
 * what it holds; clearing an account is refused server-side with `required_setting` rather
 * than accepted and failed at the next GRN.
 */
export interface OrderDefaultsPayload {
  grn_accrual_account_id?: number;
  purchase_price_variance_account_id?: number;
  landed_cost_clearing_account_id?: number;
  backorder_policy?: BackorderPolicy;
}

// --- Orders (P6 decision 3) ----------------------------------------------------------------

/** One line as the grid sends it. A **kit** line carries the kit item and its quantity only:
 * the components are the service's to explode and Breakup's to edit. */
export interface OrderLinePayload {
  item_id: number;
  quantity: string;
  /** Names an existing line on an edit — an invoice line points at it, so it has to survive
   * the edit. A line without one is new. */
  line_id?: number | null;
  uom_id?: number | null;
  unit_price?: string | null;
  discount_percent?: string;
  tax_code_id?: number | null;
  warehouse_id?: number | null;
  project_id?: number | null;
  description?: string | null;
  /** Consent to re-explode a hand-edited kit line from the catalogue, losing what Breakup put
   * there. Without it the service refuses the edit with `kit_breakup_would_reset`. */
  reset_breakup?: boolean;
}

interface OrderWriteBase {
  partner_id: number;
  order_date: string;
  description: string;
  expected_date?: string | null;
  reference?: string | null;
  currency_id?: number | null;
  exchange_rate?: string | null;
  branch_id?: number | null;
  project_id?: number | null;
  warehouse_id?: number | null;
  tax_mode?: TaxMode | null;
  lines: OrderLinePayload[];
}

export interface SalesOrderPayload extends OrderWriteBase {
  payment_terms_id?: number | null;
  sales_rep_id?: number | null;
}

export type PurchaseOrderPayload = OrderWriteBase;

/** Close or cancel. The date is the day the decision was taken, not today by default. */
export interface OrderTransitionPayload {
  on_date: string;
}

interface OrderLineBase {
  id: number;
  line_no: number;
  item_id: number;
  description: string | null;
  uom_id: number;
  quantity: string;
  base_quantity: string;
  unit_price: string;
  discount_percent: string;
  tax_code_id: number | null;
  net_amount: string;
  tax_amount: string;
  gross_amount: string;
  warehouse_id: number;
  project_id: number | null;
  remaining: string;
}

export interface SalesOrderLine extends OrderLineBase {
  kit_parent_line_id: number | null;
  /** True when Breakup edited this kit line's explosion — the one fact no arithmetic over the
   * catalogue definition can recover, and what `kit_breakup_would_reset` protects. */
  kit_breakup_edited: boolean;
  invoiced: string;
}

export interface PurchaseOrderLine extends OrderLineBase {
  received: string;
}

interface OrderBase {
  id: number;
  number: string;
  partner_id: number;
  order_date: string;
  expected_date: string | null;
  reference: string | null;
  description: string;
  currency_id: number;
  /** Display only — an order never values stock. */
  exchange_rate: string;
  branch_id: number;
  project_id: number | null;
  warehouse_id: number;
  tax_mode: TaxMode;
  net_amount: string;
  tax_amount: string;
  total_amount: string;
  closed_on: string | null;
  cancelled_on: string | null;
}

export interface SalesOrder extends OrderBase {
  payment_terms_id: number | null;
  sales_rep_id: number | null;
  status: SalesOrderStatus;
  lines: SalesOrderLine[];
}

export interface PurchaseOrder extends OrderBase {
  status: PurchaseOrderStatus;
  lines: PurchaseOrderLine[];
}

export interface SalesOrderSummary {
  id: number;
  number: string;
  partner_id: number;
  order_date: string;
  expected_date: string | null;
  currency_id: number;
  status: SalesOrderStatus;
  total_amount: string;
  /** What this order has promised that its warehouses cannot currently cover — the same rule
   * the enquiry uses per line, so the two screens cannot disagree. */
  backordered: string;
}

export interface PurchaseOrderSummary {
  id: number;
  number: string;
  partner_id: number;
  order_date: string;
  expected_date: string | null;
  currency_id: number;
  status: PurchaseOrderStatus;
  total_amount: string;
}

/** `quantity` is what ships in **this** order's box, in the component's base unit — not a
 * per-kit rate (P6 decision 8). */
export interface BreakupComponentPayload {
  item_id: number;
  quantity: string;
}

export interface BreakupPayload {
  components: BreakupComponentPayload[];
}

// --- Goods receipts (P6 decision 6) ---------------------------------------------------------

export interface GrnLinePayload {
  item_id: number;
  quantity: string;
  /** Per **keyed** unit, in the receipt's currency — what the delivery note says. Zero is a
   * real case: a free replacement moves quantity and posts no entry. */
  unit_cost: string;
  uom_id?: number | null;
  warehouse_id?: number | null;
  description?: string | null;
  project_id?: number | null;
  purchase_order_line_id?: number | null;
}

export interface GrnPayload {
  partner_id: number;
  grn_date: string;
  description: string;
  warehouse_id?: number | null;
  purchase_order_id?: number | null;
  /** The supplier's own delivery-note number, as written on the paper that came with the
   * goods. Not ours, not unique, and the thing a storeman actually quotes. */
  supplier_reference?: string | null;
  currency_id?: number | null;
  branch_id?: number | null;
  lines: GrnLinePayload[];
}

export interface GrnReversePayload {
  on_date: string;
  reason: string;
}

export interface GrnLine {
  id: number;
  line_no: number;
  purchase_order_line_id: number | null;
  item_id: number;
  uom_id: number;
  warehouse_id: number;
  description: string | null;
  quantity: string;
  base_quantity: string;
  unit_cost: string;
  /** Frozen at receipt: what the accrual carries and the match relieves. */
  value: string;
  project_id: number | null;
  stock_move_id: number | null;
  matched: string;
  unmatched: string;
}

export interface Grn {
  id: number;
  number: string;
  partner_id: number;
  purchase_order_id: number | null;
  warehouse_id: number;
  branch_id: number;
  grn_date: string;
  supplier_reference: string | null;
  description: string;
  currency_id: number;
  exchange_rate: string;
  status: GrnStatus;
  journal_entry_id: number | null;
  reversal_entry_id: number | null;
  reversed_on: string | null;
  lines: GrnLine[];
}

export interface GrnSummary {
  id: number;
  number: string;
  partner_id: number;
  grn_date: string;
  status: GrnStatus;
  currency_id: number;
}

// --- Prepared documents (P6 decision 7) ------------------------------------------------------

/** One line of a document a flow built and did **not** post. */
export interface PreparedLine {
  item_id: number | null;
  quantity: string;
  unit_price: string | null;
  discount_percent: string;
  tax_code_id: number | null;
  warehouse_id: number | null;
  project_id: number | null;
  description: string | null;
  sales_order_line_id: number | null;
  purchase_order_line_id: number | null;
  grn_line_id: number | null;
  kit_components: PreparedLine[] | null;
  /** What the order or receipt still had outstanding on this line, so the screen can show
   * "5 of 20" without a second request. */
  remaining: string | null;
}

export interface PreparedDocument {
  role: "ar" | "ap";
  kind: string;
  partner_id: number;
  document_date: string;
  description: string;
  reference: string | null;
  currency_id: number | null;
  branch_id: number | null;
  project_id: number | null;
  payment_terms_id: number | null;
  sales_rep_id: number | null;
  tax_mode: TaxMode | null;
  lines: PreparedLine[];
}

export interface PreparedGrnLine {
  item_id: number;
  quantity: string;
  unit_cost: string;
  warehouse_id: number | null;
  description: string | null;
  project_id: number | null;
  purchase_order_line_id: number | null;
  remaining: string;
}

export interface PreparedGrn {
  partner_id: number;
  grn_date: string;
  description: string;
  warehouse_id: number | null;
  purchase_order_id: number | null;
  supplier_reference: string | null;
  currency_id: number | null;
  lines: PreparedGrnLine[];
}

// --- Landed cost (P6 decision 9) --------------------------------------------------------------

export interface LandedCostPreviewPayload {
  amount: string;
  basis: LandedCostBasis;
  /** Any receipt line, from any GRN and any supplier — one freight bill routinely covers
   * consignments from several. */
  grn_line_ids: number[];
}

export interface LandedCostPayload extends LandedCostPreviewPayload {
  cost_date: string;
  description: string;
  reference?: string | null;
  source_document_id?: number | null;
  source_cashbook_line_id?: number | null;
}

export interface LandedCostReversePayload {
  on_date: string;
  reason: string;
}

/** One target's share before it is posted. `would_go_to_cogs` is a **preview**, not a promise:
 * whether the location still holds the item is settled under the costing lock at posting. */
export interface LandedCostShare {
  grn_line_id: number;
  grn_id: number;
  item_id: number;
  warehouse_id: number;
  weight: string;
  share: string;
  would_go_to_cogs: boolean;
}

export interface LandedCostPreview {
  amount: string;
  basis: LandedCostBasis;
  shares: LandedCostShare[];
}

export interface LandedCostLine {
  id: number;
  line_no: number;
  grn_line_id: number;
  item_id: number;
  warehouse_id: number;
  weight: string;
  share: string;
  /** True when the location held none of the item and the share went to cost of sales. Such a
   * line has no move. */
  went_to_cogs: boolean;
  stock_move_id: number | null;
}

export interface LandedCost {
  id: number;
  number: string;
  cost_date: string;
  description: string;
  reference: string | null;
  amount: string;
  basis: LandedCostBasis;
  status: LandedCostStatus;
  source_document_id: number | null;
  source_cashbook_line_id: number | null;
  journal_entry_id: number | null;
  reversal_entry_id: number | null;
  reversed_on: string | null;
  lines: LandedCostLine[];
}

export interface LandedCostSummary {
  id: number;
  number: string;
  cost_date: string;
  description: string;
  amount: string;
  basis: LandedCostBasis;
  status: LandedCostStatus;
}

// --- Listings (P6 step 5) ---------------------------------------------------------------------

export interface GrnListRow {
  id: number;
  number: string;
  grn_date: string;
  partner_id: number;
  partner_name: string;
  warehouse_id: number;
  branch_id: number;
  status: GrnStatus;
  reference: string | null;
  journal_entry_id: number | null;
  received_value: string;
  matched_value: string;
  unmatched_value: string;
}

/** `unmatched_total` is over the whole filtered set rather than this page, because it exists
 * to be compared with the GRN accrual account and a per-page total could not be. */
export interface GrnListing {
  items: GrnListRow[];
  next_cursor: number | null;
  unmatched_total: string;
}

// --- The order enquiry (P6 step 5's endpoint, step 8's screens) --------------------------------

export interface EnquiryLine {
  line_id: number;
  line_no: number;
  item_id: number;
  item_code: string;
  item_name: string;
  description: string;
  uom_id: number;
  warehouse_id: number | null;
  /** As keyed, in `uom_id`. */
  quantity: string;
  /** Promised, done and left — all in the **item's base unit**, which is what every derived
   * figure in decision 4 is counted in. */
  ordered: string;
  fulfilled: string;
  remaining: string;
  /** The part of `remaining` the warehouse cannot currently cover. Zero on a service line and
   * on any line whose location holds enough. */
  backordered: string;
  unit_price: string;
  net_amount: string;
  tax_amount: string;
  gross_amount: string;
  kit_parent_line_id: number | null;
}

/** A document raised against the order, and the entries it posted.
 *
 * `stock_entry_id` is the companion (decision 2) — one document posted both entries, which is
 * why it is a second link on one row rather than a second row. Listing them separately would
 * invite somebody to reverse one of them.
 *
 * A **reversed** document stays in this list, carrying its own status: "what happened to this
 * order" includes the invoice that was raised and taken back. Its `quantity` is therefore what
 * that document keyed, not a net contribution — the netting lives in the line's `fulfilled`.
 */
export interface LinkedDocument {
  kind: string;
  document_id: number;
  number: string;
  document_date: string;
  status: string;
  quantity: string;
  journal_entry_id: number | null;
  journal_entry_number: string | null;
  stock_entry_id: number | null;
  stock_entry_number: string | null;
}

export interface OrderEnquiry {
  order_id: number;
  number: string;
  partner_id: number;
  partner_name: string;
  order_date: string;
  expected_date: string | null;
  reference: string | null;
  description: string;
  currency_id: number;
  exchange_rate: string;
  branch_id: number;
  warehouse_id: number | null;
  status: string;
  net_amount: string;
  tax_amount: string;
  total_amount: string;
  closed_on: string | null;
  cancelled_on: string | null;
  lines: EnquiryLine[];
  documents: LinkedDocument[];
  total_backordered: string;
}

// --- The order reports, per line (P6 step 8) ---------------------------------------------------

export interface OrderLineRow {
  order_id: number;
  number: string;
  order_date: string;
  expected_date: string | null;
  partner_id: number;
  partner_name: string;
  status: string;
  currency_id: number;
  reference: string | null;
  line_id: number;
  line_no: number;
  item_id: number;
  item_code: string;
  item_name: string;
  /** The unit the line was keyed in. */
  uom_id: number;
  /** The unit `ordered`, `fulfilled` and `remaining` are counted in. */
  base_uom_id: number;
  warehouse_id: number | null;
  description: string | null;
  quantity: string;
  ordered: string;
  fulfilled: string;
  remaining: string;
  unit_price: string;
  net_amount: string;
  tax_amount: string;
  gross_amount: string;
  kit_parent_line_id: number | null;
}

export interface UnitSubtotal {
  uom_id: number;
  ordered: string;
  remaining: string;
}

export interface CurrencySubtotal {
  currency_id: number;
  net_amount: string;
  gross_amount: string;
}

/**
 * The report's page, and the totals over everything the filters select.
 *
 * There is deliberately **no single quantity total and no single money total**. 3 kg plus 2
 * crates is not a quantity, and an order's `exchange_rate` is display only (decision 3), so
 * there is no rate that could put RWF and USD on one line either. Quantities are subtotalled
 * by unit, money by currency, and `line_count` is the total that always means something.
 */
export interface OrderLineReport {
  items: OrderLineRow[];
  next_cursor: number | null;
  line_count: number;
  by_unit: UnitSubtotal[];
  by_currency: CurrencySubtotal[];
}

// --- Landed cost, per receipt line (P6 step 5's endpoint) --------------------------------------

export interface LandedCostAllocation {
  landed_cost_id: number;
  number: string;
  cost_date: string;
  description: string;
  basis: LandedCostBasis;
  status: LandedCostStatus;
  document_amount: string;
  line_id: number;
  grn_line_id: number;
  grn_id: number;
  grn_number: string;
  item_id: number;
  warehouse_id: number;
  weight: string;
  share: string;
  /** The share went to cost of sales because the location held none of the item. */
  went_to_cogs: boolean;
  quantity_at_posting: string;
  stock_move_id: number | null;
  journal_entry_id: number | null;
}

export interface LandedCostAllocationListing {
  items: LandedCostAllocation[];
  next_cursor: number | null;
  total_allocated: string;
}
