/**
 * Fiscalization wire types (P7).
 *
 * Step 6's half: the device, the two synced code tables and the TIN lookup — what Maintenance
 * needs before anything can be fiscalized at all. Step 7 adds the transaction half: the queue
 * and its rows, the receipt a document prints, the purchase feed, the import register, and the
 * two facts a posting screen needs before it can draw a purchase-code field at all.
 *
 * Enum-valued fields import from `@/lib/api-enums`, which is generated from the Python enums
 * and never typed by hand (`backend/app/scripts/export_api_enums.py`, with
 * `backend/tests/test_api_enums_export.py` as the drift gate). A profile, an environment or a
 * device status spelled out in a screen file is the P5 step 6 defect exactly, and
 * `src/lib/api-enums.test.ts` now fails on one.
 */
import type {
  FiscalDeviceStatus,
  FiscalEnvironment,
  FiscalFeedDecision,
  FiscalImportStatus,
  FiscalItemTypeCode,
  FiscalOutboxKind,
  FiscalOutboxStatus,
  FiscalProfile,
  FiscalReceiptType,
  FiscalSyncKind,
  FiscalTaxType,
  PaymentMethod,
} from "@/lib/api-enums";

export type {
  FiscalDeviceStatus,
  FiscalEnvironment,
  FiscalFeedDecision,
  FiscalImportStatus,
  FiscalItemTypeCode,
  FiscalOutboxKind,
  FiscalOutboxStatus,
  FiscalProfile,
  FiscalReceiptType,
  FiscalSyncKind,
  FiscalTaxType,
  PaymentMethod,
};

/**
 * One EBM device, one branch.
 *
 * **No key, ever.** `cmc_key`, `intrl_key` and `sign_key` are the only secrets this phase
 * holds and no response model has a field for one — `has_keys` answers the question a screen
 * actually asks ("is this device holding its keys") without going near the values. The
 * backend pins that over the model rather than over one endpoint's output
 * (`tests/fiscal/test_key_redaction.py`), and this interface is the frontend saying the same
 * thing: there is nowhere here to put a key, so there is nothing to render by accident.
 */
export interface FiscalDevice {
  id: number;
  branch_id: number;
  profile: FiscalProfile;
  environment: FiscalEnvironment;
  base_url: string;
  /** Copied from the company at initialization; a later company rename refuses with
   * `tin_mismatch` rather than sending sales under a number the device never registered. */
  tin: string | null;
  bhf_id: string;
  dvc_srl_no: string;
  mrc_no: string | null;
  sdc_id: string | null;
  dvc_id: string | null;
  status: FiscalDeviceStatus;
  /** Per sync kind, the last thing the authority said it had published. */
  watermarks: Record<string, string>;
  last_success_at: string | null;
  last_error: string | null;
  has_keys: boolean;
}

export interface DeviceCreatePayload {
  branch_id: number;
  profile: FiscalProfile;
  environment: FiscalEnvironment;
  base_url: string;
  dvc_srl_no: string;
  bhf_id: string;
}

export interface DeviceSyncResult {
  device_id: number;
  kind: FiscalSyncKind;
  rows: number;
  watermark: string | null;
}

/** A row of one of the authority's own code tables (§4.6 and its neighbours). Synced, never
 * typed: `code_class` 04 is the tax classes A–D, 10 is the quantity units. */
export interface FiscalCode {
  code_class: string;
  code_class_name: string | null;
  code: string;
  name: string;
  is_active: boolean;
}

/** The authority's item classification — tens of thousands of rows, so the picker is a
 * typeahead over the server rather than a list the browser holds. */
export interface FiscalItemClass {
  item_cls_cd: string;
  item_cls_nm: string;
  item_cls_lvl: number | null;
  tax_ty_cd: string | null;
}

/** What the authority says about a TIN, at a moment. `found: false` is an answer, not a
 * failure — the authority's own "no such taxpayer" (884) arrives this way. */
export interface TinLookup {
  tin: string;
  found: boolean;
  name: string | null;
  status: string | null;
}

/** An item and what RRA holds about it. The Items screen reads the registration half of this
 * and shows it beside the four fields that produce it. */
export interface ItemRegistration {
  item_id: number;
  item_code: string;
  item_name: string;
  item_type: string;
  registered: boolean;
  item_cd: string | null;
  item_cls_cd: string | null;
  item_ty_cd: string | null;
  orgn_nat_cd: string | null;
  pkg_unit_cd: string | null;
  qty_unit_cd: string | null;
  tax_ty_cd: string | null;
  default_price_inclusive: string | null;
  barcode: string | null;
  active: boolean;
  registered_at: string | null;
  pending_rows: number;
  last_error: string | null;
}

