"""AR/AP journal batches (P4 step 7).

A batch line posts an ordinary invoice- or credit-note-shaped partner document — the control
account moves the same way — but under the **JNL** transaction type. Three rules follow, and
each has a test here:

1. Numbering comes from the journal sequence. A journal debit must never consume an invoice
   number, or the INV- series has a hole an audit cannot explain.
2. Everything a user reads shows the transaction type, not the kind. A JNL debit reads
   "AR journal", never "Customer invoice".
3. Any figure derived from sales or purchases keys on transaction type, not kind — so a
   journal debit is not swept into sales. Nothing material depends on that today; sales
   analysis and VAT returns do in later phases, and the rule needs to exist first.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import set_tenant
from app.models.fiscal import AccountingPeriod, PeriodStatus
from app.models.subledger import PartnerDocument
from tests.conftest import make_tenant
from tests.kernel.conftest import YEAR

MARCH = date(YEAR, 3, 10)
PASSWORD = "correct horse battery staple"


class Api:
    def __init__(self, client: TestClient, db: Session, company_id: int) -> None:
        self.client = client
        self.db = db
        self.company_id = company_id


@pytest.fixture
def api(client: TestClient, db: Session) -> Api:
    tenant = make_tenant(db, company_name="Batch Traders Ltd", email="owner@batch.example")
    set_tenant(db, tenant.company.id)
    for period in db.scalars(select(AccountingPeriod)):
        period.status = PeriodStatus.OPEN
    db.commit()
    response = client.post(
        "/api/v1/auth/login", json={"email": "owner@batch.example", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return Api(client, db, tenant.company.id)


def _accounts(api: Api) -> dict[str, int]:
    return {row["code"]: row["id"] for row in api.client.get("/api/v1/gl/accounts").json()}


def _customer(api: Api, code: str, name: str) -> dict:
    created = api.client.post(
        "/api/v1/subledger/ar/partners", json={"name": name, "customer_code": code}
    )
    assert created.status_code == 201, created.text
    return created.json()


def _post_batch(api: Api, lines: list[dict], key: str = "batch-1") -> dict:
    response = api.client.post(
        "/api/v1/subledger/ar/batches",
        headers={"Idempotency-Key": key},
        json={"batch_date": MARCH.isoformat(), "reference": "Monthly charges", "lines": lines},
    )
    return response


# --- 1. Numbering ---------------------------------------------------------------------------


def test_batch_lines_draw_from_the_journal_sequence_never_the_invoice_one(
    api: Api, db: Session
) -> None:
    accounts = _accounts(api)
    alice = _customer(api, "CUST-A", "Alice Ltd")

    # An ordinary invoice first, so the invoice sequence is genuinely in play.
    invoice = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "inv-1"},
        json={
            "kind": "invoice",
            "partner_id": alice["id"],
            "document_date": MARCH.isoformat(),
            "description": "A real invoice",
            "lines": [{"unit_price": "5000", "gl_account_id": accounts["4100"]}],
        },
    )
    assert invoice.status_code == 201, invoice.text
    assert invoice.json()["number"] == "INV-000001"

    posted = _post_batch(
        api,
        [
            {
                "partner_id": alice["id"],
                "contra_account_id": accounts["4300"],
                "amount": "1200",
                "description": "Interest on overdue account",
            }
        ],
    )
    assert posted.status_code == 201, posted.text
    document = posted.json()["documents"][0]

    # The journal series, not the invoice series...
    assert document["number"].startswith("ARJ-"), document["number"]
    assert document["transaction_type"] == "JNL"

    # ...and the invoice sequence is untouched, so the next invoice is still INV-000002.
    following = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "inv-2"},
        json={
            "kind": "invoice",
            "partner_id": alice["id"],
            "document_date": MARCH.isoformat(),
            "description": "The next real invoice",
            "lines": [{"unit_price": "2500", "gl_account_id": accounts["4100"]}],
        },
    )
    assert following.status_code == 201, following.text
    assert following.json()["number"] == "INV-000002", "a journal debit consumed an invoice number"


# --- 2. Display -----------------------------------------------------------------------------


def test_the_enquiry_and_statement_show_the_transaction_type_not_the_kind(api: Api) -> None:
    accounts = _accounts(api)
    alice = _customer(api, "CUST-A", "Alice Ltd")
    posted = _post_batch(
        api,
        [
            {
                "partner_id": alice["id"],
                "contra_account_id": accounts["4300"],
                "amount": "1200",
                "description": "Interest on overdue account",
            }
        ],
    )
    assert posted.status_code == 201, posted.text

    enquiry = api.client.get(
        f"/api/v1/subledger/ar/enquiry/{alice['id']}",
        params={"as_of": (MARCH + timedelta(days=1)).isoformat()},
    ).json()
    row = enquiry["entries"][0]
    # Invoice-shaped in the ledger...
    assert row["kind"] == "invoice"
    # ...but it reads as what it is.
    assert row["transaction_type"] == "JNL"
    assert row["transaction_type_name"] == "AR journal"
    assert "invoice" not in row["transaction_type_name"].lower()

    open_item = enquiry["open_items"][0]
    assert open_item["transaction_type_name"] == "AR journal"

    from app.subledger.statements import render_statement_html

    html = render_statement_html(
        api.db,
        api.company_id,
        __import__("app.models.partner", fromlist=["PartnerRole"]).PartnerRole.AR,
        partner_ids=[alice["id"]],
        as_of=MARCH + timedelta(days=1),
    )
    assert "AR journal" in html
    assert "Customer invoice" not in html


# --- 3. Exclusion ---------------------------------------------------------------------------


def test_a_sales_total_keyed_on_transaction_type_excludes_journal_debits(
    api: Api, db: Session
) -> None:
    """The rule sales analysis and VAT returns will need, asserted before they exist.

    Both documents are invoice-shaped and both debit the customer, so anything keyed on `kind`
    counts them together. Keyed on transaction type, only the invoice is a sale.
    """
    accounts = _accounts(api)
    alice = _customer(api, "CUST-A", "Alice Ltd")

    invoice = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "sale-1"},
        json={
            "kind": "invoice",
            "partner_id": alice["id"],
            "document_date": MARCH.isoformat(),
            "description": "Goods sold",
            "lines": [{"unit_price": "5000", "gl_account_id": accounts["4100"]}],
        },
    )
    assert invoice.status_code == 201, invoice.text
    assert _post_batch(
        api,
        [
            {
                "partner_id": alice["id"],
                "contra_account_id": accounts["4300"],
                "amount": "1200",
                "description": "Interest on overdue account",
            }
        ],
    ).status_code == 201

    set_tenant(db, api.company_id)
    keyed_on_kind = db.execute(
        text(
            "SELECT COALESCE(SUM(total_amount), 0) FROM partner_documents "
            "WHERE company_id = :cid AND role = 'ar' AND kind = 'invoice'"
        ),
        {"cid": api.company_id},
    ).scalar_one()
    keyed_on_transaction_type = db.execute(
        text(
            "SELECT COALESCE(SUM(total_amount), 0) FROM partner_documents "
            "WHERE company_id = :cid AND role = 'ar' AND transaction_type = 'INV'"
        ),
        {"cid": api.company_id},
    ).scalar_one()

    # Keyed on kind the journal debit is swept in — 6,200 instead of 5,000. This is the
    # mistake the rule exists to prevent, and it is asserted so the difference is visible.
    assert Decimal(keyed_on_kind) == Decimal(6200)
    assert Decimal(keyed_on_transaction_type) == Decimal(5000)


# --- Due dates, atomicity, credit hold -------------------------------------------------------


def test_a_journal_debit_ages_on_the_partner_terms(api: Api) -> None:
    """Journal debits age: due date is the document date plus the partner's terms, and it can
    be overridden per line like any other document."""
    accounts = _accounts(api)
    alice = _customer(api, "CUST-A", "Alice Ltd")
    terms = {
        row["code"]: row["id"] for row in api.client.get("/api/v1/subledger/payment-terms").json()
    }
    api.client.put(
        f"/api/v1/subledger/ar/partners/{alice['id']}/settings",
        json={"payment_terms_id": terms["NET30"], "tax_mode": "exclusive"},
    )

    posted = _post_batch(
        api,
        [
            {
                "partner_id": alice["id"],
                "contra_account_id": accounts["4300"],
                "amount": "1200",
                "description": "Takes the partner's terms",
            },
            {
                "partner_id": alice["id"],
                "contra_account_id": accounts["4300"],
                "amount": "800",
                "description": "Overridden",
                "due_date": (MARCH + timedelta(days=7)).isoformat(),
            },
        ],
    )
    assert posted.status_code == 201, posted.text
    documents = posted.json()["documents"]
    assert documents[0]["due_date"] == (MARCH + timedelta(days=30)).isoformat()
    assert documents[1]["due_date"] == (MARCH + timedelta(days=7)).isoformat()


def test_the_batch_posts_one_journal_entry_per_line_and_control_equals_open_items(
    api: Api, db: Session
) -> None:
    accounts = _accounts(api)
    partners = [_customer(api, f"CUST-{n}", f"Partner {n}") for n in ("A", "B", "C")]
    posted = _post_batch(
        api,
        [
            {
                "partner_id": partners[0]["id"],
                "contra_account_id": accounts["4300"],
                "amount": "1200",
                "description": "Charge",
            },
            {
                "partner_id": partners[1]["id"],
                "contra_account_id": accounts["4300"],
                "amount": "900",
                "description": "Charge",
            },
            {
                "partner_id": partners[2]["id"],
                "contra_account_id": accounts["4300"],
                "amount": "-400",
                "description": "Credit",
            },
        ],
    )
    assert posted.status_code == 201, posted.text
    documents = posted.json()["documents"]
    assert len(documents) == 3

    # One journal entry per line, all distinct.
    entry_ids = {
        api.client.get(f"/api/v1/subledger/ar/documents/{d['id']}").json()["journal_entry_id"]
        for d in documents
    }
    assert len(entry_ids) == 3

    # The negative line posted as a credit note, and reads as a journal all the same.
    kinds = {d["kind"] for d in documents}
    assert kinds == {"invoice", "credit_note"}
    assert {d["transaction_type"] for d in documents} == {"JNL"}

    # Control balance equals the sum of open items.
    set_tenant(db, api.company_id)
    control = db.execute(
        text(
            "SELECT COALESCE(SUM(base_amount), 0) FROM journal_lines "
            "WHERE company_id = :cid AND gl_account_id = :account"
        ),
        {"cid": api.company_id, "account": accounts["1200"]},
    ).scalar_one()
    open_items = db.execute(
        text(
            "SELECT COALESCE(SUM(direction * open_amount), 0) FROM partner_documents "
            "WHERE company_id = :cid AND role = 'ar'"
        ),
        {"cid": api.company_id},
    ).scalar_one()
    assert Decimal(control) == Decimal(open_items) == Decimal(1700)


def test_a_batch_with_a_partner_on_hold_is_refused_whole(api: Api, db: Session) -> None:
    """One bad line takes the batch with it — nothing is committed, so the good lines are not
    left stranded for someone to reconcile by hand."""
    accounts = _accounts(api)
    alice = _customer(api, "CUST-A", "Alice Ltd")
    held = _customer(api, "CUST-H", "Held Ltd")
    api.client.put(
        f"/api/v1/subledger/ar/partners/{held['id']}/settings",
        json={"is_on_hold": True, "tax_mode": "exclusive"},
    )

    response = _post_batch(
        api,
        [
            {
                "partner_id": alice["id"],
                "contra_account_id": accounts["4300"],
                "amount": "1200",
                "description": "Would have been fine",
            },
            {
                "partner_id": held["id"],
                "contra_account_id": accounts["4300"],
                "amount": "900",
                "description": "On hold",
            },
        ],
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "partner_on_hold"
    assert "Line 2" in body["message"]

    # Nothing at all was written — not even the first line.
    set_tenant(db, api.company_id)
    assert db.scalars(select(PartnerDocument)).all() == []
