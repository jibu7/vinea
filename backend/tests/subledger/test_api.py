"""P4 API surface: masters CRUD, tenant isolation on the new tables, and the AR/AP
endpoints end to end through the HTTP layer."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import set_tenant
from app.models.audit import AuditLog
from app.models.fiscal import AccountingPeriod, PeriodStatus
from app.models.partner import Partner
from app.subledger import masters
from tests.conftest import make_tenant
from tests.kernel.conftest import YEAR

MARCH = date(YEAR, 3, 10)
PASSWORD = "correct horse battery staple"


class Api:
    def __init__(self, client: TestClient, db: Session, company_id: int) -> None:
        self.client = client
        self.db = db
        self.company_id = company_id


@pytest.fixture
def api(client: TestClient, db: Session) -> Api:
    tenant = make_tenant(db, company_name="Kigali Traders Ltd", email="owner@kigali.example")
    set_tenant(db, tenant.company.id)
    for period in db.scalars(select(AccountingPeriod)):
        period.status = PeriodStatus.OPEN
    db.commit()
    response = client.post(
        "/api/v1/auth/login", json={"email": "owner@kigali.example", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return Api(client, db, tenant.company.id)


def test_partner_crud_and_rename(api: Api) -> None:
    created = api.client.post(
        "/api/v1/subledger/ar/partners",
        json={"name": "Amahoro Retail", "customer_code": "CUST001"},
    )
    assert created.status_code == 201, created.text
    partner = created.json()
    assert partner["is_customer"] is True and partner["is_supplier"] is False

    duplicate = api.client.post(
        "/api/v1/subledger/ar/partners",
        json={"name": "Clash", "customer_code": "CUST001"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "partner_code_taken"

    renamed = api.client.patch(
        f"/api/v1/subledger/ar/partners/{partner['id']}",
        json={"customer_code": "CUST100"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["customer_code"] == "CUST100"

    # The same entity can become a supplier without a second row.
    both = api.client.patch(
        f"/api/v1/subledger/ap/partners/{partner['id']}",
        json={"supplier_code": "SUPP100"},
    )
    assert both.status_code == 200
    assert both.json()["is_customer"] and both.json()["is_supplier"]

    listed = api.client.get("/api/v1/subledger/ap/partners").json()
    assert [row["supplier_code"] for row in listed] == ["SUPP100"]


def test_role_settings_round_trip(api: Api) -> None:
    partner = api.client.post(
        "/api/v1/subledger/ar/partners",
        json={"name": "Amahoro Retail", "customer_code": "CUST001"},
    ).json()
    terms = api.client.get("/api/v1/subledger/payment-terms").json()
    net30 = next(row for row in terms if row["code"] == "NET30")

    saved = api.client.put(
        f"/api/v1/subledger/ar/partners/{partner['id']}/settings",
        json={"payment_terms_id": net30["id"], "credit_limit": "500000", "tax_mode": "exclusive"},
    )
    assert saved.status_code == 200, saved.text
    assert Decimal(saved.json()["credit_limit"]) == Decimal(500000)

    # Sales reps are a customer-side concept.
    rejected = api.client.put(
        f"/api/v1/subledger/ap/partners/{partner['id']}/settings",
        json={"sales_rep_id": 1},
    )
    assert rejected.status_code in (409, 422)


def test_seeded_masters_are_present(api: Api) -> None:
    terms = api.client.get("/api/v1/subledger/payment-terms").json()
    assert {"COD", "NET30", "NET60", "EOM30", "2/10N30"} <= {row["code"] for row in terms}

    sets = api.client.get("/api/v1/subledger/ageing-bucket-sets").json()
    default = next(row for row in sets if row["is_default"])
    assert default["basis"] == "due_date"
    assert [bucket["label"] for bucket in default["buckets"]] == [
        "Current",
        "31 - 60",
        "61 - 90",
        "91 - 120",
        "120+",
    ]

    defaults = api.client.get("/api/v1/subledger/defaults").json()
    assert defaults["ar_control_account_id"] is not None
    assert defaults["ap_control_account_id"] is not None
    assert defaults["realized_fx_gain_account_id"] is not None
    assert defaults["post_dated_receivable_account_id"] is not None


def test_payment_terms_validation(api: Api) -> None:
    bad = api.client.post(
        "/api/v1/subledger/payment-terms",
        json={"code": "FIXED", "name": "15th", "due_basis": "fixed_day_of_month"},
    )
    assert bad.status_code == 409 and bad.json()["code"] == "due_day_required"

    good = api.client.post(
        "/api/v1/subledger/payment-terms",
        json={
            "code": "FIXED",
            "name": "15th of the month",
            "due_basis": "fixed_day_of_month",
            "due_day_of_month": 15,
        },
    )
    assert good.status_code == 201, good.text


def test_ageing_bucket_sets_must_be_contiguous(api: Api) -> None:
    gap = api.client.post(
        "/api/v1/subledger/ageing-bucket-sets",
        json={
            "code": "GAP",
            "name": "Gappy",
            "buckets": [
                {"label": "Current", "from_days": 0, "to_days": 30},
                {"label": "Late", "from_days": 45, "to_days": None},
            ],
        },
    )
    assert gap.status_code == 409 and gap.json()["code"] == "bucket_gap"


def test_document_post_allocate_and_report(api: Api) -> None:
    partner = api.client.post(
        "/api/v1/subledger/ar/partners",
        json={"name": "Amahoro Retail", "customer_code": "CUST001"},
    ).json()
    accounts = {
        row["code"]: row["id"] for row in api.client.get("/api/v1/gl/accounts").json()
    }

    invoice = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "inv-1"},
        json={
            "kind": "invoice",
            "partner_id": partner["id"],
            "document_date": MARCH.isoformat(),
            "description": "Consulting",
            "lines": [{"unit_price": "100000", "gl_account_id": accounts["4100"]}],
        },
    )
    assert invoice.status_code == 201, invoice.text
    invoice_body = invoice.json()
    assert invoice_body["number"].startswith("INV-")
    assert Decimal(invoice_body["open_amount"]) == Decimal(100000)

    replay = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "inv-1"},
        json={
            "kind": "invoice",
            "partner_id": partner["id"],
            "document_date": MARCH.isoformat(),
            "description": "Consulting",
            "lines": [{"unit_price": "100000", "gl_account_id": accounts["4100"]}],
        },
    )
    assert replay.status_code == 200 and replay.json()["id"] == invoice_body["id"]

    receipt = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "rct-1"},
        json={
            "kind": "settlement",
            "partner_id": partner["id"],
            "document_date": MARCH.isoformat(),
            "description": "Part payment",
            "amount": "40000",
            "cash_account_id": accounts["1120"],
            "instrument_type": "bank",
        },
    )
    assert receipt.status_code == 201, receipt.text

    pairs = {
        "partner_id": partner["id"],
        "allocation_date": MARCH.isoformat(),
        "pairs": [
            {
                "debit_document_id": invoice_body["id"],
                "credit_document_id": receipt.json()["id"],
                "amount": "40000",
            }
        ],
    }
    preview = api.client.post("/api/v1/subledger/ar/allocations/preview", json=pairs)
    assert preview.status_code == 200, preview.text
    assert Decimal(preview.json()["total_allocated"]) == Decimal(40000)
    assert preview.json()["postings"] == []  # no FX, no discount

    posted = api.client.post(
        "/api/v1/subledger/ar/allocations", headers={"Idempotency-Key": "alc-1"}, json=pairs
    )
    assert posted.status_code == 201, posted.text

    refreshed = api.client.get(
        f"/api/v1/subledger/ar/documents/{invoice_body['id']}"
    ).json()
    assert Decimal(refreshed["open_amount"]) == Decimal(60000)

    ageing = api.client.get(
        "/api/v1/subledger/ar/ageing", params={"as_of": (MARCH + timedelta(days=1)).isoformat()}
    )
    assert ageing.status_code == 200
    assert Decimal(ageing.json()["grand_total"]) == Decimal(60000)

    enquiry = api.client.get(
        f"/api/v1/subledger/ar/enquiry/{partner['id']}",
        params={"as_of": (MARCH + timedelta(days=1)).isoformat()},
    ).json()
    assert Decimal(enquiry["balance_base"]) == Decimal(60000)
    assert len(enquiry["entries"]) == 2

    listing = api.client.get("/api/v1/subledger/ar/documents").json()
    assert len(listing["items"]) == 2


def test_statement_job_downloads_a_pdf(api: Api) -> None:
    partner = api.client.post(
        "/api/v1/subledger/ar/partners",
        json={"name": "Amahoro Retail", "customer_code": "CUST001"},
    ).json()
    accounts = {
        row["code"]: row["id"] for row in api.client.get("/api/v1/gl/accounts").json()
    }
    api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "inv-stmt"},
        json={
            "kind": "invoice",
            "partner_id": partner["id"],
            "document_date": MARCH.isoformat(),
            "description": "Consulting",
            "lines": [{"unit_price": "7500", "gl_account_id": accounts["4100"]}],
        },
    )
    queued = api.client.post(
        "/api/v1/subledger/ar/statements",
        json={"partner_ids": [partner["id"]], "as_of": MARCH.isoformat()},
    )
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["id"]

    status = api.client.get(f"/api/v1/subledger/jobs/{job_id}").json()
    assert status["status"] == "succeeded", status.get("error")

    artifact = api.client.get(f"/api/v1/subledger/jobs/{job_id}/artifact")
    assert artifact.status_code == 200
    assert artifact.headers["content-type"] == "application/pdf"
    assert artifact.content[:4] == b"%PDF"


def test_partners_are_tenant_isolated(db: Session, two_tenants) -> None:  # noqa: ANN001
    first, second = two_tenants
    set_tenant(db, first.company.id)
    masters.create_partner(
        db,
        first.company.id,
        masters.PartnerInput(name="Only Ours", customer_code="C1"),
        actor=first.user,
    )
    db.commit()

    set_tenant(db, second.company.id)
    assert db.query(Partner).all() == []


def test_partner_history_records_every_code_rename(api: Api) -> None:
    partner = api.client.post(
        "/api/v1/subledger/ar/partners",
        json={"name": "Amahoro Retail", "customer_code": "CUST001"},
    ).json()
    api.client.patch(
        f"/api/v1/subledger/ar/partners/{partner['id']}",
        json={"customer_code": "CUST100"},
    )
    api.client.patch(
        f"/api/v1/subledger/ar/partners/{partner['id']}",
        json={"name": "Amahoro Retail Ltd"},
    )

    history = api.client.get(f"/api/v1/subledger/ar/partners/{partner['id']}/history")
    assert history.status_code == 200, history.text
    rows = history.json()

    # Newest first: the plain edit, the rename, then creation.
    assert [row["action"] for row in rows] == [
        "partner.updated",
        "partner.renamed",
        "partner.created",
    ]
    rename = rows[1]
    assert rename["before"]["customer_code"] == "CUST001"
    assert rename["after"]["customer_code"] == "CUST100"
    assert rename["actor_email"] == "owner@kigali.example"


def _invite_with_role(
    client: TestClient, db: Session, company_id: int, role_name: str
) -> TestClient:
    """A second session holding exactly one seeded role — no owner bypass."""
    from app.models.membership import Role
    from app.services import email as email_service

    set_tenant(db, company_id)
    role_id = db.scalars(
        select(Role.id).where(Role.company_id == company_id, Role.name == role_name)
    ).one()
    email = f"{role_name.lower().replace(' ', '.')}@kigali.example"
    invited = client.post("/api/v1/invitations", json={"email": email, "role_ids": [role_id]})
    assert invited.status_code in (200, 201), invited.text
    token = email_service.outbox[-1].context["token"]
    member = TestClient(client.app)
    accepted = member.post(
        "/api/v1/invitations/accept",
        json={"token": token, "full_name": role_name, "password": PASSWORD},
    )
    assert accepted.status_code in (200, 201), accepted.text
    return member


def test_ar_setup_manager_maintains_ar_transaction_types_without_gl_rights(
    api: Api, db: Session
) -> None:
    """AR/AP transaction types live in the GL table under a `module` discriminator, so the
    AR maintenance screen has to work for a Sales Manager, who holds no GL permission."""
    sales = _invite_with_role(api.client, db, api.company_id, "Sales Manager")

    listed = sales.get("/api/v1/gl/transaction-types", params={"module": "ar"})
    assert listed.status_code == 200, listed.text
    assert {row["module"] for row in listed.json()} == {"ar"}

    created = sales.post(
        "/api/v1/gl/transaction-types",
        json={"module": "ar", "code": "INTCH", "name": "Interest charge"},
    )
    assert created.status_code == 201, created.text
    patched = sales.patch(
        f"/api/v1/gl/transaction-types/{created.json()['id']}",
        json={"name": "Interest charged"},
    )
    assert patched.status_code == 200, patched.text

    # Neither the AP module, the GL module, nor the unscoped listing opens up.
    assert sales.get("/api/v1/gl/transaction-types", params={"module": "ap"}).status_code == 403
    assert sales.get("/api/v1/gl/transaction-types").status_code == 403
    assert (
        sales.post(
            "/api/v1/gl/transaction-types",
            json={"module": "gl", "code": "NOPE", "name": "Not allowed"},
        ).status_code
        == 403
    )


# --- AR/AP defaults validation (review item 3) --------------------------------------------

DEFAULTS_PATH = "/api/v1/subledger/defaults"


def _accounts(api: Api) -> dict[str, dict]:
    return {row["code"]: row for row in api.client.get("/api/v1/gl/accounts").json()}


@pytest.mark.parametrize(
    ("field", "account_code", "reason"),
    [
        # Control keys demand a control account of their own type — the AP control account
        # in the AR slot would reconcile the wrong subledger.
        ("ar_control_account_id", "2100", "not a ar control account"),
        ("ap_control_account_id", "1200", "not a ap control account"),
        ("ar_control_account_id", "4100", "not a ar control account"),
        # The four P&L keys refuse a balance-sheet account (1500 Prepayments and 2300
        # Accrued Expenses are both plain, postable, non-control)...
        ("realized_fx_gain_account_id", "1500", "must be expense or income"),
        ("realized_fx_loss_account_id", "2300", "must be expense or income"),
        ("settlement_discount_granted_account_id", "1500", "must be expense or income"),
        ("settlement_discount_received_account_id", "2300", "must be expense or income"),
        # ...and a control account, whichever class it is. 1120 is the bank control account,
        # so it is refused for being control-owned before its class is even considered.
        ("realized_fx_gain_account_id", "1200", "control account"),
        ("realized_fx_loss_account_id", "1120", "control account"),
        # The post-dated holding accounts sit on their own side of the balance sheet.
        ("post_dated_receivable_account_id", "2100", "control account"),
        ("post_dated_receivable_account_id", "4100", "must be asset"),
        ("post_dated_payable_account_id", "1500", "must be liability"),
    ],
)
def test_ar_ap_defaults_refuse_the_wrong_account_for_each_key(
    api: Api, field: str, account_code: str, reason: str
) -> None:
    accounts = _accounts(api)
    before = api.client.get(DEFAULTS_PATH).json()

    response = api.client.patch(DEFAULTS_PATH, json={field: accounts[account_code]["id"]})

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "invalid_gl_setting_account"
    assert body["field_errors"] == {field: [reason]}
    # A refused write changes nothing at all.
    assert api.client.get(DEFAULTS_PATH).json() == before


def test_ar_ap_defaults_refuse_an_inactive_or_header_account(api: Api) -> None:
    accounts = _accounts(api)
    header = next(row for row in accounts.values() if not row["is_postable"])

    header_response = api.client.patch(
        DEFAULTS_PATH, json={"realized_fx_gain_account_id": header["id"]}
    )
    assert header_response.status_code == 409
    assert header_response.json()["field_errors"] == {
        "realized_fx_gain_account_id": ["not postable"]
    }

    # 4400 is the seeded FX gain account; deactivate it and it stops qualifying.
    fx_gain = accounts["4400"]
    api.client.patch(f"/api/v1/gl/accounts/{fx_gain['id']}", json={"is_active": False})
    inactive_response = api.client.patch(
        DEFAULTS_PATH, json={"realized_fx_gain_account_id": fx_gain["id"]}
    )
    assert inactive_response.status_code == 409
    assert inactive_response.json()["field_errors"] == {"realized_fx_gain_account_id": ["inactive"]}


def test_ar_ap_defaults_accept_a_valid_change_and_audit_only_what_moved(
    api: Api, db: Session
) -> None:
    accounts = _accounts(api)
    before = api.client.get(DEFAULTS_PATH).json()
    # 4300 Other Income and 6990 Sundry Expenses are both valid P&L targets and both differ
    # from what the seed set, so this is a real change; ap_control is re-sent unchanged.
    payload = {
        "realized_fx_gain_account_id": accounts["4300"]["id"],
        "realized_fx_loss_account_id": accounts["6990"]["id"],
        "ap_control_account_id": before["ap_control_account_id"],
    }

    response = api.client.patch(DEFAULTS_PATH, json=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["realized_fx_gain_account_id"] == accounts["4300"]["id"]
    assert body["realized_fx_loss_account_id"] == accounts["6990"]["id"]
    # Untouched keys keep their seeded values.
    assert body["ar_control_account_id"] == before["ar_control_account_id"]

    set_tenant(db, api.company_id)
    record = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.company_id == api.company_id,
            AuditLog.action == "ar_ap_defaults.updated",
        )
        .order_by(AuditLog.at.desc())
    ).first()
    assert record is not None, "the defaults update must be audited"
    assert record.entity == "gl_settings"
    assert record.actor_email == "owner@kigali.example"
    # Only the two keys that actually moved are recorded, with both sides.
    assert set(record.after) == {"realized_fx_gain_account_id", "realized_fx_loss_account_id"}
    assert record.after["realized_fx_gain_account_id"] == accounts["4300"]["id"]
    assert record.before["realized_fx_gain_account_id"] == before["realized_fx_gain_account_id"]


def test_ar_ap_defaults_write_no_audit_record_when_nothing_changes(api: Api, db: Session) -> None:
    current = api.client.get(DEFAULTS_PATH).json()

    assert api.client.patch(DEFAULTS_PATH, json=current).status_code == 200

    set_tenant(db, api.company_id)
    assert (
        db.scalars(
            select(AuditLog).where(
                AuditLog.company_id == api.company_id,
                AuditLog.action == "ar_ap_defaults.updated",
            )
        ).first()
        is None
    )


def test_ar_ap_defaults_may_always_be_cleared(api: Api) -> None:
    """An unset default fails loudly the first time it is needed; a wrongly set one posts
    silently to the wrong account. Clearing therefore stays unvalidated."""
    response = api.client.patch(DEFAULTS_PATH, json={"realized_fx_gain_account_id": None})

    assert response.status_code == 200, response.text
    assert response.json()["realized_fx_gain_account_id"] is None


# --- The allocation preview is the posting minus the commit (step 7 contract) --------------


def test_allocation_preview_matches_what_posting_actually_writes(api: Api) -> None:
    """The allocation screen shows realized FX and discount *before* Post. That preview must
    come from the allocation service's own `prepare()` — the function `allocate()` then posts
    — not from a second implementation that can drift. This pins the equality on a case with
    both an FX movement and a settlement discount, where a re-implementation would show.
    """
    accounts = {row["code"]: row["id"] for row in api.client.get("/api/v1/gl/accounts").json()}
    currencies = {row["code"]: row for row in api.client.get("/api/v1/gl/currencies").json()}
    usd = currencies["USD"]["id"]
    terms = {
        row["code"]: row["id"]
        for row in api.client.get("/api/v1/subledger/payment-terms").json()
    }

    # Two rates: the invoice books at 1200, the receipt eight days later at 1250, so settling
    # in full realizes an FX gain on the base-currency difference.
    rates = (
        (MARCH.isoformat(), "1200"),
        ((MARCH + timedelta(days=8)).isoformat(), "1250"),
    )
    for valid_from, rate in rates:
        response = api.client.post(
            "/api/v1/gl/exchange-rates",
            json={"currency_id": usd, "valid_from": valid_from, "rate": rate},
        )
        assert response.status_code in (200, 201), response.text

    partner = api.client.post(
        "/api/v1/subledger/ar/partners",
        json={"name": "Kivu Exports", "customer_code": "CUST-FX", "currency_id": usd},
    ).json()
    api.client.put(
        f"/api/v1/subledger/ar/partners/{partner['id']}/settings",
        json={"payment_terms_id": terms["2/10N30"], "tax_mode": "exclusive"},
    )

    invoice = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "fx-inv"},
        json={
            "kind": "invoice",
            "partner_id": partner["id"],
            "document_date": MARCH.isoformat(),
            "currency_id": usd,
            "description": "Export consulting",
            "payment_terms_id": terms["2/10N30"],
            "lines": [{"unit_price": "1000", "gl_account_id": accounts["4100"]}],
        },
    )
    assert invoice.status_code == 201, invoice.text

    settle_on = MARCH + timedelta(days=8)
    receipt = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "fx-rct"},
        json={
            "kind": "settlement",
            "partner_id": partner["id"],
            "document_date": settle_on.isoformat(),
            "currency_id": usd,
            "description": "Settled within the discount window",
            "amount": "980",
            "cash_account_id": accounts["1120"],
            "instrument_type": "bank",
        },
    )
    assert receipt.status_code == 201, receipt.text

    body = {
        "partner_id": partner["id"],
        "allocation_date": settle_on.isoformat(),
        "pairs": [
            {
                "debit_document_id": invoice.json()["id"],
                "credit_document_id": receipt.json()["id"],
                "amount": "980",
                "discount_amount": "20",
            }
        ],
    }

    preview = api.client.post("/api/v1/subledger/ar/allocations/preview", json=body)
    assert preview.status_code == 200, preview.text
    previewed = preview.json()
    # Anti-vacuity: this case must actually produce FX and discount postings, or the equality
    # below would hold trivially for an empty list.
    assert Decimal(previewed["total_discount"]) == Decimal(20)
    assert Decimal(previewed["total_fx_base"]) != 0
    assert len(previewed["postings"]) >= 2

    posted = api.client.post(
        "/api/v1/subledger/ar/allocations", headers={"Idempotency-Key": "fx-alc"}, json=body
    )
    assert posted.status_code == 201, posted.text

    entry = api.client.get(
        f"/api/v1/gl/journal-entries/{posted.json()['journal_entry_id']}"
    ).json()
    # `preview.postings` is the complete set of lines the allocation writes — the control
    # leg included — so compare the whole entry, unfiltered. Filtering either side would
    # weaken exactly the claim being made.
    actual = sorted(
        (line["gl_account_id"], line["description"], Decimal(line["base_amount"]))
        for line in entry["lines"]
    )
    expected = sorted(
        (item["gl_account_id"], item["description"], Decimal(item["base_amount"]))
        for item in previewed["postings"]
    )
    assert actual == expected

    # And the preview left nothing behind: one allocation exists, the one that was posted.
    assert len(api.client.get("/api/v1/subledger/ar/allocations").json()) == 1
