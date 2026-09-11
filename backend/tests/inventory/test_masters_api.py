"""P5 step 1 — the inventory masters API.

Endpoint-level coverage for the screens P5 step 6 will render: what the pickers read, what
the Maintenance forms write, and which permission each route actually demands. Every request
goes through the real cookie session on the RLS-enforcing role, so a route that forgot its
tenant filter returns nothing rather than someone else's catalogue.
"""

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


def _signup(client: TestClient) -> int:
    return client.post("/api/v1/auth/signup", json=OWNER).json()["company_id"]


def _clerk(client: TestClient, db: Session, company_id: int) -> TestClient:
    """A Clerk holds no inventory permission at all — the negative case for every route."""
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


def _count_category(client: TestClient) -> dict:
    categories = client.get("/api/v1/inventory/uom-categories").json()
    return next(row for row in categories if row["code"] == "COUNT")


def _create_item(client: TestClient, **overrides) -> dict:
    category = _count_category(client)
    payload = {
        "code": "WINE-001",
        "name": "Rugari Red 750ml",
        "uom_category_id": category["id"],
        "base_uom_id": category["uoms"][0]["id"],
        "selling_price": "8500",
        **overrides,
    }
    return client.post("/api/v1/inventory/items", json=payload).json()


# --- Units of measure ---------------------------------------------------------------------


def test_the_seeded_categories_come_back_with_their_base_units(client: TestClient) -> None:
    _signup(client)

    response = client.get("/api/v1/inventory/uom-categories")

    assert response.status_code == 200
    rows = {row["code"]: row for row in response.json()}
    assert set(rows) == {"COUNT", "WEIGHT", "VOLUME", "LENGTH"}
    assert [unit["code"] for unit in rows["COUNT"]["uoms"]] == ["EA"]
    assert rows["COUNT"]["uoms"][0]["is_base"] is True


def test_a_category_and_a_unit_can_be_added(client: TestClient) -> None:
    _signup(client)

    created = client.post(
        "/api/v1/inventory/uom-categories",
        json={
            "code": "AREA",
            "name": "Area",
            "base_uom_code": "M2",
            "base_uom_name": "Square metre",
            "base_uom_decimal_places": 2,
        },
    )
    assert created.status_code == 201
    category_id = created.json()["id"]

    unit = client.post(
        "/api/v1/inventory/uoms",
        json={
            "category_id": category_id,
            "code": "HA",
            "name": "Hectare",
            "factor_to_base": "10000",
            "decimal_places": 4,
        },
    )
    assert unit.status_code == 201
    assert unit.json()["is_base"] is False

    units = client.get(f"/api/v1/inventory/uoms?category_id={category_id}").json()
    assert {row["code"] for row in units} == {"M2", "HA"}


def test_a_clerk_cannot_read_or_write_inventory_masters(
    client: TestClient, db: Session
) -> None:
    company_id = _signup(client)
    clerk = _clerk(client, db, company_id)

    assert clerk.get("/api/v1/inventory/uom-categories").status_code == 403
    assert (
        clerk.post(
            "/api/v1/inventory/warehouses",
            json={"code": "X", "name": "X", "branch_id": 1},
        ).status_code
        == 403
    )


# --- Items --------------------------------------------------------------------------------


def test_an_item_is_created_and_listed(client: TestClient) -> None:
    _signup(client)

    item = _create_item(client)

    assert item["code"] == "WINE-001"
    assert item["item_type"] == "stock"
    assert item["selling_price"] == "8500"

    listing = client.get("/api/v1/inventory/items").json()
    assert [row["code"] for row in listing] == ["WINE-001"]


def test_an_item_search_matches_code_name_and_barcode(client: TestClient) -> None:
    _signup(client)
    item = _create_item(client)
    category = _count_category(client)
    client.post(
        f"/api/v1/inventory/items/{item['id']}/barcodes",
        json={"barcode": "5901234123457", "uom_id": category["uoms"][0]["id"]},
    )

    for term in ("WINE", "Rugari", "590123"):
        rows = client.get(f"/api/v1/inventory/items?search={term}").json()
        assert [row["code"] for row in rows] == ["WINE-001"], term

    assert client.get("/api/v1/inventory/items?search=nothing").json() == []