/**
 * The code classes these screens read, named once rather than spelled at each call site.
 *
 * They are the authority's own numbering (VSDC §4) and the backend names the same four in
 * `app/fiscal/rwanda/codes.py`; a screen asking for "the packaging units" should not carry a
 * bare `"17"` any more than it should carry a bare `"vsdc"`.
 */
export const CODE_CLASS = {
  /** §4.1 — the tax classes A–D, which a Vinea tax code maps onto. */
  TAX_TYPE: "04",
  /** §4.4 — nations, the item's country of origin. */
  NATION: "05",
  /** §4.6 — quantity units, what a Vinea unit of measure maps onto. */
  QUANTITY_UNIT: "10",
  /** §4.5 — packaging units. */
  PACKAGING_UNIT: "17",
  /** §4.16 — the reasons a refund may give for itself, on a credit note. */
  REFUND_REASON: "32",
} as const;

// --- What a posting screen needs (P7 step 7) -------------------------------------------------

/** One of the authority's published refund reasons, as a code and a name. */
export interface RefundReason {
  code: string;
  name: string;
}

/**
 * The two facts the Invoice and Credit-note screens draw their fiscal fields from.
 *
 * Its own endpoint rather than a corner of the device listing, and the reason is the **Sales
 * Manager** role: it holds `ar:transactions_post` and neither fiscal permission, so reading
 * `/fiscal/devices` to find out whether a purchase code is required would mean every sales
 * manager needed the authority to reconfigure the devices in order to key an invoice.
 */
export interface FiscalDocumentContext {
  /** At least one **active** device (decision 2). A registered-but-uninitialized one declares
   * nothing, and a screen demanding a purchase code for it would be demanding a field for a
   * company whose sales RRA has never heard of. */
  fiscalized: boolean;
  refund_reasons: RefundReason[];
}

// --- The queue (P7 decision 4) ---------------------------------------------------------------

export interface QueueStatusCount {
  status: FiscalOutboxStatus;
  rows: number;
}

/** The row at the front of a device's queue. Every row behind it is waiting on this one —
 * per-device FIFO, one in flight, because sending out of order is worse than waiting. */
export interface QueueHead {
  row_id: number;
  kind: FiscalOutboxKind;
  status: FiscalOutboxStatus;
  sequence_no: number;
  attempts: number;
  next_attempt_at: string | null;
  last_result_cd: string | null;
  last_error: string | null;
}

export interface QueueDevice {
  device_id: number;
  branch_id: number;
  branch_code: string;
  branch_name: string;
  status: string;
  profile: string;
  environment: string;
  sdc_id: string | null;
  mrc_no: string | null;
  last_success_at: string | null;
  last_error: string | null;
  /** The oldest unsent row is over 24 hours old — VSDC §2.2 item 4, after which the device
   * stops issuing. */
  offline: boolean;
  oldest_queued_at: string | null;
  oldest_queued_age_seconds: number | null;
  pending_rows: number;
  /** The head is in a state that will not move by itself: a person has to act. */
  blocked: boolean;
  counts: QueueStatusCount[];
  head: QueueHead | null;
}

export interface QueueRow {
  row_id: number;
  device_id: number;
  kind: FiscalOutboxKind;
  status: FiscalOutboxStatus;
  sequence_no: number;
  invc_no: number | null;
  sar_no: number | null;
  attempts: number;
  next_attempt_at: string | null;
  last_result_cd: string | null;
  last_error: string | null;
  sent_at: string | null;
  created_at: string | null;
  source_doc_type: string | null;
  source_doc_id: number | null;
  document_number: string | null;
  document_id: number | null;
  partner_name: string | null;
  receipt_id: number | null;
}

export interface QueueAction {
  at: string;
  action: string;
  actor_email: string | null;
  detail: Record<string, unknown>;
}

/** The row, what was sent, what came back, and who has touched it. `request` and `response`
 * are redacted at enqueue and again on the way out, so a screen showing a payload can never be
 * where a device key escapes. */
export interface QueueRowDetail {
  row: QueueRow;
  request: Record<string, unknown>;
  response: Record<string, unknown> | null;
  resolved_by_email: string | null;
  resolution_note: string | null;
  actions: QueueAction[];
}

