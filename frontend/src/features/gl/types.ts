/** Mirrors backend/app/schemas/gl.py — snake_case, matching the raw JSON wire shape. */
import type {
  FiscalTaxType,
  FxRevaluationRole,
  FxRevaluationStatus,
} from "@/lib/api-enums";

export type { FiscalTaxType, FxRevaluationRole, FxRevaluationStatus };

export interface GLAccount {
  id: number;
  code: string;
  name: string;
  class: "asset" | "liability" | "equity" | "income" | "expense";
  parent_id: number | null;
  is_postable: boolean;
  is_control: boolean;
  control_type: string | null;
  is_active: boolean;
}

/** Wire values match backend `app.models.gl.ControlType` (a two-letter StrEnum for AR/AP —
 * CSS `capitalize` mangles those to "Ar"/"Ap", so render through the catalogue instead). */
const CONTROL_TYPE_KEYS = ["bank", "cash", "ar", "ap", "inventory"];

/** The short control-type label, as a chip and as a CSV cell. Not a component, so it takes
 * the `gl` translator rather than calling a hook. A wire value this build does not know is
 * returned verbatim, exactly as the old label map did — a new backend ControlType shows up
 * as itself rather than as a missing-message crash. */
export function controlTypeLabel(
  controlType: string | null | undefined,
  t: (key: string) => string,
): string {
  if (!controlType) return t("controlTypeShort.none");
  return CONTROL_TYPE_KEYS.includes(controlType) ? t(`controlTypeShort.${controlType}`) : controlType;
}

export interface Project {
  id: number;
  code: string;
  name: string;
  is_active: boolean;
}

/**
 * Inventory's transaction kinds (P5 decision 9). A kind is *what the type does to stock* —
 * users add their own types of an existing kind (Damaged, Samples, ...) with their own contra
 * account, which is what the Maintenance screen is for. `null` on every non-inventory module,
 * where the concept does not apply.
 *
 * Re-exported from the generated module rather than re-declared here — imported too, because
 * `TransactionType` below uses it.
 */
import type { InventoryTransactionKind } from "@/lib/api-enums";

export type { InventoryTransactionKind };

export interface TransactionType {
  id: number;
  module: string;
  code: string;
  name: string;
  kind: InventoryTransactionKind | null;
  default_gl_account_id: number | null;
  is_active: boolean;
}

export interface Branch {
  id: number;
  code: string;
  name: string;
  is_main: boolean;
  is_active: boolean;
}

export interface TaxCode {
  id: number;
  code: string;
  name: string;
  nature: "output" | "input" | "exempt" | "zero_rated";
  rate_pct: string;
  gl_account_id: number | null;
  valid_from: string;
  valid_to: string | null;
  is_active: boolean;
  /** P7: which EBM tax class (A–D) a line carrying this code is reported under. `null` on a
   * company that never fiscalizes; a fiscalized sale on an unmapped code is refused with
   * `tax_class_unmapped` rather than defaulted to a class RRA would then report on. */
  fiscal_tax_type: FiscalTaxType | null;
}

export interface Currency {
  id: number;
  code: string;
  name: string;
  symbol: string | null;
  decimal_places: number;
  is_base: boolean;
  is_active: boolean;
}

export interface ExchangeRate {
  id: number;
  currency_id: number;
  valid_from: string;
  rate: string;
}

export interface JournalLineInput {
  gl_account_id?: number | null;
  transaction_type?: string | null;
  debit: string;
  credit: string;
  currency_id?: number | null;
  exchange_rate?: string | null;
  branch_id?: number | null;
  project_id?: number | null;
  tax_code_id?: number | null;
  tax_amount?: string;
  description?: string | null;
}

export interface JournalEntryCreatePayload {
  entry_date: string;
  description: string;
  reference?: string | null;
  branch_id?: number | null;
  lines: JournalLineInput[];
}

export interface CashbookLineInput {
  gl_account_id?: number | null;
  transaction_type?: string | null;
  amount: string;
  tax_code_id?: number | null;
  tax_inclusive: boolean;
  branch_id?: number | null;
  project_id?: number | null;
  description?: string | null;
}

export interface CashbookEntryCreatePayload {
  entry_date: string;
  description: string;
  cash_account_id: number;
  kind: "receipt" | "payment";
  currency_id?: number | null;
  exchange_rate?: string | null;
  branch_id?: number | null;
  reference?: string | null;
  lines: CashbookLineInput[];
}

export interface ReversalPayload {
  entry_date: string;
  reason: string;
}

export interface JournalLine {
  id: number;
  line_no: number;
  gl_account_id: number;
  branch_id: number;
  project_id: number | null;
  currency_id: number;
  exchange_rate: string;
  amount: string;
  base_amount: string;
  tax_code_id: number | null;
  tax_amount: string;
  is_rounding_line: boolean;
  description: string | null;
}

export interface JournalEntry {
  id: number;
  number: string;
  doc_type: string;
  event_type: string;
  entry_date: string;
  period_id: number;
  description: string;
  reference?: string | null;
  status: "draft" | "posted";
  /** Which module posted this — `"gl"` for a manual journal or a cashbook entry, otherwise the
   * module that owns it. An owned entry is reversed through its module, because the GL
   * reversal writes only the ledger half and would leave that module's own side behind. */
  module: string;
  /** The module document this entry belongs to, resolved by the API through that module's own
   * table (`inventory_documents.journal_entry_id`, `partner_documents.journal_entry_id`) —
   * **not** from `source_doc_id`, which was only populated from P5 step 9 and cannot be
   * back-filled onto a posted entry. So an entry from any phase resolves. */
  module_document_id: number | null;
  module_document_number: string | null;
  /** Which **kind** of page opens that document — `lib/document-route.ts` maps it. `module` is
   * not enough and P6 is where that stopped being a detail: a goods receipt, a landed cost and
   * an inventory adjustment are all `inv` and live on three different screens. */
  module_document_target: string | null;
  posted_by: number | null;
  posted_at: string | null;
  reverses_entry_id: number | null;
  reverses_entry_number?: string | null;
  reversal_reason: string | null;
  reversed_by_entry_id?: number | null;
  reversed_by_number?: string | null;
  source_doc_type: string | null;
  source_doc_id: number | null;
  lines: JournalLine[];
}

