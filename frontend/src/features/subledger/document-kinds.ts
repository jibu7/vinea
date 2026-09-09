import type { DocumentKind, PartnerRole } from "./types";

/**
 * The role/kind matrix, mirroring the single backend service: six screens, one component.
 * `slug` is the route segment; `messages` is the key under `arap.documents.<messages>` that
 * holds this screen's title, subtitle and copy. The AP credit note is "Return to supplier"
 * in the owner's menu (Appendix C) and a debit note in the ledger — same row either way.
 */
export interface DocumentScreenSpec {
  slug: string;
  role: PartnerRole;
  kind: DocumentKind;
  messages: string;
  /** Settlements carry a header amount and a cash account instead of lines. */
  isSettlement: boolean;
}

export const DOCUMENT_SCREENS: readonly DocumentScreenSpec[] = [
  { slug: "invoice", role: "ar", kind: "invoice", messages: "arInvoice", isSettlement: false },
  { slug: "credit-note", role: "ar", kind: "credit_note", messages: "arCreditNote", isSettlement: false },
  { slug: "receipt", role: "ar", kind: "settlement", messages: "arReceipt", isSettlement: true },
  { slug: "supplier-invoice", role: "ap", kind: "invoice", messages: "apInvoice", isSettlement: false },
  { slug: "return-to-supplier", role: "ap", kind: "credit_note", messages: "apCreditNote", isSettlement: false },
  { slug: "payment", role: "ap", kind: "settlement", messages: "apPayment", isSettlement: true },
] as const;

export function documentScreen(slug: string): DocumentScreenSpec {
  const spec = DOCUMENT_SCREENS.find((s) => s.slug === slug);
  if (!spec) throw new Error(`Unknown document screen: ${slug}`);
  return spec;
}
