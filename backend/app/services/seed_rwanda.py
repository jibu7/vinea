"""Rwanda seed pack — what every new tenant gets at signup (Master Plan §5 P1/P2).

Ported from the v4 `init_db.py` defaults (Appendix A.2) with the Appendix C.1 correction:
**output** VAT is charged on sales, **input** VAT is paid on purchases. The chart of
accounts (`rw_sme_v1`) is a compact Sage-style SME chart drafted for Rwanda (§8 Q2 is still
open — the template is data, so swapping it is a seed change, not a schema change).
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.permissions import SYSTEM_ROLES
from app.kernel.periods import create_fiscal_year
from app.kernel.sequences import DEFAULT_PREFIXES, ensure_sequence
from app.models.company import Branch, Company
from app.models.currency import Currency
from app.models.fiscal import FiscalYear
from app.models.gl import AccountClass, ControlType, GLAccount, GLSettings
from app.models.membership import Role
from app.models.tax import TaxCode, TaxNature

COA_TEMPLATE = "rw_sme_v1"

BASE_CURRENCY = {"code": "RWF", "name": "Rwandan Franc", "symbol": "FRw", "decimal_places": 0}
SECONDARY_CURRENCIES = [
    {"code": "USD", "name": "US Dollar", "symbol": "$", "decimal_places": 2},
]

# Well-known account codes the kernel and seed rely on.
ACCOUNT_VAT_INPUT = "1400"
ACCOUNT_VAT_OUTPUT = "2200"
ACCOUNT_RETAINED_EARNINGS = "3200"

RWANDA_TAX_CODES = [
    {
        "code": "VAT-OUT-18",
        "name": "Output VAT 18% (Sales)",
        "nature": TaxNature.OUTPUT,
        "rate_pct": Decimal("18"),
        "account_code": ACCOUNT_VAT_OUTPUT,
    },
    {
        "code": "VAT-IN-18",
        "name": "Input VAT 18% (Purchases)",
        "nature": TaxNature.INPUT,
        "rate_pct": Decimal("18"),
        "account_code": ACCOUNT_VAT_INPUT,
    },
    {
        "code": "VAT-EXEMPT",
        "name": "Exempt",
        "nature": TaxNature.EXEMPT,
        "rate_pct": Decimal("0"),
        "account_code": None,
    },
    {
        "code": "VAT-ZERO",
        "name": "Zero-rated",
        "nature": TaxNature.ZERO_RATED,
        "rate_pct": Decimal("0"),
        "account_code": None,
    },
]

MAIN_BRANCH_CODE = "MAIN"

# (code, name, class, parent code, postable, control type)
_A, _L, _E, _I, _X = (
    AccountClass.ASSET,
    AccountClass.LIABILITY,
    AccountClass.EQUITY,
    AccountClass.INCOME,
    AccountClass.EXPENSE,
)
RW_SME_V1_ACCOUNTS: tuple[
    tuple[str, str, AccountClass, str | None, bool, ControlType | None], ...
] = (
    ("1000", "Assets", _A, None, False, None),
    ("1100", "Current Assets", _A, "1000", False, None),
    ("1110", "Cash on Hand", _A, "1100", True, ControlType.CASH),
    ("1120", "Bank Account", _A, "1100", True, ControlType.BANK),
    ("1200", "Accounts Receivable", _A, "1100", True, ControlType.AR),
    ("1300", "Inventory", _A, "1100", True, ControlType.INVENTORY),
    (ACCOUNT_VAT_INPUT, "VAT Input (Receivable)", _A, "1100", True, None),
    ("1500", "Prepayments & Deposits", _A, "1100", True, None),
    ("1600", "Non-current Assets", _A, "1000", False, None),
    ("1610", "Property, Plant & Equipment", _A, "1600", True, None),
    ("1620", "Accumulated Depreciation", _A, "1600", True, None),
    ("2000", "Liabilities", _L, None, False, None),
    ("2100", "Accounts Payable", _L, "2000", True, ControlType.AP),
    (ACCOUNT_VAT_OUTPUT, "VAT Output (Payable)", _L, "2000", True, None),
    ("2300", "Accrued Expenses", _L, "2000", True, None),
    ("2400", "PAYE & Social Security Payable", _L, "2000", True, None),
    ("2500", "Loans Payable", _L, "2000", True, None),
    ("3000", "Equity", _E, None, False, None),
    ("3100", "Share Capital", _E, "3000", True, None),
    (ACCOUNT_RETAINED_EARNINGS, "Retained Earnings", _E, "3000", True, None),
    ("3300", "Owner's Drawings", _E, "3000", True, None),
    ("4000", "Income", _I, None, False, None),
    ("4100", "Sales Revenue", _I, "4000", True, None),
    ("4200", "Service Revenue", _I, "4000", True, None),
    ("4300", "Other Income", _I, "4000", True, None),
    ("4400", "Foreign Exchange Gain", _I, "4000", True, None),
    ("5000", "Cost of Sales", _X, None, False, None),
    ("5100", "Cost of Goods Sold", _X, "5000", True, None),
    ("5200", "Inventory Adjustments", _X, "5000", True, None),
    ("5300", "Purchase Price Variance", _X, "5000", True, None),
    ("6000", "Operating Expenses", _X, None, False, None),
    ("6100", "Salaries & Wages", _X, "6000", True, None),
    ("6200", "Rent", _X, "6000", True, None),
    ("6300", "Utilities", _X, "6000", True, None),
    ("6400", "Telephone & Internet", _X, "6000", True, None),
    ("6500", "Office Supplies", _X, "6000", True, None),
    ("6600", "Transport & Fuel", _X, "6000", True, None),
    ("6700", "Bank Charges", _X, "6000", True, None),
    ("6800", "Depreciation", _X, "6000", True, None),
    ("6900", "Professional Fees", _X, "6000", True, None),
    ("6950", "Foreign Exchange Loss", _X, "6000", True, None),
    ("6990", "Sundry Expenses", _X, "6000", True, None),
)


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


def seed_chart_of_accounts(db: Session, company: Company) -> dict[str, GLAccount]:
    """Materialise `rw_sme_v1`; parents are flushed before children so `parent_id` resolves."""
    accounts: dict[str, GLAccount] = {}
    for code, name, class_, parent_code, postable, control in RW_SME_V1_ACCOUNTS:
        account = GLAccount(
            company_id=company.id,
            code=code,
            name=name,
            class_=class_,
            parent_id=accounts[parent_code].id if parent_code else None,
            is_postable=postable,
            is_control=control is not None,
            control_type=control,
            is_active=True,
        )
        db.add(account)
        db.flush()
        accounts[code] = account
    db.add(
        GLSettings(
            company_id=company.id,
            retained_earnings_account_id=accounts[ACCOUNT_RETAINED_EARNINGS].id,
        )
    )
    db.flush()
    return accounts


def seed_tax_codes(
    db: Session, company: Company, *, valid_from: date, accounts: dict[str, GLAccount] | None = None
) -> list[TaxCode]:
    accounts = accounts or {}
    codes = []
    for spec in RWANDA_TAX_CODES:
        account_code = spec["account_code"]
        account = accounts.get(account_code) if account_code else None
        codes.append(
            TaxCode(
                company_id=company.id,
                code=spec["code"],
                name=spec["name"],
                nature=spec["nature"],
                rate_pct=spec["rate_pct"],
                gl_account_id=account.id if account is not None else None,
                valid_from=valid_from,
                is_active=True,
            )
        )
    db.add_all(codes)
    return codes


def seed_fiscal_year(db: Session, company: Company, *, year: int) -> FiscalYear:
    """Rwanda's tax year is the calendar year (ADR-08); periods are calendar months, open
    up to today and `future` beyond."""
    return create_fiscal_year(
        db,
        company.id,
        name=str(year),
        start_date=date(year, 1, 1),
        end_date=date(year, 12, 31),
        open_through=date.today(),
    )


def seed_document_sequences(db: Session, company: Company) -> None:
    for doc_type, prefix in DEFAULT_PREFIXES.items():
        ensure_sequence(db, company.id, doc_type, prefix=prefix)


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
    accounts = seed_chart_of_accounts(db, company)
    seed_tax_codes(db, company, valid_from=date(fiscal_year, 1, 1), accounts=accounts)
    seed_fiscal_year(db, company, year=fiscal_year)
    seed_document_sequences(db, company)
    return seed_roles(db, company)
