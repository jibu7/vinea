"""P2 hardening at the API surface: idempotency-key payload fingerprinting, the
transaction-type master feeding the determination chain, and the projects master."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import set_tenant
from app.models.audit import AuditLog
from tests.kernel.invariants import assert_ledger_invariants
from tests.kernel.test_gl_api import Api, _invite

YEAR = date.today().year


@pytest.fixture
def api(client: TestClient, db: Session) -> Api:
    return Api(client, db)


# --- idempotency fingerprint -----------------------------------------------------------------


def test_same_key_with_a_different_payload_is_refused(api: Api) -> None:
    first = api.post_je("key-1")
    assert first.status_code == 201

    reused = api.post_je(
        "key-1",
        lines=[
            {"gl_account_id": api.accounts["6500"], "debit": "2000"},
            {"gl_account_id": api.accounts["2300"], "credit": "2000"},
        ],
    )

    assert reused.status_code == 409
    body = reused.json()
    assert body["code"] == "idempotency_key_reused"
    assert "JE-000001" in body["message"]
    # Neither document was lost: the first still stands, the second was never posted.
    assert [
        item["number"] for item in api.client.get("/api/v1/gl/journal-entries").json()["items"]
    ] == ["JE-000001"]
    assert api.post_je("key-2", **{"description": "Office supplies"}).status_code == 201


def test_identical_payload_still_replays(api: Api) -> None:
    first = api.post_je("retry")
    second = api.post_je("retry")
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]


def test_fingerprint_is_scoped_per_endpoint(api: Api) -> None:
    api.post_je("shared-key")
    cashbook = api.client.post(
        "/api/v1/gl/cashbook-entries",
        json={
            "entry_date": f"{YEAR}-03-20",
            "description": "Office supplies",
            "cash_account_id": api.accounts["1120"],
            "kind": "payment",
            "lines": [{"gl_account_id": api.accounts["6500"], "amount": "1000"}],
        },
        headers={"Idempotency-Key": "shared-key"},
    )
    assert cashbook.status_code == 409
    assert cashbook.json()["code"] == "idempotency_key_reused"


def test_reversal_key_reuse_with_another_reason_is_refused(api: Api) -> None:
    entry_id = api.post_je("orig").json()["id"]
    headers = {"Idempotency-Key": "rev"}
    url = f"/api/v1/gl/journal-entries/{entry_id}/reverse"
    assert (
        api.client.post(
            url, json={"entry_date": f"{YEAR}-04-01", "reason": "a"}, headers=headers
        ).status_code
        == 201
    )
    replay = api.client.post(
        url, json={"entry_date": f"{YEAR}-04-01", "reason": "a"}, headers=headers
    )
    assert replay.status_code == 200
    changed = api.client.post(
        url, json={"entry_date": f"{YEAR}-04-01", "reason": "b"}, headers=headers
    )
    assert changed.status_code == 409 and changed.json()["code"] == "idempotency_key_reused"


# --- transaction types ---------------------------------------------------------------------------


def _create_type(api: Api, **overrides) -> dict:
    body = {
        "module": "gl",
        "code": "BANK-CHARGES",
        "name": "Bank charges",
        "default_gl_account_id": api.accounts["6700"],
    }
    body.update(overrides)
    return api.client.post("/api/v1/gl/transaction-types", json=body).json()


def test_transaction_type_crud_and_posting(api: Api, db: Session) -> None:
    created = api.client.post(
        "/api/v1/gl/transaction-types",
        json={
            "module": "gl",
            "code": "BANK-CHARGES",
            "name": "Bank charges",
            "default_gl_account_id": api.accounts["6700"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["default_gl_account_id"] == api.accounts["6700"]

    duplicate = api.client.post(
        "/api/v1/gl/transaction-types",
        json={"module": "gl", "code": "BANK-CHARGES", "name": "dup"},
    )
    assert (
        duplicate.status_code == 409 and duplicate.json()["code"] == "transaction_type_code_taken"
    )
    # Same code in another module is a different type.
    assert (
        api.client.post(
            "/api/v1/gl/transaction-types",
            json={"module": "ar", "code": "BANK-CHARGES", "name": "AR version"},
        ).status_code
        == 201
    )

    reserved = api.client.post(
        "/api/v1/gl/transaction-types",
        json={"module": "gl", "code": "__retained_earnings", "name": "sentinel"},
    )
    assert (
        reserved.status_code == 409 and reserved.json()["code"] == "reserved_transaction_type_code"
    )

    header_default = api.client.post(
        "/api/v1/gl/transaction-types",
        json={
            "module": "gl",
            "code": "BAD",
            "name": "header default",
            "default_gl_account_id": api.accounts["6000"],
        },
    )
    assert (
        header_default.status_code == 409
        and header_default.json()["code"] == "account_not_postable"
    )

    # A line may name the transaction type instead of the account.
    posted = api.post_je(
        "by-type",
        lines=[
            {"transaction_type": "BANK-CHARGES", "debit": "2500"},
            {"gl_account_id": api.accounts["2300"], "credit": "2500"},
        ],
    )
    assert posted.status_code == 201, posted.text
    assert posted.json()["lines"][0]["gl_account_id"] == api.accounts["6700"]
    assert "gl_transaction_type.created" in set(db.scalars(select(AuditLog.action)))
    assert_ledger_invariants(api.db, api.company_id)


def test_transaction_type_listing_and_update(api: Api) -> None:
    created = _create_type(api)
    _create_type(api, module="ar", code="AR-TYPE", name="AR type", default_gl_account_id=None)

    assert {row["code"] for row in api.client.get("/api/v1/gl/transaction-types").json()} == {
        "BANK-CHARGES",
        "AR-TYPE",
    }
    gl_only = api.client.get("/api/v1/gl/transaction-types", params={"module": "gl"}).json()
    assert [row["code"] for row in gl_only] == ["BANK-CHARGES"]

    cleared = api.client.patch(
        f"/api/v1/gl/transaction-types/{created['id']}",
        json={"name": "Bank fees", "clear_default_account": True},
    )
    assert cleared.status_code == 200
    assert cleared.json()["default_gl_account_id"] is None and cleared.json()["name"] == "Bank fees"
    # With no default the chain can no longer resolve the line.
    unresolved = api.post_je(
        "no-default",
        lines=[
            {"transaction_type": "BANK-CHARGES", "debit": "10"},
            {"gl_account_id": api.accounts["2300"], "credit": "10"},
        ],
    )
    assert unresolved.status_code == 422 and unresolved.json()["code"] == "account_undetermined"

    deactivated = api.client.patch(
        f"/api/v1/gl/transaction-types/{created['id']}", json={"is_active": False}
    )
    assert deactivated.json()["is_active"] is False
    assert api.client.get("/api/v1/gl/transaction-types", params={"module": "gl"}).json() == []
    assert (
        len(
            api.client.get(
                "/api/v1/gl/transaction-types", params={"module": "gl", "include_inactive": True}
            ).json()
        )
        == 1
    )


def test_cashbook_line_can_use_a_transaction_type(api: Api) -> None:
    _create_type(api)
    response = api.client.post(
        "/api/v1/gl/cashbook-entries",
        json={
            "entry_date": f"{YEAR}-03-20",
            "description": "Monthly bank charges",
            "cash_account_id": api.accounts["1120"],
            "kind": "payment",
            "lines": [{"transaction_type": "BANK-CHARGES", "amount": "3000"}],
        },
        headers={"Idempotency-Key": "cb-type"},
    )
    assert response.status_code == 201, response.text
    assert [(line["gl_account_id"], line["amount"]) for line in response.json()["lines"]] == [
        (api.accounts["6700"], "3000.000000"),
        (api.accounts["1120"], "-3000.000000"),
    ]


def test_a_line_needs_an_account_or_a_transaction_type(api: Api) -> None:
    response = api.post_je(
        "neither",
        lines=[
            {"debit": "10"},
            {"gl_account_id": api.accounts["2300"], "credit": "10"},
        ],
    )
    assert response.status_code == 422 and response.json()["code"] == "validation_error"


def test_reserved_codes_are_blocked_by_the_database_too(api: Api, db: Session) -> None:
    """The service refuses `__` codes, but the sentinel only stays un-forgeable if the DB
    refuses them as well."""
    insert = text(
        "INSERT INTO gl_transaction_types (company_id, module, code, name, is_active) "
        "VALUES (:cid, 'gl', :code, 'forged', true)"
    )
    with pytest.raises(IntegrityError) as excinfo:
        db.execute(insert, {"cid": api.company_id, "code": "__retained_earnings"})
    assert "ck_gl_transaction_types_code_not_reserved" in str(excinfo.value.orig)
    db.rollback()

    set_tenant(db, api.company_id)
    db.execute(insert, {"cid": api.company_id, "code": "A_B"})  # single underscore is fine
    db.commit()


# --- projects --------------------------------------------------------------------------------


def test_project_crud_and_dimension_use(api: Api, db: Session) -> None:
    assert api.client.get("/api/v1/gl/projects").json() == []

    created = api.client.post(
        "/api/v1/gl/projects", json={"code": "P-ALPHA", "name": "Alpha build"}
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    assert created.json()["is_active"] is True

    duplicate = api.client.post("/api/v1/gl/projects", json={"code": "P-ALPHA", "name": "again"})
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "project_code_taken"

    api.post_je(
        "tagged",
        lines=[
            {"gl_account_id": api.accounts["6500"], "debit": "800", "project_id": project_id},
            {"gl_account_id": api.accounts["2300"], "credit": "800", "project_id": project_id},
        ],
    )
    by_project = api.trial_balance(project_id=project_id)
    assert {row["code"]: row["net"] for row in by_project["rows"]} == {
        "6500": "800.000000",
        "2300": "-800.000000",
    }

    # Renaming keeps the history attached to the id (same rule as accounts).
    renamed = api.client.patch(f"/api/v1/gl/projects/{project_id}", json={"code": "P-A1"})
    assert renamed.status_code == 200 and renamed.json()["code"] == "P-A1"
    assert api.trial_balance(project_id=project_id)["rows"] == by_project["rows"]
    assert "project.renamed" in set(db.scalars(select(AuditLog.action)))

    closed = api.client.patch(f"/api/v1/gl/projects/{project_id}", json={"is_active": False})
    assert closed.json()["is_active"] is False
    assert api.client.get("/api/v1/gl/projects").json() == []
    assert len(api.client.get("/api/v1/gl/projects", params={"include_inactive": True}).json()) == 1
    # An inactive project can no longer be tagged, but its history stays queryable.
    rejected = api.post_je(
        "after-close",
        lines=[
            {"gl_account_id": api.accounts["6500"], "debit": "5", "project_id": project_id},
            {"gl_account_id": api.accounts["2300"], "credit": "5"},
        ],
    )
    assert rejected.status_code == 422 and rejected.json()["code"] == "project_not_found"
    assert_ledger_invariants(api.db, api.company_id)


def test_project_permissions_and_tenancy(api: Api, db: Session) -> None:
    project_id = api.client.post(
        "/api/v1/gl/projects", json={"code": "P-ALPHA", "name": "Alpha"}
    ).json()["id"]

    clerk = _invite(api.client, db, api.company_id, "Clerk", "clerk@kigali.example")
    assert clerk.get("/api/v1/gl/projects").status_code == 200  # projects:read
    denied = clerk.post("/api/v1/gl/projects", json={"code": "P-X", "name": "x"})
    assert denied.status_code == 403 and "projects:manage" in denied.json()["message"]

    other = TestClient(api.client.app)
    other.post(
        "/api/v1/auth/signup",
        json={
            "company_name": "Musanze Supplies Ltd",
            "full_name": "Bob Habimana",
            "email": "owner@musanze.example",
            "password": "correct horse battery staple",
        },
    )
    assert other.get(f"/api/v1/gl/projects/{project_id}").status_code == 404
    assert other.get("/api/v1/gl/projects").json() == []
    # Same code in another tenant is fine — uniqueness is per company.
    assert (
        other.post("/api/v1/gl/projects", json={"code": "P-ALPHA", "name": "theirs"}).status_code
        == 201
    )
