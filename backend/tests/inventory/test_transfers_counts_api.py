"""P5 step 4 — the transfer and count API.

Endpoint-level coverage for the screens step 7 will render: what the Warehouse-transfers
workspace posts for Transfer now / Dispatch / Receive, what the count screen gets back for
its sheet and its preview, which permission each route demands, and where a refusal's
`field_errors` land so the grid can show them in place. Every request goes through the real
cookie session on the RLS-enforcing role.
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
    """A Clerk holds neither `inv:transactions_adjust` nor `inv:count_process`."""
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


def _item(client: TestClient, code: str = "WINE-001") -> dict:
    categories = client.get("/api/v1/inventory/uom-categories").json()
    category = next(row for row in categories if row["code"] == "COUNT")
    return client.post(
        "/api/v1/inventory/items",
        json={
            "code": code,
            "name": "Rugari Red 750ml",
            "uom_category_id": category["id"],
            "base_uom_id": category["uoms"][0]["id"],
        },
    ).json()


def _warehouse(client: TestClient, code: str) -> dict:
    rows = client.get(
        "/api/v1/inventory/warehouses", params={"include_in_transit": True}
    ).json()
    return next(row for row in rows if row["code"] == code)


def _depot(client: TestClient) -> dict:
    branches = client.get("/api/v1/gl/branches").json()
    main_branch = next(row for row in branches if row["is_main"])
    response = client.post(
        "/api/v1/inventory/warehouses",
        json={"code": "DEPOT", "name": "Musanze Depot", "branch_id": main_branch["id"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _type_id(client: TestClient, code: str) -> int:
    rows = client.get("/api/v1/gl/transaction-types", params={"module": "inv"}).json()
    rows = rows["items"] if isinstance(rows, dict) else rows
    return next(row["id"] for row in rows if row["code"] == code)


def _stock_in(client: TestClient, item: dict, warehouse: dict, quantity: str = "20") -> None:
    response = client.post(
        "/api/v1/inventory/adjustments",
        json={
            "document_date": TODAY,
            "description": "opening",
            "lines": [
                {
                    "item_id": item["id"],
                    "warehouse_id": warehouse["id"],
                    "quantity": quantity,
                    "unit_cost": "100",
                    "transaction_type_id": _type_id(client, "ADJIN"),
                }
            ],
        },
        headers={"Idempotency-Key": f"in-{item['id']}-{warehouse['id']}-{quantity}"},
    )
    assert response.status_code == 201, response.text


def _transfer_payload(client: TestClient, item: dict, source: dict, destination: dict, **over):
    return {
        "transfer_date": TODAY,
        "description": "Main to Depot",
        "from_warehouse_id": source["id"],
        "to_warehouse_id": destination["id"],
        "lines": [{"item_id": item["id"], "quantity": "5"}],
        **over,
    }


# --- Transfers --------------------------------------------------------------------------------


def test_transfer_now_comes_back_completed_with_both_legs(client: TestClient) -> None:
    _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    depot = _depot(client)
    _stock_in(client, item, main)

    response = client.post(
        "/api/v1/inventory/transfers",
        json=_transfer_payload(client, item, main, depot),
        headers={"Idempotency-Key": "trf-1"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["number"].startswith("TRF-")
    assert body["dispatch_entry_id"] != body["receive_entry_id"]
    line = body["lines"][0]
    assert Decimal(line["quantity_base"]) == Decimal(5)
    assert all(
        line[column] is not None
        for column in (
            "dispatch_out_move_id",
            "dispatch_in_move_id",
            "receive_out_move_id",
            "receive_in_move_id",
        )
    )


def test_a_dispatch_waits_in_transit_until_received(client: TestClient) -> None:
    _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    depot = _depot(client)
    _stock_in(client, item, main)

    dispatched = client.post(
        "/api/v1/inventory/transfers",
        json=_transfer_payload(client, item, main, depot, receive_now=False),
        headers={"Idempotency-Key": "trf-2"},
    ).json()
    assert dispatched["status"] == "in_transit"
    assert dispatched["receive_entry_id"] is None

    received = client.post(
        f"/api/v1/inventory/transfers/{dispatched['id']}/receive",
        json={"receive_date": TODAY},
        headers={"Idempotency-Key": "trf-2-receive"},
    )

    assert received.status_code == 200, received.text
    assert received.json()["status"] == "completed"
    assert received.json()["received_date"] == TODAY


def test_receiving_twice_is_refused_with_a_usable_code(client: TestClient) -> None:
    _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    depot = _depot(client)
    _stock_in(client, item, main)
    transfer = client.post(
        "/api/v1/inventory/transfers",
        json=_transfer_payload(client, item, main, depot),
        headers={"Idempotency-Key": "trf-3"},
    ).json()

    again = client.post(
        f"/api/v1/inventory/transfers/{transfer['id']}/receive",
        json={},
        headers={"Idempotency-Key": "trf-3-receive"},
    )

    assert again.status_code == 409, again.text
    assert again.json()["code"] == "transfer_already_received"


def test_cancelling_an_unreceived_transfer_brings_the_stock_home(client: TestClient) -> None:
    _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    depot = _depot(client)
    _stock_in(client, item, main)
    transfer = client.post(
        "/api/v1/inventory/transfers",
        json=_transfer_payload(client, item, main, depot, receive_now=False),
        headers={"Idempotency-Key": "trf-4"},
    ).json()

    cancelled = client.post(
        f"/api/v1/inventory/transfers/{transfer['id']}/cancel",
        json={"reason": "loaded the wrong pallet"},
        headers={"Idempotency-Key": "trf-4-cancel"},
    )

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancellation_entry_id"] is not None


def test_insufficient_stock_lands_on_the_quantity_cell_of_the_line_that_failed(
    client: TestClient,
) -> None:
    """The error contract step 7's grid depends on: `insufficient_stock` on the *keyed* line's
    quantity.

    Two lines, and the second is the one with nothing to send — because a leg is two moves per
    line, so a one-line payload cannot tell a correct answer from an off-by-a-factor-of-two
    one. Here the wrong answer is `lines.2.quantity`, a row the grid does not have.
    """
    _signup(client)
    plenty = _item(client, "WINE-001")
    empty = _item(client, "WINE-375")
    main = _warehouse(client, "MAIN")
    depot = _depot(client)
    _stock_in(client, plenty, main)

    response = client.post(
        "/api/v1/inventory/transfers",
        json=_transfer_payload(
            client,
            plenty,
            main,
            depot,
            lines=[
                {"item_id": plenty["id"], "quantity": "5"},
                {"item_id": empty["id"], "quantity": "1"},
            ],
        ),
        headers={"Idempotency-Key": "trf-5"},
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "insufficient_stock"
    assert "lines.1.quantity" in body["field_errors"], body["field_errors"]
    assert client.get("/api/v1/inventory/transfers").json()["items"] == [], (
        "a refused transfer left a header behind"
    )


def test_the_in_transit_warehouse_is_refused_as_a_destination(client: TestClient) -> None:
    _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    transit = _warehouse(client, "TRANSIT")
    _stock_in(client, item, main)

    response = client.post(
        "/api/v1/inventory/transfers",
        json=_transfer_payload(client, item, main, transit),
        headers={"Idempotency-Key": "trf-6"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "in_transit_warehouse_not_selectable"


def test_replaying_the_transfer_key_returns_200_and_the_same_transfer(
    client: TestClient,
) -> None:
    _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    depot = _depot(client)
    _stock_in(client, item, main)
    payload = _transfer_payload(client, item, main, depot)

    first = client.post(
        "/api/v1/inventory/transfers", json=payload, headers={"Idempotency-Key": "trf-7"}
    )
    second = client.post(
        "/api/v1/inventory/transfers", json=payload, headers={"Idempotency-Key": "trf-7"}
    )

    assert first.status_code == 201
    assert second.status_code == 200, "a replay is not a second transfer"
    assert second.json()["id"] == first.json()["id"]


def test_the_listing_matches_either_end_of_a_transfer(client: TestClient) -> None:
    _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    depot = _depot(client)
    _stock_in(client, item, main)
    client.post(
        "/api/v1/inventory/transfers",
        json=_transfer_payload(client, item, main, depot, receive_now=False),
        headers={"Idempotency-Key": "trf-8"},
    )

    for warehouse in (main, depot):
        listing = client.get(
            "/api/v1/inventory/transfers", params={"warehouse_id": warehouse["id"]}
        ).json()
        assert len(listing["items"]) == 1, warehouse["code"]
    assert (
        client.get("/api/v1/inventory/transfers", params={"status": "completed"})
        .json()["items"]
        == []
    )


def test_a_clerk_cannot_post_a_transfer(client: TestClient, db: Session) -> None:
    company_id = _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    depot = _depot(client)
    clerk = _clerk(client, db, company_id)

    response = clerk.post(
        "/api/v1/inventory/transfers",
        json=_transfer_payload(client, item, main, depot),
        headers={"Idempotency-Key": "trf-9"},
    )

    assert response.status_code == 403, response.text


# --- Counts -----------------------------------------------------------------------------------


def _open_count(client: TestClient, warehouse: dict, **over) -> dict:
    response = client.post(
        "/api/v1/inventory/counts",
        json={
            "warehouse_id": warehouse["id"],
            "count_date": TODAY,
            "description": "January stock take",
            **over,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_session_comes_back_with_a_frozen_sheet(client: TestClient) -> None:
    _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    _stock_in(client, item, main)

    session = _open_count(client, main)

    assert session["status"] == "counting"
    assert session["number"].startswith("CNS-")
    assert len(session["lines"]) == 1
    line = session["lines"][0]
    assert Decimal(line["system_quantity"]) == Decimal(20)
    assert line["counted_quantity"] is None
    assert line["variance"] is None
    assert line["stale"] is False


def test_entering_a_count_returns_the_variance(client: TestClient) -> None:
    _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    _stock_in(client, item, main)
    session = _open_count(client, main)
    line = session["lines"][0]

    response = client.patch(
        f"/api/v1/inventory/counts/{session['id']}/lines/{line['id']}",
        json={"counted_quantity": "18", "note": "two bottles broken"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["counted_quantity"]) == Decimal(18)
    assert Decimal(body["variance"]) == Decimal(-2)
    assert body["note"] == "two bottles broken"


def test_the_preview_shows_the_posting_and_process_posts_it(client: TestClient) -> None:
    _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    _stock_in(client, item, main)
    session = _open_count(client, main)
    line = session["lines"][0]
    client.patch(
        f"/api/v1/inventory/counts/{session['id']}/lines/{line['id']}",
        json={"counted_quantity": "18"},
    )

    preview = client.get(f"/api/v1/inventory/counts/{session['id']}/preview").json()

    assert preview["can_process"] is True
    assert preview["variance_lines"] == 1
    assert Decimal(preview["total_value"]) == Decimal(-200)
    assert preview["lines"][0]["item_code"] == item["code"]
    assert Decimal(preview["lines"][0]["unit_cost"]) == Decimal(100)

    processed = client.post(
        f"/api/v1/inventory/counts/{session['id']}/process",
        headers={"Idempotency-Key": "cnt-1"},
    )

    assert processed.status_code == 200, processed.text
    body = processed.json()
    assert body["session"]["status"] == "completed"
    assert body["document"]["doc_type"] == "INCT"
    assert body["document"]["number"].startswith("CNT-")
    assert Decimal(body["document"]["lines"][0]["quantity"]) == Decimal(-2)


def test_a_stale_line_blocks_process_until_it_is_resnapshotted(client: TestClient) -> None:
    """`count_line_stale` on the line, which is where step 7 puts the message."""
    _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    _stock_in(client, item, main)
    session = _open_count(client, main)
    line = session["lines"][0]
    client.patch(
        f"/api/v1/inventory/counts/{session['id']}/lines/{line['id']}",
        json={"counted_quantity": "18"},
    )
    _stock_in(client, item, main, quantity="5")

    preview = client.get(f"/api/v1/inventory/counts/{session['id']}/preview").json()
    assert preview["can_process"] is False
    assert preview["stale_lines"] == [line["id"]]

    refused = client.post(
        f"/api/v1/inventory/counts/{session['id']}/process",
        headers={"Idempotency-Key": "cnt-2"},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "count_line_stale"
    assert f"lines.{line['id']}" in refused.json()["field_errors"]

    refreshed = client.post(
        f"/api/v1/inventory/counts/{session['id']}/lines/{line['id']}/resnapshot"
    ).json()
    assert refreshed["stale"] is False
    assert Decimal(refreshed["system_quantity"]) == Decimal(25)
    assert refreshed["counted_quantity"] is None

    client.patch(
        f"/api/v1/inventory/counts/{session['id']}/lines/{line['id']}",
        json={"counted_quantity": "24"},
    )
    processed = client.post(
        f"/api/v1/inventory/counts/{session['id']}/process",
        headers={"Idempotency-Key": "cnt-3"},
    )
    assert processed.status_code == 200, processed.text
    assert Decimal(processed.json()["document"]["lines"][0]["quantity"]) == Decimal(-1)


def test_an_item_the_warehouse_does_not_hold_can_be_added_to_the_sheet(
    client: TestClient,
) -> None:
    _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    session = _open_count(client, main)
    assert session["lines"] == []

    line = client.post(
        f"/api/v1/inventory/counts/{session['id']}/lines", json={"item_id": item["id"]}
    )

    assert line.status_code == 201, line.text
    assert Decimal(line.json()["system_quantity"]) == Decimal(0)


def test_a_count_that_agrees_with_the_books_posts_no_document(client: TestClient) -> None:
    _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    _stock_in(client, item, main)
    session = _open_count(client, main)
    client.patch(
        f"/api/v1/inventory/counts/{session['id']}/lines/{session['lines'][0]['id']}",
        json={"counted_quantity": "20"},
    )

    processed = client.post(
        f"/api/v1/inventory/counts/{session['id']}/process",
        headers={"Idempotency-Key": "cnt-4"},
    )

    assert processed.status_code == 200, processed.text
    assert processed.json()["document"] is None
    assert processed.json()["session"]["status"] == "completed"


def test_a_cancelled_session_keeps_its_sheet(client: TestClient) -> None:
    _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    _stock_in(client, item, main)
    session = _open_count(client, main)

    cancelled = client.post(
        f"/api/v1/inventory/counts/{session['id']}/cancel",
        json={"reason": "counted the wrong aisle"},
    )

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert len(cancelled.json()["lines"]) == 1


def test_a_clerk_can_neither_open_nor_process_a_count(
    client: TestClient, db: Session
) -> None:
    company_id = _signup(client)
    item = _item(client)
    main = _warehouse(client, "MAIN")
    _stock_in(client, item, main)
    session = _open_count(client, main)
    clerk = _clerk(client, db, company_id)

    opened = clerk.post(
        "/api/v1/inventory/counts",
        json={
            "warehouse_id": main["id"],
            "count_date": TODAY,
            "description": "sneaky count",
        },
    )
    processed = clerk.post(
        f"/api/v1/inventory/counts/{session['id']}/process",
        headers={"Idempotency-Key": "cnt-5"},
    )

    assert opened.status_code == 403, opened.text
    assert processed.status_code == 403, processed.text
    assert item["id"]
