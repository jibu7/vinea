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
  status: "draft" | "posted";
  posted_by: number | null;
  posted_at: string | null;
  reverses_entry_id: number | null;
  reversal_reason: string | null;
  source_doc_type: string | null;
  source_doc_id: number | null;
  lines: JournalLine[];
}
