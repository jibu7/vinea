from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

OWNER = {
    "company_name": "Kigali Wines Ltd",
    "full_name": "Aline Uwase",
    "email": "owner@kigaliwines.example",
    "password": "correct horse battery staple",
}


def test_company_details_api(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=OWNER)

    # Read company details
    res = client.get("/api/v1/company")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Kigali Wines Ltd"
    assert body["fiscal_country"] == "RW"

    # Update company details
    patch_res = client.patch(
        "/api/v1/company",
        json={"name": "Kigali Fine Wines Ltd", "tin": "123456789", "vat_registered": True},
    )
    assert patch_res.status_code == 200
    updated = patch_res.json()
    assert updated["name"] == "Kigali Fine Wines Ltd"
    assert updated["tin"] == "123456789"
    assert updated["vat_registered"] is True


def test_currencies_and_branches_and_taxes_api(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=OWNER)

    # Branch CRUD
    br = client.post("/api/v1/gl/branches", json={"code": "KGL-NORTH", "name": "Kigali North"})
    assert br.status_code == 201
    br_id = br.json()["id"]

    br_patch = client.patch(f"/api/v1/gl/branches/{br_id}", json={"name": "Kigali North Hub"})
    assert br_patch.status_code == 200
    assert br_patch.json()["name"] == "Kigali North Hub"

    branches = client.get("/api/v1/gl/branches").json()
    assert any(b["code"] == "KGL-NORTH" for b in branches)

    # Currency CRUD
    curr = client.post(
        "/api/v1/gl/currencies",
        json={"code": "EUR", "name": "Euro", "symbol": "€", "decimal_places": 2},
    )
    assert curr.status_code == 201
    curr_id = curr.json()["id"]

    curr_patch = client.patch(f"/api/v1/gl/currencies/{curr_id}", json={"name": "European Euro"})
    assert curr_patch.status_code == 200
    assert curr_patch.json()["name"] == "European Euro"

    # Tax code CRUD
    tc = client.post(
        "/api/v1/gl/tax-codes",
        json={
            "code": "VAT-SPECIAL",
            "name": "Special VAT",
            "nature": "output",
            "rate_pct": "15",
            "valid_from": "2026-01-01",
        },
    )
    assert tc.status_code == 201
    tc_id = tc.json()["id"]

    tc_patch = client.patch(f"/api/v1/gl/tax-codes/{tc_id}", json={"rate_pct": "14"})
    assert tc_patch.status_code == 200
    assert float(tc_patch.json()["rate_pct"]) == 14.0


def test_account_history_api(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=OWNER)

    # Create account
    acc = client.post(
        "/api/v1/gl/accounts",
        json={
            "code": "6888",
            "name": "Testing Supplies",
            "class_": "expense",
            "is_postable": True,
        },
    )
    assert acc.status_code == 201
    acc_id = acc.json()["id"]

    # Rename account
    renamed = client.patch(f"/api/v1/gl/accounts/{acc_id}", json={"code": "6889"})
    assert renamed.status_code == 200

    # History
    hist = client.get(f"/api/v1/gl/accounts/{acc_id}/history")
    assert hist.status_code == 200
    items = hist.json()
    assert len(items) >= 1
    rename_event = next(it for it in items if it["action"] == "gl_account.renamed")
    assert rename_event["before"]["code"] == "6888"
    assert rename_event["after"]["code"] == "6889"


def test_memberships_api(client: TestClient, db: Session) -> None:
    client.post("/api/v1/auth/signup", json=OWNER)

    # List roles
    roles_res = client.get("/api/v1/memberships/roles")
    assert roles_res.status_code == 200
    roles = roles_res.json()
    assert len(roles) > 0
    clerk_role = next(r for r in roles if r["name"] == "Clerk")

    # Invite member
    inv = client.post(
        "/api/v1/invitations",
        json={"email": "clerk@kigaliwines.example", "role_ids": [clerk_role["id"]]},
    )
    assert inv.status_code == 201
    member_id = inv.json()["id"]

    # List memberships
    members = client.get("/api/v1/memberships").json()
    assert len(members) == 2  # owner + invited clerk
    clerk_m = next(m for m in members if m["id"] == member_id)
    assert clerk_m["status"] == "pending"

    # Deactivate / activate member
    deact = client.post(f"/api/v1/memberships/{member_id}/deactivate")
    assert deact.status_code == 200
    assert deact.json()["status"] == "suspended"

    act = client.post(f"/api/v1/memberships/{member_id}/activate")
    assert act.status_code == 200
    assert act.json()["status"] == "active"


