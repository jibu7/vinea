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
from tests.inventory.invariants import assert_stock_invariants

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


# --- Quantity on hand for the typeahead (P5 step 7) --------------------------------------------


def test_on_hand_reads_what_the_warehouse_holds_after_a_posting(client: TestClient) -> None:
    """The figure the item typeahead shows beside each option — from the cache the step-2
    checker proves, after a real posting rather than a seeded row."""
    _signup(client)
    payload = _adjustment_payload(client)
    warehouse_id = payload["lines"][0]["warehouse_id"]
    item_id = payload["lines"][0]["item_id"]

    assert client.get(f"/api/v1/inventory/on-hand?warehouse_id={warehouse_id}").json() == []

    client.post("/api/v1/inventory/adjustments", json=payload, headers={"Idempotency-Key": "oh-1"})

    rows = client.get(f"/api/v1/inventory/on-hand?warehouse_id={warehouse_id}").json()
    assert [row["item_id"] for row in rows] == [item_id]
    assert Decimal(rows[0]["quantity"]) == Decimal(10)
    assert Decimal(rows[0]["value"]) == Decimal(1000)
    assert rows[0]["warehouse_id"] == warehouse_id


def test_on_hand_is_per_warehouse(client: TestClient) -> None:
    _signup(client)
    payload = _adjustment_payload(client)
    client.post("/api/v1/inventory/adjustments", json=payload, headers={"Idempotency-Key": "oh-2"})
    other = client.post(
        "/api/v1/inventory/warehouses",
        json={"code": "DEPOT", "name": "Depot", "branch_id": 1},
    ).json()

    assert client.get(f"/api/v1/inventory/on-hand?warehouse_id={other['id']}").json() == []


def test_on_hand_of_an_unknown_warehouse_is_a_404(client: TestClient) -> None:
    _signup(client)
    assert client.get("/api/v1/inventory/on-hand?warehouse_id=999999").status_code == 404


def test_on_hand_needs_an_inventory_permission(client: TestClient, db: Session) -> None:
    company_id = _signup(client)
    warehouse_id = _main_warehouse(client)["id"]
    clerk = _clerk(client, db, company_id)
    assert clerk.get(f"/api/v1/inventory/on-hand?warehouse_id={warehouse_id}").status_code == 403


# --- Reversal belongs to the module that posted ------------------------------------------------


def test_the_gl_reversal_endpoint_refuses_an_inventory_entry(
    client: TestClient, db: Session
) -> None:
    """The phase invariant, defended at the one door that could walk through it.

    `ReversalRequested` skips the control-account guard by design — it mirrors an entry that
    was legitimately posted — and inherits the original's module, so
    `POST /gl/journal-entries/{id}/reverse` could write the reversing side of an INV account.
    What it could not do is write the reversing *moves*: the inventory account moved and the
    stock did not, and `assert_stock_invariants` fails with "INV line N has no stock move
    behind it". The phase invariant, broken from a button — the adjustment screen navigates to
    `/gl/entries/{id}` after posting, and that screen offers Reverse.
    """
    company_id = _signup(client)
    posted = client.post(
        "/api/v1/inventory/adjustments",
        json=_adjustment_payload(client),
        headers={"Idempotency-Key": "adj-for-gl-reversal"},
    ).json()
    entry_id = posted["journal_entry_id"]
    assert entry_id is not None

    refused = client.post(
        f"/api/v1/gl/journal-entries/{entry_id}/reverse",
        headers={"Idempotency-Key": "gl-rev-inv"},
        json={"entry_date": TODAY, "reason": "wrong module"},
    )

    # 409, the kernel's code for "this state does not permit that" — not 422, which would
    # say the request was malformed. The request is well-formed and the answer is still no.
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["code"] == "module_owned_entry", body
    # It names where the reversal does belong, rather than only saying no.
    assert "/inventory/documents/" in body["message"], body

    # Nothing moved: the stock side and the ledger side still agree.
    set_tenant(db, company_id)
    assert_stock_invariants(db, company_id)

    # And the module's own path still works, which is the point of the refusal.
    undone = client.post(
        f"/api/v1/inventory/documents/{posted['id']}/reverse",
        headers={"Idempotency-Key": "inv-rev-ok"},
        json={"reversal_date": TODAY, "reason": "keyed twice"},
    )
    assert undone.status_code == 201, undone.text
    set_tenant(db, company_id)
    assert_stock_invariants(db, company_id)


def test_a_manual_journal_is_still_reversible_through_the_ledger(client: TestClient) -> None:
    """Anti-overreach: the guard must refuse module-owned entries and nothing else.

    A refusal that caught every entry would break the kernel's own correction path, which is
    the only way a manual journal is ever undone (ADR-04: corrections are never edits).
    """
    _signup(client)
    accounts = client.get("/api/v1/gl/accounts").json()
    postable = [a for a in accounts if a["is_postable"] and not a["is_control"]]
    entry = client.post(
        "/api/v1/gl/journal-entries",
        headers={"Idempotency-Key": "manual-1"},
        json={
            "entry_date": TODAY,
            "description": "a manual journal",
            "lines": [
                {"gl_account_id": postable[0]["id"], "debit": "500", "description": "d"},
                {"gl_account_id": postable[1]["id"], "credit": "500", "description": "c"},
            ],
        },
    )
    assert entry.status_code == 201, entry.text
    assert entry.json()["module"] == "gl"

    undone = client.post(
        f"/api/v1/gl/journal-entries/{entry.json()['id']}/reverse",
        headers={"Idempotency-Key": "manual-rev"},
        json={"entry_date": TODAY, "reason": "keyed twice"},
    )
    assert undone.status_code == 201, undone.text
