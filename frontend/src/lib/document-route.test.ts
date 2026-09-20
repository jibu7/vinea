import { describe, expect, it } from "vitest";
import { documentHref, ROUTED_TARGETS } from "./document-route";

/**
 * The seven routing keys the API can send, against the seven routes that exist.
 *
 * This is a spelling test, and spelling is exactly what went wrong: the keys are produced in
 * `backend/app/order_entry/sources.py` and consumed here, with a string literal at each end
 * and nothing in between that fails when they stop matching. A screen given a key it does not
 * know renders plain text — which is correct for a source with no page, and silent for a typo.
 */
describe("document routing keys", () => {
  it("routes every source type P6 and P7 can post", () => {
    expect([...ROUTED_TARGETS].sort()).toEqual([
      "ap_document",
      "ar_document",
      "fx_revaluation",
      "goods_received_note",
      "inventory_document",
      "landed_cost_document",
      "vat_return",
    ]);
  });

  it.each([
    ["inventory_document", "/inventory/documents/7"],
    ["ar_document", "/ar/documents/7"],
    ["ap_document", "/ap/documents/7"],
    ["goods_received_note", "/oe/goods-received/7"],
    ["landed_cost_document", "/oe/landed-costs/7"],
    // P7's two go to the **report** routes, which take an id for exactly this reason. An
    // entry opened from the trial balance lands on the return or the run it belongs to rather
    // than on a picker asking which one was meant.
    ["vat_return", "/tax/reports/vat-return/7"],
    ["fx_revaluation", "/gl/reports/fx-revaluation/7"],
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
