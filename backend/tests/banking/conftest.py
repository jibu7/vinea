"""Fixtures for the banking suite.

One tenant, three bank accounts and the one shape the phase turns on: `BK-RWF` over `1120` in
the base currency, `CASH` over `1110`, and `BK-USD` over a `1121` created **through the chart
of accounts** so that its master row appears by the hook rather than by the fixture. That last
one is not convenience — "the hook fires on the ordinary create path" is the claim clause 6
exists to catch, and a fixture that inserted the row itself would test nothing about it.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.banking import accounts as accounts_service
from app.kernel import accounts as kernel_accounts
from app.models.banking import BankAccount
from app.models.gl import AccountClass, ControlType, GLAccount
from tests.kernel.conftest import Ledger, build_ledger

# Re-exported the way `tests/fiscal/conftest.py` re-exports it: several tests here are about
# what a tenant looks like *before* any bank account is renamed or given a currency, and they
# need the plain seeded ledger rather than this module's three-account one.
from tests.kernel.conftest import ledger as ledger  # noqa: PLC0414

SAMPLES = Path(__file__).parent / "samples"


def sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


@dataclass
class Banking:
    ledger: Ledger
    accounts: dict[str, BankAccount]

    @property
    def company_id(self) -> int:
        return self.ledger.company_id

    @property
    def owner(self):  # noqa: ANN201 - the ORM user, as `Ledger` hands it over
        return self.ledger.owner

    def bank(self, code: str) -> BankAccount:
        return self.accounts[code]


def build_banking(db: Session, **kwargs) -> Banking:
    ledger = build_ledger(db, **kwargs)
    usd_gl = kernel_accounts.create_account(
        db,
        ledger.company_id,
        kernel_accounts.AccountInput(
            code="1121",
            name="Bank Account USD",
            class_=AccountClass.ASSET,
            parent_id=ledger.acct("1100"),
            control_type=ControlType.BANK,
        ),
        actor=ledger.owner,
    )
    # The hook, exactly as `POST /gl/accounts` calls it — the fixture creates no row itself.
    accounts_service.ensure_row(db, usd_gl)
    db.flush()

    rows = {row.code: row for row in db.scalars(select(BankAccount))}
    accounts_service.update(
        db,
        rows["1120"],
        code="BK-RWF",
        name="Bank of Kigali current account",
        bank_name="Bank of Kigali",
        account_number="00040-0000123-45",
        actor=ledger.owner,
    )
    accounts_service.update(
        db,
        rows["1121"],
        code="BK-USD",
        name="Bank of Kigali USD account",
        currency_id=ledger.cur("USD"),
        actor=ledger.owner,
    )
    accounts_service.update(db, rows["1110"], code="CASH", actor=ledger.owner)
    db.commit()
    ledger.accounts["1121"] = db.get(GLAccount, usd_gl.id)
    return Banking(
        ledger=ledger,
        accounts={row.code: row for row in db.scalars(select(BankAccount))},
    )


@pytest.fixture
def banking(db: Session) -> Banking:
    return build_banking(db)