def test_tax_code_rate_immutability_with_postings(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=OWNER)
    accounts = {a["code"]: a["id"] for a in client.get("/api/v1/gl/accounts").json()}
    tax_codes = {tc["code"]: tc["id"] for tc in client.get("/api/v1/gl/tax-codes").json()}
    vat_id = tax_codes["VAT-OUT-18"]

    # Post an entry referencing VAT-OUT-18
    entry_res = client.post(
        "/api/v1/gl/journal-entries",
        json={
            "entry_date": "2026-03-15",
            "description": "Sale with tax",
            "lines": [
                {
                    "gl_account_id": accounts["4100"],
                    "credit": "1000",
                    "tax_code_id": vat_id,
                    "tax_amount": "180",
                },
                {"gl_account_id": accounts["2200"], "credit": "180"},
                {"gl_account_id": accounts["2300"], "debit": "1180"},
            ],
        },
        headers={"Idempotency-Key": "tax-test-1"},
    )
    assert entry_res.status_code == 201, entry_res.text

    # Path A: Changing rate_pct on a tax code with postings must be rejected (409)
    rejected = client.patch(f"/api/v1/gl/tax-codes/{vat_id}", json={"rate_pct": "19"})
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "tax_code_has_postings"

    # Path B: Labels, valid_to, and active status stay editable even with postings
    updated = client.patch(
        f"/api/v1/gl/tax-codes/{vat_id}",
        json={"name": "Standard Rate Output VAT Updated", "valid_to": "2026-12-31"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Standard Rate Output VAT Updated"
    assert updated.json()["valid_to"] == "2026-12-31"

    # Path C: Changing rate on a tax code with NO postings succeeds
    fresh_tc = client.post(
        "/api/v1/gl/tax-codes",
        json={
            "code": "VAT-UNUSED",
            "name": "Unused VAT",
            "nature": "output",
            "rate_pct": "10",
            "valid_from": "2026-01-01",
        },
    ).json()
    fresh_patch = client.patch(
        f"/api/v1/gl/tax-codes/{fresh_tc['id']}", json={"rate_pct": "12"}
    )
    assert fresh_patch.status_code == 200
    assert float(fresh_patch.json()["rate_pct"]) == 12.0


def test_currency_decimal_places_immutability_with_postings(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=OWNER)
    accounts = {a["code"]: a["id"] for a in client.get("/api/v1/gl/accounts").json()}
    currencies = {c["code"]: c["id"] for c in client.get("/api/v1/gl/currencies").json()}
    usd_id = currencies["USD"]

    # Post an entry referencing USD
    entry_res = client.post(
        "/api/v1/gl/journal-entries",
        json={
            "entry_date": "2026-03-15",
            "description": "USD posting",
            "lines": [
                {
                    "gl_account_id": accounts["6500"],
                    "debit": "100",
                    "currency_id": usd_id,
                    "exchange_rate": "1300",
                },
                {
                    "gl_account_id": accounts["2300"],
                    "credit": "100",
                    "currency_id": usd_id,
                    "exchange_rate": "1300",
                },
            ],
        },
        headers={"Idempotency-Key": "curr-test-1"},
    )
    assert entry_res.status_code == 201, entry_res.text

    # Changing decimal_places on USD must be rejected (409)
    rejected = client.patch(f"/api/v1/gl/currencies/{usd_id}", json={"decimal_places": 3})
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "currency_has_postings"

    # Name and symbol stay editable
    updated = client.patch(
        f"/api/v1/gl/currencies/{usd_id}", json={"name": "US Dollar Updated", "symbol": "US$"}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "US Dollar Updated"
    assert updated.json()["symbol"] == "US$"

    # Currency with NO postings allows changing decimal_places
    fresh_curr = client.post(
        "/api/v1/gl/currencies",
        json={"code": "GBP", "name": "British Pound", "symbol": "£", "decimal_places": 2},
    ).json()
    fresh_patch = client.patch(
        f"/api/v1/gl/currencies/{fresh_curr['id']}", json={"decimal_places": 4}
    )
    assert fresh_patch.status_code == 200
    assert fresh_patch.json()["decimal_places"] == 4


def test_coa_deactivation_rules(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=OWNER)
    accounts = {a["code"]: a for a in client.get("/api/v1/gl/accounts").json()}

    # Case 1: Account referenced by gl_settings (retained earnings 3200) -> account_in_use
    re_acc = accounts["3200"]
    res1 = client.patch(f"/api/v1/gl/accounts/{re_acc['id']}", json={"is_active": False})
    assert res1.status_code == 409
    assert res1.json()["code"] == "account_in_use"

    # Case 2: Control account (e.g. 2100 Accounts Payable) -> control_account_cannot_be_deactivated
    ap_acc = accounts["2100"]
    assert ap_acc["is_control"] is True
    res2 = client.patch(f"/api/v1/gl/accounts/{ap_acc['id']}", json={"is_active": False})
    assert res2.status_code == 409
    assert res2.json()["code"] == "control_account_cannot_be_deactivated"

    # Case 3: Account with a non-zero balance -> account_has_non_zero_balance
    # Post a journal debiting 6500 (Office Supplies)
    supplies = accounts["6500"]
    accrued = accounts["2300"]
    client.post(
        "/api/v1/gl/journal-entries",
        json={
            "entry_date": "2026-03-15",
            "description": "Supplies purchase",
            "lines": [
                {"gl_account_id": supplies["id"], "debit": "500"},
                {"gl_account_id": accrued["id"], "credit": "500"},
            ],
        },
        headers={"Idempotency-Key": "bal-test-1"},
    )
    res3 = client.patch(f"/api/v1/gl/accounts/{supplies['id']}", json={"is_active": False})
    assert res3.status_code == 409
    assert res3.json()["code"] == "account_has_non_zero_balance"

    # Case 4: Account with zero balance / no postings can be deactivated
    rent_acc = accounts["6200"]
    res4 = client.patch(f"/api/v1/gl/accounts/{rent_acc['id']}", json={"is_active": False})
    assert res4.status_code == 200
    assert res4.json()["is_active"] is False
