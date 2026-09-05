"""P2 DoD gap-fill: caller-owned transactions, validation order, enquiry filters,
reversal edge cases, router permission guard, cashbook idempotency parity, COA hierarchy.

Reuses `ledger` and `assert_ledger_invariants`. No kernel behaviour changes — these pin
what the prompt required and what the code actually does.
"""

import inspect
from datetime import date
from decimal import Decimal

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1 import gl as gl_api
from app.db import set_tenant
from app.kernel import accounts as accounts_service
from app.kernel import posting
from app.kernel.enquiries import account_transactions, trial_balance
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import LineSpec, ManualJournal
from app.kernel.sequences import DocType, claim_number
from app.models.fiscal import PeriodStatus
from app.models.gl import AccountClass
from app.models.journal import DocumentSequence, JournalEntry, JournalLine, PeriodBalance
from tests.kernel.conftest import YEAR, Ledger, post_simple
from tests.kernel.invariants import assert_ledger_invariants
from tests.kernel.test_gl_api import Api

MARCH = date(YEAR, 3, 15)
APRIL = date(YEAR, 4, 2)
FEBRUARY = date(YEAR, 2, 10)


@pytest.fixture
def api(client: TestClient, db: Session) -> Api:
    return Api(client, db)


# --- 5. Caller-owned transaction -------------------------------------------------------------


def test_post_rolls_back_with_the_caller_transaction(db: Session, ledger: Ledger) -> None:
    """`post()` never commits: aborting the caller leaves no entry, no lines, no cache
    movement, and the claimed document number is reusable."""
    post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(400), on=MARCH)
    db.rollback()
    set_tenant(db, ledger.company_id)

    assert db.scalar(select(func.count()).select_from(JournalEntry)) == 0
    assert db.scalar(select(func.count()).select_from(JournalLine)) == 0
    assert db.scalar(select(func.count()).select_from(PeriodBalance)) == 0

    claimed = claim_number(db, ledger.company_id, DocType.JOURNAL)
    assert claimed.sequence_no == 1
    sequence = db.scalars(
        select(DocumentSequence).where(
            DocumentSequence.company_id == ledger.company_id,
            DocumentSequence.doc_type == DocType.JOURNAL,
            DocumentSequence.branch_id.is_(None),
        )
    ).one()
    assert sequence.next_number == 2
    db.rollback()
    set_tenant(db, ledger.company_id)
    assert_ledger_invariants(db, ledger.company_id)


# --- 6. Validation order ---------------------------------------------------------------------


def test_validation_order_is_period_then_account_then_dimension_then_balance(
    db: Session, ledger: Ledger
) -> None:
    """Prompt order: period open → accounts active & postable → dimensions → balances.

    A ManualJournal cannot reach the AR-partner dimension (control accounts are refused
    first as `control_account_manual_posting`); `tax_amount` without `tax_code_id` is the
    dimension rule that *is* reachable on a manual entry.
    """
    march = next(p for p in ledger.periods if p.start_date <= MARCH <= p.end_date)
    march.status = PeriodStatus.CLOSED
    ledger.accounts["6500"].is_active = False
    db.flush()

    unbalanced_untaxed = ManualJournal(
        entry_date=MARCH,
        description="bad on every axis",
        lines=(
            LineSpec(
                amount=Decimal(100),
                gl_account_id=ledger.acct("6500"),
                tax_amount=Decimal(18),
            ),
            LineSpec(amount=Decimal(-99), gl_account_id=ledger.acct("2300")),
        ),
    )

    with pytest.raises(PostingError) as excinfo:
        posting.post(db, unbalanced_untaxed, company_id=ledger.company_id, actor=ledger.owner)
    assert excinfo.value.code == "period_not_open"

    march.status = PeriodStatus.OPEN
    db.flush()
    with pytest.raises(PostingError) as excinfo:
        posting.post(db, unbalanced_untaxed, company_id=ledger.company_id, actor=ledger.owner)
    assert excinfo.value.code == "account_inactive"

    ledger.accounts["6500"].is_active = True
    db.flush()
    with pytest.raises(PostingError) as excinfo:
        posting.post(db, unbalanced_untaxed, company_id=ledger.company_id, actor=ledger.owner)
    assert excinfo.value.code == "dimension_invalid"

    vat = ledger.tax_codes["VAT-IN-18"].id
    still_unbalanced = ManualJournal(
        entry_date=MARCH,
        description="dimension fixed, still unbalanced",
        lines=(
            LineSpec(
                amount=Decimal(100),
                gl_account_id=ledger.acct("6500"),
                tax_code_id=vat,
                tax_amount=Decimal(18),
            ),
            LineSpec(amount=Decimal(-99), gl_account_id=ledger.acct("2300")),
        ),
    )
    with pytest.raises(PostingError) as excinfo:
        posting.post(db, still_unbalanced, company_id=ledger.company_id, actor=ledger.owner)
    assert excinfo.value.code == "unbalanced_entry"
    db.rollback()


