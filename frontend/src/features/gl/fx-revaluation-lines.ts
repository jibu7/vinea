import type { FxRevaluationLine } from "./types";

/**
 * A document line is keyed and labelled by its document; a **bank line** (P8 decision 8) has no
 * document — `document_id` is null on it — and is keyed and labelled by the bank account, whose
 * code sits where the document number does and whose name sits where the partner does.
 *
 * Shared by the run screen (Transactions) and the report over a posted run (Reports), so the two
 * cannot label the same line two ways.
 */
export function lineKey(line: FxRevaluationLine): string {
  return line.document_id !== null ? `document-${line.document_id}` : `bank-${line.bank_account_id}`;
}

export function lineLabel(line: FxRevaluationLine): string {
  return (line.document_number ?? line.bank_account_code) ?? "";
}

export function lineName(line: FxRevaluationLine): string {
  return (line.partner_name ?? line.bank_account_name) ?? "";
}

/** Where a line drills: a document to its partner document, a bank line to the account's
 * Cashbooks detail — the ledger whose balance was revalued. */
export function lineHref(line: FxRevaluationLine, revaluationDate: string): string {
  if (line.document_id !== null) {
    return `/${line.role === "ar" ? "ar" : "ap"}/documents/${line.document_id}`;
  }
  return `/gl/reports/cashbooks?mode=detail&account=${line.bank_account_id}&to=${revaluationDate}`;
}
