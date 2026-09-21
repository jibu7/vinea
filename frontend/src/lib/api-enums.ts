/**
 * GENERATED FILE — do not edit by hand.
 *
 * Every enum value that crosses the API, derived from the Python enums in `backend/app/models`
 * by `backend/app/scripts/export_api_enums.py`. Regenerate with:
 *
 *     cd backend && uv run python -m app.scripts.export_api_enums
 *
 * `backend/tests/test_api_enums_export.py` fails if this file and those enums disagree, so a
 * value here cannot drift from the one the server actually sends.
 *
 * Import these instead of writing the string. P5 step 6 shipped two screens comparing
 * `control_type` against `"INV"` when the wire value is `"inventory"`: the pickers were empty
 * and a mapped account read "Not set" — a screen that rendered perfectly and was wrong.
 * `src/lib/api-enums.test.ts` keeps those literals out of screen files.
 *
 * Each export is both a value and a type:
 *
 *     import { ControlType } from "@/lib/api-enums";
 *     account.control_type === ControlType.INVENTORY      // value
 *     function f(t: ControlType) {}                        // type — the union of the values
 */


export const CompanyStatus = {
  ACTIVE: "active",
  SUSPENDED: "suspended",
  CLOSED: "closed",
} as const;
export type CompanyStatus = (typeof CompanyStatus)[keyof typeof CompanyStatus];

export const MembershipStatus = {
  PENDING: "pending",
  ACTIVE: "active",
  SUSPENDED: "suspended",
} as const;
export type MembershipStatus = (typeof MembershipStatus)[keyof typeof MembershipStatus];

export const PeriodStatus = {
  FUTURE: "future",
  OPEN: "open",
  CLOSED: "closed",
  LOCKED: "locked",
} as const;
export type PeriodStatus = (typeof PeriodStatus)[keyof typeof PeriodStatus];

export const JobStatus = {
  QUEUED: "queued",
  RUNNING: "running",
  SUCCEEDED: "succeeded",
  FAILED: "failed",
} as const;
export type JobStatus = (typeof JobStatus)[keyof typeof JobStatus];

export const TaxNature = {
  OUTPUT: "output",
  INPUT: "input",
  EXEMPT: "exempt",
  ZERO_RATED: "zero_rated",
} as const;
export type TaxNature = (typeof TaxNature)[keyof typeof TaxNature];

export const AccountClass = {
  ASSET: "asset",
  LIABILITY: "liability",
  EQUITY: "equity",
  INCOME: "income",
  EXPENSE: "expense",
} as const;
export type AccountClass = (typeof AccountClass)[keyof typeof AccountClass];

export const ControlType = {
  BANK: "bank",
  CASH: "cash",
  AR: "ar",
  AP: "ap",
  INVENTORY: "inventory",
  GRN_ACCRUAL: "grn_accrual",
} as const;
export type ControlType = (typeof ControlType)[keyof typeof ControlType];

export const JournalStatus = {
  DRAFT: "draft",
  POSTED: "posted",
} as const;
export type JournalStatus = (typeof JournalStatus)[keyof typeof JournalStatus];

export const PartnerRole = {
  AR: "ar",
  AP: "ap",
} as const;
export type PartnerRole = (typeof PartnerRole)[keyof typeof PartnerRole];

export const TaxMode = {
  EXCLUSIVE: "exclusive",
  INCLUSIVE: "inclusive",
} as const;
export type TaxMode = (typeof TaxMode)[keyof typeof TaxMode];

export const DueBasis = {
  DAYS_FROM_DOCUMENT_DATE: "days_from_document_date",
  DAYS_FROM_END_OF_MONTH: "days_from_end_of_month",
  FIXED_DAY_OF_MONTH: "fixed_day_of_month",
} as const;
export type DueBasis = (typeof DueBasis)[keyof typeof DueBasis];

export const AgeingBasis = {
  DOCUMENT_DATE: "document_date",
  DUE_DATE: "due_date",
} as const;
export type AgeingBasis = (typeof AgeingBasis)[keyof typeof AgeingBasis];

export const DocumentKind = {
  INVOICE: "invoice",
  CREDIT_NOTE: "credit_note",
  SETTLEMENT: "settlement",
} as const;
export type DocumentKind = (typeof DocumentKind)[keyof typeof DocumentKind];

export const DocumentStatus = {
  POSTED: "posted",
  REVERSED: "reversed",
} as const;
export type DocumentStatus = (typeof DocumentStatus)[keyof typeof DocumentStatus];

export const InstrumentType = {
  CASH: "cash",
  BANK: "bank",
  CHEQUE: "cheque",
  MOBILE: "mobile",
  OTHER: "other",
} as const;
export type InstrumentType = (typeof InstrumentType)[keyof typeof InstrumentType];

export const BackorderPolicy = {
  ALLOW: "allow",
  BLOCK: "block",
} as const;
export type BackorderPolicy = (typeof BackorderPolicy)[keyof typeof BackorderPolicy];

