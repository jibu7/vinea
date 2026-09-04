"""Rwanda seed pack — what every new tenant gets at signup (Master Plan §5 P1).

Ported from the v4 `init_db.py` defaults (Appendix A.2) with the Appendix C.1 correction:
**output** VAT is charged on sales, **input** VAT is paid on purchases.
"""

import calendar
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.permissions import SYSTEM_ROLES
from app.models.company import Branch, Company
from app.models.currency import Currency
from app.models.fiscal import AccountingPeriod, FiscalYear, PeriodStatus
from app.models.membership import Role
from app.models.tax import TaxCode, TaxNature

# The accounts themselves are materialised by the P2 ledger kernel; P1 only records
# which template the tenant was provisioned from.
COA_TEMPLATE = "rw_sme_v1"

BASE_CURRENCY = {"code": "RWF", "name": "Rwandan Franc", "symbol": "FRw", "decimal_places": 0}
SECONDARY_CURRENCIES = [
    {"code": "USD", "name": "US Dollar", "symbol": "$", "decimal_places": 2},
]

RWANDA_TAX_CODES = [
    {
        "code": "VAT-OUT-18",
        "name": "Output VAT 18% (Sales)",
        "nature": TaxNature.OUTPUT,
        "rate_pct": Decimal("18"),
    },
    {
        "code": "VAT-IN-18",
        "name": "Input VAT 18% (Purchases)",
        "nature": TaxNature.INPUT,
        "rate_pct": Decimal("18"),
    },
    {
        "code": "VAT-EXEMPT",
        "name": "Exempt",
        "nature": TaxNature.EXEMPT,
        "rate_pct": Decimal("0"),
    },
    {
        "code": "VAT-ZERO",
        "name": "Zero-rated",
        "nature": TaxNature.ZERO_RATED,
        "rate_pct": Decimal("0"),
    },
]

MAIN_BRANCH_CODE = "MAIN"


def seed_currencies(db: Session, company: Company) -> list[Currency]:
    currencies = [
        Currency(company_id=company.id, is_base=True, is_active=True, **BASE_CURRENCY),
        *(
            Currency(company_id=company.id, is_base=False, is_active=True, **spec)
            for spec in SECONDARY_CURRENCIES
        ),
    ]
    db.add_all(currencies)
    return currencies


def seed_branch(db: Session, company: Company) -> Branch:
    branch = Branch(
        company_id=company.id,
        code=MAIN_BRANCH_CODE,
        name="Head Office",
        is_main=True,
        is_active=True,
    )
    db.add(branch)
    return branch


def seed_tax_codes(db: Session, company: Company, *, valid_from: date) -> list[TaxCode]:
    codes = [
        TaxCode(company_id=company.id, valid_from=valid_from, is_active=True, **spec)
        for spec in RWANDA_TAX_CODES
    ]
    db.add_all(codes)
    return codes


def seed_fiscal_year(db: Session, company: Company, *, year: int) -> FiscalYear:
    """Rwanda's tax year is the calendar year (ADR-08); periods are calendar months."""
    fiscal_year = FiscalYear(
        company_id=company.id,
        name=str(year),
        start_date=date(year, 1, 1),
        end_date=date(year, 12, 31),
        status=PeriodStatus.OPEN,
    )
    db.add(fiscal_year)
    db.flush()

    today = date.today()
    for month in range(1, 13):
        last_day = calendar.monthrange(year, month)[1]
        start = date(year, month, 1)
        db.add(
            AccountingPeriod(
                company_id=company.id,
                fiscal_year_id=fiscal_year.id,
                period_no=month,
                name=f"{calendar.month_abbr[month]} {year}",
                start_date=start,
                end_date=date(year, month, last_day),
                status=PeriodStatus.OPEN if start <= today else PeriodStatus.FUTURE,
            )
        )
    return fiscal_year


def seed_roles(db: Session, company: Company) -> list[Role]:
    roles = [
        Role(
            company_id=company.id,
            name=str(spec["name"]),
            description=str(spec["description"]),
            permissions=list(spec["permissions"]),  # type: ignore[arg-type]
            is_system=True,
        )
        for spec in SYSTEM_ROLES
    ]
    db.add_all(roles)
    db.flush()
    return roles


def seed_company(db: Session, company: Company, *, year: int | None = None) -> list[Role]:
    """Apply the full seed pack. Returns the seeded roles (the owner needs one)."""
    fiscal_year = year or date.today().year
    seed_currencies(db, company)
    seed_branch(db, company)
    seed_tax_codes(db, company, valid_from=date(fiscal_year, 1, 1))
    seed_fiscal_year(db, company, year=fiscal_year)
    return seed_roles(db, company)
