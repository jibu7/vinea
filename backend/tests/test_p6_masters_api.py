"""P6 step 1 — the order-entry settings and the kit definition, through the HTTP layer.

Nothing here posts. What is asserted is that the vocabulary the rest of the phase keys
against arrives correctly seeded, refuses the shapes it must refuse, and is reachable only by
someone holding the right permission.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

OWNER = {
    "email": "owner@rugari.example",
    "password": "correct horse battery staple",
    "full_name": "Rugari Owner",
    "company_name": "Rugari Wines Ltd",
}


def _signup(client: TestClient) -> int:
    return client.post("/api/v1/auth/signup", json=OWNER).json()["company_id"]


def _accounts(client: TestClient) -> dict[str, int]:
    return {row["code"]: row["id"] for row in client.get("/api/v1/gl/accounts").json()}


def _count_category(client: TestClient) -> dict:
    categories = client.get("/api/v1/inventory/uom-categories").json()
    return next(row for row in categories if row["code"] == "COUNT")


def _item(client: TestClient, code: str, **overrides) -> dict:
    category = _count_category(client)
    payload = {
        "code": code,
        "name": f"Item {code}",
        "uom_category_id": category["id"],
        "base_uom_id": category["uoms"][0]["id"],
        **overrides,
    }
    response = client.post("/api/v1/inventory/items", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --- Order defaults ---------------------------------------------------------------------------


def test_the_order_defaults_read_back_seeded(client: TestClient) -> None:
    """The Rwanda pack seeds all three accounts, so a new tenant can receive goods and match
    an invoice without anybody visiting a settings screen first."""
    _signup(client)
    accounts = _accounts(client)

    defaults = client.get("/api/v1/oe/defaults").json()

    assert defaults["grn_accrual_account_id"] == accounts["2350"]
    assert defaults["purchase_price_variance_account_id"] == accounts["5300"]
    assert defaults["landed_cost_clearing_account_id"] == accounts["1370"]
    # Allow by default (decision 7): a sales order may promise what is not on the shelf.
    assert defaults["backorder_policy"] == "allow"


def test_the_seeded_accrual_is_a_control_account_and_the_clearing_account_is_not(
    client: TestClient,
) -> None:
    """The asymmetry is the design, not an oversight. Only `inv` and `ap` may reach the
    accrual; the clearing account has to be reachable from a supplier invoice and a cashbook
    payment, which a control account would refuse."""
    _signup(client)
    by_code = {row["code"]: row for row in client.get("/api/v1/gl/accounts").json()}

    assert by_code["2350"]["control_type"] == "grn_accrual"
    assert by_code["2350"]["is_control"] is True
    assert by_code["1370"]["control_type"] is None
    assert by_code["1370"]["is_control"] is False


def test_the_backorder_policy_can_be_switched(client: TestClient) -> None:
    _signup(client)
    accounts = _accounts(client)

    updated = client.put("/api/v1/oe/defaults", json={"backorder_policy": "block"})

    assert updated.status_code == 200, updated.text
    assert updated.json()["backorder_policy"] == "block"
    # A policy-only write leaves the accounts alone.
    assert updated.json()["grn_accrual_account_id"] == accounts["2350"]


def test_pointing_the_accrual_at_an_ordinary_account_is_refused(client: TestClient) -> None:
    """Without the control type the account has no guard: any module could post to it and a
    line on it need carry no item, so the accrual proof would stop being provable."""
    _signup(client)
    accounts = _accounts(client)

    response = client.put(
        "/api/v1/oe/defaults", json={"grn_accrual_account_id": accounts["6990"]}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_grn_accrual_account"
    assert response.json()["field_errors"] == {
        "grn_accrual_account_id": ["not a GRN accrual control account"]
    }


@pytest.mark.parametrize(
    "field", ["purchase_price_variance_account_id", "landed_cost_clearing_account_id"]
)
def test_pointing_a_contra_at_a_control_account_is_refused(
    client: TestClient, field: str
) -> None:
    """PPV on the accrual would post an entry that moves nothing; freight on a control
    account could never be got there in the first place."""
    _signup(client)
    accounts = _accounts(client)

    response = client.put("/api/v1/oe/defaults", json={field: accounts["2350"]})

    assert response.status_code == 409
    assert response.json()["code"] == "contra_is_a_control_account"


@pytest.mark.parametrize(
    "field",
    [
        "grn_accrual_account_id",
        "purchase_price_variance_account_id",
        "landed_cost_clearing_account_id",
    ],
)
def test_clearing_any_required_order_setting_is_refused(client: TestClient, field: str) -> None:
    """P4's reasoning, three phases on: a NULL here fails at the first GRN or the first match
    with a variance, long after the operator who cleared it has gone."""
    _signup(client)

    response = client.put("/api/v1/oe/defaults", json={field: None})

    assert response.status_code == 409
    assert response.json()["code"] == "required_setting"
    assert response.json()["field_errors"] == {field: ["required"]}


def test_the_order_defaults_are_audited_with_before_and_after(
    client: TestClient, db
) -> None:
    """There is no audit *endpoint* — the log is read from the table, as every other audit
    assertion in the suite does."""
    from sqlalchemy import select

    from app.db import set_tenant
    from app.models.audit import AuditLog

    company_id = _signup(client)
    client.put("/api/v1/oe/defaults", json={"backorder_policy": "block"})

    set_tenant(db, company_id)
    record = db.scalars(
        select(AuditLog)
        .where(AuditLog.company_id == company_id, AuditLog.action == "order_defaults.updated")
        .order_by(AuditLog.at.desc())
    ).first()

    assert record is not None, "the settings change must be audited"
    assert record.entity == "gl_settings"
    assert record.actor_email == OWNER["email"]
    assert record.before["backorder_policy"] == "allow"
    assert record.after["backorder_policy"] == "block"


def test_writing_the_order_defaults_needs_the_setup_permission(
    client: TestClient, db
) -> None:
    """Reading opens to anyone with an order-entry permission; writing is `oe:setup_manage`."""
    from sqlalchemy import select

    from app.db import set_tenant
    from app.models.membership import Role
    from app.services import email as email_service

    company_id = _signup(client)
    set_tenant(db, company_id)
    role_id = db.scalars(
        select(Role.id).where(Role.company_id == company_id, Role.name == "Clerk")
    ).one()
    client.post(
        "/api/v1/invitations", json={"email": "clerk@rugari.example", "role_ids": [role_id]}
    )
    token = email_service.outbox[-1].context["token"]
    clerk = TestClient(client.app)
    clerk.post(
        "/api/v1/invitations/accept",
        json={
            "token": token,
            "full_name": "Clerk Person",
            "password": "another correct horse battery",
        },
    )

    assert clerk.put("/api/v1/oe/defaults", json={"backorder_policy": "block"}).status_code == 403


# --- Kits ---------------------------------------------------------------------------------


def test_a_kit_definition_round_trips_and_is_numbered_in_order(client: TestClient) -> None:
    _signup(client)
    bottle = _item(client, "WINE-750")
    box = _item(client, "BOX-01")
    kit = _item(client, "GIFT-01", item_type="kit")

    response = client.put(
        f"/api/v1/inventory/items/{kit['id']}/kit-components",
        json={
            "components": [
                {"component_item_id": bottle["id"], "quantity_per_kit": "2"},
                {"component_item_id": box["id"], "quantity_per_kit": "1"},
            ]
        },
    )

    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["component_item_id"] for row in rows] == [bottle["id"], box["id"]]
    assert [row["line_no"] for row in rows] == [1, 2]
    assert Decimal(rows[0]["quantity_per_kit"]) == Decimal(2)

    fetched = client.get(f"/api/v1/inventory/items/{kit['id']}/kit-components").json()
    assert [row["id"] for row in fetched] == [row["id"] for row in rows]


def test_replacing_a_definition_replaces_it_whole(client: TestClient) -> None:
    """A PUT, not a patch: a kit is only meaningful as a set, and a re-ordered definition
    reuses line numbers — which is why the delete is flushed before the inserts."""
    _signup(client)
    bottle = _item(client, "WINE-750")
    box = _item(client, "BOX-01")
    kit = _item(client, "GIFT-01", item_type="kit")
    url = f"/api/v1/inventory/items/{kit['id']}/kit-components"

    client.put(
        url,
        json={
            "components": [
                {"component_item_id": bottle["id"], "quantity_per_kit": "2"},
                {"component_item_id": box["id"], "quantity_per_kit": "1"},
            ]
        },
    )
    # The same two components, swapped — so both line numbers are reused by the other row.
    swapped = client.put(
        url,
        json={
            "components": [
                {"component_item_id": box["id"], "quantity_per_kit": "3"},
                {"component_item_id": bottle["id"], "quantity_per_kit": "4"},
            ]
        },
    )

    assert swapped.status_code == 200, swapped.text
    rows = swapped.json()
    assert [row["component_item_id"] for row in rows] == [box["id"], bottle["id"]]
    assert [row["line_no"] for row in rows] == [1, 2]
    assert [Decimal(row["quantity_per_kit"]) for row in rows] == [Decimal(3), Decimal(4)]

    emptied = client.put(url, json={"components": []})
    assert emptied.status_code == 200
    assert emptied.json() == []


def test_components_are_refused_on_an_item_that_is_not_a_kit(client: TestClient) -> None:
    _signup(client)
    bottle = _item(client, "WINE-750")
    plain = _item(client, "BOX-01")

    response = client.put(
        f"/api/v1/inventory/items/{plain['id']}/kit-components",
        json={"components": [{"component_item_id": bottle["id"], "quantity_per_kit": "1"}]},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "not_a_kit"


def test_a_kit_may_not_contain_itself_or_another_kit(client: TestClient) -> None:
    """No nesting in v1 — the explosion is one level, so it terminates by construction
    rather than by a depth limit somebody has to remember."""
    _signup(client)
    kit = _item(client, "GIFT-01", item_type="kit")
    inner = _item(client, "GIFT-02", item_type="kit")
    url = f"/api/v1/inventory/items/{kit['id']}/kit-components"

    itself = client.put(
        url, json={"components": [{"component_item_id": kit["id"], "quantity_per_kit": "1"}]}
    )
    assert itself.status_code == 409
    assert itself.json()["code"] == "kit_is_its_own_component"

    nested = client.put(
        url, json={"components": [{"component_item_id": inner["id"], "quantity_per_kit": "1"}]}
    )
    assert nested.status_code == 409
    assert nested.json()["code"] == "nested_kit"


def test_a_component_must_be_a_stock_or_a_service_item(client: TestClient) -> None:
    """Decision 8's other half. A kit explodes into things that can be delivered — stock, which
    moves and carries COGS, and service, which is billed and never on a shelf. A **non-stock**
    component is neither: it would ride the order as a line committing nothing, relieving
    nothing and costing nothing, while taking a share of the kit's revenue with it.

    Keyed on the row, like the other five refusals, so the kit editor puts the message on the
    cell that is wrong rather than in a toast over five rows.
    """
    _signup(client)
    kit = _item(client, "GIFT-01", item_type="kit")
    non_stock = _item(client, "CARD-01", item_type="non_stock")
    service = _item(client, "GIFTWRAP", item_type="service")
    stock = _item(client, "WINE-750", item_type="stock")

    refused = client.put(
        f"/api/v1/inventory/items/{kit['id']}/kit-components",
        json={
            "components": [
                {"component_item_id": stock["id"], "quantity_per_kit": "2"},
                {"component_item_id": non_stock["id"], "quantity_per_kit": "1"},
            ]
        },
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "component_not_stock_or_service"
    assert refused.json()["field_errors"] == {
        "components.1.component_item_id": ["not a stock or service item"]
    }

    # And the two types that are allowed go in together, so the refusal is about non-stock
    # rather than about anything that is not stock.
    allowed = client.put(
        f"/api/v1/inventory/items/{kit['id']}/kit-components",
        json={
            "components": [
                {"component_item_id": stock["id"], "quantity_per_kit": "2"},
                {"component_item_id": service["id"], "quantity_per_kit": "1"},
            ]
        },
    )
    assert allowed.status_code == 200, allowed.text
    assert [row["component_item_id"] for row in allowed.json()] == [stock["id"], service["id"]]


def test_a_component_may_not_appear_twice(client: TestClient) -> None:
    _signup(client)
    bottle = _item(client, "WINE-750")
    kit = _item(client, "GIFT-01", item_type="kit")

    response = client.put(
        f"/api/v1/inventory/items/{kit['id']}/kit-components",
        json={
            "components": [
                {"component_item_id": bottle["id"], "quantity_per_kit": "1"},
                {"component_item_id": bottle["id"], "quantity_per_kit": "2"},
            ]
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_kit_component"


# --- The two new item fields -----------------------------------------------------------------


def test_an_item_carries_a_purchase_account_and_a_weight(client: TestClient) -> None:
    """Both are data for later steps — the purchase account is where a *service* AP line
    expenses to, and the weight is the ratio a `weight`-basis landed cost allocates by."""
    _signup(client)
    accounts = _accounts(client)

    item = _item(
        client,
        "WINE-750",
        purchase_account_id=accounts["6990"],
        weight_per_base_unit="1.2",
    )

    assert item["purchase_account_id"] == accounts["6990"]
    assert Decimal(item["weight_per_base_unit"]) == Decimal("1.2")

    cleared = client.patch(
        f"/api/v1/inventory/items/{item['id']}", json={"clear_weight_per_base_unit": True}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["weight_per_base_unit"] is None


def test_a_weight_of_zero_is_refused(client: TestClient) -> None:
    """Zero is not "no weight": it would give the line no share of the freight and say
    nothing about it. `None` is how an item says it has no weight, and a `weight`-basis
    allocation refuses such a target outright at step 4."""
    _signup(client)

    response = client.post(
        "/api/v1/inventory/items",
        json={
            "code": "WINE-750",
            "name": "Rugari Red",
            "uom_category_id": _count_category(client)["id"],
            "base_uom_id": _count_category(client)["uoms"][0]["id"],
            "weight_per_base_unit": "0",
        },
    )

    assert response.status_code == 422