export const SalesOrderStatus = {
  OPEN: "open",
  PARTIALLY_INVOICED: "partially_invoiced",
  INVOICED: "invoiced",
  CLOSED: "closed",
  CANCELLED: "cancelled",
} as const;
export type SalesOrderStatus = (typeof SalesOrderStatus)[keyof typeof SalesOrderStatus];

export const PurchaseOrderStatus = {
  OPEN: "open",
  PARTIALLY_RECEIVED: "partially_received",
  RECEIVED: "received",
  CLOSED: "closed",
  CANCELLED: "cancelled",
} as const;
export type PurchaseOrderStatus = (typeof PurchaseOrderStatus)[keyof typeof PurchaseOrderStatus];

export const GrnStatus = {
  RECEIVED: "received",
  PARTIALLY_MATCHED: "partially_matched",
  MATCHED: "matched",
  REVERSED: "reversed",
} as const;
export type GrnStatus = (typeof GrnStatus)[keyof typeof GrnStatus];

export const LandedCostBasis = {
  VALUE: "value",
  QUANTITY: "quantity",
  WEIGHT: "weight",
} as const;
export type LandedCostBasis = (typeof LandedCostBasis)[keyof typeof LandedCostBasis];

export const LandedCostStatus = {
  POSTED: "posted",
  REVERSED: "reversed",
} as const;
export type LandedCostStatus = (typeof LandedCostStatus)[keyof typeof LandedCostStatus];

export const ItemType = {
  STOCK: "stock",
  SERVICE: "service",
  NON_STOCK: "non_stock",
  KIT: "kit",
} as const;
export type ItemType = (typeof ItemType)[keyof typeof ItemType];

export const NegativeStockPolicy = {
  BLOCK: "block",
  ALLOW: "allow",
} as const;
export type NegativeStockPolicy = (typeof NegativeStockPolicy)[keyof typeof NegativeStockPolicy];

export const InventoryTransactionKind = {
  ADJUSTMENT_IN: "adjustment_in",
  ADJUSTMENT_OUT: "adjustment_out",
  REVALUATION: "revaluation",
  TRANSFER: "transfer",
  COUNT_VARIANCE: "count_variance",
  OPENING_BALANCE: "opening_balance",
} as const;
export type InventoryTransactionKind = (typeof InventoryTransactionKind)[keyof typeof InventoryTransactionKind];

export const InventoryDocumentStatus = {
  POSTED: "posted",
  REVERSED: "reversed",
} as const;
export type InventoryDocumentStatus = (typeof InventoryDocumentStatus)[keyof typeof InventoryDocumentStatus];

export const StockTransferStatus = {
  IN_TRANSIT: "in_transit",
  COMPLETED: "completed",
  CANCELLED: "cancelled",
  REVERSED: "reversed",
} as const;
export type StockTransferStatus = (typeof StockTransferStatus)[keyof typeof StockTransferStatus];

export const StockCountStatus = {
  COUNTING: "counting",
  COMPLETED: "completed",
  CANCELLED: "cancelled",
} as const;
export type StockCountStatus = (typeof StockCountStatus)[keyof typeof StockCountStatus];

export const FiscalProfile = {
  VSDC: "vsdc",
  OSDC: "osdc",
} as const;
export type FiscalProfile = (typeof FiscalProfile)[keyof typeof FiscalProfile];

export const FiscalEnvironment = {
  TEST: "test",
  PRODUCTION: "production",
} as const;
export type FiscalEnvironment = (typeof FiscalEnvironment)[keyof typeof FiscalEnvironment];

export const FiscalDeviceStatus = {
  PENDING: "pending",
  ACTIVE: "active",
  SUSPENDED: "suspended",
} as const;
export type FiscalDeviceStatus = (typeof FiscalDeviceStatus)[keyof typeof FiscalDeviceStatus];

export const FiscalSyncKind = {
  CODES: "codes",
  ITEM_CLASSES: "item_classes",
  BRANCHES: "branches",
  NOTICES: "notices",
  PURCHASES: "purchases",
  IMPORTS: "imports",
  STOCK_MOVES: "stock_moves",
} as const;
export type FiscalSyncKind = (typeof FiscalSyncKind)[keyof typeof FiscalSyncKind];

export const FiscalOutboxKind = {
  ITEM: "item",
  SALE: "sale",
  REFUND: "refund",
  PURCHASE: "purchase",
  PURCHASE_CONFIRM: "purchase_confirm",
  STOCK_IO: "stock_io",
  STOCK_MASTER: "stock_master",
  IMPORT_UPDATE: "import_update",
} as const;
export type FiscalOutboxKind = (typeof FiscalOutboxKind)[keyof typeof FiscalOutboxKind];