export interface AccountTransaction {
  line_id: number;
  entry_id: number;
  entry_number: string;
  entry_date: string;
  description: string | null;
  reference?: string | null;
  branch_id: number;
  project_id: number | null;
  currency_id: number;
  amount: string;
  base_amount: string;
  running_base: string;
}

export interface AccountTransactionsResponse {
  gl_account_id: number;
  date_from: string;
  date_to: string;
  opening_base: string;
  items: AccountTransaction[];
  next_cursor: number | null;
}

export interface TrialBalanceRow {
  gl_account_id: number;
  code: string;
  name: string;
  class: "asset" | "liability" | "equity" | "income" | "expense";
  debit: string;
  credit: string;
  net: string;
}

export interface TrialBalanceResponse {
  as_of: string;
  branch_id: number | null;
  project_id: number | null;
  rows: TrialBalanceRow[];
  total_debit: string;
  total_credit: string;
  foots: boolean;
}

export interface CompanyDetails {
  id: number;
  name: string;
  tin: string | null;
  vat_registered: boolean;
  fiscal_country: string;
  address: Record<string, unknown> | null;
  status: string;
  coa_template: string;
}

/**
 * The GL keys the Defaults screen owns.
 *
 * P7 added five accounts and one code. They were seeded by the Rwanda pack at step 1 and had
 * nowhere to be *set* until this screen: a tenant that did not come from the pack had a VAT
 * return that refused to file and no way to fix it.
 */
export interface GLSettings {
  retained_earnings_account_id: number | null;
  rounding_difference_account_id: number | null;
  vat_settlement_account_id: number | null;
  ar_revaluation_account_id: number | null;
  ap_revaluation_account_id: number | null;
  unrealized_fx_gain_account_id: number | null;
  unrealized_fx_loss_account_id: number | null;
  fiscal_default_purchase_class_code: string | null;
}

/** What the Defaults screen sends. Every key is optional — the service writes only what is
 * present — and the class code needs an explicit clear, because `null` already means "leave
 * it alone" on a body the screen does not fill in full. */
export interface GLSettingsPayload {
  retained_earnings_account_id?: number | null;
  rounding_difference_account_id?: number | null;
  vat_settlement_account_id?: number | null;
  ar_revaluation_account_id?: number | null;
  ap_revaluation_account_id?: number | null;
  unrealized_fx_gain_account_id?: number | null;
  unrealized_fx_loss_account_id?: number | null;
  fiscal_default_purchase_class_code?: string | null;
  clear_fiscal_default_purchase_class_code?: boolean;
}

export interface FiscalYear {
  id: number;
  code: string;
  start_date: string;
  end_date: string;
  status: "open" | "closing" | "closed" | "locked";
  closing_entry_id: number | null;
}

export interface AccountingPeriod {
  id: number;
  fiscal_year_id: number;
  period_number: number;
  name: string;
  start_date: string;
  end_date: string;
  status: "pending" | "open" | "closed" | "locked";
  closed_at: string | null;
  closed_by: number | null;
}

export interface AccountAuditRecord {
  id: number;
  action: string;
  at: string;
  actor_email: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface Role {
  id: number;
  name: string;
  description: string | null;
  is_system: boolean;
}

export interface CompanyMember {
  id: number;
  user_id: number | null;
  full_name: string | null;
  email: string;
  is_owner: boolean;
  status: string;
  roles: Role[];
  invited_at: string | null;
  accepted_at: string | null;
}

// --- Unrealized FX revaluation (P7 decision 13) -----------------------------------------------

/**
 * One open foreign-currency partner document in a run.
 *
 * Every figure is the one the run **used**, not a recomputation: a later correction to
 * `exchange_rates` must not restate a posted revaluation, so the lines are stored and read
 * back rather than derived on the way to the screen.
 */
export interface FxRevaluationLine {
  document_id: number;
  document_number: string;
  role: string;
  partner_id: number;
  partner_name: string;
  currency_id: number;
  currency_code: string;
  /** Signed by the control account's side — an AR invoice positive, an AP invoice negative. */
  open_amount: string;
  booking_rate: string;
  carrying_base: string;
  rate_at_date: string;
  revalued_base: string;
  difference: string;
}

export interface FxRevaluationPreview {
  revaluation_date: string;
  role: FxRevaluationRole;
  total_difference: string;
  lines: FxRevaluationLine[];
}

export interface FxRevaluation {
  id: number;
  number: string;
  revaluation_date: string;
  role: FxRevaluationRole;
  journal_entry_id: number | null;
  /** The next-day reversal posted in the same transaction as the entry — the balance sheet at
   * the date carries the revaluation and the next period does not. */
  mirror_entry_id: number | null;
  /** The counter-entry that undid the whole run, when one was posted. */
  reversal_entry_id: number | null;
  status: FxRevaluationStatus;
}

export interface FxRevaluationDetail extends FxRevaluation {
  lines: FxRevaluationLine[];
}