def test_lookup_resolves_a_code_and_a_barcode_to_their_units(client: TestClient) -> None:
    _signup(client)
    item = _create_item(client)
    category = _count_category(client)
    case = client.post(
        "/api/v1/inventory/uoms",
        json={
            "category_id": category["id"],
            "code": "CASE6",
            "name": "Case of 6",
            "factor_to_base": "6",
        },
    ).json()
    client.post(
        f"/api/v1/inventory/items/{item['id']}/barcodes",
        json={"barcode": "5901234123457", "uom_id": case["id"], "pack_quantity": "1"},
    )

    by_code = client.get("/api/v1/inventory/items/lookup?q=WINE-001").json()
    assert by_code["item"]["id"] == item["id"]
    assert by_code["uom"]["code"] == "EA"
    assert by_code["matched_barcode"] is None

    by_barcode = client.get("/api/v1/inventory/items/lookup?q=5901234123457").json()
    assert by_barcode["item"]["id"] == item["id"]
    assert by_barcode["uom"]["code"] == "CASE6"
    assert by_barcode["uom"]["factor_to_base"] == "6.0000000000"
    assert by_barcode["matched_barcode"] == "5901234123457"


def test_lookup_of_an_unknown_term_is_a_404(client: TestClient) -> None:
    _signup(client)
    response = client.get("/api/v1/inventory/items/lookup?q=NOPE")
    assert response.status_code == 404


