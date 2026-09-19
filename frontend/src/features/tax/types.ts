/**
 * VAT return wire types (P7 decision 12).
 *
 * Mirrors `backend/app/schemas/tax.py` — snake_case, the raw JSON shape.
 *
 * The thing to keep in mind reading these: **the return is a reconciliation, not a total.**
 * The sections are what goes on the form, and `ties` is what makes them believable — each VAT
 * account's movement over the range, what the return declares of it, and every journal line
 * making up the rest. A screen that showed only the sections would be hiding the one thing an
 * accountant opens it for.
 */
import type { VatReturnStatus } from "@/lib/api-enums";

export type { VatReturnStatus };

/** One journal line behind a tie's difference, named well enough to be looked up. */
export interface LineMovement {
  line_id: number;
  entry_id: number;
  entry_number: string;
  entry_date: string;
  doc_type: string;
  module: string;
  description: string | null;
  base_amount: string;
}

/**
 * One VAT account, and whether the return accounts for its movement.
 *
 * `reconciled` is not "balanced": a VAT payment to RRA and a manual journal without a tax code
 * are both real movements on `2200` that no tax line explains, and the return's job is to list
 * them rather than to hide them in a rounding.
 */
export interface AccountTie {
  account_id: number;
  code: string;
  name: string;
  movement: string;
  declared_in_range: string;
  late_total: string;
  difference: string;
  reconciled: boolean;
  untagged: LineMovement[];
}

/** Declared on this return, dated inside a filed one. A filed return never changes, so an
 * entry posted into a filed month lands here on the next one. */
export interface LateEntry {
  entry_id: number;
  entry_number: string;
  entry_date: string;
  filed_return_number: string;
  code: string;
  side: string;
  base: string;
  tax: string;
}

export interface CodeTotal {
  tax_code_id: number;
  code: string;
  name: string;
  rate_pct: string;
  side: string;
  base: string;
  tax: string;
  late_base: string;
  late_tax: string;
  declared_base: string;
  declared_tax: string;
}

/** The figures as they go on the form. Signs follow the ledger — a credit note reduces sales,
 * a return reduces purchases. */
export interface VatReturnSections {
  sales_standard_base: string;
  sales_standard_vat: string;
  sales_zero_rated_base: string;
  sales_exempt_base: string;
  purchases_standard_base: string;
  purchases_standard_vat: string;
  purchases_imports_base: string;
  purchases_imports_vat: string;
  purchases_zero_rated_base: string;
  purchases_exempt_base: string;
  output_vat: string;
  input_vat: string;
  /** Positive is payable to the authority; negative is a credit carried forward. */
  net_payable: string;
}

/** The return over a range as it stands right now — nothing stored. */
export interface VatReturnPreview {
  period_from: string;
  period_to: string;
  high_water_entry_id: number;
  sections: VatReturnSections;
  codes: CodeTotal[];
  late_entries: LateEntry[];
  ties: AccountTie[];
}

export interface VatReturn {
  id: number;
  number: string;
  period_from: string;
  period_to: string;
  output_vat: string;
  input_vat: string;
  net_payable: string;
  status: VatReturnStatus;
  /** Journal entries are append-only with monotonic ids, so this one number is what makes a
   * filed return immune to a later posting into its range. */
  high_water_entry_id: number;
  journal_entry_id: number | null;
  reversal_entry_id: number | null;
  filed_by: number | null;
  filed_at: string;
}

/**
 * The snapshot a filed return was filed on.
 *
 * Deliberately **not** the preview's shape, and the difference is the point: a preview is
 * computed now and carries everything a screen might want to drill (account ids, code names,
 * line ids), while this is evidence — the figures as submitted, decimals as strings so nothing
 * restates them, and the only identifiers are the ones printed on the form. A filed return
 * renders from this and never from a recomputation, which is what makes "a filed return never
 * changes" observable rather than asserted.
 */
export interface FiledFigures {
  period_from: string;
  period_to: string;
  high_water_entry_id: number;
  sections: VatReturnSections;
  codes: Array<{
    tax_code_id: number;
    code: string;
    nature: string;
    rate_pct: string;
    side: string;
    base: string;
    tax: string;
    late_base: string;
    late_tax: string;
  }>;
  late_entries: Array<{
    entry_id: number;
    entry_number: string;
    entry_date: string;
    filed_return_number: string;
    code: string;
    base: string;
    tax: string;
  }>;
  ties: Array<{
    account_code: string;
    movement: string;
    declared_in_range: string;
    late_total: string;
    difference: string;
    untagged: Array<{
      entry_number: string;
      entry_date: string;
      description: string | null;
      base_amount: string;
    }>;
  }>;
}

export interface VatReturnDetail extends VatReturn {
  figures: FiledFigures;
}
