"""P5 step 3 — the stock document API.

Endpoint-level coverage for the screens step 7 will render: what the Adjustments workspace
and the Journal-batch grid post, what comes back, which permission each route demands, and
what a retried request does. Every request goes through the real cookie session on the
RLS-enforcing role.
"""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import set_tenant
from app.models.membership import Role
from app.services import email as email_service

OWNER = {
    "company_name": "Rugari Wines Ltd",
    "full_name": "Aline Uwase",
    "email": "owner@rugari.example",
    "password": "correct horse battery staple",
}
CLERK_PASSWORD = "another good passphrase"
TODAY = date.today().replace(month=1, day=15).isoformat()


def _signup(client: TestClient) -> int:
    return client.post("/api/v1/auth/signup", json=OWNER).json()["company_id"]


def _clerk(client: TestClient, db: Session, company_id: int) -> TestClient:
    set_tenant(db, company_id)
    role_id = db.scalars(
        select(Role.id).where(Role.company_id == company_id, Role.name == "Clerk")
    ).one()
    client.post(
        "/api/v1/invitations",
        json={"email": "clerk@rugari.example", "role_ids": [role_id]},
    )
    token = email_service.outbox[-1].context["token"]
    clerk = TestClient(client.app)
    clerk.post(
        "/api/v1/invitations/accept",
        json={"token": token, "full_name": "Clerk Person", "password": CLERK_PASSWORD},
    )
    return clerk


def _stock_item(client: TestClient) -> dict:
    categories = client.get("/api/v1/inventory/uom-categories").json()
    category = next(row for row in categories if row["code"] == "COUNT")
    return client.post(
        "/api/v1/inventory/items",
        json={
            "code": "WINE-001",
            "name": "Rugari Red 750ml",
            "uom_category_id": category["id"],
            "base_uom_id": category["uoms"][0]["id"],
            "selling_price": "8500",
        },
    ).json()


def _main_warehouse(client: TestClient) -> dict:
    rows = client.get("/api/v1/inventory/warehouses").json()
    return next(row for row in rows if row["code"] == "MAIN")


def _type_id(client: TestClient, code: str) -> int:
    rows = client.get("/api/v1/gl/transaction-types", params={"module": "inv"}).json()
    rows = rows["items"] if isinstance(rows, dict) else rows
    return next(row["id"] for row in rows if row["code"] == code)


def _adjustment_payload(client: TestClient, **overrides) -> dict:
    item = _stock_item(client)
    warehouse = _main_warehouse(client)
    return {
        "document_date": TODAY,
        "description": "Opening count correction",
        "lines": [
            {
                "item_id": item["id"],
                "warehouse_id": warehouse["id"],
                "quantity": "10",
                "unit_cost": "100",
                "transaction_type_id": _type_id(client, "ADJIN"),
            }
        ],
        **overrides,
    }


# --- Posting ---------------------------------------------------------------------------------


