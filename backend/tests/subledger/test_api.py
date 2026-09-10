"""P4 API surface: masters CRUD, tenant isolation on the new tables, and the AR/AP
endpoints end to end through the HTTP layer."""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
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
ZERO_DECIMAL = Decimal(0)


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


@pytest.mark.parametrize("field", sorted(masters.AR_AP_DEFAULT_RULES))
def test_ar_ap_defaults_refuse_to_clear_any_required_key(api: Api, field: str) -> None:
    """All eight are required. A NULL here is not a loud early failure — it is a silent one
    that surfaces at whichever post next needs the account, long after the operator who
    cleared it has gone."""
    before = api.client.get(DEFAULTS_PATH).json()

    response = api.client.patch(DEFAULTS_PATH, json={field: None})

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "required_setting"
    assert body["field_errors"] == {field: ["required"]}
    assert api.client.get(DEFAULTS_PATH).json() == before
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


# --- The preview is a dry run: it consumes nothing ------------------------------------------

CONSUMABLE_TABLES = (
    "journal_entries",
    "journal_lines",
    "period_balances",
    "allocations",
    "allocation_lines",
    "partner_documents",
    "partner_document_lines",
    "jobs",
    "audit_log",
)


def _ledger_footprint(db: Session, company_id: int) -> dict:
    """Everything an allocation would consume if it ran: gapless numbers, rows, audit."""
    set_tenant(db, company_id)
    counts = {
        table: db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in CONSUMABLE_TABLES
    }
    sequences = {
        (row.doc_type, row.branch_id): row.next_number
        for row in db.execute(
            text("SELECT doc_type, branch_id, next_number FROM document_sequences")
        )
    }
    return {"counts": counts, "sequences": sequences}


def _fx_setup(api: Api) -> dict:
    accounts = {row["code"]: row["id"] for row in api.client.get("/api/v1/gl/accounts").json()}
    currencies = {row["code"]: row for row in api.client.get("/api/v1/gl/currencies").json()}
    usd = currencies["USD"]["id"]
    for valid_from, rate in (
        (MARCH.isoformat(), "1234.5678"),
        ((MARCH + timedelta(days=8)).isoformat(), "1301.1111"),
    ):
        api.client.post(
            "/api/v1/gl/exchange-rates",
            json={"currency_id": usd, "valid_from": valid_from, "rate": rate},
        )
    partner = api.client.post(
        "/api/v1/subledger/ar/partners",
        json={"name": "Residual Ltd", "customer_code": "CUST-RND", "currency_id": usd},
    ).json()
    invoice = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "rnd-inv"},
        json={
            "kind": "invoice",
            "partner_id": partner["id"],
            "document_date": MARCH.isoformat(),
            "currency_id": usd,
            "description": "Odd amount",
            "lines": [{"unit_price": "333.33", "gl_account_id": accounts["4100"]}],
        },
    )
    assert invoice.status_code == 201, invoice.text
    settle_on = MARCH + timedelta(days=8)
    receipt = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "rnd-rct"},
        json={
            "kind": "settlement",
            "partner_id": partner["id"],
            "document_date": settle_on.isoformat(),
            "currency_id": usd,
            "description": "Settled in full",
            "amount": "333.33",
            "cash_account_id": accounts["1120"],
            "instrument_type": "bank",
        },
    )
    assert receipt.status_code == 201, receipt.text
    return {
        "accounts": accounts,
        "body": {
            "partner_id": partner["id"],
            "allocation_date": settle_on.isoformat(),
            "pairs": [
                {
                    "debit_document_id": invoice.json()["id"],
                    "credit_document_id": receipt.json()["id"],
                    "amount": "333.33",
                }
            ],
        },
    }


def test_allocation_preview_consumes_nothing(api: Api, db: Session) -> None:
    """A preview must not take a document-sequence number, write an idempotency-bearing row,
    or insert a job. Anything it consumes is consumed whether or not the operator posts."""
    setup = _fx_setup(api)
    before = _ledger_footprint(db, api.company_id)

    for _ in range(3):  # repeated previews must be as free as one
        response = api.client.post("/api/v1/subledger/ar/allocations/preview", json=setup["body"])
        assert response.status_code == 200, response.text

    auto = api.client.post(
        "/api/v1/subledger/ar/allocations/auto",
        json={
            "partner_id": setup["body"]["partner_id"],
            "allocation_date": setup["body"]["allocation_date"],
        },
    )
    assert auto.status_code == 200, auto.text

    assert _ledger_footprint(db, api.company_id) == before


