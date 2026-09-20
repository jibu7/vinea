/** Mirrors backend/app/schemas/subledger.py — snake_case, matching the raw JSON wire shape.
 *
 * AR and AP share every shape here: the role is a path segment on the API and a prop on the
 * screens, never a second set of types. */

import { PaymentMethod as PaymentMethodValues } from "@/lib/api-enums";
import type { PaymentMethod } from "@/lib/api-enums";

export type { PaymentMethod };

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

/**
 * How a document is paid (P7 decision 7), in the order the picker offers them.
 *
 * Derived from the generated enum rather than re-listed: the values are the authority's seven
 * payment types under Vinea's own names, and the adapter maps them onto `pmtTyCd 01–07`. A
 * `"cash"` typed into a screen is the P5 step-6 defect exactly, which `api-enums.test.ts` now
 * fails on. Labels live in the catalogue under `arap.documents.common.paymentMethodLabel.*`.
 */
export const PAYMENT_METHODS: readonly PaymentMethod[] = Object.values(
  PaymentMethodValues,
) as readonly PaymentMethod[];

/** One component of one kit line, in the component item's **base** unit — what ships, not a
 * per-kit rate. A screen sends these only when it is invoicing a sales order whose kit line
 * was broken up by hand; keyed straight onto an invoice, a kit explodes from its catalogue
 * definition and this stays empty (P6 decision 8). */
export interface DocumentKitComponentPayload {
  item_id: number;
  quantity: string;
  warehouse_id?: number | null;
  project_id?: number | null;
  description?: string | null;
  sales_order_line_id?: number | null;
}

export interface DocumentLinePayload {
  description?: string | null;
  quantity: string;
  /** Optional on an **item** line — the catalogue price is used when it is left out — and
   * required on a GL line, which has no catalogue to fall back on (P6 decision 1). */
  unit_price?: string | null;
  discount_percent: string;
  gl_account_id?: number | null;
  tax_code_id?: number | null;
  branch_id?: number | null;
  project_id?: number | null;
  // --- P6 item line ----------------------------------------------------------------------
  item_id?: number | null;
  uom_id?: number | null;
  warehouse_id?: number | null;
  /** What this line fulfils or matches. A flow prepares them; nothing on this screen invents
   * one, because a line claiming to relieve a receipt it was not built from would relieve the
   * accrual by the wrong amount. */
  sales_order_line_id?: number | null;
  purchase_order_line_id?: number | null;
  grn_line_id?: number | null;
  returns_line_id?: number | null;
  kit_components?: DocumentKitComponentPayload[] | null;
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
  // --- P7 fiscalization (decision 7) ---------------------------------------------------------
  /** Left unset it defaults from the payment terms — `credit` with them, `cash` without. */
  payment_method?: PaymentMethod | null;
  /** The customer's EBM purchase code, six characters. Required on a fiscalized sale to a
   * customer with a TIN, and refused **at post** rather than here: whether it is needed depends
   * on the company's devices and the partner's TIN. */
  purchase_code?: string | null;
  /** Which invoice a credit note refunds, when no line carries a `returns_line_id` that says
   * so. RRA registers a refund against exactly one original. */
  refund_of_document_id?: number | null;
  /** One of the authority's §4.16 reason codes. Required on a fiscalized credit note. */
  refund_reason?: string | null;
}

export interface PartnerDocument {
  id: number;
  role: string;
  kind: string;
  number: string;
  partner_id: number;
  journal_entry_id: number;
  /** The companion stock entry (P6 decision 2), when this document carried a valued stock
   * line. One document, two entries — and null is an ordinary answer: a document with no
   * valued stock line has no companion and claims no `STK-` number. */
  stock_entry_id: number | null;
  document_date: string;
  due_date: string | null;
  currency_id: number;
  exchange_rate: string;
  total_amount: string;
  open_amount: string;
  direction: number;
  status: string;
}

/** One line of a posted document, as the detail screen shows it. */
export interface PartnerDocumentLine {
  id: number;
  line_no: number;
  description: string | null;
  quantity: string;
  unit_price: string;
  discount_percent: string;
  gl_account_id: number;
  tax_code_id: number | null;
  branch_id: number;
  project_id: number | null;
  net_amount: string;
  tax_amount: string;
  gross_amount: string;
}

/**
 * What `GET /subledger/{role}/documents/{id}` returns — the header, its lines and both ends
 * of the reversal link. `PartnerDocument` above is the thinner body a *post* replies with;
 * this is the shape the document detail screen reads.
 */
export interface PartnerDocumentDetail extends PartnerDocument {
  transaction_type: string;
  doc_type: string;
  branch_id: number;
  project_id: number | null;
  payment_terms_id: number | null;
  sales_rep_id: number | null;
  tax_mode: string;
  control_account_id: number;
  reference: string | null;
  description: string;
  net_amount: string;
  tax_amount: string;
  base_total_amount: string;
  instrument_type: string | null;
  maturity_date: string | null;
  cash_account_id: number | null;
  matured_entry_id: number | null;
  reversal_entry_id: number | null;
  reversed_on: string | null;
  // --- P7 fiscalization ----------------------------------------------------------------------
  payment_method: PaymentMethod | null;
  purchase_code: string | null;
  refund_of_document_id: number | null;
  refund_reason: string | null;
  /** The receipt RRA signed for this document, once its queue row has been sent. `null` while
   * the row is still in flight — which is what the print refusal reads. */
  fiscal_receipt_id: number | null;
  lines: PartnerDocumentLine[];
}