# --- 7. Enquiry filters ----------------------------------------------------------------------


def test_enquiry_filters_by_project_and_branch_and_running_balance_matches(
    db: Session, ledger: Ledger
) -> None:
    alpha = ledger.projects["P-ALPHA"].id
    beta = ledger.projects["P-BETA"].id
    musanze = ledger.branches["MUS"].id

    post_simple(
        db,
        ledger,
        debit="6500",
        credit="2300",
        amount=Decimal(500),
        on=FEBRUARY,
        project_id=alpha,
        description="alpha feb",
    )
    post_simple(
        db,
        ledger,
        debit="6200",
        credit="2300",
        amount=Decimal(300),
        on=MARCH,
        project_id=beta,
        description="beta mar",
    )
    post_simple(
        db,
        ledger,
        debit="6100",
        credit="2300",
        amount=Decimal(100),
        on=MARCH,
        description="untagged",
    )
    post_simple(
        db,
        ledger,
        debit="6300",
        credit="2300",
        amount=Decimal(40),
        on=MARCH,
        branch_id=musanze,
        project_id=alpha,
        description="alpha musanze",
    )
    db.commit()

    alpha_tb = trial_balance(db, ledger.company_id, as_of=MARCH, project_id=alpha)
    assert alpha_tb.foots
    assert {row.code: row.net for row in alpha_tb.rows if row.net != 0} == {
        "6500": Decimal(500),
        "6300": Decimal(40),
        "2300": Decimal(-540),
    }

    beta_tb = trial_balance(db, ledger.company_id, as_of=MARCH, project_id=beta)
    assert beta_tb.foots
    assert {row.code: row.net for row in beta_tb.rows if row.net != 0} == {
        "6200": Decimal(300),
        "2300": Decimal(-300),
    }

    musanze_tb = trial_balance(db, ledger.company_id, as_of=MARCH, branch_id=musanze)
    assert musanze_tb.foots
    assert {row.code: row.net for row in musanze_tb.rows if row.net != 0} == {
        "6300": Decimal(40),
        "2300": Decimal(-40),
    }

    tx = account_transactions(
        db,
        ledger.company_id,
        ledger.acct("2300"),
        date_from=MARCH,
        date_to=APRIL,
        project_id=alpha,
    )
    assert tx.opening_base == Decimal(-500)
    running = tx.opening_base
    for item in tx.items:
        running += item.base_amount
        assert item.running_base == running
        assert item.project_id == alpha
    assert [item.base_amount for item in tx.items] == [Decimal(-40)]
    assert running == Decimal(-540)

    branch_tx = account_transactions(
        db,
        ledger.company_id,
        ledger.acct("2300"),
        date_from=FEBRUARY,
        date_to=APRIL,
        branch_id=musanze,
    )
    assert branch_tx.opening_base == Decimal(0)
    running = branch_tx.opening_base
    for item in branch_tx.items:
        running += item.base_amount
        assert item.running_base == running
        assert item.branch_id == musanze
    assert running == Decimal(-40)
    assert_ledger_invariants(db, ledger.company_id)


# --- 8. Reversal into a closed period / before the original ----------------------------------