def test_allocation_postings_balance_in_base_so_no_rounding_line_is_possible(api: Api) -> None:
    """The review asked for a preview-equals-post case with a non-zero rounding residual.
    There isn't one, and this pins why rather than contriving one.

    `_post_postings` builds every allocation line *already in base currency* and balances the
    control leg against the FX amount itself, so the engine's `difference` is exactly zero and
    `_rounding_line` is never reached. Per-line rounding residues come from converting several
    foreign-currency lines independently — which is document posting, not allocation. The
    rounding path is covered where it can actually happen, in the test below.

    If allocation ever posts in document currency, this test fails, and the preview will need
    to account for a rounding line before that ships.
    """
    setup = _fx_setup(api)

    previewed = api.client.post(
        "/api/v1/subledger/ar/allocations/preview", json=setup["body"]
    ).json()
    posted = api.client.post(
        "/api/v1/subledger/ar/allocations",
        headers={"Idempotency-Key": "rnd-alc"},
        json=setup["body"],
    )
    assert posted.status_code == 201, posted.text

    entry = api.client.get(
        f"/api/v1/gl/journal-entries/{posted.json()['journal_entry_id']}"
    ).json()

    # Rates carrying four decimals against a zero-decimal base: if anything could leave a
    # residue it would be this, and it does not.
    assert sum(Decimal(line["base_amount"]) for line in entry["lines"]) == 0
    assert not any(line["is_rounding_line"] for line in entry["lines"])
    assert all(line["currency_id"] == entry["lines"][0]["currency_id"] for line in entry["lines"])

    actual = sorted(
        (line["gl_account_id"], Decimal(line["base_amount"])) for line in entry["lines"]
    )
    expected = sorted(
        (item["gl_account_id"], Decimal(item["base_amount"])) for item in previewed["postings"]
    )
    assert actual == expected


def test_a_multi_line_foreign_currency_invoice_posts_a_rounding_line(api: Api) -> None:
    """Where the residue actually comes from in the subledger.

    33.33 + 33.33 + 33.34 USD is exactly 100.00, but each line converts and rounds to whole
    RWF on its own while the control leg converts the document total, so the two sides miss by
    a sub-unit and the kernel absorbs it into the rounding account. This is the same shape as
    `tests/kernel/test_acceptance.py::test_4a_fx_rounding_residue_posts_as_an_explicit_rounding_line`,
    reached through a real AR invoice rather than a manual journal.

    (An *allocation* cannot do this — see the test above — which is why the allocation preview
    never has to account for a rounding line.)
    """
    accounts = {row["code"]: row["id"] for row in api.client.get("/api/v1/gl/accounts").json()}
    currencies = {row["code"]: row for row in api.client.get("/api/v1/gl/currencies").json()}
    usd = currencies["USD"]["id"]
    api.client.post(
        "/api/v1/gl/exchange-rates",
        json={"currency_id": usd, "valid_from": MARCH.isoformat(), "rate": "1234.5678"},
    )
    partner = api.client.post(
        "/api/v1/subledger/ar/partners",
        json={"name": "Residue Ltd", "customer_code": "CUST-RES", "currency_id": usd},
    ).json()

    invoice = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "res-inv"},
        json={
            "kind": "invoice",
            "partner_id": partner["id"],
            "document_date": MARCH.isoformat(),
            "currency_id": usd,
            "description": "Three thirds of a hundred dollars",
            "lines": [
                {"unit_price": "33.33", "gl_account_id": accounts["4100"]},
                {"unit_price": "33.33", "gl_account_id": accounts["4200"]},
                {"unit_price": "33.34", "gl_account_id": accounts["4300"]},
            ],
        },
    )
    assert invoice.status_code == 201, invoice.text

    entry = api.client.get(
        f"/api/v1/gl/journal-entries/{invoice.json()['journal_entry_id']}"
    ).json()
    rounding = [line for line in entry["lines"] if line["is_rounding_line"]]

    assert len(rounding) == 1, [
        (line["gl_account_id"], line["base_amount"], line["is_rounding_line"])
        for line in entry["lines"]
    ]
    # A residue, not a wrong number: bounded by half a minor unit per line.
    assert 0 < abs(Decimal(rounding[0]["base_amount"])) <= len(entry["lines"])
    # The residue is a base-currency artefact, so the line is booked in base — not in the
    # document's currency like the lines that produced it.
    base_currency_id = next(
        row["id"] for row in api.client.get("/api/v1/gl/currencies").json() if row["is_base"]
    )
    assert rounding[0]["currency_id"] == base_currency_id
    assert entry["lines"][0]["currency_id"] != base_currency_id
    # And it still foots, which is the whole point of absorbing it.
    assert sum(Decimal(line["base_amount"]) for line in entry["lines"]) == 0