def test_an_adjustment_posts_and_comes_back_with_its_lines(client: TestClient) -> None:
    _signup(client)

    response = client.post(
        "/api/v1/inventory/adjustments",
        json=_adjustment_payload(client),
        headers={"Idempotency-Key": "adj-1"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "posted"
    assert body["number"]
    assert body["journal_entry_id"] is not None
    assert len(body["lines"]) == 1
    assert body["lines"][0]["quantity_base"] == "10.000000"
    assert body["lines"][0]["stock_move_id"] is not None


def test_replaying_the_key_returns_200_and_the_same_document(client: TestClient) -> None:
    _signup(client)
    payload = _adjustment_payload(client)

    first = client.post(
        "/api/v1/inventory/adjustments", json=payload, headers={"Idempotency-Key": "adj-2"}
    )
    second = client.post(
        "/api/v1/inventory/adjustments", json=payload, headers={"Idempotency-Key": "adj-2"}
    )

    assert first.status_code == 201
    assert second.status_code == 200, "a replay is not a new document"
    assert second.json()["id"] == first.json()["id"]


def test_a_journal_batch_posts_many_lines_as_one_document(client: TestClient) -> None:
    _signup(client)
    item = _stock_item(client)
    warehouse = _main_warehouse(client)

    response = client.post(
        "/api/v1/inventory/journal-batches",
        json={
            "document_date": TODAY,
            "description": "Opening stock",
            "lines": [
                {
                    "item_id": item["id"],
                    "warehouse_id": warehouse["id"],
                    "quantity": "10",
                    "unit_cost": "100",
                    "transaction_type_id": _type_id(client, "OPEN"),
                },
                {
                    "item_id": item["id"],
                    "warehouse_id": warehouse["id"],
                    "quantity": "5",
                    "unit_cost": "120",
                    "transaction_type_id": _type_id(client, "OPEN"),
                },
            ],
        },
        headers={"Idempotency-Key": "batch-1"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["doc_type"] == "INJN"
    assert len(body["lines"]) == 2


def test_an_opening_balance_batch_books_its_contra_to_3400(client: TestClient) -> None:
    """Decision 2's go-live path, end to end through the endpoint.

    Inventory accounts are control accounts, so opening stock cannot arrive as a GL journal.
    It comes through here under the seeded `OPEN` type, whose contra is `3400 Opening Balance
    Suspense` — the account that carries what the opening stock was worth until the rest of
    the opening trial balance lands against it.
    """
    _signup(client)
    item = _stock_item(client)
    warehouse = _main_warehouse(client)

    response = client.post(
        "/api/v1/inventory/journal-batches",
        json={
            "document_date": TODAY,
            "description": "Opening stock at go-live",
            "lines": [
                {
                    "item_id": item["id"],
                    "warehouse_id": warehouse["id"],
                    "quantity": "10",
                    "unit_cost": "100",
                    "transaction_type_id": _type_id(client, "OPEN"),
                }
            ],
        },
        headers={"Idempotency-Key": "opening-1"},
    )
    assert response.status_code == 201, response.text
    entry_id = response.json()["journal_entry_id"]

    code_of = {
        row["id"]: row["code"] for row in client.get("/api/v1/gl/accounts").json()
    }
    entry = client.get(f"/api/v1/gl/journal-entries/{entry_id}").json()
    by_code = {code_of[line["gl_account_id"]]: line for line in entry["lines"]}

    assert "3400" in by_code, f"opening stock must book to 3400, got {sorted(by_code)}"
    assert Decimal(by_code["3400"]["base_amount"]) == Decimal("-1000")

    inventory_line = next(line for code, line in by_code.items() if code != "3400")
    assert Decimal(inventory_line["base_amount"]) == Decimal("1000")
    assert inventory_line["item_id"] == item["id"], "every INV line carries its item"


def test_a_batch_line_failure_refuses_the_whole_batch(client: TestClient) -> None:
    _signup(client)
    item = _stock_item(client)
    warehouse = _main_warehouse(client)

    response = client.post(
        "/api/v1/inventory/journal-batches",
        json={
            "document_date": TODAY,
            "description": "Opening stock",
            "lines": [
                {
                    "item_id": item["id"],
                    "warehouse_id": warehouse["id"],
                    "quantity": "10",
                    "unit_cost": "100",
                    "transaction_type_id": _type_id(client, "OPEN"),
                },
                {
                    "item_id": item["id"],
                    "warehouse_id": warehouse["id"],
                    "quantity": "5",
                    "transaction_type_id": _type_id(client, "OPEN"),
                },
            ],
        },
        headers={"Idempotency-Key": "batch-2"},
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "unit_cost_required"
    # The message lands on the offending line, which is what the grid binds to.
    assert "lines.1.unit_cost" in body["field_errors"]

    listed = client.get("/api/v1/inventory/documents").json()
    assert listed["items"] == [], "nothing of a refused batch is kept"


def test_insufficient_stock_comes_back_on_the_quantity_cell(client: TestClient) -> None:
    _signup(client)
    item = _stock_item(client)
    warehouse = _main_warehouse(client)

    response = client.post(
        "/api/v1/inventory/adjustments",
        json={
            "document_date": TODAY,
            "description": "Take out what is not there",
            "lines": [
                {
                    "item_id": item["id"],
                    "warehouse_id": warehouse["id"],
                    "quantity": "3",
                    "transaction_type_id": _type_id(client, "ADJOUT"),
                }
            ],
        },
        headers={"Idempotency-Key": "adj-short"},
    )

    # A state refusal, not a malformed payload: `LedgerStateError` maps to 409, while the
    # batch's missing unit cost above is a `PostingError` and maps to 422.
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "insufficient_stock"
    assert "lines.0.quantity" in body["field_errors"]


# --- Reversal, reading and permissions -------------------------------------------------------


def test_a_document_can_be_reversed_and_links_both_ways(client: TestClient) -> None:
    _signup(client)
    posted = client.post(
        "/api/v1/inventory/adjustments",
        json=_adjustment_payload(client),
        headers={"Idempotency-Key": "adj-3"},
    ).json()

    reversal = client.post(
        f"/api/v1/inventory/documents/{posted['id']}/reverse",
        json={"reversal_date": TODAY, "reason": "keyed twice"},
        headers={"Idempotency-Key": "rev-1"},
    )

    assert reversal.status_code == 201, reversal.text
    assert reversal.json()["reverses_document_id"] == posted["id"]

    original = client.get(f"/api/v1/inventory/documents/{posted['id']}").json()
    assert original["status"] == "reversed"


def test_documents_list_and_fetch(client: TestClient) -> None:
    _signup(client)
    posted = client.post(
        "/api/v1/inventory/adjustments",
        json=_adjustment_payload(client),
        headers={"Idempotency-Key": "adj-4"},
    ).json()

    listed = client.get("/api/v1/inventory/documents").json()
    assert [row["id"] for row in listed["items"]] == [posted["id"]]

    fetched = client.get(f"/api/v1/inventory/documents/{posted['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["number"] == posted["number"]


def test_a_clerk_may_not_post_a_stock_document(client: TestClient, db: Session) -> None:
    company_id = _signup(client)
    payload = _adjustment_payload(client)
    clerk = _clerk(client, db, company_id)

    response = clerk.post(
        "/api/v1/inventory/adjustments",
        json=payload,
        headers={"Idempotency-Key": "adj-clerk"},
    )

    assert response.status_code == 403
