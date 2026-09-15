"""Every mutating endpoint is reachable from a screen, or says why it is not.

A service that ships as an endpoint with no caller is a capability the product does not have.
It passes every test, appears in the OpenAPI schema, and nobody can use it. The pattern has now
cost this build three times:

* **P4.** `mature_instruments` shipped as an endpoint and a scheduled job with no screen, so a
  post-dated cheque could be raised and never banked (Appendix C.1.6).
* **P4, still open when P5 closed.** `POST /subledger/{role}/documents/{id}/reverse` and
  `POST /subledger/{role}/allocations/{id}/unallocate` have no caller anywhere in the frontend
  and no document-detail route to put one on — so an invoice posted in error, or an allocation
  made against the wrong invoice, is uncorrectable by anybody using the product. Found by
  reading the code at the P5 step-9 review, not by a test.
* **P5.** `useReverseStockDocument` sat in the frontend hooks with no caller, which is the same
  hole seen from the other end (Appendix C.1.7).

Each time it was found by someone happening to look. This is the test that looks.

**What counts as a caller.** A string or template literal anywhere under `frontend/src`
containing the endpoint's path, with `${…}` wherever the path has a parameter. That is how
`api.post()` is called throughout the app, and matching the literal rather than the call
expression keeps this independent of how the request helper is spelled.

**What counts as an exemption.** An entry in `NO_UI` below with a reason. Two kinds, and the
difference matters: `by design` is a settled decision, `GAP` is debt that somebody owes. Both
are visible in every diff that touches this file, which is the point — an exemption should cost
a line of review, not nothing.
"""

import os
import re
from pathlib import Path

import pytest

from app.main import app

REPO_ROOT = Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[2])
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

#: Stripped before matching: the frontend's `api` helper carries it, so no call site repeats it.
API_PREFIX = "/api/v1"

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