# --- Full settlement across three receipts, with a rounding residual ------------------------


def test_full_settlement_by_three_receipts_leaves_no_base_residual(api: Api, db: Session) -> None:
    """The concrete case: USD 100.00 invoiced at 1234.5678 is 123,457 base, but three receipts
    of 33.33 / 33.33 / 33.34 at the same rate convert to 41,148 + 41,148 + 41,160 = 123,456.

    So the invoice's control leg and the receipts' control legs disagree by 1 base unit even
    though the document currency settles exactly. Allocating all three must leave the partner's
    control balance at exactly zero — not at 1 — and the open items fully closed. This asserts
    where the 1 went and names the account that absorbed it.
    """
    accounts = {row["code"]: row["id"] for row in api.client.get("/api/v1/gl/accounts").json()}
    by_id = {v: k for k, v in accounts.items()}
    currencies = {row["code"]: row for row in api.client.get("/api/v1/gl/currencies").json()}
    usd = currencies["USD"]["id"]
    api.client.post(
        "/api/v1/gl/exchange-rates",
        json={"currency_id": usd, "valid_from": MARCH.isoformat(), "rate": "1234.5678"},
    )
    partner = api.client.post(
        "/api/v1/subledger/ar/partners",
        json={"name": "Thirds Ltd", "customer_code": "CUST-THIRDS", "currency_id": usd},
    ).json()

    invoice = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "thirds-inv"},
        json={
            "kind": "invoice",
            "partner_id": partner["id"],
            "document_date": MARCH.isoformat(),
            "currency_id": usd,
            "description": "One hundred dollars",
            "lines": [{"unit_price": "100.00", "gl_account_id": accounts["4100"]}],
        },
    )
    assert invoice.status_code == 201, invoice.text
    invoice_id = invoice.json()["id"]

    receipts = []
    for index, amount in enumerate(("33.33", "33.33", "33.34")):
        receipt = api.client.post(
            "/api/v1/subledger/ar/documents",
            headers={"Idempotency-Key": f"thirds-rct-{index}"},
            json={
                "kind": "settlement",
                "partner_id": partner["id"],
                "document_date": MARCH.isoformat(),
                "currency_id": usd,
                "description": f"Receipt {index + 1} of 3",
                "amount": amount,
                "cash_account_id": accounts["1120"],
                "instrument_type": "bank",
            },
        )
        assert receipt.status_code == 201, receipt.text
        receipts.append(receipt.json())

    # The premise: the base amounts genuinely disagree by one.
    assert Decimal(invoice.json()["base_total_amount"]) == Decimal(123457)
    assert sum(Decimal(r["base_total_amount"]) for r in receipts) == Decimal(123456)

    # The Rwanda seed points `rounding_difference_account_id` and `realized_fx_loss_account_id`
    # at the same account (6950), which would make "rounding" and "FX" indistinguishable here.
    # Repoint rounding at 6990 so the assertion below actually identifies which key was read.
    settings = api.client.get("/api/v1/gl/settings").json()
    repointed = api.client.put(
        "/api/v1/gl/settings",
        json={
            "retained_earnings_account_id": settings["retained_earnings_account_id"],
            "rounding_difference_account_id": accounts["6990"],
        },
    )
    assert repointed.status_code == 200, repointed.text

    body = {
        "partner_id": partner["id"],
        "allocation_date": MARCH.isoformat(),
        "pairs": [
            {
                "debit_document_id": invoice_id,
                "credit_document_id": receipt["id"],
                "amount": receipt["total_amount"],
            }
            for receipt in receipts
        ],
    }
    previewed = api.client.post("/api/v1/subledger/ar/allocations/preview", json=body).json()
    posted = api.client.post(
        "/api/v1/subledger/ar/allocations", headers={"Idempotency-Key": "thirds-alc"}, json=body
    )
    assert posted.status_code == 201, posted.text

    # 1. Every document is fully closed in document currency...
    refreshed = api.client.get(f"/api/v1/subledger/ar/documents/{invoice_id}").json()
    assert Decimal(refreshed["open_amount"]) == 0
    for receipt in receipts:
        row = api.client.get(f"/api/v1/subledger/ar/documents/{receipt['id']}").json()
        assert Decimal(row["open_amount"]) == 0

    # 2. ...and the open items carry no base residual either.
    enquiry = api.client.get(
        f"/api/v1/subledger/ar/enquiry/{partner['id']}",
        params={"as_of": (MARCH + timedelta(days=1)).isoformat()},
    ).json()
    assert [Decimal(item["open_base_amount"]) for item in enquiry["open_items"]] == []
    assert Decimal(enquiry["balance_base"]) == 0

    # 3. The partner's control balance in base is exactly zero — the assertion that fails if
    #    the residual is left stranded on the control account.
    set_tenant(db, api.company_id)
    control_balance = db.execute(
        text(
            "SELECT COALESCE(SUM(base_amount), 0) FROM journal_lines "
            "WHERE company_id = :cid AND gl_account_id = :account AND partner_id = :partner"
        ),
        {"cid": api.company_id, "account": accounts["1200"], "partner": partner["id"]},
    ).scalar_one()
    assert Decimal(control_balance) == 0, f"control account left holding {control_balance}"

    # 4. Name the account that absorbed the 1, and what the ledger calls it. It is the
    #    **rounding-difference** account, not realized FX: both documents booked at the same
    #    rate, so there is no rate movement to realize — only 100.00 and 33.33+33.33+33.34
    #    rounding to different whole francs. `allocations.py` reads
    #    `rounding_account_id` for exactly this, and repointing it above proves which key.
    postings = {
        by_id[item["gl_account_id"]]: (Decimal(item["base_amount"]), item["description"])
        for item in previewed["postings"]
    }
    assert postings == {
        "1200": (Decimal(-1), "Allocation"),
        "6990": (Decimal(1), "Settlement rounding"),
    }, postings
    assert "6950" not in postings, "a same-rate residual must not be booked as realized FX"

    # And the preview said so before the post did.
    entry = api.client.get(
        f"/api/v1/gl/journal-entries/{posted.json()['journal_entry_id']}"
    ).json()
    assert sorted(
        (by_id[line["gl_account_id"]], Decimal(line["base_amount"]), line["description"])
        for line in entry["lines"]
    ) == sorted((code, amount, text_) for code, (amount, text_) in postings.items())