// --- Allocation -----------------------------------------------------------------------------

export interface OpenItem {
  document_id: number;
  number: string;
  /** Shape in the ledger. Screens show `transaction_type_name` instead — a journal debit is
   * invoice-shaped and reads "AR journal". */
  kind: string;
  transaction_type: string;
  transaction_type_name: string;
  document_date: string;
  due_date: string | null;
  currency_id: number;
  total_amount: string;
  open_amount: string;
  open_base_amount: string;
  /** Settlement discount on offer at the allocation date. "0" once the window has closed. */
  discount_available: string;
  direction: number;
  days_overdue: number;
}

export interface PartnerEnquiryEntry {
  document_id: number;
  number: string;
  kind: string;
  transaction_type: string;
  transaction_type_name: string;
  document_date: string;
  due_date: string | null;
  currency_id: number;
  total_amount: string;
  open_amount: string;
  direction: number;
  journal_entry_id: number;
  /** Balance after this document, in base currency — the statement's running column. */
  running_base: string;
  status: string;
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
  entries: PartnerEnquiryEntry[];
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

/** A post-dated receipt or payment whose cash has not landed: posted, dated ahead, and with
 * no maturity entry against it. It is allocatable while it waits — it is a real claim; only
 * the cash is pending. */
export interface PendingInstrument {
  id: number;
  number: string;
  partner_id: number;
  document_date: string;
  maturity_date: string;
  instrument_type: string | null;
  currency_id: number;
  total_amount: string;
  open_amount: string;
  cash_account_id: number | null;
  /** Relative to the as-of date asked for: a maturity run on that date would bank it. */
  is_due: boolean;
  description: string;
}

export interface InstrumentRunRow {
  id: number;
  number: string;
  maturity_date: string;
  /** Only on skipped rows: why this one could not be banked. */
  reason: string | null;
}

export interface MaturityRunResult {
  as_of: string;
  matured_document_ids: number[];
  journal_entry_ids: number[];
  /** Not matured at `as_of`, and left exactly as they were. */
  waiting: InstrumentRunRow[];
  /** Due, but unbankable — no post-dated account configured, or none named on the document. */
  skipped: InstrumentRunRow[];
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

// --- Reports --------------------------------------------------------------------------------

export interface AgeingBucketAmount {
  label: string;
  from_days: number;
  to_days: number | null;
  amount: string;
}

export interface AgeingRow {
  partner_id: number;
  partner_code: string | null;
  partner_name: string;
  total: string;
  buckets: AgeingBucketAmount[];
}

export interface AgeingReport {
  role: string;
  as_of: string;
  bucket_set_id: number;
  bucket_set_code: string;
  basis: AgeingBasis;
  rows: AgeingRow[];
  totals: AgeingBucketAmount[];
  grand_total: string;
}

export interface AllocationLine {
  id: number;
  line_no: number;
  debit_document_id: number;
  credit_document_id: number;
  amount: string;
  discount_amount: string;
  discount_document_id: number | null;
  fx_base_amount: string;
}

/** One row per allocation *line*, as `GET /subledger/{role}/allocations` returns it — the
 * pairing is the unit of interest ("what settled what"), so the endpoint flattens rather than
 * nesting. It was declared here as a nested `{ ...allocation, lines: [] }` for a while, and
 * the Allocation report read `.lines` off it: undefined on every row, so the report rendered
 * "Nothing to report" over a subledger with allocations in it. */
export interface AllocationRecord {
  allocation_id: number;
  number: string;
  allocation_date: string;
  /** `null` when the allocation posted nothing — same currency, no discount, no difference. */
  journal_entry_id: number | null;
  partner_id: number;
  currency_id: number;
  debit_document_id: number;
  debit_number: string;
  credit_document_id: number;
  credit_number: string;
  amount: string;
  discount_amount: string;
  fx_base_amount: string;
  /** Set when this row *is* the mirror of an earlier allocation — not itself unallocatable. */
  reverses_allocation_id: number | null;
  /** Set when an unallocation already mirrors this one. */
  is_reversed: boolean;
}

export interface DocumentSummary {
  id: number;
  role: string;
  kind: string;
  transaction_type: string;
  number: string;
  partner_id: number;
  document_date: string;
  due_date: string | null;
  currency_id: number;
  total_amount: string;
  open_amount: string;
  direction: number;
  status: string;
  reference: string | null;
  description: string;
  /** The receipt RRA signed, or `null`. On the summary as well as the detail because the credit
   * note's *Refund of* picker is a listing: it offers the partner's **fiscalized** invoices,
   * and the only other way to know which those are is `/fiscal/receipts`, which an AR clerk
   * cannot read. */
  fiscal_receipt_id: number | null;
}

/** Cursor pagination (ADR-11): `next_cursor` is the last id of this page. */
export interface Page<T> {
  items: T[];
  next_cursor: number | null;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export interface JobRecord {
  id: number;
  kind: string;
  status: JobStatus;
  error: string | null;
  created_at: string;
}
