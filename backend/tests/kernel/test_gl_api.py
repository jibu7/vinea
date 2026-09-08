"""GL API: manual journal / cashbook / reversal with Idempotency-Key, enquiries, periods,
year-end, COA — through the HTTP layer with cookies, permissions and RLS in force."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import set_tenant
from app.models.currency import Currency
from app.models.fiscal import AccountingPeriod
from app.models.gl import GLAccount
from app.models.membership import Role
from app.services import email as email_service
from tests.kernel.invariants import assert_ledger_invariants

YEAR = date.today().year
OWNER = {
    "company_name": "Kigali Traders Ltd",
    "full_name": "Aline Uwase",
    "email": "owner@kigali.example",
    "password": "correct horse battery staple",
}


class Api:
    def __init__(self, client: TestClient, db: Session) -> None:
        self.client = client
        session = client.post("/api/v1/auth/signup", json=OWNER).json()
        self.company_id: int = session["company_id"]
        set_tenant(db, self.company_id)
        self.accounts = {a.code: a.id for a in db.scalars(select(GLAccount))}
        self.currencies = {c.code: c.id for c in db.scalars(select(Currency))}
        periods = list(db.scalars(select(AccountingPeriod).order_by(AccountingPeriod.period_no)))
        self.periods = {p.period_no: p.id for p in periods}
        for period in periods:  # deterministic regardless of today's month
            client.post(f"/api/v1/gl/periods/{period.id}/open")
        client.post(
            "/api/v1/gl/exchange-rates",
            json={
                "currency_id": self.currencies["USD"],
                "valid_from": f"{YEAR}-01-01",
                "rate": "1300.5",
            },
        )
        self.db = db

    def post_je(self, key: str, **overrides):  # noqa: ANN201
        body = {
            "entry_date": f"{YEAR}-03-15",
            "description": "Office supplies",
            "lines": [
                {"gl_account_id": self.accounts["6500"], "debit": "1000"},
                {"gl_account_id": self.accounts["2300"], "credit": "1000"},
            ],
        }
        body.update(overrides)
        return self.client.post(
            "/api/v1/gl/journal-entries", json=body, headers={"Idempotency-Key": key}
        )

    def trial_balance(self, as_of: str = f"{YEAR}-12-31", **params) -> dict:
        return self.client.get("/api/v1/gl/trial-balance", params={"as_of": as_of, **params}).json()


@pytest.fixture
def api(client: TestClient, db: Session) -> Api:
    return Api(client, db)


def _invite(
    client: TestClient, db: Session, company_id: int, role_name: str, email: str
) -> TestClient:
    role_id = db.scalars(
        select(Role.id).where(Role.company_id == company_id, Role.name == role_name)
    ).one()
    client.post("/api/v1/invitations", json={"email": email, "role_ids": [role_id]})
    token = email_service.outbox[-1].context["token"]
    member = TestClient(client.app)
    member.post(
        "/api/v1/invitations/accept",
        json={"token": token, "full_name": role_name, "password": "another good passphrase"},
    )
    return member


# --- journal entries -----------------------------------------------------------------------


def test_post_multi_currency_journal_entry(api: Api) -> None:
    response = api.post_je(
        "je-1",
        description="USD consulting fee",
        lines=[
            {
                "gl_account_id": api.accounts["6900"],
                "debit": "100.00",
                "currency_id": api.currencies["USD"],
            },
            {
                "gl_account_id": api.accounts["2300"],
                "credit": "100.00",
                "currency_id": api.currencies["USD"],
            },
            {"gl_account_id": api.accounts["6700"], "debit": "250"},
            {"gl_account_id": api.accounts["2500"], "credit": "250", "description": "bank fee"},
        ],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["number"] == "JE-000001" and body["status"] == "posted"
    assert body["event_type"] == "manual_journal"
    lines = body["lines"]
    assert [line["line_no"] for line in lines] == [1, 2, 3, 4]
    assert lines[0]["exchange_rate"] == "1300.5000000000"
    assert (lines[0]["amount"], lines[0]["base_amount"]) == ("100.000000", "130050.000000")
    assert lines[3]["description"] == "bank fee"
    assert (
        api.client.get(f"/api/v1/gl/journal-entries/{body['id']}").json()["number"] == "JE-000001"
    )
    assert_ledger_invariants(api.db, api.company_id)


def test_idempotency_key_replays_instead_of_double_posting(api: Api) -> None:
    first = api.post_je("double-click")
    second = api.post_je("double-click")
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    listing = api.client.get("/api/v1/gl/journal-entries").json()
    assert [item["number"] for item in listing["items"]] == ["JE-000001"]

    missing = api.client.post(
        "/api/v1/gl/journal-entries",
        json={"entry_date": f"{YEAR}-03-15", "description": "x", "lines": []},
    )
    assert missing.status_code == 422
    assert missing.json()["code"] == "validation_error"
    assert "Idempotency-Key" in " ".join(missing.json()["field_errors"])


def test_unbalanced_entry_returns_the_error_envelope(api: Api) -> None:
    response = api.post_je(
        "unbalanced",
        lines=[
            {"gl_account_id": api.accounts["6500"], "debit": "1000"},
            {"gl_account_id": api.accounts["2300"], "credit": "999"},
        ],
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "unbalanced_entry"
    assert body["field_errors"] == {"lines": ["base-currency difference +1"]}
    assert api.client.get("/api/v1/gl/journal-entries").json()["items"] == []


def test_line_must_be_debit_xor_credit(api: Api) -> None:
    response = api.post_je(
        "both-sides",
        lines=[
            {"gl_account_id": api.accounts["6500"], "debit": "10", "credit": "10"},
            {"gl_account_id": api.accounts["2300"], "credit": "10"},
        ],
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert "lines.0" in " ".join(response.json()["field_errors"])


def test_backdated_entry_into_a_closed_period_is_rejected_by_the_api(api: Api) -> None:
    march = api.periods[3]
    assert api.client.post(f"/api/v1/gl/periods/{march}/close").json()["status"] == "closed"

    response = api.post_je("closed", entry_date=f"{YEAR}-03-15")

    assert response.status_code == 422
    assert response.json()["code"] == "period_not_open"
    assert api.client.get("/api/v1/gl/journal-entries").json()["items"] == []
    # April is still open.
    assert api.post_je("april", entry_date=f"{YEAR}-04-01").status_code == 201


def test_control_account_is_refused_for_manual_journals(api: Api) -> None:
    response = api.post_je(
        "bank",
        lines=[
            {"gl_account_id": api.accounts["6500"], "debit": "10"},
            {"gl_account_id": api.accounts["1120"], "credit": "10"},
        ],
    )
    assert response.status_code == 422
    assert response.json()["code"] == "control_account_manual_posting"


def test_cashbook_entry_derives_the_bank_line_and_splits_tax(api: Api, db: Session) -> None:
    from app.models.tax import TaxCode

    vat_in = db.scalars(select(TaxCode.id).where(TaxCode.code == "VAT-IN-18")).one()
    response = api.client.post(
        "/api/v1/gl/cashbook-entries",
        json={
            "entry_date": f"{YEAR}-03-20",
            "description": "Stationery, paid by bank",
            "cash_account_id": api.accounts["1120"],
            "kind": "payment",
            "reference": "EFT 77",
            "lines": [
                {
                    "gl_account_id": api.accounts["6500"],
                    "amount": "118",
                    "tax_code_id": vat_in,
                    "tax_inclusive": True,
                }
            ],
        },
        headers={"Idempotency-Key": "cb-1"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["number"] == "CB-000001" and body["doc_type"] == "CB"
    amounts = [(line["gl_account_id"], line["amount"]) for line in body["lines"]]
    assert amounts == [
        (api.accounts["6500"], "100.000000"),
        (api.accounts["1400"], "18.000000"),
        (api.accounts["1120"], "-118.000000"),
    ]
    assert body["lines"][0]["tax_amount"] == "18.000000"
    assert api.client.get("/api/v1/gl/period-balances/verify").json() == []


def test_reversal_endpoint_restores_the_trial_balance(api: Api) -> None:
    entry_id = api.post_je("orig").json()["id"]
    moved = api.trial_balance()
    assert moved["foots"] and moved["total_debit"] == "1000.000000"

    response = api.client.post(
        f"/api/v1/gl/journal-entries/{entry_id}/reverse",
        json={"entry_date": f"{YEAR}-04-01", "reason": "wrong account"},
        headers={"Idempotency-Key": "rev-1"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["reverses_entry_id"] == entry_id and body["number"] == "JE-000002"
    assert body["reversal_reason"] == "wrong account"
    replay = api.client.post(
        f"/api/v1/gl/journal-entries/{entry_id}/reverse",
        json={"entry_date": f"{YEAR}-04-01", "reason": "wrong account"},
        headers={"Idempotency-Key": "rev-1"},
    )
    assert replay.status_code == 200 and replay.json()["id"] == body["id"]
    again = api.client.post(
        f"/api/v1/gl/journal-entries/{entry_id}/reverse",
        json={"entry_date": f"{YEAR}-04-01", "reason": "twice"},
        headers={"Idempotency-Key": "rev-2"},
    )
    assert again.status_code == 409 and again.json()["code"] == "entry_already_reversed"

    restored = api.trial_balance()
    assert restored["foots"]
    assert all(row["net"] == "0.000000" for row in restored["rows"])
    assert_ledger_invariants(api.db, api.company_id)


# --- enquiries ----------------------------------------------------------------------------------


def test_trial_balance_and_account_transactions(api: Api, db: Session) -> None:
    from app.models.company import Branch

    musanze = Branch(
        company_id=api.company_id, code="MUS", name="Musanze", is_main=False, is_active=True
    )
    db.add(musanze)
    db.commit()
    api.post_je("a", entry_date=f"{YEAR}-02-01")
    api.post_je(
        "b",
        entry_date=f"{YEAR}-03-01",
        branch_id=musanze.id,
        lines=[
            {"gl_account_id": api.accounts["6200"], "debit": "400"},
            {"gl_account_id": api.accounts["2300"], "credit": "400"},
        ],
    )
    api.post_je(
        "c",
        entry_date=f"{YEAR}-05-01",
        lines=[
            {"gl_account_id": api.accounts["2300"], "debit": "300"},
            {"gl_account_id": api.accounts["6500"], "credit": "300"},
        ],
    )

    tb = api.trial_balance(as_of=f"{YEAR}-03-31")
    assert tb["foots"] and tb["total_debit"] == tb["total_credit"] == "1400.000000"
    by_code = {row["code"]: row for row in tb["rows"]}
    assert by_code["6500"]["debit"] == "1000.000000" and by_code["6500"]["class"] == "expense"
    assert by_code["2300"]["credit"] == "1400.000000"
    branch_tb = api.trial_balance(as_of=f"{YEAR}-12-31", branch_id=musanze.id)
    assert {row["code"]: row["net"] for row in branch_tb["rows"]} == {
        "6200": "400.000000",
        "2300": "-400.000000",
    }

    tx = api.client.get(
        f"/api/v1/gl/accounts/{api.accounts['6500']}/transactions",
        params={"date_from": f"{YEAR}-03-01", "date_to": f"{YEAR}-12-31"},
    ).json()
    assert tx["opening_base"] == "1000.000000"
    assert [(item["entry_number"], item["running_base"]) for item in tx["items"]] == [
        ("JE-000003", "700.000000")
    ]
    assert tx["next_cursor"] is None
    assert (
        api.client.get(
            "/api/v1/gl/accounts/999999/transactions",
            params={"date_from": f"{YEAR}-03-01", "date_to": f"{YEAR}-12-31"},
        ).status_code
        == 404
    )


# --- permissions & tenancy ----------------------------------------------------------------------


def test_permissions_gate_posting_and_reopening(api: Api, db: Session) -> None:
    clerk = _invite(api.client, db, api.company_id, "Clerk", "clerk@kigali.example")
    accountant = _invite(api.client, db, api.company_id, "Accountant", "acc@kigali.example")

    assert (
        clerk.get("/api/v1/gl/trial-balance", params={"as_of": f"{YEAR}-12-31"}).status_code == 200
    )
    denied = clerk.post(
        "/api/v1/gl/journal-entries",
        json={"entry_date": f"{YEAR}-03-15", "description": "x", "lines": []},
        headers={"Idempotency-Key": "clerk"},
    )
    assert denied.status_code == 403 and "gl:journal_post" in denied.json()["message"]

    march = api.periods[3]
    assert accountant.post(f"/api/v1/gl/periods/{march}/close").status_code == 200
    reopen = accountant.post(f"/api/v1/gl/periods/{march}/reopen", json={"reason": "late invoice"})
    assert reopen.status_code == 403 and "accounting_periods:reopen" in reopen.json()["message"]
    # The owner holds every permission, including reopen, and the action is audited.
    assert (
        api.client.post(f"/api/v1/gl/periods/{march}/reopen", json={"reason": "late"}).json()[
            "status"
        ]
        == "open"
    )


def test_another_tenant_cannot_see_the_entry(api: Api) -> None:
    entry_id = api.post_je("mine").json()["id"]
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
    assert other.get(f"/api/v1/gl/journal-entries/{entry_id}").status_code == 404
    assert other.get("/api/v1/gl/journal-entries").json()["items"] == []
    assert (
        other.get("/api/v1/gl/trial-balance", params={"as_of": f"{YEAR}-12-31"}).json()["rows"]
        == []
    )


def test_reference_roundtrips_and_is_searchable(api: Api) -> None:
    res = api.post_je(
        "with-ref",
        entry_date=f"{YEAR}-02-15",
        reference="INV-2026-001",
        lines=[
            {"gl_account_id": api.accounts["6500"], "debit": "250"},
            {"gl_account_id": api.accounts["2300"], "credit": "250"},
        ],
    )
    assert res.status_code == 201
    entry_id = res.json()["id"]
    assert res.json()["reference"] == "INV-2026-001"

    # Detail read
    detail = api.client.get(f"/api/v1/gl/journal-entries/{entry_id}").json()
    assert detail["reference"] == "INV-2026-001"
    assert detail["reversed_by_entry_id"] is None

    # Search by reference
    found = api.client.get(
        "/api/v1/gl/journal-entries", params={"reference": "INV-2026-001"}
    ).json()
    assert len(found["items"]) == 1
    assert found["items"][0]["id"] == entry_id

    # Search with general query
    by_q = api.client.get("/api/v1/gl/journal-entries", params={"query": "INV-2026"}).json()
    assert len(by_q["items"]) == 1
    assert by_q["items"][0]["id"] == entry_id

    # Not found search
    not_found = api.client.get(
        "/api/v1/gl/journal-entries", params={"reference": "NONEXISTENT"}
    ).json()
    assert len(not_found["items"]) == 0

    # Appears in account transactions
    tx = api.client.get(
        f"/api/v1/gl/accounts/{api.accounts['6500']}/transactions",
        params={"date_from": f"{YEAR}-02-01", "date_to": f"{YEAR}-02-28"},
    ).json()
    item = next(it for it in tx["items"] if it["entry_id"] == entry_id)
    assert item["reference"] == "INV-2026-001"

    # Reversal shows reversed_by_entry_id on original entry
    rev = api.client.post(
        f"/api/v1/gl/journal-entries/{entry_id}/reverse",
        json={"entry_date": f"{YEAR}-02-16", "reason": "wrong reference"},
        headers={"Idempotency-Key": "rev-ref-test"},
    )
    assert rev.status_code == 201
    rev_id = rev.json()["id"]

    # Re-reading original entry now returns reversed_by_entry_id and reversed_by_number
    detail_after = api.client.get(f"/api/v1/gl/journal-entries/{entry_id}").json()
    assert detail_after["reversed_by_entry_id"] == rev_id
    assert detail_after["reversed_by_number"] == rev.json()["number"]

    # Reading reversal entry returns reverses_entry_id and reverses_entry_number
    rev_detail = api.client.get(f"/api/v1/gl/journal-entries/{rev_id}").json()
    assert rev_detail["reverses_entry_id"] == entry_id
    assert rev_detail["reverses_entry_number"] == detail["number"]


def test_account_transactions_filtered_opening_and_reconciliation(api: Api, db: Session) -> None:
    from app.models.company import Branch
    from app.models.gl import Project

    branch_b = Branch(
        company_id=api.company_id, code="BB", name="Branch B", is_main=False, is_active=True
    )
    proj_a = Project(company_id=api.company_id, code="PRJ-A", name="Project A", is_active=True)
    proj_b = Project(company_id=api.company_id, code="PRJ-B", name="Project B", is_active=True)
    db.add_all([branch_b, proj_a, proj_b])
    db.commit()

    # Pre-period entries (before 2026-06-01):
    # Entry 1 on PRJ-A: +500 to 6500
    api.post_je(
        "pre-1",
        entry_date=f"{YEAR}-05-10",
        lines=[
            {"gl_account_id": api.accounts["6500"], "debit": "500", "project_id": proj_a.id},
            {"gl_account_id": api.accounts["2300"], "credit": "500"},
        ],
    )
    # Entry 2 on PRJ-B: +300 to 6500
    api.post_je(
        "pre-2",
        entry_date=f"{YEAR}-05-20",
        lines=[
            {"gl_account_id": api.accounts["6500"], "debit": "300", "project_id": proj_b.id},
            {"gl_account_id": api.accounts["2300"], "credit": "300"},
        ],
    )

    # In-period entries (between 2026-06-01 and 2026-06-30):
    # Entry 3 on PRJ-A: +200 to 6500
    api.post_je(
        "in-1",
        entry_date=f"{YEAR}-06-05",
        lines=[
            {"gl_account_id": api.accounts["6500"], "debit": "200", "project_id": proj_a.id},
            {"gl_account_id": api.accounts["2300"], "credit": "200"},
        ],
    )
    # Entry 4 on PRJ-B: +150 to 6500
    api.post_je(
        "in-2",
        entry_date=f"{YEAR}-06-15",
        lines=[
            {"gl_account_id": api.accounts["6500"], "debit": "150", "project_id": proj_b.id},
            {"gl_account_id": api.accounts["2300"], "credit": "150"},
        ],
    )
    # Entry 5 on PRJ-A: -50 (credit) to 6500
    api.post_je(
        "in-3",
        entry_date=f"{YEAR}-06-25",
        lines=[
            {"gl_account_id": api.accounts["2300"], "debit": "50"},
            {"gl_account_id": api.accounts["6500"], "credit": "50", "project_id": proj_a.id},
        ],
    )

    # Query filtered by project_id=proj_a.id
    res_a = api.client.get(
        f"/api/v1/gl/accounts/{api.accounts['6500']}/transactions",
        params={
            "date_from": f"{YEAR}-06-01",
            "date_to": f"{YEAR}-06-30",
            "project_id": proj_a.id,
        },
    ).json()

    # Filtered opening balance must ONLY include Entry 1 (500), ignoring Entry 2 (300)
    assert Decimal(res_a["opening_base"]) == Decimal("500.000000")
    # In-period rows must only include Entry 3 (+200) and Entry 5 (-50)
    assert len(res_a["items"]) == 2
    assert Decimal(res_a["items"][0]["base_amount"]) == Decimal("200.000000")
    assert Decimal(res_a["items"][0]["running_base"]) == Decimal("700.000000")
    assert Decimal(res_a["items"][1]["base_amount"]) == Decimal("-50.000000")
    assert Decimal(res_a["items"][1]["running_base"]) == Decimal("650.000000")

    # Reconciliation check: opening + sum(rows) == closing balance
    opening = Decimal(res_a["opening_base"])
    rows_net = sum(Decimal(item["base_amount"]) for item in res_a["items"])
    closing = Decimal(res_a["items"][-1]["running_base"])
    assert opening + rows_net == closing == Decimal("650.000000")


def test_account_transactions_running_balance_accumulates_across_pagination(api: Api) -> None:
    # Pre-period entry to set an opening balance: +1000
    api.post_je(
        "pag-pre",
        entry_date=f"{YEAR}-07-01",
        lines=[
            {"gl_account_id": api.accounts["6500"], "debit": "1000"},
            {"gl_account_id": api.accounts["2300"], "credit": "1000"},
        ],
    )

    # 5 in-period entries: +100, +200, +300, +400, +500
    for idx, amt in enumerate([100, 200, 300, 400, 500], start=1):
        api.post_je(
            f"pag-in-{idx}",
            entry_date=f"{YEAR}-08-0{idx}",
            lines=[
                {"gl_account_id": api.accounts["6500"], "debit": str(amt)},
                {"gl_account_id": api.accounts["2300"], "credit": str(amt)},
            ],
        )

    # Page 1: limit=2 (smaller than the 5 rows)
    p1 = api.client.get(
        f"/api/v1/gl/accounts/{api.accounts['6500']}/transactions",
        params={"date_from": f"{YEAR}-08-01", "date_to": f"{YEAR}-08-31", "limit": 2},
    ).json()
    assert Decimal(p1["opening_base"]) == Decimal("1000.000000")
    assert len(p1["items"]) == 2
    assert Decimal(p1["items"][0]["running_base"]) == Decimal("1100.000000")  # 1000 + 100
    assert Decimal(p1["items"][1]["running_base"]) == Decimal("1300.000000")  # 1100 + 200
    assert p1["next_cursor"] is not None

    # Page 2: limit=2 from p1["next_cursor"]
    p2 = api.client.get(
        f"/api/v1/gl/accounts/{api.accounts['6500']}/transactions",
        params={
            "date_from": f"{YEAR}-08-01",
            "date_to": f"{YEAR}-08-31",
            "cursor": p1["next_cursor"],
            "limit": 2,
        },
    ).json()
    assert Decimal(p2["opening_base"]) == Decimal("1000.000000")
    assert len(p2["items"]) == 2
    # Running balance must accumulate from opening + page 1, NOT reset to zero or page-opening!
    assert Decimal(p2["items"][0]["running_base"]) == Decimal("1600.000000")  # 1300 + 300
    assert Decimal(p2["items"][1]["running_base"]) == Decimal("2000.000000")  # 1600 + 400
    assert p2["next_cursor"] is not None

    # Page 3: limit=2 from p2["next_cursor"]
    p3 = api.client.get(
        f"/api/v1/gl/accounts/{api.accounts['6500']}/transactions",
        params={
            "date_from": f"{YEAR}-08-01",
            "date_to": f"{YEAR}-08-31",
            "cursor": p2["next_cursor"],
            "limit": 2,
        },
    ).json()
    assert len(p3["items"]) == 1
    assert Decimal(p3["items"][0]["running_base"]) == Decimal("2500.000000")  # 2000 + 500
    assert p3["next_cursor"] is None


# --- periods & year-end --------------------------------------------------------------------------


def test_year_end_close_and_reopen_via_api(api: Api) -> None:
    api.post_je(
        "sale",
        entry_date=f"{YEAR}-02-01",
        lines=[
            {"gl_account_id": api.accounts["2300"], "debit": "5000"},
            {"gl_account_id": api.accounts["4100"], "credit": "5000"},
        ],
    )
    year = api.client.get("/api/v1/gl/fiscal-years").json()[0]
    for period_no in range(1, 12):
        api.client.post(f"/api/v1/gl/periods/{api.periods[period_no]}/close")

    blocked = api.client.post(f"/api/v1/gl/fiscal-years/{year['id']}/reopen", json={"reason": "x"})
    assert blocked.status_code == 409 and blocked.json()["code"] == "year_not_locked"

    closed = api.client.post(f"/api/v1/gl/fiscal-years/{year['id']}/close")
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "locked" and closed.json()["closing_entry_id"] is not None
    tb = api.trial_balance()
    assert {row["code"]: row["net"] for row in tb["rows"] if row["net"] != "0.000000"} == {
        "2300": "5000.000000",
        "3200": "-5000.000000",
    }
    assert {p["status"] for p in api.client.get("/api/v1/gl/periods").json()} == {"locked"}
    assert api.post_je("late", entry_date=f"{YEAR}-06-01").json()["code"] == "period_not_open"

    reopened = api.client.post(
        f"/api/v1/gl/fiscal-years/{year['id']}/reopen", json={"reason": "audit adjustment"}
    )
    assert reopened.status_code == 200 and reopened.json()["status"] == "open"
    tb = api.trial_balance()
    assert {row["code"]: row["net"] for row in tb["rows"] if row["net"] != "0.000000"} == {
        "2300": "5000.000000",
        "4100": "-5000.000000",
    }
    assert_ledger_invariants(api.db, api.company_id)


def test_create_fiscal_year_via_api(api: Api) -> None:
    response = api.client.post(
        "/api/v1/gl/fiscal-years",
        json={
            "name": str(YEAR + 1),
            "start_date": f"{YEAR + 1}-01-01",
            "end_date": f"{YEAR + 1}-12-31",
        },
    )
    assert response.status_code == 201, response.text
    periods = api.client.get(
        "/api/v1/gl/periods", params={"fiscal_year_id": response.json()["id"]}
    ).json()
    assert len(periods) == 12 and {p["status"] for p in periods} == {"future"}
    overlap = api.client.post(
        "/api/v1/gl/fiscal-years",
        json={"name": "dup", "start_date": f"{YEAR}-06-01", "end_date": f"{YEAR + 1}-05-31"},
    )
    assert overlap.status_code == 409 and overlap.json()["code"] == "fiscal_year_overlap"


# --- chart of accounts ----------------------------------------------------------------------------


def test_chart_of_accounts_maintenance(api: Api, db: Session) -> None:
    listing = api.client.get("/api/v1/gl/accounts").json()
    codes = {row["code"] for row in listing}
    assert {"1000", "1120", "3200", "4100", "6500"} <= codes
    bank = next(row for row in listing if row["code"] == "1120")
    assert (bank["is_control"], bank["control_type"], bank["class"]) == (True, "bank", "asset")

    created = api.client.post(
        "/api/v1/gl/accounts",
        json={
            "code": "6550",
            "name": "Printing",
            "class": "expense",
            "parent_id": api.accounts["6000"],
        },
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]

    bad_parent = api.client.post(
        "/api/v1/gl/accounts",
        json={"code": "6560", "name": "x", "class": "expense", "parent_id": api.accounts["6500"]},
    )
    assert bad_parent.status_code == 409 and bad_parent.json()["code"] == "parent_must_be_header"
    wrong_class = api.client.post(
        "/api/v1/gl/accounts",
        json={"code": "6570", "name": "x", "class": "asset", "parent_id": api.accounts["6000"]},
    )
    assert wrong_class.json()["code"] == "parent_class_mismatch"
    duplicate = api.client.post(
        "/api/v1/gl/accounts", json={"code": "6550", "name": "dup", "class": "expense"}
    )
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "account_code_taken"

    # Rename Accounts (Appendix C): the code changes, history stays attached to the id.
    api.post_je(
        "print",
        lines=[
            {"gl_account_id": account_id, "debit": "10"},
            {"gl_account_id": api.accounts["2300"], "credit": "10"},
        ],
    )
    renamed = api.client.patch(f"/api/v1/gl/accounts/{account_id}", json={"code": "6555"})
    assert renamed.status_code == 200 and renamed.json()["code"] == "6555"
    tb = api.trial_balance()
    assert next(row for row in tb["rows"] if row["gl_account_id"] == account_id)["code"] == "6555"
    from app.models.audit import AuditLog

    assert "gl_account.renamed" in set(db.scalars(select(AuditLog.action)))

    header = api.client.patch(f"/api/v1/gl/accounts/{account_id}", json={"is_postable": False})
    assert header.status_code == 409 and header.json()["code"] == "account_has_postings"
    settings = api.client.get("/api/v1/gl/settings").json()
    assert settings["retained_earnings_account_id"] == api.accounts["3200"]
    assert (
        api.client.patch(
            f"/api/v1/gl/accounts/{api.accounts['3200']}", json={"is_active": False}
        ).status_code
        == 409
    )


def test_exchange_rates_endpoint(api: Api) -> None:
    rates = api.client.get("/api/v1/gl/exchange-rates").json()
    assert [(r["currency_id"], r["rate"]) for r in rates] == [
        (api.currencies["USD"], "1300.5000000000")
    ]
    duplicate = api.client.post(
        "/api/v1/gl/exchange-rates",
        json={"currency_id": api.currencies["USD"], "valid_from": f"{YEAR}-01-01", "rate": "1"},
    )
    assert duplicate.status_code == 409
    base = api.client.post(
        "/api/v1/gl/exchange-rates",
        json={"currency_id": api.currencies["RWF"], "valid_from": f"{YEAR}-01-01", "rate": "1"},
    )
    assert base.status_code == 409 and base.json()["code"] == "base_currency_rate"
    assert Decimal(rates[0]["rate"]) == Decimal("1300.5")


def test_branches_tax_codes_currencies_are_listable(api: Api) -> None:
    """Thin reads the document workspaces need for account/branch/tax/currency line cells."""
    branches = api.client.get("/api/v1/gl/branches").json()
    assert {b["code"] for b in branches} >= {"MAIN"}
    assert next(b for b in branches if b["code"] == "MAIN")["is_main"] is True

    tax_codes = api.client.get("/api/v1/gl/tax-codes").json()
    assert {t["code"] for t in tax_codes} >= {"VAT-OUT-18", "VAT-IN-18", "VAT-EXEMPT", "VAT-ZERO"}
    output = next(t for t in tax_codes if t["code"] == "VAT-OUT-18")
    assert output["nature"] == "output" and Decimal(str(output["rate_pct"])) == Decimal("18")

    currencies = api.client.get("/api/v1/gl/currencies").json()
    by_code = {c["code"]: c for c in currencies}
    assert by_code["RWF"]["is_base"] is True and by_code["RWF"]["decimal_places"] == 0
    assert by_code["USD"]["is_base"] is False and by_code["USD"]["decimal_places"] == 2

    # RLS still applies: a second tenant sees only its own rows.
    other = TestClient(api.client.app)
    other.post(
        "/api/v1/auth/signup",
        json={
            "company_name": "Other Co",
            "full_name": "Other Owner",
            "email": "other@example.com",
            "password": "correct horse battery staple",
        },
    )
    other_branches = other.get("/api/v1/gl/branches").json()
    assert {b["code"] for b in other_branches} >= {"MAIN"}
    assert {b["id"] for b in other_branches}.isdisjoint({b["id"] for b in branches})