# --- The nine settings accounts are nine distinct accounts -----------------------------------

SETTINGS_ACCOUNT_KEYS = (
    "ar_control_account_id",
    "ap_control_account_id",
    "realized_fx_gain_account_id",
    "realized_fx_loss_account_id",
    "settlement_discount_granted_account_id",
    "settlement_discount_received_account_id",
    "post_dated_receivable_account_id",
    "post_dated_payable_account_id",
    "rounding_difference_account_id",
)


def test_the_eight_p4_keys_plus_rounding_resolve_to_nine_distinct_accounts(api: Api) -> None:
    """A rounding residue must never be reportable as an FX loss, which is what sharing 6950
    between `rounding_difference_account_id` and `realized_fx_loss_account_id` made it. Every
    settings account now stands on its own, so each figure can be read for what it is."""
    defaults = api.client.get("/api/v1/subledger/defaults").json()
    gl_settings = api.client.get("/api/v1/gl/settings").json()
    resolved = {
        key: (defaults.get(key) if key in defaults else gl_settings.get(key))
        for key in SETTINGS_ACCOUNT_KEYS
    }

    assert all(value is not None for value in resolved.values()), resolved
    assert len(set(resolved.values())) == 9, resolved

    accounts = {row["id"]: row["code"] for row in api.client.get("/api/v1/gl/accounts").json()}
    assert accounts[resolved["rounding_difference_account_id"]] == "6970"
    assert accounts[resolved["realized_fx_loss_account_id"]] == "6950"