#: Endpoints with no caller under `frontend/src`, and why. Keyed `"METHOD /path"` exactly as the
#: OpenAPI schema spells it.
#:
#: `by design` — the endpoint is not meant to be driven from a screen.
#: `GAP` — it should be, and is not. Debt, with the phase that owes it named.
NO_UI: dict[str, str] = {
    # --- by design ---------------------------------------------------------------------------
    "POST /api/v1/subledger/jobs/sweep": (
        "by design — the retention reaper. It fails jobs abandoned by a restarted process and "
        "deletes expired artifacts on a schedule; there is no moment at which a person wants "
        "to press it."
    ),
    # --- GAP: P6, endpoints ahead of their screens ----------------------------------------------
    # These are **scheduled debt, not a decision**, and the schedule is the point: P6 builds
    # its services in steps 1-5 and its screens in steps 6-8, so between those two points the
    # endpoints exist and nothing calls them. Each entry is deleted by the step that builds the
    # screen calling it, and P6's definition of done requires this list to carry no P6 entry
    # at all.
    #
    # **Step 6 has taken its two.** `PUT /oe/defaults` and
    # `PUT /inventory/items/{item_id}/kit-components` stood here until the Order defaults
    # screen and the Items screen's Kit components section landed; they are now called from
    # `features/order-entry/hooks.ts` and `features/inventory/hooks.ts`, so the register is
    # down to step 7's lines.
    #
    # A hook with no screen would satisfy this test and would be the worse answer: that is
    # exactly what `useReverseStockDocument` was (C.1.7), and what the AR/AP reversal was for a
    # whole phase. An honest line in the register beats a caller that nobody can reach.
    #
    # Step 3's orders, receipts and flows. Their screens are **step 7** — Sales order, Purchase
    # order and GRV, plus Breakup under Transactions → Order Entry — and that step deletes every
    # line below. Each names the screen that owns it, so the register reads as a schedule rather
    # than a pile.
    "POST /api/v1/oe/sales-orders": (
        "GAP (P6) — the Sales order screen is step 7. Remove this entry with `/oe/sales-orders`."
    ),
    "PUT /api/v1/oe/sales-orders/{order_id}": (
        "GAP (P6) — editing an open order, on the same step-7 Sales order screen."
    ),
    "POST /api/v1/oe/sales-orders/{order_id}/close": (
        "GAP (P6) — the Close remaining action on the step-7 Sales order screen."
    ),
    "POST /api/v1/oe/sales-orders/{order_id}/cancel": (
        "GAP (P6) — the Cancel action on the step-7 Sales order screen."
    ),
    "POST /api/v1/oe/sales-orders/{order_id}/invoice": (
        "GAP (P6) — the Invoice action on the step-7 Sales order screen, which opens the AR "
        "invoice pre-filled. It posts nothing; it prepares a document."
    ),
    "PUT /api/v1/oe/sales-orders/{order_id}/lines/{line_id}/breakup": (
        "GAP (P6) — the Breakup screen (Transactions → Order Entry) is step 7, and the same "
        "action opens from the line on the order workspace."
    ),
    "POST /api/v1/oe/purchase-orders": (
        "GAP (P6) — the Purchase order screen is step 7."
    ),
    "PUT /api/v1/oe/purchase-orders/{order_id}": (
        "GAP (P6) — editing an open order, on the same step-7 Purchase order screen."
    ),
    "POST /api/v1/oe/purchase-orders/{order_id}/close": (
        "GAP (P6) — the Close remaining action on the step-7 Purchase order screen."
    ),
    "POST /api/v1/oe/purchase-orders/{order_id}/cancel": (
        "GAP (P6) — the Cancel action on the step-7 Purchase order screen."
    ),
    "POST /api/v1/oe/purchase-orders/{order_id}/receive": (
        "GAP (P6) — the Receive action on the step-7 Purchase order screen, which opens the GRV "
        "pre-filled. Prepares a receipt; posts nothing."
    ),
    "POST /api/v1/oe/purchase-orders/{order_id}/process-invoice": (
        "GAP (P6) — the Process invoice action for a purchase order's service lines, step 7."
    ),
    "POST /api/v1/oe/goods-received-notes": (
        "GAP (P6) — the GRV screen is step 7. The service landed at step 2 and had no endpoint "
        "at all until step 3 needed the Receive flow to lead somewhere."
    ),
    "POST /api/v1/oe/goods-received-notes/{grn_id}/reverse": (
        "GAP (P6) — the Reverse action on the step-7 GRV screen."
    ),
    "POST /api/v1/oe/goods-received-notes/{grn_id}/process-invoice": (
        "GAP (P6) — the Process invoice action on the step-7 GRV screen, which opens the "
        "supplier invoice in matching mode. Prepares a document; posts nothing."
    ),
    # Step 4's landed cost. Its screen is **step 7** — Landed cost under Transactions → Order
    # Entry, with the share preview and Reverse on the detail — and that step deletes these
    # three lines.
    "POST /api/v1/oe/landed-costs": (
        "GAP (P6) — the Landed cost screen is step 7. Remove this entry with `/oe/landed-costs`."
    ),
    "POST /api/v1/oe/landed-costs/preview": (
        "GAP (P6) — the share preview the step-7 Landed cost screen shows before Post. A POST "
        "because the target list is a body and the answer depends on today's stock position; "
        "it writes nothing."
    ),
    "POST /api/v1/oe/landed-costs/{document_id}/reverse": (
        "GAP (P6) — the Reverse action on the step-7 Landed cost detail screen."
    ),
    # --- GAP: the operator console, planned but unscheduled ------------------------------------
    "POST /api/v1/operator/tenants/{company_id}/activate": (
        "GAP (SaaS admin, Appendix C.2) — the operator console has no screens in any phase yet. "
        "Grouped here rather than marked by design because the plan says it is coming."
    ),
    "POST /api/v1/operator/tenants/{company_id}/suspend": "GAP (SaaS admin, C.2) — as above.",
    "POST /api/v1/operator/tenants/{company_id}/impersonate": "GAP (SaaS admin, C.2) — as above.",
}


def _mutating_operations() -> list[str]:
    """`["METHOD /path", …]` for every mutating operation the app serves.

    Read from the OpenAPI schema rather than `app.routes`: routers are included lazily, so the
    route list is a handful of shells and the schema is the only flattened view of what is
    actually served. It is also what a client sees, which is the right definition here.
    """
    spec = app.openapi()
    return sorted(
        f"{method.upper()} {path}"
        for path, operations in spec["paths"].items()
        for method in operations
        if method.upper() in MUTATING
    )


def _frontend_sources() -> list[str]:
    return [
        path.read_text(encoding="utf-8")
        for path in FRONTEND_SRC.rglob("*")
        if path.suffix in {".ts", ".tsx"} and path.is_file()
    ]


