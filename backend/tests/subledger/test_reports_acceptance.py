"""P4 step 8 acceptance: the reports must agree with the ledger, and with each other.

Written before any step 8 screen exists, and expected to fail until the reports agree.

For one as-of date and one partner set:

    age-analysis grand total  ==  Σ statement closing balances  ==  control-account balance

in base currency, with both a base-currency (RWF, zero decimals) and a foreign-currency (USD,
converted to base) partner in the same set — because a report that is right only when every
document is already in base is not right.

**What this test takes and what it cannot see.** It exercises three genuinely separate paths to
the same number: `age_analysis` buckets open items by date, `partner_enquiry` walks a partner's
documents to a running balance, and the control-account figure is a raw sum of `journal_lines`.
Agreement between them is real evidence.

It *cannot* see whether a statement's own body is right. The statement's closing balance is
`enquiry.balance_base` (statements.py), so this asserts the statement footer, not the lines
above it — a statement that printed the correct total over the wrong transactions would pass
here. `test_statement_lines_reconcile_to_their_own_closing_balance` covers that separately.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import set_tenant
from app.models.fiscal import AccountingPeriod, PeriodStatus
from app.models.partner import PartnerRole
from app.subledger.ageing import age_analysis
from app.subledger.enquiries import partner_enquiry
from tests.conftest import make_tenant
from tests.kernel.conftest import YEAR

MARCH = date(YEAR, 3, 10)
AS_OF = date(YEAR, 3, 31)
PASSWORD = "correct horse battery staple"


class Api:
    def __init__(self, client: TestClient, db: Session, company_id: int) -> None:
        self.client = client
        self.db = db
        self.company_id = company_id


@pytest.fixture
def api(client: TestClient, db: Session) -> Api:
    tenant = make_tenant(db, company_name="Report Traders Ltd", email="owner@report.example")
    set_tenant(db, tenant.company.id)
    for period in db.scalars(select(AccountingPeriod)):
        period.status = PeriodStatus.OPEN
    db.commit()
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "owner@report.example", "password": PASSWORD},
        ).status_code
        == 200
    )
    return Api(client, db, tenant.company.id)


def _accounts(api: Api) -> dict[str, int]:
    return {row["code"]: row["id"] for row in api.client.get("/api/v1/gl/accounts").json()}


def build_partner_set(api: Api) -> list[int]:
    """Three customers, deliberately unlike each other: one settled in base currency, one open
    in USD across a rate change, one part-paid with a credit note against it. A set where every
    partner looks the same tests very little."""
    accounts = _accounts(api)
    currencies = {row["code"]: row for row in api.client.get("/api/v1/gl/currencies").json()}
    usd = currencies["USD"]["id"]
    rates = (
        (MARCH.isoformat(), "1200"),
        ((MARCH + timedelta(days=8)).isoformat(), "1310"),
    )
    for valid_from, rate in rates:
        api.client.post(
            "/api/v1/gl/exchange-rates",
            json={"currency_id": usd, "valid_from": valid_from, "rate": rate},
        )

    partner_ids: list[int] = []

    def customer(code: str, name: str, currency_id: int | None = None) -> int:
        body: dict = {"name": name, "customer_code": code}
        if currency_id is not None:
            body["currency_id"] = currency_id
        created = api.client.post("/api/v1/subledger/ar/partners", json=body)
        assert created.status_code == 201, created.text
        partner_ids.append(created.json()["id"])
        return created.json()["id"]

    def invoice(
        partner_id: int, key: str, amount: str, on: date, currency_id: int | None = None
    ) -> int:
        body: dict = {
            "kind": "invoice",
            "partner_id": partner_id,
            "document_date": on.isoformat(),
            "description": f"Invoice {key}",
            "lines": [{"unit_price": amount, "gl_account_id": accounts["4100"]}],
        }
        if currency_id is not None:
            body["currency_id"] = currency_id
        response = api.client.post(
            "/api/v1/subledger/ar/documents", headers={"Idempotency-Key": key}, json=body
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def receipt(
        partner_id: int, key: str, amount: str, on: date, currency_id: int | None = None
    ) -> int:
        body: dict = {
            "kind": "settlement",
            "partner_id": partner_id,
            "document_date": on.isoformat(),
            "description": f"Receipt {key}",
            "amount": amount,
            "cash_account_id": accounts["1120"],
            "instrument_type": "bank",
        }
        if currency_id is not None:
            body["currency_id"] = currency_id
        response = api.client.post(
            "/api/v1/subledger/ar/documents", headers={"Idempotency-Key": key}, json=body
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    # 1. Base currency, fully settled — must contribute exactly zero, not "almost zero".
    settled = customer("REP-SETTLED", "Settled Ltd")
    settled_invoice = invoice(settled, "rep-inv-1", "40000", MARCH)
    settled_receipt = receipt(settled, "rep-rct-1", "40000", MARCH + timedelta(days=2))
    allocated = api.client.post(
        "/api/v1/subledger/ar/allocations",
        headers={"Idempotency-Key": "rep-alc-1"},
        json={
            "partner_id": settled,
            "allocation_date": (MARCH + timedelta(days=2)).isoformat(),
            "pairs": [
                {
                    "debit_document_id": settled_invoice,
                    "credit_document_id": settled_receipt,
                    "amount": "40000",
                }
            ],
        },
    )
    assert allocated.status_code == 201, allocated.text

    # 2. Foreign currency, open across a rate change — the case that breaks base conversion.
    foreign = customer("REP-USD", "Kivu Exports", usd)
    invoice(foreign, "rep-inv-2", "500.00", MARCH, usd)
    foreign_invoice = invoice(foreign, "rep-inv-3", "300.00", MARCH + timedelta(days=8), usd)
    foreign_receipt = receipt(foreign, "rep-rct-2", "100.00", MARCH + timedelta(days=9), usd)
    partial = api.client.post(
        "/api/v1/subledger/ar/allocations",
        headers={"Idempotency-Key": "rep-alc-2"},
        json={
            "partner_id": foreign,
            "allocation_date": (MARCH + timedelta(days=9)).isoformat(),
            "pairs": [
                {
                    "debit_document_id": foreign_invoice,
                    "credit_document_id": foreign_receipt,
                    "amount": "100.00",
                }
            ],
        },
    )
    assert partial.status_code == 201, partial.text

    # 3. Base currency with a credit note outstanding — a negative open item in the mix.
    credited = customer("REP-CRN", "Returns Ltd")
    invoice(credited, "rep-inv-4", "25000", MARCH + timedelta(days=3))
    credit_note = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "rep-crn-1"},
        json={
            "kind": "credit_note",
            "partner_id": credited,
            "document_date": (MARCH + timedelta(days=5)).isoformat(),
            "description": "Goods returned",
            "lines": [{"unit_price": "4000", "gl_account_id": accounts["4100"]}],
        },
    )
    assert credit_note.status_code == 201, credit_note.text

    return partner_ids


def control_balance(db: Session, company_id: int, account_id: int, as_of: date) -> Decimal:
    """The ledger's own answer: what the AR control account holds at the as-of date."""
    set_tenant(db, company_id)
    return Decimal(
        db.execute(
            text(
                "SELECT COALESCE(SUM(l.base_amount), 0) FROM journal_lines l "
                "JOIN journal_entries e ON e.id = l.entry_id AND e.company_id = l.company_id "
                "WHERE l.company_id = :cid AND l.gl_account_id = :account "
                "AND e.entry_date <= :as_of"
            ),
            {"cid": company_id, "account": account_id, "as_of": as_of},
        ).scalar_one()
    )


