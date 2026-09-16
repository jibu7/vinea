import { describe, expect, it } from "vitest";
import { documentHref, ROUTED_TARGETS } from "./document-route";

/**
 * The five routing keys the API can send, against the five routes that exist.
 *
 * This is a spelling test, and spelling is exactly what went wrong: the keys are produced in
 * `backend/app/order_entry/sources.py` and consumed here, with a string literal at each end
 * and nothing in between that fails when they stop matching. A screen given a key it does not
 * know renders plain text — which is correct for a source with no page, and silent for a typo.
 */
describe("document routing keys", () => {
  it("routes every source type P6 can post", () => {
    expect([...ROUTED_TARGETS].sort()).toEqual([
      "ap_document",
      "ar_document",
      "goods_received_note",
      "inventory_document",
      "landed_cost_document",
    ]);
  });

  it.each([
    ["inventory_document", "/inventory/documents/7"],
    ["ar_document", "/ar/documents/7"],
    ["ap_document", "/ap/documents/7"],
    ["goods_received_note", "/oe/goods-received/7"],
    ["landed_cost_document", "/oe/landed-costs/7"],
  ])("sends %s to %s", (target, href) => {
    expect(documentHref(target, 7)).toBe(href);
  });

  it("has no href for a source with no page of its own", () => {
    // `allocation` and `journal_entry` are real `source_doc_type` values and neither is a
    // document a person can open. Inventing a route would be worse than the empty cell.
    expect(documentHref("allocation", 7)).toBeNull();
    expect(documentHref("journal_entry", 7)).toBeNull();
  });

  it("has no href without both halves", () => {
    expect(documentHref(null, 7)).toBeNull();
    expect(documentHref("ar_document", null)).toBeNull();
  });
});
