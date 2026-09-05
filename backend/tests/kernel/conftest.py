"""Kernel fixtures: one provisioned tenant with the Rwanda seed pack, a third currency (EUR),
dated rates, a second branch, two projects and every period of the current year open."""

import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import set_tenant
from app.kernel import posting
from app.kernel.events import LineSpec, ManualJournal
from app.models.company import Branch
from app.models.currency import Currency, ExchangeRate
from app.models.fiscal import AccountingPeriod, FiscalYear, PeriodStatus
from app.models.gl import GLAccount, Project
from app.models.journal import JournalEntry
from app.models.tax import TaxCode
from app.models.user import User
from tests.conftest import make_tenant

settings.register_profile(
    "ci",
    max_examples=2,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
settings.register_profile(
    "dev",
    max_examples=1,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))

YEAR = date.today().year
USD_RATE = Decimal("1300.5")
EUR_RATE = Decimal("1410.25")


@dataclass
class Ledger:
    company_id: int
    owner: User
    accounts: dict[str, GLAccount]
    currencies: dict[str, Currency]
    branches: dict[str, Branch]
    projects: dict[str, Project]
    tax_codes: dict[str, TaxCode]
    fiscal_year: FiscalYear
    periods: list[AccountingPeriod]

    def acct(self, code: str) -> int:
        return self.accounts[code].id

    def cur(self, code: str) -> int:
        return self.currencies[code].id

    @property
    def base(self) -> Currency:
        return self.currencies["RWF"]

    @property
    def main_branch(self) -> Branch:
        return self.branches["MAIN"]


@pytest.fixture
def ledger(db: Session) -> Ledger:
    tenant = make_tenant(db, company_name="Kigali Traders Ltd", email="owner@kigali.example")
    company_id = tenant.company.id
    set_tenant(db, company_id)

    db.add(
        Currency(
            company_id=company_id,
            code="EUR",
            name="Euro",
            symbol="€",
            decimal_places=2,
            is_base=False,
            is_active=True,
        )
    )
    db.add(Branch(company_id=company_id, code="MUS", name="Musanze", is_main=False, is_active=True))
    db.add_all(
        [
            Project(company_id=company_id, code="P-ALPHA", name="Alpha build", is_active=True),
            Project(company_id=company_id, code="P-BETA", name="Beta rollout", is_active=True),
        ]
    )
    db.flush()
    currencies = {c.code: c for c in db.scalars(select(Currency))}
    db.add_all(
        [
            ExchangeRate(
                company_id=company_id,
                currency_id=currencies["USD"].id,
                valid_from=date(YEAR, 1, 1),
                rate=USD_RATE,
            ),
            ExchangeRate(
                company_id=company_id,
                currency_id=currencies["EUR"].id,
                valid_from=date(YEAR, 1, 1),
                rate=EUR_RATE,
            ),
        ]
    )
    periods = list(db.scalars(select(AccountingPeriod).order_by(AccountingPeriod.period_no)))
    for period in periods:
        period.status = PeriodStatus.OPEN
    db.commit()

    return Ledger(
        company_id=company_id,
        owner=tenant.user,
        accounts={a.code: a for a in db.scalars(select(GLAccount))},
        currencies=currencies,
        branches={b.code: b for b in db.scalars(select(Branch))},
        projects={p.code: p for p in db.scalars(select(Project))},
        tax_codes={t.code: t for t in db.scalars(select(TaxCode))},
        fiscal_year=db.scalars(select(FiscalYear)).one(),
        periods=periods,
    )


def post_simple(
    db: Session,
    ledger: Ledger,
    *,
    debit: str,
    credit: str,
    amount: Decimal,
    on: date,
    currency: str = "RWF",
    branch_id: int | None = None,
    project_id: int | None = None,
    description: str = "test entry",
    idempotency_key: str | None = None,
) -> JournalEntry:
    """Two-line entry, same currency both sides (so it balances in base by construction)."""
    common = {
        "currency_id": ledger.cur(currency),
        "branch_id": branch_id,
        "project_id": project_id,
    }
    entry = posting.post(
        db,
        ManualJournal(
            entry_date=on,
            description=description,
            idempotency_key=idempotency_key,
            lines=(
                LineSpec(amount=amount, gl_account_id=ledger.acct(debit), **common),
                LineSpec(amount=-amount, gl_account_id=ledger.acct(credit), **common),
            ),
        ),
        company_id=ledger.company_id,
        actor=ledger.owner,
    )
    assert entry is not None
    return entry
