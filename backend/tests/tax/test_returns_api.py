"""The step-4 endpoints: who may call them, and what they actually render.

The service tests prove the behaviour; these prove the **surface**. Step 4 shipped fifteen
endpoints and, at first, not one test that opened any of them — which is precisely how a
revaluation detail came to render every partner name as an empty string. Rule 13's lesson, in
the small: a route that compiles is not a route that works, and the only way to know is to ask
it for data and assert a figure that came back.

The screens are steps 7 and 8, so each mutating endpoint here carries its `GAP` line in
`tests/test_api_has_a_caller.py`. That register is about callers; this file is about whether
there is anything worth calling.
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import set_actor, set_tenant
from app.models.currency import ExchangeRate
from app.models.gl import GLSettings
from app.models.partner import PartnerRole
from app.subledger import revaluation
from tests.conftest import make_tenant
from tests.kernel.conftest import YEAR
from tests.subledger.conftest import Subledger
from tests.subledger.test_documents import post_invoice

PASSWORD = "correct horse battery staple"
MARCH_END = date(YEAR, 3, 31)


@pytest.fixture
def signed_in(client: TestClient, db: Session, subledger: Subledger):  # noqa: ANN201
    """The subledger tenant, reached through the real login so the permissions are real."""
    set_tenant(db, subledger.company_id)
    set_actor(db, subledger.owner.id)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": subledger.owner.email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return subledger


def test_the_vat_return_preview_renders_its_sections_and_its_tie(
    client: TestClient, signed_in: Subledger
) -> None:
    """A preview with nothing in the month is still a shape a screen can lay out."""
    response = client.get(
        "/api/v1/tax/vat-returns/preview",
        params={"period_from": f"{YEAR}-03-01", "period_to": f"{YEAR}-03-31"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {"period_from", "sections", "codes", "late_entries", "ties"}
    assert "net_payable" in body["sections"]
    assert "high_water_entry_id" in body


def test_filing_a_return_needs_the_filing_permission(
    client: TestClient, signed_in: Subledger
) -> None:
    """`tax:vat_return_view` reads; it does not file. A viewer who could file would be the
    whole point of splitting the two."""
    response = client.post(
        "/api/v1/tax/vat-returns",
        json={"period_from": f"{YEAR}-03-01", "period_to": f"{YEAR}-03-31"},
        headers={"Idempotency-Key": "file-march-api"},
    )
    # The owner holds both, so this is the positive case; the negative is the permission
    # constant itself, asserted in `tests/fiscal/test_devices_api.py`.
    assert response.status_code == 201, response.text
    assert response.json()["number"].startswith("VATR-")


def test_the_annexes_come_back_as_csv_with_their_headers(
    client: TestClient, signed_in: Subledger
) -> None:
    for annex, first_column in (("sales", "customer_tin"), ("purchases", "supplier_tin")):
        response = client.get(
            f"/api/v1/tax/vat-returns/annexes/{annex}.csv",
            params={"period_from": f"{YEAR}-03-01", "period_to": f"{YEAR}-03-31"},
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        assert response.text.splitlines()[0].startswith(first_column)


@pytest.fixture
def a_revaluation(db: Session, signed_in: Subledger) -> int:
    """One posted run over an open USD receivable, so the detail has a line to render."""
    post_invoice(
        db,
        signed_in,
        role=PartnerRole.AR,
        amount=Decimal("47.20"),
        currency="USD",
        exchange_rate=Decimal(1320),
    )
    db.add(
        ExchangeRate(
            company_id=signed_in.company_id,
            currency_id=signed_in.ledger.cur("USD"),
            valid_from=MARCH_END,
            rate=Decimal(1350),
        )
    )
    db.flush()
    run = revaluation.post_revaluation(
        db,
        signed_in.company_id,
        revaluation_date=MARCH_END,
        role=revaluation.FxRevaluationRole.AR,
        actor=signed_in.owner,
    )
    db.commit()
    return run.id


def test_the_revaluation_detail_names_the_partner_each_line_drills_to(
    client: TestClient, signed_in: Subledger, a_revaluation: int
) -> None:
    """Decision 13: the lines carry the partner **so the report drills**.

    This is the assertion the blank name got past. It asserts the name, not the presence of a
    key, because `partner_name: ""` satisfies a schema and satisfies nothing else.
    """
    response = client.get(f"/api/v1/gl/fx-revaluations/{a_revaluation}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["number"].startswith("FXR-")
    assert len(body["lines"]) == 1

    line = body["lines"][0]
    assert line["partner_name"] == signed_in.customer.name
    assert line["partner_name"] != ""
    assert line["currency_code"] == "USD"
    assert Decimal(line["difference"]) == Decimal(1416)
    assert line["document_number"].startswith("INV-")


def test_the_revaluation_preview_renders_a_figure(
    client: TestClient, signed_in: Subledger, a_revaluation: int
) -> None:
    """A preview over a date already revalued still computes — the refusals are at posting."""
    response = client.get(
        "/api/v1/gl/fx-revaluations/preview",
        params={"revaluation_date": MARCH_END.isoformat(), "role": "ar"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lines"], "an open USD receivable must appear in the preview"
    assert body["lines"][0]["partner_name"] == signed_in.customer.name


def test_a_second_tenant_cannot_read_the_revaluation(
    client: TestClient, db: Session, signed_in: Subledger, a_revaluation: int
) -> None:
    """Tenancy on the surface, not only in the policy."""
    other = make_tenant(db, company_name="Kivu Traders Ltd", email="kivu@kivutraders.example")
    db.commit()
    response = client.post(
        "/api/v1/auth/login", json={"email": other.user.email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text

    response = client.get(f"/api/v1/gl/fx-revaluations/{a_revaluation}")
    assert response.status_code == 404, response.text


def test_the_settings_the_revaluation_needs_are_named_when_missing(
    client: TestClient, db: Session, signed_in: Subledger
) -> None:
    """A refusal that names the setting is what lets somebody fix it."""
    settings = db.scalars(
        select(GLSettings).where(GLSettings.company_id == signed_in.company_id)
    ).one()
    settings.ar_revaluation_account_id = None
    db.commit()

    post_invoice(
        db,
        signed_in,
        amount=Decimal("47.20"),
        currency="USD",
        exchange_rate=Decimal(1320),
    )
    db.add(
        ExchangeRate(
            company_id=signed_in.company_id,
            currency_id=signed_in.ledger.cur("USD"),
            valid_from=MARCH_END,
            rate=Decimal(1350),
        )
    )
    db.commit()

    response = client.post(
        "/api/v1/gl/fx-revaluations",
        json={"revaluation_date": MARCH_END.isoformat(), "role": "ar"},
        headers={"Idempotency-Key": "fxr-missing-setting"},
    )
    assert response.status_code == 422, response.text
    assert "ar_revaluation_account_id" in response.text


# --- Every endpoint answers at least once ---------------------------------------------------
#
# The eight below had no test at all when step 4 first proposed itself done. That is the
# systemic finding of this step, and it is worth stating plainly: the rule-14 register proves an
# endpoint has a **caller**, never that it **answers**. `GET /gl/fx-revaluations/{id}` satisfied
# the register, carried a `GAP` line naming the screen that would call it, and returned 500 to
# every request — because `lines` is a required field and nothing had ever asked it for one.


def test_the_vat_return_listing_and_detail_answer(
    client: TestClient, signed_in: Subledger
) -> None:
    created = client.post(
        "/api/v1/tax/vat-returns",
        json={"period_from": f"{YEAR}-03-01", "period_to": f"{YEAR}-03-31"},
        headers={"Idempotency-Key": "file-for-listing"},
    )
    assert created.status_code == 201, created.text
    return_id = created.json()["id"]

    listing = client.get("/api/v1/tax/vat-returns")
    assert listing.status_code == 200, listing.text
    assert [row["id"] for row in listing.json()] == [return_id]

    detail = client.get(f"/api/v1/tax/vat-returns/{return_id}")
    assert detail.status_code == 200, detail.text
    # `figures` is the snapshot as submitted — the reason a filed return has a detail at all.
    assert detail.json()["figures"]["sections"]["net_payable"] is not None

    missing = client.get("/api/v1/tax/vat-returns/999999")
    assert missing.status_code == 404, missing.text


def test_reversing_a_return_over_the_api_reopens_the_range(
    client: TestClient, signed_in: Subledger
) -> None:
    created = client.post(
        "/api/v1/tax/vat-returns",
        json={"period_from": f"{YEAR}-03-01", "period_to": f"{YEAR}-03-31"},
        headers={"Idempotency-Key": "file-for-reversal"},
    )
    return_id = created.json()["id"]

    reversed_ = client.post(
        f"/api/v1/tax/vat-returns/{return_id}/reverse",
        json={"reason": "Filed against the wrong month"},
    )
    assert reversed_.status_code == 200, reversed_.text
    assert reversed_.json()["status"] == "reversed"

    # The range is open again, which is what reversing a return is for.
    again = client.post(
        "/api/v1/tax/vat-returns",
        json={"period_from": f"{YEAR}-03-01", "period_to": f"{YEAR}-03-31"},
        headers={"Idempotency-Key": "file-after-reversal"},
    )
    assert again.status_code == 201, again.text


def test_the_revaluation_listing_and_reversal_answer(
    client: TestClient, signed_in: Subledger, a_revaluation: int
) -> None:
    listing = client.get("/api/v1/gl/fx-revaluations")
    assert listing.status_code == 200, listing.text
    assert [row["id"] for row in listing.json()] == [a_revaluation]

    reversed_ = client.post(
        f"/api/v1/gl/fx-revaluations/{a_revaluation}/reverse",
        json={"reason": "Rate corrected after the close"},
    )
    assert reversed_.status_code == 200, reversed_.text
    assert reversed_.json()["status"] == "reversed"
    assert reversed_.json()["reversal_entry_id"] is not None