#: What a path parameter may look like at a call site: a template interpolation
#: (`${membershipId}`) or a literal id. **Not an arbitrary run of characters** — see below.
_PATH_PARAMETER = r"(?:\$\{[^}]*\}|\d+)"


def _caller_pattern(path: str) -> re.Pattern[str]:
    """A regex matching the path as a frontend literal would spell it.

    `/inventory/documents/{document_id}/reverse` becomes
    `/inventory/documents/ <an interpolation> /reverse`, so a template literal
    (`` `/inventory/documents/${id}/reverse` ``) matches.

    **A path parameter matches an interpolation, not any text**, and that distinction is the
    whole of this function's usefulness. The first version matched anything-but-a-quote, which
    meant a *sibling literal path* satisfied a parameterised one: `"/invitations/accept"`
    covered `DELETE /invitations/{membership_id}`, so an endpoint could be reported as having a
    caller on the strength of a call to a different endpoint. That is precisely the false
    coverage this file exists to prevent.

    Found by a sensitivity pass rather than by reading: gutting the revoke call site left this
    test green. Tightening it changed no other verdict — every real call site interpolates.
    """
    without_prefix = path[len(API_PREFIX) :] if path.startswith(API_PREFIX) else path
    literal_parts = [re.escape(part) for part in re.split(r"\{[^}]+\}", without_prefix)]
    return re.compile(_PATH_PARAMETER.join(literal_parts))


def test_a_sibling_literal_path_does_not_cover_a_parameterised_one() -> None:
    """Anti-vacuity for the rule above, written as the case that was wrong.

    `POST /invitations/accept` and `DELETE /invitations/{membership_id}` are different
    endpoints sharing a prefix. A matcher that cannot tell them apart reports the second as
    covered whenever the first is called, and the register's claim collapses quietly.
    """
    revoke = _caller_pattern("/api/v1/invitations/{membership_id}")
    assert revoke.search("api.delete(`/invitations/${membershipId}`)")
    assert not revoke.search('api.post("/invitations/accept", payload)')
    assert not revoke.search('api.post("/invitations", payload)')


def test_the_scan_reads_the_frontend_it_claims_to() -> None:
    """Anti-vacuity. A moved directory must fail here, not quietly check nothing."""
    assert FRONTEND_SRC.is_dir(), f"{FRONTEND_SRC} is missing"
    assert len(_frontend_sources()) > 100


def test_the_schema_reports_a_plausible_number_of_mutating_endpoints() -> None:
    """Anti-vacuity for the other half: if the schema comes back thin — lazy routers not yet
    resolved, a refactor that moved the app object — every endpoint would look covered."""
    assert len(_mutating_operations()) > 60


def test_no_exemption_is_left_for_an_endpoint_that_no_longer_exists() -> None:
    """An allow-list that outlives its endpoints stops being a list of known debt and becomes
    a list of things nobody has checked."""
    served = set(_mutating_operations())
    stale = sorted(key for key in NO_UI if key not in served)
    assert stale == [], f"NO_UI names endpoints the app no longer serves: {stale}"


def test_every_mutating_endpoint_has_a_caller_or_a_reason() -> None:
    sources = _frontend_sources()
    uncovered = []
    for operation in _mutating_operations():
        if operation in NO_UI:
            continue
        _, path = operation.split(" ", 1)
        pattern = _caller_pattern(path)
        if not any(pattern.search(source) for source in sources):
            uncovered.append(operation)

    assert uncovered == [], (
        "These endpoints have no caller under frontend/src and no entry in NO_UI. An endpoint "
        "nothing calls is a capability the product does not have — build the screen, or add it "
        "to NO_UI with a reason saying whether that is by design or debt:\n  "
        + "\n  ".join(uncovered)
    )


@pytest.mark.parametrize("key, reason", sorted(NO_UI.items()))
def test_every_exemption_states_which_kind_it_is(key: str, reason: str) -> None:
    """A reason that does not say whether it is a decision or a debt is not a reason."""
    assert reason.startswith(("by design", "GAP")), (
        f"{key}: a NO_UI reason must begin with 'by design' or 'GAP (<phase>)' so the list can "
        f"be read as a debt register. Got: {reason[:60]}…"
    )