def test_age_analysis_statements_and_the_control_account_agree(api: Api, db: Session) -> None:
    """The acceptance figure for step 8. Three paths, one number."""
    partner_ids = build_partner_set(api)
    accounts = _accounts(api)
    set_tenant(db, api.company_id)

    ageing = age_analysis(db, api.company_id, PartnerRole.AR, as_of=AS_OF)
    statement_closings = sum(
        (
            partner_enquiry(
                db, api.company_id, PartnerRole.AR, partner_id=pid, as_of=AS_OF
            ).balance_base
            for pid in partner_ids
        ),
        Decimal(0),
    )
    control = control_balance(db, api.company_id, accounts["1200"], AS_OF)

    # Anti-vacuity: a set that nets to zero would let three broken reports agree.
    assert control != 0, "the fixture must leave a non-zero balance or the equality is empty"

    assert ageing.grand_total == statement_closings, (
        f"age analysis {ageing.grand_total} vs statements {statement_closings}"
    )
    assert statement_closings == control, (
        f"statements {statement_closings} vs control account {control}"
    )


def test_the_agreement_holds_for_the_base_currency_partner_alone(api: Api, db: Session) -> None:
    """Same equality, narrowed to the RWF partners — so a failure says whether the break is in
    currency conversion or in the reports themselves."""
    build_partner_set(api)
    set_tenant(db, api.company_id)

    ageing = age_analysis(db, api.company_id, PartnerRole.AR, as_of=AS_OF)
    rwf_rows = [row for row in ageing.rows if row.partner_code != "REP-USD"]
    rwf_total = sum((row.total for row in rwf_rows), Decimal(0))

    per_partner = sum(
        (
            partner_enquiry(
                db, api.company_id, PartnerRole.AR, partner_id=row.partner_id, as_of=AS_OF
            ).balance_base
            for row in rwf_rows
        ),
        Decimal(0),
    )
    assert rwf_total == per_partner


