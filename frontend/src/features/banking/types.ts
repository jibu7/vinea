import type {
  BankAccountKind,
  StatementAmountMode,
  StatementFormatPreset,
  StatementSignConvention,
} from "@/lib/api-enums";

/**
 * Wire shapes for the banking API (P8), mirroring `app/schemas/banking.py`.
 *
 * Money arrives as a **string** — `Decimal` on the server, never a float on either side — and
 * a statement line's `amount` is credit positive on the wire exactly as it is stored.
 */

export interface BankAccount {
  id: number;
  gl_account_id: number;
  kind: BankAccountKind;
  code: string;
  name: string;
  currency_id: number;
  bank_name: string | null;
  account_number: string | null;
  account_holder: string | null;
  bank_branch: string | null;
  swift_bic: string | null;
  statement_format: StatementFormat | null;
  is_active: boolean;
  last_reconciled_at: string | null;
  last_reconciled_balance: string | null;
  /** The GL account carries a journal line, so the currency can no longer change. */
  has_lines: boolean;
}

export interface UnregisteredAccount {
  id: number;
  code: string;
  name: string;
}

/** The bank's own details — the half of the master a person keys. */
export interface BankDetailsPayload {
  code?: string | null;
  name?: string | null;
  currency_id?: number | null;
  bank_name?: string | null;
  account_number?: string | null;
  account_holder?: string | null;
  bank_branch?: string | null;
  swift_bic?: string | null;
}

/** *Register* names an existing flagged account; *New* names the GL account to make with it.
 * Exactly one — the server refuses neither and both (`bank_account_target_ambiguous`). */
export interface BankAccountRegisterPayload extends BankDetailsPayload {
  gl_account_id?: number;
  new_account?: { code: string; name: string; kind: BankAccountKind; parent_id: number | null };
}

export interface BankAccountUpdatePayload extends BankDetailsPayload {
  statement_format?: StatementFormat | null;
  is_active?: boolean;
}

/** `app/banking/formats.py::StatementFormat`. Every field is optional on the wire — the
 * server fills the generic preset's defaults — but the editor always sends a whole mapping, so
 * what is saved is what was on the screen. */
export interface StatementFormat {
  preset?: StatementFormatPreset;
  delimiter?: string;
  encoding?: string;
  header_rows?: number;
  date_column?: string;
  date_format?: string;
  booking_date_column?: string | null;
  description_column?: string;
  reference_column?: string | null;
  amount_mode?: StatementAmountMode;
  debit_column?: string | null;
  credit_column?: string | null;
  amount_column?: string | null;
  sign_convention?: StatementSignConvention;
  balance_column?: string | null;
  external_id_column?: string | null;
  decimal_separator?: string;
  thousands_separator?: string | null;
}

export interface ParseError {
  row: number;
  column: string | null;
  message: string;
}

export interface PreviewLine {
  row: number;
  value_date: string;
  booking_date: string | null;
  description: string;
  reference: string | null;
  amount: string;
  balance_after: string | null;
  external_id: string | null;
  already_held: boolean;
}

export interface StatementPreview {
  bank_account_id: number;
  file_name: string | null;
  file_sha256: string;
  format_snapshot: StatementFormat;
  lines: PreviewLine[];
  line_count: number;
  new_count: number;
  skipped_count: number;
  from_date: string | null;
  to_date: string | null;
  opening_balance: string | null;
  closing_balance: string | null;
  errors: ParseError[];
  duplicate_file: boolean;
}

export interface BankRule {
  id: number;
  bank_account_id: number;
  pattern: string;
  gl_account_id: number | null;
  tax_code_id: number | null;
  partner_type: string | null;
  partner_id: number | null;
  description: string | null;
  priority: number;
  is_active: boolean;
}

export interface BankRulePayload {
  pattern: string;
  gl_account_id: number | null;
  tax_code_id: number | null;
  description: string | null;
  priority: number;
  is_active?: boolean;
}