/** The six fields read off the authority's portal, plus the note that says who read them.
 * `fields` goes to the adapter untouched: an operator copying a sales response is keying one,
 * and a second parser on the client would eventually disagree with `normalize_receipt()`. */
export interface AttachReceiptPayload {
  fields: Record<string, string>;
  note: string;
}

// --- Receipts (P7 decision 5 and 11) ---------------------------------------------------------

export interface FiscalReceipt {
  receipt_id: number;
  device_id: number;
  document_id: number;
  document_number: string;
  document_date: string;
  partner_id: number;
  partner_name: string;
  receipt_type: FiscalReceiptType;
  invc_no: number;
  org_invc_no: number | null;
  rcpt_no: number;
  tot_rcpt_no: number;
  /** `rcptNo/totRcptNo LABEL`, as the paper prints it — what somebody holding one will type. */
  receipt_number: string;
  sdc_id: string;
  mrc_no: string | null;
  sdc_datetime: string;
  intrl_data: string;
  rcpt_sign: string;
  qr_payload: string;
  copy_count: number;
  total_amount: string;
  base_total_amount: string;
  currency_id: number;
  journal_entry_id: number | null;
}

/** One programmed rate on the printed receipt. Every rate above zero prints on every receipt
 * and a zero rate prints only when it was used (CIS §7.22–7.23) — `used` is that decision,
 * made on the server so the template prints what it is given. */
export interface ReceiptClassLine {
  tax_class: string;
  rate: string;
  taxable: string;
  tax: string;
  used: boolean;
}

/** Everything the CIS §13/§14 layout prints, with nobody's field names in it. */
export interface ReceiptBlock {
  document_id: number;
  document_number: string;
  document_date: string;
  receipt_id: number;
  receipt_type: FiscalReceiptType;
  /** `NS` or `NR` — the label §5 gives the receipt, printed beside the counters. */
  label: string;
  rcpt_no: number;
  tot_rcpt_no: number;
  receipt_number: string;
  invc_no: number;
  /** The original receipt's total counter on a refund — `REF. NORMAL RECEIPT#` (§14). */
  refund_of_tot_rcpt_no: number | null;
  sdc_id: string;
  mrc_no: string | null;
  sdc_datetime: string;
  intrl_data: string;
  rcpt_sign: string;
  qr_payload: string;
  taxpayer_name: string;
  taxpayer_tin: string | null;
  branch_name: string;
  branch_address: string | null;
  customer_name: string;
  customer_tin: string | null;
  payment_method: PaymentMethod | null;
  purchase_code: string | null;
  items_count: number;
  discount_total: string;
  taxable_total: string;
  tax_total: string;
  gross_total: string;
  classes: ReceiptClassLine[];
  /** True on every print after the first: the template adds `COPY` and the warning line. */
  is_copy: boolean;
  copy_count: number;
}

// --- The purchase feed and the import register (P7 decision 9) --------------------------------

export interface PurchaseFeedRow {
  id: number;
  device_id: number;
  spplr_tin: string;
  spplr_nm: string | null;
  spplr_bhf_id: string | null;
  spplr_invc_no: number;
  sales_dt: string | null;
  total_taxable_amount: string;
  total_tax_amount: string;
  total_amount: string;
  fetched_at: string;
  decision: FiscalFeedDecision;
  decided_at: string | null;
  ap_document_id: number | null;
}

/** What a decision did — including the one case where it deliberately queued nothing, because
 * the linked document's own registration is already with RRA and confirming would register the
 * same invoice twice. */
export interface FeedDecisionResult {
  row: PurchaseFeedRow;
  confirmation_row_id: number | null;
  cancelled_row_id: number | null;
  note: string;
}

export interface ImportDeclaration {
  id: number;
  device_id: number;
  task_cd: string;
  dcl_no: string;
  dcl_de: string | null;
  item_seq: number;
  hs_cd: string | null;
  item_nm: string | null;
  orgn_nat_cd: string | null;
  pkg: string | null;
  pkg_unit_cd: string | null;
  qty: string | null;
  qty_unit_cd: string | null;
  spplr_nm: string | null;
  agnt_nm: string | null;
  invc_fcur_amt: string | null;
  invc_fcur_cd: string | null;
  invc_fcur_exc_rt: string | null;
  fetched_at: string;
  status: FiscalImportStatus;
  item_id: number | null;
  decided_at: string | null;
}
