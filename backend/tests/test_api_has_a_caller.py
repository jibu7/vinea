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
    # --- GAP (P7, step 6): the EBM device setup, whose screens arrive with step 6 --------------
    #
    # Five lines, each naming the step that deletes it. P7 builds its services in steps 1-5 and
    # its screens in steps 6-8, so between those points the endpoints exist and nothing calls
    # them — the same shape as P6's twenty, which step 6 and step 7 cleared down to nothing.
    #
    # The screen is **Maintenance → Tax → EBM devices** (`/maintenance/ebm-devices`): register,
    # Initialize, Suspend, Sync codes. `lookup-tin` is a GET and needs no line; so do the two
    # listings this file does not enumerate.
    "POST /api/v1/fiscal/devices": (
        "GAP (P7, step 6) — deleted by the EBM devices screen, which registers a device "
        "against a branch."
    ),
    "POST /api/v1/fiscal/devices/{device_id}/initialize": (
        "GAP (P7, step 6) — deleted by Initialize on the EBM devices screen."
    ),
    "POST /api/v1/fiscal/devices/{device_id}/suspend": (
        "GAP (P7, step 6) — deleted by Suspend on the EBM devices screen."
    ),
    "POST /api/v1/fiscal/devices/{device_id}/sync-codes": (
        "GAP (P7, step 6) — deleted by Sync codes on the EBM devices screen."
    ),
    "POST /api/v1/fiscal/devices/{device_id}/sync-item-classes": (
        "GAP (P7, step 6) — deleted by Sync codes on the EBM devices screen, which syncs the "
        "item classification beside the code tables."
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
