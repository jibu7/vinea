/** Mirrors backend/app/schemas/subledger.py — snake_case, matching the raw JSON wire shape.
 *
 * AR and AP share every shape here: the role is a path segment on the API and a prop on the
 * screens, never a second set of types. */

/** `ar` = customers, `ap` = suppliers. The same partner row is often both. Everything a
 * user reads about a role lives under `arap.role.<role>` in the message catalogue. */
export type PartnerRole = "ar" | "ap";

export type DueBasis =
  | "days_from_document_date"
  | "days_from_end_of_month"
  | "fixed_day_of_month";
export type AgeingBasis = "document_date" | "due_date";
export type TaxMode = "exclusive" | "inclusive";

/** Wire values, in the order the pickers offer them. Labels live in `messages/en.json`
 * under `arap.paymentTerms.basis*` / `arap.bucketSets.basis*` — nothing user-visible is
 * spelled here, so a second locale needs no code change. */
export const DUE_BASES: readonly DueBasis[] = [
  "days_from_document_date",
  "days_from_end_of_month",
  "fixed_day_of_month",
] as const;

export const AGEING_BASES: readonly AgeingBasis[] = ["document_date", "due_date"] as const;

/** `arap.paymentTerms.<key>` for a due basis, and `arap.bucketSets.<key>` for an ageing one. */
export const DUE_BASIS_MESSAGE: Record<DueBasis, string> = {
  days_from_document_date: "basisDaysFromDocumentDate",
  days_from_end_of_month: "basisDaysFromEndOfMonth",
  fixed_day_of_month: "basisFixedDayOfMonth",
};

export const DUE_SUMMARY_MESSAGE: Record<DueBasis, string> = {
  days_from_document_date: "dueDaysFromDocumentDate",
  days_from_end_of_month: "dueDaysFromEndOfMonth",
  fixed_day_of_month: "dueFixedDayOfMonth",
};

export const AGEING_BASIS_MESSAGE: Record<AgeingBasis, string> = {
  document_date: "basisDocumentDate",
  due_date: "basisDueDate",
};

export interface Partner {
  id: number;
  name: string;
  customer_code: string | null;
  supplier_code: string | null;
  is_customer: boolean;
  is_supplier: boolean;
  tin: string | null;
  email: string | null;
  phone: string | null;
  address: Record<string, unknown> | null;
  notes: string | null;
  currency_id: number | null;
  is_active: boolean;
}

/** The code that identifies a partner depends on which role you are looking at it through. */
export function partnerCode(partner: Partner, role: PartnerRole): string | null {
  return role === "ar" ? partner.customer_code : partner.supplier_code;
}

export interface PartnerCreatePayload {
  name: string;
  customer_code?: string | null;
  supplier_code?: string | null;
  tin?: string | null;
  email?: string | null;
  phone?: string | null;
  notes?: string | null;
  currency_id?: number | null;
}

export interface PartnerUpdatePayload {
  name?: string;
  customer_code?: string;
  clear_customer_code?: boolean;
  supplier_code?: string;
  clear_supplier_code?: boolean;
  tin?: string | null;
  email?: string | null;
  phone?: string | null;
  notes?: string | null;
  currency_id?: number;
  clear_currency?: boolean;
  is_active?: boolean;
}

export interface RoleSettings {
  id: number;
  partner_id: number;
  control_account_id: number | null;
  payment_terms_id: number | null;
  credit_limit: string | null;
  sales_rep_id: number | null;
  default_tax_code_id: number | null;
  default_branch_id: number | null;
  default_project_id: number | null;
  default_gl_account_id: number | null;
  tax_mode: TaxMode;
  is_on_hold: boolean;
}

export interface RoleSettingsPayload {
  control_account_id: number | null;
  payment_terms_id: number | null;
  credit_limit: string | null;
  sales_rep_id: number | null;
  default_tax_code_id: number | null;
  default_branch_id: number | null;
  default_project_id: number | null;
  default_gl_account_id: number | null;
  tax_mode: TaxMode;
  is_on_hold: boolean;
}

export interface PartnerContact {
  id: number;
  partner_id: number;
  name: string;
  role: string | null;
  email: string | null;
  phone: string | null;
  notes: string | null;
  is_primary: boolean;
  is_active: boolean;
}

export interface ContactPayload {
  name: string;
  role?: string | null;
  email?: string | null;
  phone?: string | null;
  notes?: string | null;
  is_primary?: boolean;
  is_active?: boolean;
}

