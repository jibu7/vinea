"""P5 step 5 — the enquiry and report API.

Endpoint coverage for the screens step 8 will render: what each route returns with data behind
it, which permission it demands, and that the valuation figure the screen will print is the
same figure the GL report prints. Every request goes through the real cookie session on the
RLS-enforcing role.
"""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import permissions
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
TODAY = date.today().replace(month=1, day=15)
DAY = TODAY.isoformat()


def _signup(client: TestClient) -> int:
    return client.post("/api/v1/auth/signup", json=OWNER).json()["company_id"]


def _invited(
    client: TestClient, db: Session, company_id: int, *, role_name: str, email: str
) -> TestClient:
    set_tenant(db, company_id)
    role_id = db.scalars(
        select(Role.id).where(Role.company_id == company_id, Role.name == role_name)
    ).one()
    client.post("/api/v1/invitations", json={"email": email, "role_ids": [role_id]})
    token = email_service.outbox[-1].context["token"]
    invited = TestClient(client.app)
    invited.post(
        "/api/v1/invitations/accept",
        json={"token": token, "full_name": "Invited Person", "password": CLERK_PASSWORD},
    )
    return invited


def _grant(db: Session, company_id: int, role_name: str, permission: str) -> None:
    set_tenant(db, company_id)
    role = db.scalars(
        select(Role).where(Role.company_id == company_id, Role.name == role_name)
    ).one()
    role.permissions = [*role.permissions, permission]
    db.commit()


def _type_id(client: TestClient, code: str) -> int:
    rows = client.get("/api/v1/gl/transaction-types", params={"module": "inv"}).json()
    rows = rows["items"] if isinstance(rows, dict) else rows
    return next(row["id"] for row in rows if row["code"] == code)


def _warehouse(client: TestClient, code: str) -> dict:
    rows = client.get("/api/v1/inventory/warehouses").json()
    return next(row for row in rows if row["code"] == code)


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


