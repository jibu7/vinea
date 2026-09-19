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

**What counts as a caller.** A string or template literal under `frontend/src` containing the
endpoint's path, with `${…}` wherever the path has a parameter, **passed to the helper method
the endpoint serves** — `api.put("/oe/defaults", …)` for a `PUT`. Comments are stripped before
the search, so prose naming an endpoint is not a caller.

The last two clauses were added at P6 step 6, each because a sensitivity pass found the
version without it green over a gutted call site: a docstring beside the call covered the
call, and the `GET` half of a settings pair covered the `PUT`. `_caller_pattern` and
`_without_comments` carry the detail.

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
    "POST /api/v1/fiscal/outbox/drain": (
        "by design — the EBM queue's scheduler hook. The queue drains by itself: a worker "
        "loop every fifteen seconds (`python -m app.fiscal.worker`, a service in "
        "docker-compose) and an after-response kick from the posting that filled it. This "
        "endpoint exists for a deployment that runs no worker and drives an external "
        "scheduler instead, and for the e2e stack, which drives it rather than waiting. The "
        "same shape as `jobs/sweep`: there is no moment at which a person wants to press it."
    ),
    # --- P6 carries no entry. -------------------------------------------------------------------
    # There were twenty. P6 builds its services in steps 1-5 and its screens in steps 6-8, so
    # between those two points the endpoints existed and nothing called them; each entry named
    # the step that would delete it, and each step did. Step 6 took `PUT /oe/defaults` and
    # `PUT /inventory/items/{item_id}/kit-components`; **step 7 took the remaining eighteen** —
    # the sales and purchase orders and their close, cancel, invoice, receive and
    # process-invoice flows, the breakup PUT, the goods receipt with its reverse and
    # process-invoice, and the landed cost with its preview and reverse. P6's definition of
    # done requires this list to carry no P6 entry at all, and it does not.
    #
    # A hook with no screen would satisfy this test and would be the worse answer: that is
    # exactly what `useReverseStockDocument` was (C.1.7), and what the AR/AP reversal was for a
    # whole phase. An honest line in the register beats a caller that nobody can reach. So
    # every one of the eighteen was deleted by a screen a person can open and press, not by a
    # hook written to satisfy the matcher.
    #
    # --- P7 step 6 carried five lines. It carries none. ----------------------------------------
    #
    # They were `POST /fiscal/devices`, `/{id}/initialize`, `/{id}/suspend`, `/{id}/sync-codes`
    # and `/{id}/sync-item-classes`, each naming the step that would delete it. **Maintenance →
    # Tax → EBM devices** (`/maintenance/ebm-devices`) deleted all five: Register, Initialize,
    # Suspend and one Sync codes button that drives both syncs, because an operator who
    # refreshed the code tables and not the classification would have a Tax-types screen
    # offering A-D and an Items screen whose class typeahead found nothing.
    #
    # `lookup-tin` never needed a line — it is a GET — and it is called now anyway, by Verify
    # TIN on Customers and Suppliers. So are `/fiscal/codes`, `/fiscal/item-classes` and
    # `/fiscal/items`, by the Units-of-measure, Items and Defaults screens.
    #
    # **Every one was deleted by a screen a person can open and press**, not by a hook written
    # to satisfy the matcher — the standard P6 set for its own twenty, and the reason
    # `useReverseStockDocument` (C.1.7) is remembered as a defect rather than as a pass.
    # --- GAP (P7, step 7): the purchase feed and the import register ---------------------------
    #
    # Six lines. The screens are **Transactions → Tax → EBM purchases** (`/fiscal/purchases`:
    # the feed, with Accept, Reject and the AP-document link) and **Import declarations**
    # (`/fiscal/imports`: the list, with the item picker, Approve and Reject). Each carries its
    # own Fetch, because a feed nobody can refresh is a feed that is always yesterday's.
    #
    # Nothing here registers a purchase or reports stock: those are written by the posting that
    # caused them, in its own transaction, and have no endpoint at all — which is the design
    # rather than a gap (decision 4).
    "POST /api/v1/fiscal/devices/{device_id}/fetch-purchase-feed": (
        "GAP (P7, step 7) — deleted by Fetch on the EBM purchases screen."
    ),
    "POST /api/v1/fiscal/purchase-feed/{row_id}/accept": (
        "GAP (P7, step 7) — deleted by Accept on the EBM purchases screen, which links the AP "
        "document the purchase became."
    ),
    "POST /api/v1/fiscal/purchase-feed/{row_id}/reject": (
        "GAP (P7, step 7) — deleted by Reject on the EBM purchases screen."
    ),
    "POST /api/v1/fiscal/devices/{device_id}/fetch-imports": (
        "GAP (P7, step 7) — deleted by Fetch on the Import declarations screen."
    ),
    "POST /api/v1/fiscal/import-declarations/{declaration_id}/approve": (
        "GAP (P7, step 7) — deleted by Approve on the Import declarations screen, which names "
        "the Vinea item the declared line became."
    ),
    "POST /api/v1/fiscal/import-declarations/{declaration_id}/reject": (
        "GAP (P7, step 7) — deleted by Reject on the Import declarations screen."
    ),
    # --- GAP (P7, step 7): the VAT return and the FX revaluation --------------------------------
    #
    # Five lines. The screens are **Transactions → Tax → VAT returns** (`/tax/vat-returns`: the
    # preview with its tie and late entries, File, Reverse, and the two annex downloads) and
    # **General Ledger → Period end → FX revaluation** (`/gl/fx-revaluations`: the preview per
    # open document, Post, Reverse).
    #
    # The previews, the listings and the annex CSVs are reads and need no exemption; only the
    # four acts below do.
    "POST /api/v1/tax/vat-returns": (
        "GAP (P7, step 7) — deleted by File on the VAT returns screen, which freezes the "
        "figures and posts the settlement entry."
    ),
    "POST /api/v1/tax/vat-returns/{return_id}/reverse": (
        "GAP (P7, step 7) — deleted by Reverse on the VAT return detail, which reopens the "
        "range so it can be filed again."
    ),
    "POST /api/v1/gl/fx-revaluations": (
        "GAP (P7, step 7) — deleted by Post on the FX revaluation screen, which posts the run "
        "and its next-day mirror."
    ),
    "POST /api/v1/gl/fx-revaluations/{revaluation_id}/reverse": (
        "GAP (P7, step 7) — deleted by Reverse on the FX revaluation detail."
    ),
    # --- GAP (P7, step 7): the queue screen's three actions and the copy print ------------------
    #
    # The screens are **Transactions → Tax → Fiscal queue** (`/fiscal/queue`: per device, rows
    # by status, Retry now / Verify with device / Attach receipt manually, the row's request and
    # response, the action log) and the **document detail's Copy print**.
    #
    # The queue listing, the row detail, the receipts enquiry and the item registrations are
    # reads and need no exemption. These four are acts: two of them tell the authority
    # something, and the other two are a person asserting what the authority holds and a second
    # piece of paper leaving the building.
    "POST /api/v1/fiscal/queue/rows/{row_id}/retry": (
        "GAP (P7, step 7) — deleted by Retry now on the Fiscal queue screen, which releases a "
        "failed or backing-off row."
    ),
    "POST /api/v1/fiscal/queue/rows/{row_id}/verify": (
        "GAP (P7, step 7) — deleted by Verify with device on the Fiscal queue screen, which "
        "asks the device what it holds and decides from its counters."
    ),
    "POST /api/v1/fiscal/queue/rows/{row_id}/attach-receipt": (
        "GAP (P7, step 7) — deleted by Attach receipt manually on the Fiscal queue screen, "
        "which records a receipt read off MyRRA against a row that needs one."
    ),
    "POST /api/v1/fiscal/documents/{document_id}/receipt/copy": (
        "GAP (P7, step 7) — deleted by Copy print on the document detail, which increments the "
        "receipt's copy counter and prints the COPY layout. Nothing is sent to RRA."
    ),
    # --- GAP (P7, step 8): closing the fiscal day ----------------------------------------------
    #
    # The screen is **Transactions → Tax → Close day**, which shows the X and offers the Z. The
    # X and the Z listing beside it are reads and need no exemption.
    "POST /api/v1/fiscal/devices/{device_id}/close-day": (
        "GAP (P7, step 8) — deleted by Close day, which stores the Z and opens the next day "
        "where this one ended."
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


def _without_comments(source: str) -> str:
    """The file with its comments blanked out, string literals left intact.

    **Prose is not a caller.** The rule this file states is "a string or template literal
    containing the endpoint's path", and until P6 step 6 the scan read the raw text, so a
    comment naming an endpoint covered it. That is not a hypothetical: gutting both of step
    6's call sites left this test green, because the screen and the hook each *explain*
    `/oe/defaults` and `/inventory/items/{id}/kit-components` in a docstring beside the call.
    An endpoint documented and not called is exactly the state the register exists to name.

    A character scanner rather than a regex, because the two constructs nest the wrong way
    round for one: `"http://localhost"` is a string containing what looks like a comment, and
    `/* api.put("/x") */` is a comment containing what looks like a string. Quotes are
    tracked, so neither is mistaken for the other.
    """
    out: list[str] = []
    i, n = 0, len(source)
    quote: str | None = None
    while i < n:
        ch = source[i]
        if quote is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(source[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            while i < n and source[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            end = source.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _frontend_sources() -> list[str]:
    return [
        _without_comments(path.read_text(encoding="utf-8"))
        for path in FRONTEND_SRC.rglob("*")
        if path.suffix in {".ts", ".tsx"} and path.is_file()
    ]


#: What a path parameter may look like at a call site: a template interpolation
#: (`${membershipId}`) or a literal id. **Not an arbitrary run of characters** — see below.
_PATH_PARAMETER = r"(?:\$\{[^}]*\}|\d+)"

#: What may follow the path at a call site: the quote that closes the literal, a query string,
#: or an interpolation that appends one. **Not a slash**, which is the whole point — see
#: `_caller_pattern`.
_PATH_END = r"(?=[`\"']|\?|\$\{)"


#: How the request helper is spelled at every mutating call site: `api.put(`, with an optional
#: type argument, and the path as the first argument. Checked because the path alone cannot
#: tell a reader which *method* reaches it.
_CALL = r"api\.{method}(?:<[^()]*>)?\(\s*[`\"']"

#: The one caller that cannot go through the helper, because it *is* the helper: `api.ts`
#: refreshes the session with a bare `fetch(url, { method: "POST" })`, since routing that
#: through `request()` would recurse on its own 401 handling. Matched by its shape rather
#: than exempted by name — an exemption would cover any future bare fetch as well.
_FETCH_HEAD = r"fetch\(\s*[`\"'][^`\"']*"
_FETCH_TAIL = r"[^)]{{0,200}}method:\s*[\"']{method}[\"']"


def _caller_pattern(path: str, method: str | None = None) -> re.Pattern[str]:
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

    **`method` requires the call to be the one the endpoint serves**, and that is the second
    thing a sensitivity pass found, at P6 step 6. The path alone cannot tell a `GET` from a
    `PUT`, so wherever a settings endpoint serves both on one path — `/oe/defaults`,
    `/gl/settings`, `/inventory/items/{id}/kit-components` — the *reader* covered the
    *writer*, and a screen that could display a setting and not change it read as covered.
    Both of step 6's call sites could be gutted with this test still green. The frontend
    calls through one helper (`api.put(path, …)`, `src/lib/api.ts`), so requiring the verb
    beside the path costs nothing and closes the hole; passing no method keeps the loose
    behaviour for the anti-vacuity tests below.

    **The path must end where the endpoint's path ends**, and that is the third hole a
    sensitivity pass found, at P6 step 7. Without an end anchor a *longer* sibling covered a
    *shorter* one: `api.post(`/oe/sales-orders/${orderId}/close`)` satisfied
    `POST /oe/sales-orders`, so the create endpoint read as called on the strength of a call
    to the close endpoint. Gutting `useCreateSalesOrder` left this test green. It is the
    mirror of the sibling-path case below — that one is a shorter literal covering a longer
    parameterised path, this one a longer literal covering a shorter collection path — and
    the same answer closes both: a path parameter is an interpolation, and a path ends at the
    quote that closes it.
    """
    without_prefix = path[len(API_PREFIX) :] if path.startswith(API_PREFIX) else path
    literal_parts = [re.escape(part) for part in re.split(r"\{[^}]+\}", without_prefix)]
    body = _PATH_PARAMETER.join(literal_parts) + _PATH_END
    if method is None:
        return re.compile(body)
    helper = _CALL.format(method=method.lower()) + body
    bare_fetch = _FETCH_HEAD + body + _FETCH_TAIL.format(method=method.upper())
    return re.compile(f"(?:{helper})|(?:{bare_fetch})", re.IGNORECASE | re.DOTALL)


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


def test_a_longer_sibling_does_not_cover_a_collection_path() -> None:
    """Anti-vacuity for the end anchor, written as the case that was wrong.

    `POST /oe/sales-orders` creates an order and `POST /oe/sales-orders/{order_id}/close`
    gives up what is left of one. They share a prefix and a verb, so a matcher with no end
    anchor reports the first as called whenever the second is — and the create endpoint, the
    one the whole screen exists to reach, is the one that goes unproven.
    """
    create = _caller_pattern("/api/v1/oe/sales-orders", "post")
    assert create.search('api.post<SalesOrder>("/oe/sales-orders", payload, headers)')
    assert not create.search("api.post<SalesOrder>(`/oe/sales-orders/${orderId}/close`, body)")
    assert not create.search("api.post<SalesOrder>(`/oe/sales-orders/${orderId}/invoice`)")


def test_a_reader_does_not_cover_a_writer_on_the_same_path() -> None:
    """Anti-vacuity for the method rule, written as the case that was wrong.

    `GET /oe/defaults` and `PUT /oe/defaults` are one path and two capabilities. A matcher
    that cannot tell them apart reports the screen as able to *change* the settings on the
    strength of its being able to *show* them.
    """
    put = _caller_pattern("/api/v1/oe/defaults", "PUT")
    assert put.search('api.put<OrderDefaults>("/oe/defaults", payload)')
    assert not put.search('api.get<OrderDefaults>("/oe/defaults")')
    # The multi-line shape several hooks use, where the path is on its own line.
    assert put.search('api.put<T>(\n        `/oe/defaults`,\n        payload,\n      )')


def test_the_session_refresher_counts_although_it_cannot_use_the_helper() -> None:
    """`api.ts` refreshes with a bare `fetch`, because routing that through `request()` would
    recurse on its own 401 handling. It is a caller; the shape says so, so no name has to be
    written down anywhere. A `fetch` of the same path with no method is still a GET."""
    refresh = _caller_pattern("/api/v1/auth/refresh", "POST")
    assert refresh.search(
        'fetch(`${API_BASE}/auth/refresh`, {\n'
        '    method: "POST",\n'
        '    credentials: "include",\n  })'
    )
    assert not refresh.search('fetch(`${API_BASE}/auth/refresh`, { credentials: "include" })')


def test_a_comment_naming_an_endpoint_is_not_a_caller() -> None:
    """Anti-vacuity for `_without_comments`, written as the case that was wrong.

    Both spellings that covered step 6's endpoints while nothing called them: a `//` line
    comment and a `/** */` docstring. And the two constructs that must survive, or the
    stripper would delete real call sites: a string that contains `//`, and a template
    literal spanning lines.
    """
    assert "/oe/defaults" not in _without_comments('// `GET /oe/defaults` refuses a caller')
    assert "/oe/defaults" not in _without_comments('/** A PUT: `/oe/defaults` takes … */')
    assert '"/oe/defaults"' in _without_comments('api.put<T>("/oe/defaults", payload)')
    assert "http://localhost" in _without_comments('const base = "http://localhost:8000";')
    assert "/inventory/items/" in _without_comments(
        "api.put(`/inventory/items/${itemId}/kit-components`, payload)"
    )


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
        method, path = operation.split(" ", 1)
        pattern = _caller_pattern(path, method)
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