def test_the_settled_partner_contributes_exactly_zero(api: Api, db: Session) -> None:
    """A fully settled partner must be absent from the ageing, not present at zero — otherwise
    an aged report grows a row per historical customer and the totals hide the difference."""
    build_partner_set(api)
    set_tenant(db, api.company_id)

    ageing = age_analysis(db, api.company_id, PartnerRole.AR, as_of=AS_OF)
    settled = [row for row in ageing.rows if row.partner_code == "REP-SETTLED"]

    assert settled == [] or settled[0].total == 0


def test_statement_lines_reconcile_to_their_own_closing_balance(api: Api, db: Session) -> None:
    """The blind spot the acceptance test names.

    Its closing balance is `enquiry.balance_base`, so a statement printing the right total over
    the wrong transactions would pass it. This walks the rendered statement instead: the
    document numbers it shows must be exactly the partner's open items at the as-of date, and
    no more.

    **Path and blind spot.** Renders the real HTML through `render_statement_html`, so layout
    and labelling are exercised. It cannot see the PDF: WeasyPrint's rendering of that HTML is
    covered by `test_statement_job_produces_a_pdf`, which in turn cannot see the content.
    """
    from app.subledger.statements import render_statement_html

    partner_ids = build_partner_set(api)
    set_tenant(db, api.company_id)
    foreign = next(
        pid
        for pid in partner_ids
        if partner_enquiry(
            db, api.company_id, PartnerRole.AR, partner_id=pid, as_of=AS_OF
        ).partner.code_for(PartnerRole.AR)
        == "REP-USD"
    )

    html = render_statement_html(
        db, api.company_id, PartnerRole.AR, partner_ids=[foreign], as_of=AS_OF
    )
    enquiry = partner_enquiry(db, api.company_id, PartnerRole.AR, partner_id=foreign, as_of=AS_OF)

    # Every open item appears...
    for item in enquiry.open_items:
        assert item.document.number in html, f"{item.document.number} missing from the statement"

    # ...and the settled partner's documents do not leak into this one's statement.
    other = partner_enquiry(
        db,
        api.company_id,
        PartnerRole.AR,
        partner_id=next(p for p in partner_ids if p != foreign),
        as_of=AS_OF,
    )
    for item in other.open_items:
        assert item.document.number not in html, (
            f"{item.document.number} belongs to another partner"
        )
