import type {
  BankAccountKind,
  BankMatchKind,
  BankMatchRule,
  PaymentRunStatus,
  ReconciliationStatus,
  StatementAmountMode,
  StatementEmptyAmount,
  StatementEmptyDescription,
  StatementFormatPreset,
  StatementSignConvention,
  StatementSource,
  StatementStatus,
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
  empty_description?: StatementEmptyDescription;
  zero_is_empty?: boolean;
  empty_amount?: StatementEmptyAmount;
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
  /** Rows `empty_amount: skip` passed over (a `BALANCE B/FWD`) — not lines already held. */
  lines_skipped_no_amount: number;
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

// --- Statements (P8 decision 3) ------------------------------------------------------------

export interface Statement {
  id: number;
  bank_account_id: number;
  number: string;
  source: StatementSource;
  file_name: string | null;
  from_date: string;
  to_date: string;
  opening_balance: string;
  closing_balance: string;
  line_count: number;
  lines_skipped: number;
  lines_skipped_no_amount: number;
  status: StatementStatus;
  imported_at: string;
}

export interface StatementLine {
  id: number;
  statement_id: number;
  bank_account_id: number;
  line_no: number;
  value_date: string;
  booking_date: string | null;
  description: string;
  reference: string | null;
  /** Credit positive, as stored. */
  amount: string;
  balance_after: string | null;
  external_id: string | null;
}

export interface StatementLineState {
  match_id: number | null;
  match_kind: BankMatchKind | null;
  match_rule: BankMatchRule | null;
  reconciliation_number: string | null;
  journal_line_count: number;
}

export interface StatementLineDetail extends StatementLine {
  state: StatementLineState;
}

export interface StatementDetail extends Statement {
  format_snapshot: StatementFormat | null;
  lines: StatementLineDetail[];
}

/** "N new, M skipped" — an overlapping export is the normal case, so the counts are the result. */
export interface StatementImportResult {
  statement: Statement;
  new_count: number;
  skipped_count: number;
  lines_skipped_no_amount: number;
  replayed: boolean;
}

export interface ManualStatementLinePayload {
  value_date: string;
  description: string;
  reference: string | null;
  amount: string;
  balance_after: string | null;
}

export interface ManualStatementPayload {
  bank_account_id: number;
  opening_balance: string;
  closing_balance: string;
  lines: ManualStatementLinePayload[];
}

// --- Matching (P8 decision 4) ----------------------------------------------------------------

/** One ledger line on the account, the workspace's right pane. `amount` is the **reconciled**
 * amount — the account's own currency — so it is comparable with the statement pane. */
export interface LedgerLine {
  journal_line_id: number;
  entry_id: number;
  entry_number: string;
  entry_date: string;
  doc_type: string;
  description: string | null;
  reference: string | null;
  amount: string;
  match_id: number | null;
  match_kind: BankMatchKind | null;
  match_rule: BankMatchRule | null;
  reconciliation_number: string | null;
  reconciliation_id: number | null;
  /** "dated inside BRC-n": posted after that reconciliation locked, dated before its date. */
  dated_inside: string | null;
  is_outstanding: boolean;
}

/** One line of a journal entry on a bank account — the GL entry page's reading (decision 10). */
export interface EntryBankLine extends LedgerLine {
  bank_account_id: number;
  bank_account_code: string;
  bank_account_name: string;
}

export interface Match {
  id: number;
  bank_account_id: number;
  kind: BankMatchKind;
  rule: BankMatchRule;
  reconciliation_id: number | null;
  matched_at: string;
  note: string | null;
  statement_line_ids: number[];
  journal_line_ids: number[];
}

export interface MatchCandidate {
  journal_line_id: number;
  entry_id: number;
  entry_number: string;
  entry_date: string;
  doc_type: string;
  description: string | null;
  reference: string | null;
  amount: string;
  rule: BankMatchRule;
}

export interface AutoMatchResult {
  matched: Match[];
  /** statement line id → the candidates that tied. */
  ambiguous: Record<string, MatchCandidate[]>;
}

export interface Prefill {
  rule_id: number | null;
  gl_account_id: number | null;
  tax_code_id: number | null;
  partner_type: string | null;
  partner_id: number | null;
  description: string;
  kind: "receipt" | "payment";
}

export interface PostCashbookFromLinePayload {
  gl_account_id: number;
  tax_code_id: number | null;
  description: string | null;
  reference: string | null;
  entry_date: string | null;
  branch_id: number | null;
  project_id: number | null;
}

export interface PostSettlementFromLinePayload {
  partner_id: number;
  description: string | null;
  reference: string | null;
  document_date: string | null;
}

export interface PostedFromStatement {
  entry_id: number;
  entry_number: string;
  journal_line_id: number;
  match: Match;
  document_id: number | null;
  document_number: string | null;
}

// --- Reconciliation (P8 decision 5) ----------------------------------------------------------

export interface OutstandingLine {
  journal_line_id: number;
  entry_id: number;
  entry_number: string;
  entry_date: string;
  doc_type: string;
  description: string | null;
  amount: string;
  dated_inside: string | null;
}

export interface Figures {
  reconciliation_date: string;
  statement_balance: string;
  ledger_balance: string;
  outstanding_total: string;
  difference: string;
  adjusted_bank_balance: string;
  outstanding: OutstandingLine[];
  unmatched_statement: StatementLine[];
  unmatched_statement_count: number;
}

export interface Reconciliation {
  id: number;
  bank_account_id: number;
  number: string;
  reconciliation_date: string;
  statement_balance: string;
  ledger_balance: string | null;
  outstanding_total: string | null;
  difference: string | null;
  status: ReconciliationStatus;
  high_water_line_id: number | null;
  locked_at: string | null;
  reopened_at: string | null;
  reopened_reason: string | null;
}

export interface ReconciliationDetail extends Reconciliation {
  /** Live: what the date computes now. */
  figures: Figures;
  /** A locked one only: what it said at the lock. */
  stored: Figures | null;
  late_line_ids: number[];
}

// --- Payment runs (P8 decision 7, step 7b) -----------------------------------------------------

/** An open supplier invoice the run could pay. `discount_available` is P4's own figure at the
 * date asked for — the discount is a fact of the payment date. */
export interface SelectableDocument {
  document_id: number;
  number: string;
  partner_id: number;
  partner_name: string;
  supplier_code: string | null;
  document_date: string;
  due_date: string | null;
  currency_id: number;
  total_amount: string;
  open_amount: string;
  discount_available: string;
}

export interface PaymentRunLinePayload {
  document_id: number;
  /** What comes off the invoice's open amount; `null` is "all of it". */
  amount: string | null;
  take_discount: boolean;
}

export interface PaymentRunPayload {
  bank_account_id: number;
  payment_date: string;
  lines: PaymentRunLinePayload[];
}

export interface PaymentRunPreviewLine {
  document_id: number;
  document_number: string;
  due_date: string | null;
  open_amount: string;
  amount: string;
  discount_available: string;
  discount_amount: string;
  /** What leaves the bank for this line: `amount` less the discount taken. */
  cash_amount: string;
}

export interface PaymentRunPreviewSupplier {
  partner_id: number;
  partner_name: string;
  supplier_code: string | null;
  bank_name: string | null;
  bank_account_number: string | null;
  bank_account_holder: string | null;
  lines: PaymentRunPreviewLine[];
  /** `bank_details_missing`, and `open_credits: PMT-…, RTS-…` naming the documents. */
  warnings: string[];
  total: string;
  discount_total: string;
}

export interface PaymentRunPreview {
  bank_account_id: number;
  bank_account_code: string;
  payment_date: string;
  currency_id: number;
  currency_code: string;
  suppliers: PaymentRunPreviewSupplier[];
  total: string;
  discount_total: string;
}

export interface PaymentRun {
  id: number;
  bank_account_id: number;
  number: string;
  payment_date: string;
  currency_id: number;
  total: string;
  reference: string;
  status: PaymentRunStatus;
  posted_at: string;
  reversed_at: string | null;
  reversal_reason: string | null;
  supplier_count: number;
}

export interface PaymentRunLine {
  id: number;
  partner_id: number;
  partner_name: string;
  supplier_code: string | null;
  document_id: number;
  document_number: string;
  /** The **cash** paid on this line. The invoice was settled by `amount + discount_amount`. */
  amount: string;
  discount_amount: string;
  settlement_document_id: number | null;
  settlement_number: string | null;
  settlement_status: string | null;
  allocation_id: number | null;
  allocation_number: string | null;
}

export interface PaymentRunDetail extends PaymentRun {
  lines: PaymentRunLine[];
  remittance_job_ids: number[];
  /** The locked reconciliation holding the run's bank line — Reverse is refused while set. */
  reconciliation_locked: string | null;
}

/** A `remittance_pdf` job, one per supplier. `params.partner_id` says whose advice it is. */
export interface RemittanceJob {
  id: number;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed";
  params: { run_id?: number; partner_id?: number } | null;
  error: string | null;
  artifact_name: string | null;
  artifact_size: number | null;
  expires_at: string | null;
}

// --- Cashbooks, the reconciliation report and the enquiry (P8 decisions 6 and 10, step 8) -----

/** One ledger line on the account, in the account's **own** currency. `reconciled` is decision
 * 6's column: the `BRC-` its match was locked in, `"matched"` for a match not yet locked, and
 * null for outstanding. */
export interface CashbookRow {
  journal_line_id: number;
  entry_id: number;
  entry_date: string;
  entry_number: string;
  doc_type: string;
  module: string;
  reference: string | null;
  description: string | null;
  partner_name: string | null;
  receipt: string;
  payment: string;
  running_balance: string;
  base_amount: string;
  reconciled: string | null;
}

export interface CashbookDetail {
  bank_account_id: number;
  code: string;
  name: string;
  currency_id: number;
  currency_code: string;
  date_from: string;
  date_to: string;
  opening_balance: string;
  opening_base: string;
  receipts_total: string;
  payments_total: string;
  /** In the account's own currency. */
  closing_balance: string;
  /** In base — the figure that ties to the trial balance. */
  closing_base: string;
  rows: CashbookRow[];
}

export interface CashbookSummaryRow {
  bank_account_id: number;
  code: string;
  name: string;
  kind: BankAccountKind;
  currency_code: string;
  opening_balance: string;
  receipts: string;
  payments: string;
  closing_balance: string;
  closing_base: string;
  last_reconciled_at: string | null;
  last_reconciled_balance: string | null;
  last_reconciliation_id: number | null;
  unmatched_statement_lines: number;
  outstanding_lines: number;
}

/** `stored` is what a locked reconciliation said; `live` is what its date computes now. They
 * differ by exactly `posted_after_lock`. */
export interface ReconciliationReport {
  reconciliation_id: number;
  number: string;
  bank_account_id: number;
  bank_account_code: string;
  bank_account_name: string;
  currency_code: string;
  reconciliation_date: string;
  status: ReconciliationStatus;
  live: Figures;
  stored: Figures | null;
  posted_after_lock: OutstandingLine[];
}

export interface BankAccountEnquiry {
  bank_account_id: number;
  code: string;
  name: string;
  kind: BankAccountKind;
  currency_code: string;
  book_balance: string;
  book_balance_base: string;
  last_reconciliation_id: number | null;
  last_reconciliation_number: string | null;
  last_reconciled_at: string | null;
  last_reconciled_balance: string | null;
  open_reconciliation_id: number | null;
  open_reconciliation_number: string | null;
  unmatched_statement_count: number;
  unmatched_statement_total: string;
  outstanding_count: number;
  outstanding_total: string;
  latest_statement_id: number | null;
  latest_statement_number: string | null;
  latest_statement_to: string | null;
}