/** One `audit_log` row against a partner — the rename screen's history table. */
export interface PartnerAuditRecord {
  id: number;
  action: string;
  at: string;
  actor_email: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface SalesRep {
  id: number;
  code: string;
  name: string;
  email: string | null;
  is_active: boolean;
}

export interface PaymentTerms {
  id: number;
  code: string;
  name: string;
  due_basis: DueBasis;
  due_days: number;
  due_day_of_month: number | null;
  discount_percent: string;
  discount_days: number;
  is_active: boolean;
}

/** PUT replaces the whole row, so every field travels on an edit. */
export interface PaymentTermsPayload {
  code: string;
  name: string;
  due_basis: DueBasis;
  due_days: number;
  due_day_of_month: number | null;
  discount_percent: string;
  discount_days: number;
  is_active?: boolean;
}

export interface AgeingBucket {
  id: number;
  sequence: number;
  label: string;
  from_days: number;
  to_days: number | null;
}

export interface AgeingBucketInput {
  label: string;
  from_days: number;
  to_days: number | null;
}

export interface AgeingBucketSet {
  id: number;
  code: string;
  name: string;
  basis: AgeingBasis;
  is_default: boolean;
  is_active: boolean;
  buckets: AgeingBucket[];
}

export interface AgeingBucketSetCreatePayload {
  code: string;
  name: string;
  basis: AgeingBasis;
  is_default: boolean;
  buckets: AgeingBucketInput[];
}

export interface AgeingBucketSetUpdatePayload {
  name?: string;
  basis?: AgeingBasis;
  is_default?: boolean;
  is_active?: boolean;
  buckets?: AgeingBucketInput[];
}

export interface ArApDefaults {
  ar_control_account_id: number | null;
  ap_control_account_id: number | null;
  realized_fx_gain_account_id: number | null;
  realized_fx_loss_account_id: number | null;
  settlement_discount_granted_account_id: number | null;
  settlement_discount_received_account_id: number | null;
  post_dated_receivable_account_id: number | null;
  post_dated_payable_account_id: number | null;
}

export type ArApDefaultsPayload = Partial<ArApDefaults>;

// --- Documents ------------------------------------------------------------------------------

/** The six kinds, as the role/kind matrix the one backend service handles. */
export type DocumentKind = "invoice" | "credit_note" | "settlement";
export type InstrumentType = "cash" | "bank" | "cheque" | "mobile" | "other";

export const INSTRUMENT_TYPES: readonly InstrumentType[] = [
  "cash",
  "bank",
  "cheque",
  "mobile",
  "other",
] as const;

export interface DocumentLinePayload {
  description?: string | null;
  quantity: string;
  unit_price: string;
  discount_percent: string;
  gl_account_id?: number | null;
  tax_code_id?: number | null;
  branch_id?: number | null;
  project_id?: number | null;
}

export interface DocumentCreatePayload {
  kind: DocumentKind;
  partner_id: number;
  document_date: string;
  due_date?: string | null;
  currency_id?: number | null;
  exchange_rate?: string | null;
  branch_id?: number | null;
  project_id?: number | null;
  payment_terms_id?: number | null;
  sales_rep_id?: number | null;
  tax_mode?: TaxMode | null;
  reference?: string | null;
  description: string;
  lines?: DocumentLinePayload[];
  amount?: string | null;
  cash_account_id?: number | null;
  instrument_type?: InstrumentType | null;
  maturity_date?: string | null;
}

export interface PartnerDocument {
  id: number;
  role: string;
  kind: string;
  number: string;
  partner_id: number;
  journal_entry_id: number;
  document_date: string;
  due_date: string | null;
  currency_id: number;
  exchange_rate: string;
  total_amount: string;
  open_amount: string;
  direction: number;
  status: string;
}

// --- Allocation -----------------------------------------------------------------------------

export interface OpenItem {
  document_id: number;
  number: string;
  kind: string;
  document_date: string;
  due_date: string | null;
  currency_id: number;
  total_amount: string;
  open_amount: string;
  open_base_amount: string;
  direction: number;
  days_overdue: number;
}

export interface PartnerEnquiry {
  role: string;
  partner_id: number;
  partner_name: string;
  partner_code: string | null;
  as_of: string;
  balance_base: string;
  credit_limit: string | null;
  credit_available: string | null;
  open_items: OpenItem[];
}

export interface AllocationPairPayload {
  debit_document_id: number;
  credit_document_id: number;
  amount: string;
  discount_amount?: string;
}

export interface AllocationPayload {
  partner_id: number;
  allocation_date: string;
  description?: string | null;
  pairs: AllocationPairPayload[];
}

export interface AutoAllocatePayload {
  partner_id: number;
  allocation_date: string;
  credit_document_id?: number | null;
  description?: string | null;
}

export interface AllocationPreviewLine {
  debit_document_id: number;
  debit_number: string;
  credit_document_id: number;
  credit_number: string;
  amount: string;
  discount_amount: string;
  fx_base_amount: string;
}

/** One journal line the allocation will write. This comes from the backend's own `prepare()`
 * — the function `allocate()` posts — so the preview cannot disagree with the posting. */
export interface AllocationPreviewPosting {
  gl_account_id: number;
  description: string;
  base_amount: string;
}

export interface AllocationPreview {
  currency_id: number;
  lines: AllocationPreviewLine[];
  postings: AllocationPreviewPosting[];
  total_allocated: string;
  total_discount: string;
  total_fx_base: string;
}

export interface Allocation {
  id: number;
  number: string;
  role: string;
  partner_id: number;
  currency_id: number;
  allocation_date: string;
  journal_entry_id: number | null;
}

// --- Journal batches ------------------------------------------------------------------------

export interface BatchLinePayload {
  partner_id: number;
  contra_account_id: number;
  /** Signed in the partner's normal direction; negative is the credit side. */
  amount: string;
  description: string;
  tax_code_id?: number | null;
  branch_id?: number | null;
  project_id?: number | null;
  due_date?: string | null;
}

export interface BatchPayload {
  batch_date: string;
  reference?: string | null;
  lines: BatchLinePayload[];
}

export interface BatchResult {
  batch_date: string;
  reference: string | null;
  documents: PartnerDocument[];
}