def test_a_duplicate_item_code_is_a_409_with_a_field_error(client: TestClient) -> None:
    _signup(client)
    _create_item(client)
    category = _count_category(client)

    response = client.post(
        "/api/v1/inventory/items",
        json={
            "code": "WINE-001",
            "name": "Another",
            "uom_category_id": category["id"],
            "base_uom_id": category["uoms"][0]["id"],
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "item_code_taken"
    assert response.json()["field_errors"] == {"code": ["already in use"]}


def test_renaming_an_item_shows_up_in_its_history(client: TestClient) -> None:
    _signup(client)
    item = _create_item(client)

    renamed = client.patch(f"/api/v1/inventory/items/{item['id']}", json={"code": "WINE-100"})
    assert renamed.status_code == 200
    assert renamed.json()["code"] == "WINE-100"

    history = client.get(f"/api/v1/inventory/items/{item['id']}/history").json()
    assert history[0]["action"] == "item.renamed"
    assert history[0]["before"]["code"] == "WINE-001"
    assert history[0]["after"]["code"] == "WINE-100"
    assert history[0]["actor_email"] == OWNER["email"]


def test_an_item_patch_that_omits_a_field_does_not_blank_it(client: TestClient) -> None:
    """The `...` / `None` convention: a PATCH carrying only a name must not clear the
    accounts the item already had."""
    _signup(client)
    accounts = client.get("/api/v1/gl/accounts").json()
    sales = next(row for row in accounts if row["code"] == "4100")
    item = _create_item(client, sales_account_id=sales["id"])
    assert item["sales_account_id"] == sales["id"]

    patched = client.patch(
        f"/api/v1/inventory/items/{item['id']}", json={"name": "Rugari Red, 750ml"}
    ).json()

    assert patched["sales_account_id"] == sales["id"]
    assert patched["name"] == "Rugari Red, 750ml"

    cleared = client.patch(
        f"/api/v1/inventory/items/{item['id']}", json={"clear_sales_account": True}
    ).json()
    assert cleared["sales_account_id"] is None


# --- Warehouses ---------------------------------------------------------------------------


def test_warehouses_list_without_the_in_transit_location(client: TestClient) -> None:
    _signup(client)

    default_view = client.get("/api/v1/inventory/warehouses").json()
    assert [row["code"] for row in default_view] == ["MAIN"]
    assert default_view[0]["is_default"] is True

    full_view = client.get("/api/v1/inventory/warehouses?include_in_transit=true").json()
    transit = next(row for row in full_view if row["code"] == "TRANSIT")
    assert transit["is_in_transit"] is True


def test_a_second_warehouse_can_be_added_and_made_default(client: TestClient) -> None:
    _signup(client)
    branch_id = client.get("/api/v1/gl/branches").json()[0]["id"]

    depot = client.post(
        "/api/v1/inventory/warehouses",
        json={"code": "DEPOT", "name": "Musanze Depot", "branch_id": branch_id},
    )
    assert depot.status_code == 201

    client.patch(f"/api/v1/inventory/warehouses/{depot.json()['id']}", json={"is_default": True})

    rows = {row["code"]: row for row in client.get("/api/v1/inventory/warehouses").json()}
    assert rows["DEPOT"]["is_default"] is True
    assert rows["MAIN"]["is_default"] is False


def test_the_in_transit_warehouse_cannot_be_deactivated_through_the_api(
    client: TestClient,
) -> None:
    _signup(client)
    transit = next(
        row
        for row in client.get("/api/v1/inventory/warehouses?include_in_transit=true").json()
        if row["is_in_transit"]
    )

    response = client.patch(
        f"/api/v1/inventory/warehouses/{transit['id']}", json={"is_active": False}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "in_transit_warehouse_locked"


# --- Transaction types and defaults -------------------------------------------------------


def test_the_seeded_inventory_transaction_types_each_carry_a_kind(client: TestClient) -> None:
    _signup(client)

    rows = client.get("/api/v1/gl/transaction-types?module=inv").json()

    by_code = {row["code"]: row for row in rows}
    assert set(by_code) == {"ADJIN", "ADJOUT", "REVAL", "TRF", "CNTV", "OPEN"}
    assert by_code["ADJIN"]["kind"] == "adjustment_in"
    assert by_code["TRF"]["kind"] == "transfer"
    assert by_code["OPEN"]["kind"] == "opening_balance"
    assert all(row["default_gl_account_id"] is not None for row in rows)


def test_a_user_defined_inventory_type_needs_a_kind(client: TestClient) -> None:
    """A type without a kind would be a document nothing could action."""
    _signup(client)
    accounts = client.get("/api/v1/gl/accounts").json()
    adjustments = next(row for row in accounts if row["code"] == "5200")

    refused = client.post(
        "/api/v1/gl/transaction-types",
        json={
            "module": "inv",
            "code": "DAMAGED",
            "name": "Damaged stock",
            "default_gl_account_id": adjustments["id"],
        },
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "transaction_type_kind_required"

    created = client.post(
        "/api/v1/gl/transaction-types",
        json={
            "module": "inv",
            "code": "DAMAGED",
            "name": "Damaged stock",
            "kind": "adjustment_out",
            "default_gl_account_id": adjustments["id"],
        },
    )
    assert created.status_code == 201
    assert created.json()["kind"] == "adjustment_out"


def test_a_gl_transaction_type_may_not_carry_a_kind(client: TestClient) -> None:
    _signup(client)

    response = client.post(
        "/api/v1/gl/transaction-types",
        json={"module": "gl", "code": "WEIRD", "name": "Weird", "kind": "transfer"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "transaction_type_kind_not_allowed"


def test_the_inventory_defaults_read_back_seeded_and_update(client: TestClient) -> None:
    _signup(client)
    accounts = {row["code"]: row["id"] for row in client.get("/api/v1/gl/accounts").json()}

    defaults = client.get("/api/v1/inventory/defaults").json()
    assert defaults["inventory_account_id"] == accounts["1300"]
    assert defaults["inventory_in_transit_account_id"] == accounts["1350"]
    assert defaults["cogs_account_id"] == accounts["5100"]
    assert defaults["negative_stock_policy"] == "block"
    assert defaults["default_warehouse_id"] is not None

    updated = client.patch(
        "/api/v1/inventory/defaults", json={"negative_stock_policy": "allow"}
    ).json()
    assert updated["negative_stock_policy"] == "allow"
    # The accounts are untouched by a policy-only patch.
    assert updated["inventory_account_id"] == accounts["1300"]


def test_pointing_the_inventory_default_at_an_ordinary_account_is_refused(
    client: TestClient,
) -> None:
    _signup(client)
    accounts = {row["code"]: row["id"] for row in client.get("/api/v1/gl/accounts").json()}

    response = client.patch(
        "/api/v1/inventory/defaults", json={"inventory_account_id": accounts["6990"]}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_inventory_account"
    assert response.json()["field_errors"] == {
        "inventory_account_id": ["not an inventory control account"]
    }