export const FiscalOutboxStatus = {
  QUEUED: "queued",
  SENDING: "sending",
  SENT: "sent",
  FAILED: "failed",
  UNKNOWN: "unknown",
  NEEDS_RECEIPT: "needs_receipt",
  CANCELLED: "cancelled",
} as const;
export type FiscalOutboxStatus = (typeof FiscalOutboxStatus)[keyof typeof FiscalOutboxStatus];

export const FiscalReceiptType = {
  NORMAL_SALE: "NS",
  NORMAL_REFUND: "NR",
} as const;
export type FiscalReceiptType = (typeof FiscalReceiptType)[keyof typeof FiscalReceiptType];

export const FiscalTaxType = {
  A: "A",
  B: "B",
  C: "C",
  D: "D",
} as const;
export type FiscalTaxType = (typeof FiscalTaxType)[keyof typeof FiscalTaxType];

export const FiscalItemTypeCode = {
  RAW_MATERIAL: "1",
  FINISHED_PRODUCT: "2",
  SERVICE: "3",
} as const;
export type FiscalItemTypeCode = (typeof FiscalItemTypeCode)[keyof typeof FiscalItemTypeCode];

export const PaymentMethod = {
  CASH: "cash",
  CREDIT: "credit",
  CASH_CREDIT: "cash_credit",
  BANK_CHEQUE: "bank_cheque",
  CARD: "card",
  MOBILE_MONEY: "mobile_money",
  OTHER: "other",
} as const;
export type PaymentMethod = (typeof PaymentMethod)[keyof typeof PaymentMethod];

export const FiscalFeedDecision = {
  PENDING: "pending",
  ACCEPTED: "accepted",
  REJECTED: "rejected",
} as const;
export type FiscalFeedDecision = (typeof FiscalFeedDecision)[keyof typeof FiscalFeedDecision];

export const FiscalImportStatus = {
  PENDING: "pending",
  APPROVED: "approved",
  REJECTED: "rejected",
} as const;
export type FiscalImportStatus = (typeof FiscalImportStatus)[keyof typeof FiscalImportStatus];

export const VatReturnStatus = {
  POSTED: "posted",
  REVERSED: "reversed",
} as const;
export type VatReturnStatus = (typeof VatReturnStatus)[keyof typeof VatReturnStatus];

export const FxRevaluationStatus = {
  POSTED: "posted",
  REVERSED: "reversed",
} as const;
export type FxRevaluationStatus = (typeof FxRevaluationStatus)[keyof typeof FxRevaluationStatus];

export const FxRevaluationRole = {
  AR: "ar",
  AP: "ap",
  BOTH: "both",
  BANK: "bank",
  ALL: "all",
} as const;
export type FxRevaluationRole = (typeof FxRevaluationRole)[keyof typeof FxRevaluationRole];

export const BankAccountKind = {
  BANK: "bank",
  CASH: "cash",
} as const;
export type BankAccountKind = (typeof BankAccountKind)[keyof typeof BankAccountKind];

export const StatementSource = {
  CSV: "csv",
  MANUAL: "manual",
} as const;
export type StatementSource = (typeof StatementSource)[keyof typeof StatementSource];

export const StatementStatus = {
  OPEN: "open",
  VOID: "void",
} as const;
export type StatementStatus = (typeof StatementStatus)[keyof typeof StatementStatus];

export const StatementFormatPreset = {
  GENERIC: "generic",
  CUSTOM: "custom",
} as const;
export type StatementFormatPreset = (typeof StatementFormatPreset)[keyof typeof StatementFormatPreset];

export const StatementAmountMode = {
  DEBIT_CREDIT: "debit_credit",
  SIGNED: "signed",
} as const;
export type StatementAmountMode = (typeof StatementAmountMode)[keyof typeof StatementAmountMode];

export const StatementSignConvention = {
  CREDIT_POSITIVE: "credit_positive",
  DEBIT_POSITIVE: "debit_positive",
} as const;
export type StatementSignConvention = (typeof StatementSignConvention)[keyof typeof StatementSignConvention];

export const BankMatchKind = {
  AUTO: "auto",
  MANUAL: "manual",
  TICK: "tick",
  POSTED: "posted",
} as const;
export type BankMatchKind = (typeof BankMatchKind)[keyof typeof BankMatchKind];

export const BankMatchRule = {
  REFERENCE: "reference",
  PAYMENT_RUN: "payment_run",
  AMOUNT_DATE: "amount_date",
  POSTED_FROM_STATEMENT: "posted_from_statement",
  MANUAL: "manual",
  TICK: "tick",
} as const;
export type BankMatchRule = (typeof BankMatchRule)[keyof typeof BankMatchRule];

export const ReconciliationStatus = {
  OPEN: "open",
  LOCKED: "locked",
} as const;
export type ReconciliationStatus = (typeof ReconciliationStatus)[keyof typeof ReconciliationStatus];

export const PaymentRunStatus = {
  POSTED: "posted",
  REVERSED: "reversed",
} as const;
export type PaymentRunStatus = (typeof PaymentRunStatus)[keyof typeof PaymentRunStatus];