def test_reversal_into_a_closed_period_is_rejected_at_the_api(api: Api) -> None:
    entry_id = api.post_je("orig", entry_date=f"{YEAR}-02-01").json()["id"]
    march = api.periods[3]
    assert api.client.post(f"/api/v1/gl/periods/{march}/close").json()["status"] == "closed"

    response = api.client.post(
        f"/api/v1/gl/journal-entries/{entry_id}/reverse",
        json={"entry_date": f"{YEAR}-03-15", "reason": "late"},
        headers={"Idempotency-Key": "rev-closed"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "period_not_open"
    listing = api.client.get("/api/v1/gl/journal-entries").json()["items"]
    assert [item["number"] for item in listing] == ["JE-000001"]


def test_reversal_dated_before_the_original_is_rejected_at_the_api(api: Api) -> None:
    """Decision (already in the kernel): a reversal may not pre-date the entry it reverses."""
    entry_id = api.post_je("orig", entry_date=f"{YEAR}-03-15").json()["id"]
    response = api.client.post(
        f"/api/v1/gl/journal-entries/{entry_id}/reverse",
        json={"entry_date": f"{YEAR}-03-01", "reason": "backdate"},
        headers={"Idempotency-Key": "rev-before"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "reversal_before_original"


# --- 9. Router permission guard --------------------------------------------------------------


def test_every_gl_route_requires_a_permission() -> None:
    """Same honesty bar as P1's sanctioned-list test: every GL route must go through
    `permissions.require(...)`, and the walk must actually see the router (anti-vacuity)."""
    routes = [route for route in gl_api.router.routes if isinstance(route, APIRoute)]
    assert len(routes) >= 30, f"GL router shrank unexpectedly: {len(routes)} routes"

    missing: list[str] = []
    for route in routes:
        source = inspect.getsource(route.endpoint)
        if "permissions.require(" not in source:
            methods = ",".join(sorted(route.methods or []))
            missing.append(f"{methods} {route.path}")
    assert missing == [], f"GL routes without permissions.require: {missing}"


# --- 10. Cashbook idempotency parity ---------------------------------------------------------


def _cashbook_body(api: Api, amount: str) -> dict:
    return {
        "entry_date": f"{YEAR}-03-20",
        "description": "Stationery, paid by bank",
        "cash_account_id": api.accounts["1120"],
        "kind": "payment",
        "lines": [{"gl_account_id": api.accounts["6500"], "amount": amount}],
    }


def test_cashbook_idempotency_replays_the_same_key_and_body(api: Api) -> None:
    headers = {"Idempotency-Key": "cb-same"}
    first = api.client.post(
        "/api/v1/gl/cashbook-entries", json=_cashbook_body(api, "1000"), headers=headers
    )
    second = api.client.post(
        "/api/v1/gl/cashbook-entries", json=_cashbook_body(api, "1000"), headers=headers
    )
    assert first.status_code == 201 and second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]
    listing = api.client.get("/api/v1/gl/journal-entries").json()["items"]
    assert [item["number"] for item in listing] == ["CB-000001"]


def test_cashbook_same_key_different_payload_is_refused(api: Api) -> None:
    headers = {"Idempotency-Key": "cb-diff"}
    first = api.client.post(
        "/api/v1/gl/cashbook-entries", json=_cashbook_body(api, "1000"), headers=headers
    )
    assert first.status_code == 201, first.text
    changed = api.client.post(
        "/api/v1/gl/cashbook-entries", json=_cashbook_body(api, "2000"), headers=headers
    )
    assert changed.status_code == 409
    assert changed.json()["code"] == "idempotency_key_reused"


def test_cashbook_equivalent_amount_spellings_and_key_order_still_replay(api: Api) -> None:
    headers = {"Idempotency-Key": "cb-spell"}
    first = api.client.post(
        "/api/v1/gl/cashbook-entries", json=_cashbook_body(api, "1000"), headers=headers
    )
    reordered = {
        "lines": [{"amount": "1000.00", "gl_account_id": api.accounts["6500"]}],
        "kind": "payment",
        "cash_account_id": api.accounts["1120"],
        "description": "Stationery, paid by bank",
        "entry_date": f"{YEAR}-03-20",
    }
    replay = api.client.post("/api/v1/gl/cashbook-entries", json=reordered, headers=headers)
    assert (first.status_code, replay.status_code) == (201, 200), replay.text
    assert replay.json()["id"] == first.json()["id"]


# --- 11. COA hierarchy -----------------------------------------------------------------------


def test_account_cannot_be_its_own_parent(db: Session, ledger: Ledger) -> None:
    account = ledger.accounts["6500"]
    with pytest.raises(LedgerStateError) as excinfo:
        accounts_service.update_account(db, account, parent_id=account.id, actor=ledger.owner)
    assert excinfo.value.code == "invalid_parent"
    db.rollback()


def test_header_with_children_cannot_become_postable(db: Session, ledger: Ledger) -> None:
    header = ledger.accounts["6000"]
    assert header.is_postable is False
    with pytest.raises(LedgerStateError) as excinfo:
        accounts_service.update_account(db, header, is_postable=True, actor=ledger.owner)
    assert excinfo.value.code == "account_has_children"
    db.rollback()


def test_coa_parent_cycle_is_not_enforced(db: Session, ledger: Ledger) -> None:
    """Documented P2 gap: self-parent is rejected, but A→B→A through two headers is not.

    `_validate_parent` only checks 'header + same class'. Do not 'fix' this here — the
    verification suite records the decision.
    """
    parent = accounts_service.create_account(
        db,
        ledger.company_id,
        accounts_service.AccountInput(
            code="6050",
            name="Cycle parent",
            class_=AccountClass.EXPENSE,
            parent_id=ledger.acct("6000"),
            is_postable=False,
        ),
        actor=ledger.owner,
    )
    child = accounts_service.create_account(
        db,
        ledger.company_id,
        accounts_service.AccountInput(
            code="6051",
            name="Cycle child",
            class_=AccountClass.EXPENSE,
            parent_id=parent.id,
            is_postable=False,
        ),
        actor=ledger.owner,
    )
    accounts_service.update_account(db, parent, parent_id=child.id, actor=ledger.owner)
    assert parent.parent_id == child.id
    db.rollback()
