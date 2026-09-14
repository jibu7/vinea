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
    # --- GAP: P1, the parts of auth that shipped without screens -------------------------------
    "POST /api/v1/auth/password-reset/request": (
        "GAP (P1) — a user who forgets their password cannot reset it from the product. The "
        "endpoint and the email template exist; the two screens do not."
    ),
    "POST /api/v1/auth/password-reset/confirm": (
        "GAP (P1) — the other half of the reset flow above."
    ),
    "POST /api/v1/auth/email-verification/request": (
        "GAP (P1) — nothing in the product asks a user to verify their address or lets them "
        "ask for the mail again."
    ),
    "POST /api/v1/auth/email-verification/confirm": (
        "GAP (P1) — the link's landing page. Without it a verification mail leads nowhere."
    ),
    "POST /api/v1/invitations/accept": (
        "GAP (P1) — an invited user has no page to accept on, so the only way into a new "
        "tenancy is the signup form. The e2e drives this endpoint directly, which is how it "
        "went unnoticed: exercised, and unreachable."
    ),
    "DELETE /api/v1/invitations/{membership_id}": (
        "GAP (P1) — an invitation sent to the wrong address cannot be revoked from the "
        "memberships screen."
    ),
    # --- GAP: P6, endpoints ahead of their screens ----------------------------------------------
    # These two are **scheduled debt, not a decision**, and the schedule is the point: P6 builds
    # its services in steps 1-5 and its screens in steps 6-8, so between those two points the
    # endpoints exist and nothing calls them. Both entries are deleted by the step that builds
    # the screen — Order defaults and the Items screen's Kit components section, both step 6 —
    # and P6's definition of done requires this list to carry no P6 entry at all.
    #
    # A hook with no screen would satisfy this test and would be the worse answer: that is
    # exactly what `useReverseStockDocument` was (C.1.7), and what the AR/AP reversal was for a
    # whole phase. An honest line in the register beats a caller that nobody can reach.
    "PUT /api/v1/oe/defaults": (
        "GAP (P6) — the Order defaults screen is step 6; the settings service and its endpoint "
        "land at step 1 because every later step reads these keys. Remove this entry with that "
        "screen."
    ),
    "PUT /api/v1/inventory/items/{item_id}/kit-components": (
        "GAP (P6) — the Kit components section of the Items screen is step 6. The service lands "
        "at step 1 so a kit can be defined for the step-2 posting tests. Remove this entry with "
        "that section."
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


def _caller_pattern(path: str) -> re.Pattern[str]:
    """A regex matching the path as a frontend literal would spell it.

    `/inventory/documents/{document_id}/reverse` becomes
    `/inventory/documents/ <anything but a quote> /reverse`, so a template literal
    (`` `/inventory/documents/${id}/reverse` ``) matches and a *different* endpoint that merely
    shares a prefix does not. Quotes and backticks are excluded from the wildcard so a match
    cannot span two separate strings.
    """
    without_prefix = path[len(API_PREFIX) :] if path.startswith(API_PREFIX) else path
    literal_parts = [re.escape(part) for part in re.split(r"\{[^}]+\}", without_prefix)]
    return re.compile(r"[^\"'`\n]*".join(literal_parts))


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
