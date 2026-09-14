"""A refused reversal leaves nothing behind — through the HTTP layer, with real commits.

The service-level tests all run inside one transaction that is never committed, so they cannot
tell a service that refuses *before* it writes from one that writes and then refuses: both look
identical once the transaction is discarded. This file is the one that can, because the endpoint
commits on success and only on success, and every assertion below is made from a **fresh
session** after the request has finished.

What it pins down is the ordering inside `reverse_document`. Only the stock half of a reversal
can fail — undoing a sale puts goods back, but undoing a *return* takes goods off a shelf they
may since have left, and under `block` that is `insufficient_stock`. With the partner side
posted first, the refusal arrived after the ledger had already been written; the property
machine found the consequence as `AR control account is 16.000000 but open items total
15.000000`. The companion now goes first, so the refusal arrives before the first write.
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, set_tenant
from app.inventory import stock as stock_service
from app.models.fiscal import AccountingPeriod, PeriodStatus
from app.models.gl import GLAccount, GLSettings
from app.models.inventory import NegativeStockPolicy, Uom
from app.models.journal import JournalEntry
from app.models.subledger import DocumentStatus, PartnerDocument
from tests.conftest import make_tenant
from tests.inventory.invariants import assert_stock_invariants
from tests.kernel.conftest import YEAR
from tests.kernel.invariants import assert_ledger_invariants
from tests.order_entry.invariants import assert_order_invariants
from tests.subledger.invariants import assert_subledger_invariants

MARCH = date(YEAR, 3, 10)
PASSWORD = "correct horse battery staple"
ZERO = Decimal(0)


@pytest.fixture
def tenant_id(db: Session) -> int:
    """A committed tenant with open periods, so the HTTP requests below can see it."""
    tenant = make_tenant(db, company_name="Rugari Wines Ltd", email="owner@rugari.example")
    set_tenant(db, tenant.company.id)
    for period in db.scalars(select(AccountingPeriod)):
        period.status = PeriodStatus.OPEN
    db.commit()
    return tenant.company.id


def _fresh(company_id: int) -> Session:
    """A session of this test's own, opened **after** a request has finished.

    Reading through the request's session would prove nothing: it holds whatever the endpoint
    left in its identity map, committed or not. The whole question here is what reached the
    database.
    """
    session = SessionLocal()
    set_tenant(session, company_id)
    return session


def _post(client: TestClient, path: str, payload: dict, *, key: str | None = None):  # noqa: ANN202
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(f"/api/v1/{path}", json=payload, headers=headers)


def _setup(client: TestClient, company_id: int) -> dict:
    """Catalogue, partners and warehouse for the scenario, all through the API."""
    login = client.post(
        "/api/v1/auth/login", json={"email": "owner@rugari.example", "password": PASSWORD}
    )
    assert login.status_code == 200, login.text

    session = _fresh(company_id)
    try:
        accounts = {
            row.code: row.id for row in session.scalars(select(GLAccount)) if row.is_postable
        }
        settings = session.scalars(select(GLSettings)).one()
        warehouse_id = settings.default_warehouse_id
        category_id, uom_id = session.execute(
            select(Uom.category_id, Uom.id).limit(1)
        ).one()
    finally:
        session.close()

    item = _post(
        client,
        "inventory/items",
        {
            "code": "WINE-750",
            "name": "Rugari Red 750ml",
            "uom_category_id": category_id,
            "base_uom_id": uom_id,
            "item_type": "stock",
            "selling_price": "2000",
            "sales_account_id": accounts["4100"],
            "cogs_account_id": accounts["5100"],
        },
    )
    assert item.status_code == 201, item.text
    customer = _post(
        client, "subledger/ar/partners", {"name": "Bralirwa", "customer_code": "CUST001"}
    )
    assert customer.status_code == 201, customer.text
    supplier = _post(
        client, "subledger/ap/partners", {"name": "Kigali Glass", "supplier_code": "SUPP001"}
    )
    assert supplier.status_code == 201, supplier.text
    return {
        "item_id": item.json()["id"],
        "customer_id": customer.json()["id"],
        "supplier_id": supplier.json()["id"],
        "warehouse_id": warehouse_id,
    }


def _assert_everything(company_id: int) -> None:
    session = _fresh(company_id)
    try:
        assert_ledger_invariants(session, company_id)
        assert_subledger_invariants(session, company_id)
        assert_stock_invariants(session, company_id)
        assert_order_invariants(session, company_id)
    finally:
        session.close()


def test_a_refused_reversal_commits_nothing(
    client: TestClient, tenant_id: int, db: Session
) -> None:
    """Receive 10, sell 4, sell the remaining 6, then try to reverse the **credit note** that
    put stock back — under `block` there is nothing left to take out again.

    The refusal must be a clean 4xx that changed nothing: the document still posted, its open
    amount untouched, no reversal entry anywhere, and all four invariant suites holding.
    """
    ids = _setup(client, tenant_id)

    receipt = _post(
        client,
        "oe/goods-received-notes",
        {
            "partner_id": ids["supplier_id"],
            "grn_date": MARCH.isoformat(),
            "description": "Opening receipt",
            "warehouse_id": ids["warehouse_id"],
            "lines": [{"item_id": ids["item_id"], "quantity": "10", "unit_cost": "1000"}],
        },
        key="grn-1",
    )
    assert receipt.status_code == 201, receipt.text

    # A credit note first: it *receives* stock, so reversing it later is an issue — the one
    # direction that can be refused.
    credit_note = _post(
        client,
        "subledger/ar/documents",
        {
            "kind": "credit_note",
            "partner_id": ids["customer_id"],
            "document_date": MARCH.isoformat(),
            "description": "Goodwill credit",
            "lines": [
                {
                    "item_id": ids["item_id"],
                    "quantity": "4",
                    "unit_price": "2000",
                    "warehouse_id": ids["warehouse_id"],
                }
            ],
        },
        key="crn-1",
    )
    assert credit_note.status_code == 201, credit_note.text
    document_id = credit_note.json()["id"]
    open_before = Decimal(credit_note.json()["open_amount"])

    # Now empty the location: 10 received + 4 returned = 14 on the shelf, all sold.
    sale = _post(
        client,
        "subledger/ar/documents",
        {
            "kind": "invoice",
            "partner_id": ids["customer_id"],
            "document_date": MARCH.isoformat(),
            "description": "Sell the lot",
            "lines": [
                {
                    "item_id": ids["item_id"],
                    "quantity": "14",
                    "unit_price": "2000",
                    "warehouse_id": ids["warehouse_id"],
                }
            ],
        },
        key="inv-1",
    )
    assert sale.status_code == 201, sale.text

    session = _fresh(tenant_id)
    try:
        assert session.scalars(select(GLSettings)).one().negative_stock_policy == (
            NegativeStockPolicy.BLOCK
        ), "this test is about the block policy; the default moved"
        entries_before = len(list(session.scalars(select(JournalEntry))))
    finally:
        session.close()
    _assert_everything(tenant_id)

    refused = _post(
        client,
        f"subledger/ar/documents/{document_id}/reverse",
        {"on_date": MARCH.isoformat(), "reason": "Raised in error"},
    )

    assert 400 <= refused.status_code < 500, refused.text
    assert refused.json()["code"] == "insufficient_stock", refused.text

    # --- and nothing at all was committed ---------------------------------------------------
    session = _fresh(tenant_id)
    try:
        document = session.get(PartnerDocument, document_id)
        assert document.status == DocumentStatus.POSTED, "the document was half-reversed"
        assert document.reversal_entry_id is None
        assert document.reversed_on is None
        assert document.open_amount == open_before, "the open amount moved on a refused reversal"
        assert len(list(session.scalars(select(JournalEntry)))) == entries_before, (
            "a reversal entry reached the ledger for a document that was never reversed"
        )
    finally:
        session.close()
    _assert_everything(tenant_id)


def test_a_reversal_that_can_succeed_still_does(
    client: TestClient, tenant_id: int, db: Session
) -> None:
    """The other half, so the test above cannot pass by breaking reversal altogether.

    Same shape with the stock left on the shelf: the reversal goes through, both entries come
    back, and the document is closed out.
    """
    ids = _setup(client, tenant_id)
    assert (
        _post(
            client,
            "oe/goods-received-notes",
            {
                "partner_id": ids["supplier_id"],
                "grn_date": MARCH.isoformat(),
                "description": "Opening receipt",
                "warehouse_id": ids["warehouse_id"],
                "lines": [{"item_id": ids["item_id"], "quantity": "10", "unit_cost": "1000"}],
            },
            key="grn-1",
        ).status_code
        == 201
    )
    sale = _post(
        client,
        "subledger/ar/documents",
        {
            "kind": "invoice",
            "partner_id": ids["customer_id"],
            "document_date": MARCH.isoformat(),
            "description": "Sale",
            "lines": [
                {
                    "item_id": ids["item_id"],
                    "quantity": "4",
                    "unit_price": "2000",
                    "warehouse_id": ids["warehouse_id"],
                }
            ],
        },
        key="inv-1",
    )
    assert sale.status_code == 201, sale.text
    document_id = sale.json()["id"]

    reversed_response = _post(
        client,
        f"subledger/ar/documents/{document_id}/reverse",
        {"on_date": MARCH.isoformat(), "reason": "Keyed twice"},
    )

    assert reversed_response.status_code == 201, reversed_response.text
    session = _fresh(tenant_id)
    try:
        document = session.get(PartnerDocument, document_id)
        assert document.status == DocumentStatus.REVERSED
        assert document.reversal_entry_id is not None
        assert document.open_amount == ZERO
        # Both halves came back: the stock is on the shelf again at the cost it left at.
        assert stock_service.location_balance(
            session, tenant_id, ids["item_id"], ids["warehouse_id"]
        ).quantity == Decimal(10)
    finally:
        session.close()
    _assert_everything(tenant_id)
