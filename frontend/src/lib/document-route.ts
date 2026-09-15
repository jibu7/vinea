/**
 * Where a resolved source document opens.
 *
 * The server hands back a **routing key**, never a URL: `SourceDocumentRead.target` on a stock
 * move, `module_document_target` on a journal entry. It is the server's job to know that a
 * partner document belongs to AR rather than AP — that is a property of the document, and a
 * screen re-deriving it from a partner lookup would be a second place for the answer to live —
 * and it is the frontend's job to know its own routes. This file is where the two meet, once.
 *
 * It exists because P6 turned one source type into five. Until this phase a move could only
 * have come from an inventory document, so the enquiry screen wrote
 * `source_doc_type === "inventory_document"` inline and built the href by hand; every P6 move
 * therefore rendered a blank cell on a screen that had shipped and passed review, which is the
 * failure rule 13 is written against — the row is there, the link is not, and nothing fails.
 *
 * A key with no route here resolves to `null` and the caller renders plain text. That is the
 * honest answer for `allocation` and `journal_entry`: neither is a document with a page of its
 * own, and a link to nowhere is worse than no link.
 */
const ROUTES: Record<string, string> = {
  inventory_document: "/inventory/documents",
  ar_document: "/ar/documents",
  ap_document: "/ap/documents",
  goods_received_note: "/oe/goods-received",
  landed_cost_document: "/oe/landed-costs",
};

/** The href for one resolved source, or `null` when this phase has no page for it. */
export function documentHref(
  target: string | null | undefined,
  id: number | null | undefined,
): string | null {
  if (!target || id === null || id === undefined) return null;
  const base = ROUTES[target];
  return base ? `${base}/${id}` : null;
}

/** Every routing key that has a page — exported so a test can assert the set rather than
 * restate it, and so nothing has to reach into `ROUTES` to find out. */
export const ROUTED_TARGETS = Object.keys(ROUTES);