def test_mixed_rate_settlement_splits_fx_from_the_rounding_residual(api: Api, db: Session) -> None:
    """Both accounts move, and each must take only its own part.

    A USD 100.00 invoice books at 1200 (120,000 base, exactly). Three receipts of
    33.33 / 33.33 / 33.34 settle it at 1234.5678: 41,148 + 41,148 + 41,160 = 123,456, while
    100.00 at that rate is 123,457. So two different things happened at once — the rate moved
    by 34.5678 on the full 100.00 (a real exchange difference of 3,457) and the receipts
    rounded 1 short of the whole. Realized FX must take the rate movement and nothing else;
    the rounding account must take the 1 and nothing else.
    """
    accounts = {row["code"]: row["id"] for row in api.client.get("/api/v1/gl/accounts").json()}
    by_id = {v: k for k, v in accounts.items()}
    currencies = {row["code"]: row for row in api.client.get("/api/v1/gl/currencies").json()}
    usd = currencies["USD"]["id"]
    settle_on = MARCH + timedelta(days=8)
    for valid_from, rate in ((MARCH.isoformat(), "1200"), (settle_on.isoformat(), "1234.5678")):
        api.client.post(
            "/api/v1/gl/exchange-rates",
            json={"currency_id": usd, "valid_from": valid_from, "rate": rate},
        )

    partner = api.client.post(
        "/api/v1/subledger/ar/partners",
        json={"name": "Mixed Rate Ltd", "customer_code": "CUST-MIX", "currency_id": usd},
    ).json()
    invoice = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "mix-inv"},
        json={
            "kind": "invoice",
            "partner_id": partner["id"],
            "document_date": MARCH.isoformat(),
            "currency_id": usd,
            "description": "Hundred dollars at 1200",
            "lines": [{"unit_price": "100.00", "gl_account_id": accounts["4100"]}],
        },
    )
    assert invoice.status_code == 201, invoice.text
    assert Decimal(invoice.json()["base_total_amount"]) == Decimal(120000)

    receipts = []
    for index, amount in enumerate(("33.33", "33.33", "33.34")):
        receipt = api.client.post(
            "/api/v1/subledger/ar/documents",
            headers={"Idempotency-Key": f"mix-rct-{index}"},
            json={
                "kind": "settlement",
                "partner_id": partner["id"],
                "document_date": settle_on.isoformat(),
                "currency_id": usd,
                "description": f"Receipt {index + 1} of 3",
                "amount": amount,
                "cash_account_id": accounts["1120"],
                "instrument_type": "bank",
            },
        )
        assert receipt.status_code == 201, receipt.text
        receipts.append(receipt.json())

    # The premise: receipts land 1 short of what the full amount converts to at their rate.
    assert sum(Decimal(r["base_total_amount"]) for r in receipts) == Decimal(123456)
    assert Decimal(100) * Decimal("1234.5678") == Decimal("123456.78")  # rounds to 123,457

    body = {
        "partner_id": partner["id"],
        "allocation_date": settle_on.isoformat(),
        "pairs": [
            {
                "debit_document_id": invoice.json()["id"],
                "credit_document_id": receipt["id"],
                "amount": receipt["total_amount"],
            }
            for receipt in receipts
        ],
    }
    previewed = api.client.post("/api/v1/subledger/ar/allocations/preview", json=body).json()
    posted = api.client.post(
        "/api/v1/subledger/ar/allocations", headers={"Idempotency-Key": "mix-alc"}, json=body
    )
    assert posted.status_code == 201, posted.text

    postings = {
        by_id[item["gl_account_id"]]: (Decimal(item["base_amount"]), item["description"])
        for item in previewed["postings"]
    }
    # 1. The control account is left at exactly zero for this partner.
    set_tenant(db, api.company_id)
    control_balance = db.execute(
        text(
            "SELECT COALESCE(SUM(base_amount), 0) FROM journal_lines "
            "WHERE company_id = :cid AND gl_account_id = :account AND partner_id = :partner"
        ),
        {"cid": api.company_id, "account": accounts["1200"], "partner": partner["id"]},
    ).scalar_one()
    assert Decimal(control_balance) == 0, f"control account left holding {control_balance}"

    # 2. Realized FX takes the booking-rate difference on the full amount, and only that:
    #    100.00 x (1234.5678 - 1200) = 3,456.78 -> 3,457.
    fx_codes = {"4400", "6950"} & postings.keys()
    assert len(fx_codes) == 1, postings
    fx_code = fx_codes.pop()
    assert abs(postings[fx_code][0]) == Decimal(3457), postings

    # 3. The rounding account takes the residual, and only that.
    assert abs(postings["6970"][0]) == Decimal(1), postings
    assert postings["6970"][1] == "Settlement rounding"

    # 4. Nothing else moved, and the preview said all of it before the post did.
    assert set(postings) == {"1200", fx_code, "6970"}, postings
    entry = api.client.get(
        f"/api/v1/gl/journal-entries/{posted.json()['journal_entry_id']}"
    ).json()
    assert sorted(
        (by_id[line["gl_account_id"]], Decimal(line["base_amount"])) for line in entry["lines"]
    ) == sorted((code, amount) for code, (amount, _) in postings.items())


