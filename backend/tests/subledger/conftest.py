"""AR/AP fixtures: the kernel ledger plus a customer, a supplier and a set of terms."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.partner import (
    DueBasis,
    Partner,
    PartnerRole,
    PaymentTerms,
    TaxMode,
)
from app.subledger import masters
from tests.kernel.conftest import YEAR, Ledger
from tests.kernel.conftest import ledger as ledger  # noqa: PLC0414 - re-exported fixture

MARCH = date(YEAR, 3, 10)
APRIL = date(YEAR, 4, 10)


@dataclass
class Subledger:
    ledger: Ledger
    customer: Partner
    supplier: Partner
    net30: PaymentTerms
    discount_terms: PaymentTerms

    @property
    def company_id(self) -> int:
        return self.ledger.company_id

    @property
    def owner(self):  # noqa: ANN201
        return self.ledger.owner


@pytest.fixture
def subledger(db: Session, ledger: Ledger) -> Subledger:
    customer = masters.create_partner(
        db,
        ledger.company_id,
        masters.PartnerInput(name="Amahoro Retail Ltd", customer_code="CUST001"),
        actor=ledger.owner,
    )
    supplier = masters.create_partner(
        db,
        ledger.company_id,
        masters.PartnerInput(name="Rwanda Paper Supplies", supplier_code="SUPP001"),
        actor=ledger.owner,
    )
    terms = {
        row.code: row
        for row in db.scalars(
            select(PaymentTerms).where(PaymentTerms.company_id == ledger.company_id)
        )
    }
    for partner, role in ((customer, PartnerRole.AR), (supplier, PartnerRole.AP)):
        masters.upsert_role_settings(
            db,
            ledger.company_id,
            partner,
            role,
            masters.RoleSettingsInput(
                payment_terms_id=terms["NET30"].id,
                default_gl_account_id=ledger.acct("4100" if role == PartnerRole.AR else "6990"),
                tax_mode=TaxMode.EXCLUSIVE,
            ),
            actor=ledger.owner,
        )
    db.commit()
    return Subledger(
        ledger=ledger,
        customer=customer,
        supplier=supplier,
        net30=terms["NET30"],
        discount_terms=terms["2/10N30"],
    )


def set_credit_limit(
    db: Session, sub: Subledger, role: PartnerRole, limit: Decimal | None
) -> None:
    partner = sub.customer if role == PartnerRole.AR else sub.supplier
    settings = masters.get_role_settings(db, sub.company_id, partner.id, role)
    assert settings is not None
    settings.credit_limit = limit
    db.flush()


def set_terms(db: Session, sub: Subledger, role: PartnerRole, terms_id: int) -> None:
    partner = sub.customer if role == PartnerRole.AR else sub.supplier
    settings = masters.get_role_settings(db, sub.company_id, partner.id, role)
    assert settings is not None
    settings.payment_terms_id = terms_id
    db.flush()


__all__ = [
    "APRIL",
    "MARCH",
    "DueBasis",
    "Subledger",
    "ledger",
    "set_credit_limit",
    "set_terms",
    "subledger",
]
