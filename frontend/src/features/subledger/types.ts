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