def test_fx_trues_up_across_three_separate_allocations(api: Api, db: Session) -> None:
    """The case a single-allocation formula cannot get right: one invoice settled by three
    receipts on three days at three rates, in three separate allocations.

    After every one of them the running FX total must equal the rounded cumulative
    booking-rate difference on what has been settled so far — not just after the last. Each
    allocation posts the difference between that target and what is already on the document,
    so an earlier allocation's rounding is corrected by the next rather than compounding.
    """
    accounts = {row["code"]: row["id"] for row in api.client.get("/api/v1/gl/accounts").json()}
    by_id = {v: k for k, v in accounts.items()}
    currencies = {row["code"]: row for row in api.client.get("/api/v1/gl/currencies").json()}
    usd = currencies["USD"]["id"]

    invoice_date = MARCH
    schedule = [
        (MARCH + timedelta(days=1), "33.33", Decimal("1234.5678")),
        (MARCH + timedelta(days=2), "33.33", Decimal("1301.1111")),
        (MARCH + timedelta(days=9), "33.34", Decimal("1188.4321")),
    ]
    invoice_rate = Decimal("1200")
    api.client.post(
        "/api/v1/gl/exchange-rates",
        json={
            "currency_id": usd,
            "valid_from": invoice_date.isoformat(),
            "rate": str(invoice_rate),
        },
    )
    for when, _amount, rate in schedule:
        api.client.post(
            "/api/v1/gl/exchange-rates",
            json={"currency_id": usd, "valid_from": when.isoformat(), "rate": str(rate)},
        )

    partner = api.client.post(
        "/api/v1/subledger/ar/partners",
        json={"name": "Trued Up Ltd", "customer_code": "CUST-TRUE", "currency_id": usd},
    ).json()
    invoice = api.client.post(
        "/api/v1/subledger/ar/documents",
        headers={"Idempotency-Key": "true-inv"},
        json={
            "kind": "invoice",
            "partner_id": partner["id"],
            "document_date": invoice_date.isoformat(),
            "currency_id": usd,
            "description": "Settled in three instalments",
            "lines": [{"unit_price": "100.00", "gl_account_id": accounts["4100"]}],
        },
    )
    assert invoice.status_code == 201, invoice.text
    invoice_id = invoice.json()["id"]

    settled_product = ZERO_DECIMAL
    for index, (when, amount, rate) in enumerate(schedule):
        receipt = api.client.post(
            "/api/v1/subledger/ar/documents",
            headers={"Idempotency-Key": f"true-rct-{index}"},
            json={
                "kind": "settlement",
                "partner_id": partner["id"],
                "document_date": when.isoformat(),
                "currency_id": usd,
                "description": f"Instalment {index + 1}",
                "amount": amount,
                "cash_account_id": accounts["1120"],
                "instrument_type": "bank",
            },
        )
        assert receipt.status_code == 201, receipt.text

        posted = api.client.post(
            "/api/v1/subledger/ar/allocations",
            headers={"Idempotency-Key": f"true-alc-{index}"},
            json={
                "partner_id": partner["id"],
                "allocation_date": when.isoformat(),
                "pairs": [
                    {
                        "debit_document_id": invoice_id,
                        "credit_document_id": receipt.json()["id"],
                        "amount": amount,
                    }
                ],
            },
        )
        assert posted.status_code == 201, posted.text

        # After *this* allocation, the cumulative FX on the ledger must equal the rounded
        # cumulative rate difference on everything settled so far.
        settled_product += Decimal(amount) * (invoice_rate - rate)
        expected_cumulative = settled_product.quantize(Decimal(1), rounding=ROUND_HALF_UP)

        set_tenant(db, api.company_id)
        fx_posted = db.execute(
            text(
                "SELECT COALESCE(SUM(base_amount), 0) FROM journal_lines "
                "WHERE company_id = :cid AND gl_account_id IN (:gain, :loss)"
            ),
            {"cid": api.company_id, "gain": accounts["4400"], "loss": accounts["6950"]},
        ).scalar_one()
        # `settled_product` is (invoice rate - receipt rate), which is already the signed
        # base movement: a receipt at a higher rate is a gain and posts as a credit.
        assert Decimal(fx_posted) == expected_cumulative, (
            f"after instalment {index + 1}: FX is {fx_posted}, "
            f"cumulative rate difference is {expected_cumulative}"
        )

    # Fully settled, control at zero, and the rounding account carries only the conversion
    # residual — never any part of the rate movement.
    refreshed = api.client.get(f"/api/v1/subledger/ar/documents/{invoice_id}").json()
    assert Decimal(refreshed["open_amount"]) == 0

    set_tenant(db, api.company_id)
    control_balance = db.execute(
        text(
            "SELECT COALESCE(SUM(base_amount), 0) FROM journal_lines "
            "WHERE company_id = :cid AND gl_account_id = :account AND partner_id = :partner"
        ),
        {"cid": api.company_id, "account": accounts["1200"], "partner": partner["id"]},
    ).scalar_one()
    assert Decimal(control_balance) == 0, f"control account left holding {control_balance}"

    rounding = db.execute(
        text(
            "SELECT COALESCE(SUM(base_amount), 0) FROM journal_lines "
            "WHERE company_id = :cid AND gl_account_id = :account"
        ),
        {"cid": api.company_id, "account": accounts["6970"]},
    ).scalar_one()
    # Bounded by a minor unit per allocation: a residue, not a share of the rate movement.
    assert abs(Decimal(rounding)) <= len(schedule), f"rounding account holds {rounding}"
    assert by_id[accounts["6970"]] == "6970"