def _adjust(client: TestClient, item, warehouse, *, quantity, type_code, unit_cost=None) -> dict:
    line = {
        "item_id": item["id"],
        "warehouse_id": warehouse["id"],
        "quantity": quantity,
        "transaction_type_id": _type_id(client, type_code),
    }
    if unit_cost is not None:
        line["unit_cost"] = unit_cost
    response = client.post(
        "/api/v1/inventory/adjustments",
        json={"document_date": DAY, "description": "stock movement", "lines": [line]},
        headers={"Idempotency-Key": f"adj-{type_code}-{quantity}-{warehouse['id']}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _stocked(client: TestClient) -> tuple[dict, dict]:
    """Ten units in at 100, two out — enough for every report to have rows in it."""
    item = _item(client)
    main = _warehouse(client, "MAIN")
    _adjust(client, item, main, quantity="10", unit_cost="100", type_code="ADJIN")
    _adjust(client, item, main, quantity="2", type_code="ADJOUT")
    return item, main


# --- Item enquiry ----------------------------------------------------------------------------


def test_the_enquiry_returns_the_position_and_the_moves_behind_it(
    client: TestClient,
) -> None:
    _signup(client)
    item, main = _stocked(client)

    response = client.get(
        f"/api/v1/inventory/items/{item['id']}/enquiry", params={"as_of": DAY}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["item_code"] == "WINE-001"
    assert Decimal(body["total_quantity"]) == Decimal(8)
    assert Decimal(body["total_value"]) == Decimal(800)
    assert Decimal(body["average_cost"]) == Decimal(100)
    assert [row["warehouse_code"] for row in body["locations"]] == ["MAIN"]
    assert Decimal(body["locations"][0]["quantity"]) == Decimal(8)
    assert len(body["moves"]) == 2
    assert Decimal(body["moves"][-1]["running_quantity"]) == Decimal(8)
    assert Decimal(body["moves"][-1]["running_value"]) == Decimal(800)
    assert body["moves"][0]["entry_number"], "the drill-down to the journal entry is the link"
    assert body["moves"][0]["transaction_type_code"] == "ADJIN"


def test_the_enquiry_pages_and_filters(client: TestClient) -> None:
    _signup(client)
    item, main = _stocked(client)

    first = client.get(
        f"/api/v1/inventory/items/{item['id']}/enquiry",
        params={"as_of": DAY, "limit": 1},
    ).json()
    assert len(first["moves"]) == 1
    assert first["next_cursor"] is not None

    second = client.get(
        f"/api/v1/inventory/items/{item['id']}/enquiry",
        params={"as_of": DAY, "limit": 1, "cursor": first["next_cursor"]},
    ).json()
    assert second["moves"][0]["move_id"] != first["moves"][0]["move_id"]
    assert second["next_cursor"] is None

    provisional = client.get(
        f"/api/v1/inventory/items/{item['id']}/enquiry",
        params={"as_of": DAY, "provisional_only": "true"},
    ).json()
    assert provisional["moves"] == [], "nothing here was costed against thin air"


def test_the_enquiry_needs_an_inventory_permission(
    client: TestClient, db: Session
) -> None:
    company_id = _signup(client)
    item, _ = _stocked(client)
    clerk = _invited(client, db, company_id, role_name="Clerk", email="clerk@rugari.example")

    assert clerk.get(f"/api/v1/inventory/items/{item['id']}/enquiry").status_code == 403


# --- Reports ---------------------------------------------------------------------------------


def test_the_valuation_report_returns_rows_totals_and_its_tie_to_the_gl(
    client: TestClient,
) -> None:
    _signup(client)
    item, main = _stocked(client)

    response = client.get("/api/v1/inventory/reports/valuation", params={"as_of": DAY})

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body["rows"]] == ["WINE-001"]
    assert Decimal(body["rows"][0]["quantity"]) == Decimal(8)
    assert Decimal(body["rows"][0]["value"]) == Decimal(800)
    assert Decimal(body["total_value"]) == Decimal(800)
    assert len(body["account_totals"]) == 1
    assert Decimal(body["account_totals"][0]["value"]) == Decimal(800)
    assert body["account_totals"][0]["code"], "the report names the account it ties to"


def test_the_valuation_total_is_the_inventory_account_in_the_gl_report(
    client: TestClient,
) -> None:
    """Rule 13, as a test rather than as a promise: the figure the valuation screen prints is
    read back out of the GL's own report, not out of the same query twice."""
    _signup(client)
    item, main = _stocked(client)

    valuation = client.get(
        "/api/v1/inventory/reports/valuation", params={"as_of": DAY}
    ).json()
    account_code = valuation["account_totals"][0]["code"]
    trial_balance = client.get("/api/v1/gl/trial-balance", params={"as_of": DAY}).json()
    row = next(row for row in trial_balance["rows"] if row["code"] == account_code)

    assert Decimal(row["debit"]) - Decimal(row["credit"]) == Decimal(
        valuation["account_totals"][0]["value"]
    )
    assert Decimal(valuation["total_value"]) == Decimal(800)


def test_the_valuation_report_opens_for_a_finance_reader_without_inventory_rights(
    client: TestClient, db: Session
) -> None:
    """`reporting:inventory_valuation_view` has existed unused since P1; this is the report
    it was named for. It opens the valuation report and nothing else."""
    company_id = _signup(client)
    _stocked(client)
    _grant(db, company_id, "Clerk", permissions.REPORTING_INVENTORY_VALUATION_VIEW)
    reader = _invited(client, db, company_id, role_name="Clerk", email="cfo@rugari.example")

    assert reader.get(
        "/api/v1/inventory/reports/valuation", params={"as_of": DAY}
    ).status_code == 200
    assert reader.get(
        "/api/v1/inventory/reports/movement",
        params={"date_from": DAY, "date_to": DAY},
    ).status_code == 403


def test_the_movement_report_returns_opening_in_out_and_closing(client: TestClient) -> None:
    _signup(client)
    _stocked(client)

    body = client.get(
        "/api/v1/inventory/reports/movement",
        params={"date_from": DAY, "date_to": DAY},
    ).json()

    row = body["rows"][0]
    assert Decimal(row["opening_quantity"]) == Decimal(0)
    assert Decimal(row["quantity_in"]) == Decimal(10)
    assert Decimal(row["quantity_out"]) == Decimal(-2)
    assert Decimal(row["closing_quantity"]) == Decimal(8)
    assert Decimal(row["closing_value"]) == Decimal(800)
    assert Decimal(body["closing_value"]) == Decimal(800)


def test_the_transaction_report_returns_the_moves_with_their_entries(
    client: TestClient,
) -> None:
    _signup(client)
    _stocked(client)

    body = client.get(
        "/api/v1/inventory/reports/transactions",
        params={"date_from": DAY, "date_to": DAY},
    ).json()

    assert body["move_count"] == 2
    assert Decimal(body["total_quantity"]) == Decimal(8)
    assert all(row["entry_number"] for row in body["rows"])
    assert all(row["journal_entry_id"] for row in body["rows"])


def test_the_count_report_returns_sessions_with_their_variances(client: TestClient) -> None:
    _signup(client)
    item, main = _stocked(client)
    session = client.post(
        "/api/v1/inventory/counts",
        json={
            "warehouse_id": main["id"],
            "count_date": DAY,
            "description": "January count",
        },
    ).json()
    line = session["lines"][0]
    client.patch(
        f"/api/v1/inventory/counts/{session['id']}/lines/{line['id']}",
        json={"counted_quantity": "7"},
    )

    body = client.get("/api/v1/inventory/reports/counts").json()

    row = body["rows"][0]
    assert row["number"] == session["number"]
    assert row["warehouse_code"] == "MAIN"
    assert row["variance_count"] == 1
    assert Decimal(row["lines"][0]["variance"]) == Decimal(-1)
    assert row["lines"][0]["stale"] is False
    assert row["document_id"] is None

    processed = client.post(
        f"/api/v1/inventory/counts/{session['id']}/process",
        json={"session_id": session["id"]},
        headers={"Idempotency-Key": "count-1"},
    )
    assert processed.status_code == 200, processed.text

    after = client.get("/api/v1/inventory/reports/counts").json()
    assert after["rows"][0]["document_number"], "a processed count links to what it posted"
    assert after["rows"][0]["journal_entry_id"]


def test_every_report_needs_the_inventory_reports_permission(
    client: TestClient, db: Session
) -> None:
    company_id = _signup(client)
    _stocked(client)
    clerk = _invited(client, db, company_id, role_name="Clerk", email="clerk@rugari.example")

    for path, params in (
        ("/api/v1/inventory/reports/movement", {"date_from": DAY, "date_to": DAY}),
        ("/api/v1/inventory/reports/transactions", {"date_from": DAY, "date_to": DAY}),
        ("/api/v1/inventory/reports/valuation", {"as_of": DAY}),
        ("/api/v1/inventory/reports/counts", {}),
    ):
        assert clerk.get(path, params=params).status_code == 403, path
