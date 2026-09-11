/** Mirrors backend/app/schemas/gl.py — snake_case, matching the raw JSON wire shape. */

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

export interface TransactionType {
  id: number;
  module: string;
  code: string;
  name: string;
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

export interface GLSettings {
  retained_earnings_account_id: number | null;
  rounding_difference_account_id: number | null;
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