def test_ageing_endpoint_hides_zero_balances_by_default(api: Api) -> None:
    """The filter has to be the server's, not the table component's: the CSV export and the
    print layout are built from the rows the endpoint returns, so a client-side filter would
    leave both showing rows the screen does not. Path: `GET /ar/ageing`. It cannot see the
    bucket arithmetic, which `test_ageing_buckets_match_the_open_items` covers."""
    accounts = _accounts(api)
    owing = api.client.post(
        "/api/v1/subledger/ar/partners",
        json={"name": "Owing Ltd", "customer_code": "CUST-OWES"},
    ).json()
    netting = api.client.post(
        "/api/v1/subledger/ar/partners",
        json={"name": "Zero Sum Traders", "customer_code": "CUST-ZERO"},
    ).json()

    def document(kind: str, partner_id: int, amount: str, key: str) -> None:
        response = api.client.post(
            "/api/v1/subledger/ar/documents",
            headers={"Idempotency-Key": key},
            json={
                "kind": kind,
                "partner_id": partner_id,
                "document_date": MARCH.isoformat(),
                "description": "Reported",
                "lines": [{"unit_price": amount, "gl_account_id": accounts["4100"]["id"]}],
            },
        )
        assert response.status_code == 201, response.text

    document("invoice", owing["id"], "12000", "zb-inv-1")
    document("invoice", netting["id"], "9000", "zb-inv-2")
    document("credit_note", netting["id"], "9000", "zb-crn-1")

    as_of = {"as_of": (MARCH + timedelta(days=10)).isoformat()}
    default = api.client.get("/api/v1/subledger/ar/ageing", params=as_of)
    assert default.status_code == 200, default.text
    assert [row["partner_id"] for row in default.json()["rows"]] == [owing["id"]]

    everyone = api.client.get(
        "/api/v1/subledger/ar/ageing", params={**as_of, "include_zero_balance": "true"}
    ).json()
    assert {row["partner_id"] for row in everyone["rows"]} == {owing["id"], netting["id"]}
    # The toggle changes which rows are listed, never the reconciled total.
    assert Decimal(everyone["grand_total"]) == Decimal(default.json()["grand_total"])

    # The same endpoint feeds every partner picker, so its own default must stay inclusive.
    pickable = api.client.get("/api/v1/subledger/ar/partners").json()
    assert {row["id"] for row in pickable} >= {owing["id"], netting["id"]}
    listed = api.client.get(
        "/api/v1/subledger/ar/partners", params={"include_zero_balance": "false"}
    ).json()
    assert [row["id"] for row in listed] == [owing["id"]]
