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
from app.models.gl import AccountClass, ControlType, GLAccount, GLSettings, GLTransactionType
from app.models.membership import Role
from app.models.partner import (
    AgeingBasis,
    AgeingBucket,
    AgeingBucketSet,
    DueBasis,
    PaymentTerms,
)
from app.models.tax import TaxCode, TaxNature
from app.subledger.masters import DEFAULT_AGEING_BUCKETS

COA_TEMPLATE = "rw_sme_v1"

BASE_CURRENCY = {"code": "RWF", "name": "Rwandan Franc", "symbol": "FRw", "decimal_places": 0}
SECONDARY_CURRENCIES = [
    {"code": "USD", "name": "US Dollar", "symbol": "$", "decimal_places": 2},
]

# Well-known account codes the kernel and seed rely on.
ACCOUNT_VAT_INPUT = "1400"
ACCOUNT_VAT_OUTPUT = "2200"
ACCOUNT_RETAINED_EARNINGS = "3200"
# Sub-unit residues from per-line rounding get their own account, deliberately separate from
# the exchange differences. They are not exchange differences: a settlement can round without
# any rate movement at all, and pointing both keys at 6950 made every such residue reportable
# as an FX loss — indistinguishable, in the ledger, from a real one.
ACCOUNT_ROUNDING_DIFFERENCE = "6970"
# P4 AR/AP defaults.
ACCOUNT_AR_CONTROL = "1200"
ACCOUNT_AP_CONTROL = "2100"
ACCOUNT_POST_DATED_RECEIVABLE = "1250"
ACCOUNT_POST_DATED_PAYABLE = "2150"
ACCOUNT_FX_GAIN = "4400"
ACCOUNT_FX_LOSS = "6950"
ACCOUNT_DISCOUNT_RECEIVED = "4350"
ACCOUNT_DISCOUNT_GRANTED = "6960"
ACCOUNT_SALES_REVENUE = "4100"
ACCOUNT_BANK = "1120"
ACCOUNT_SUNDRY_EXPENSES = "6990"

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
    # Post-dated instruments are a real claim but not yet cash — never a control account.
    ("1250", "Post-dated Receivables", _A, "1100", True, None),
    ("1300", "Inventory", _A, "1100", True, ControlType.INVENTORY),
    (ACCOUNT_VAT_INPUT, "VAT Input (Receivable)", _A, "1100", True, None),
    ("1500", "Prepayments & Deposits", _A, "1100", True, None),
    ("1600", "Non-current Assets", _A, "1000", False, None),
    ("1610", "Property, Plant & Equipment", _A, "1600", True, None),
    ("1620", "Accumulated Depreciation", _A, "1600", True, None),
    ("2000", "Liabilities", _L, None, False, None),
    ("2100", "Accounts Payable", _L, "2000", True, ControlType.AP),
    ("2150", "Post-dated Payables", _L, "2000", True, None),
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
    ("4350", "Settlement Discount Received", _I, "4000", True, None),
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
    ("6960", "Settlement Discount Granted", _X, "6000", True, None),
    ("6970", "Rounding Difference", _X, "6000", True, None),
    ("6990", "Sundry Expenses", _X, "6000", True, None),
)

# (module, code, name, default account code) — the 4th link of the ADR-05 chain. Sign is a
# property of the (role, kind) matrix in `app.subledger.documents`, not of the type row.
SUBLEDGER_TRANSACTION_TYPES: tuple[tuple[str, str, str, str | None], ...] = (
    ("ar", "INV", "Customer invoice", ACCOUNT_SALES_REVENUE),
    ("ar", "CRN", "Customer credit note", ACCOUNT_SALES_REVENUE),
    ("ar", "RCT", "Customer receipt", ACCOUNT_BANK),
    ("ar", "JNL", "AR journal", None),
    ("ap", "INV", "Supplier invoice", ACCOUNT_SUNDRY_EXPENSES),
    ("ap", "DBN", "Return to supplier (debit note)", ACCOUNT_SUNDRY_EXPENSES),
    ("ap", "PMT", "Supplier payment", ACCOUNT_BANK),
    ("ap", "JNL", "AP journal", None),
)

# (code, name, basis, due days, discount %, discount days)
DEFAULT_PAYMENT_TERMS: tuple[tuple[str, str, DueBasis, int, Decimal, int], ...] = (
    ("COD", "Cash on delivery", DueBasis.DAYS_FROM_DOCUMENT_DATE, 0, Decimal(0), 0),
    ("NET30", "30 days from invoice", DueBasis.DAYS_FROM_DOCUMENT_DATE, 30, Decimal(0), 0),
    ("NET60", "60 days from invoice", DueBasis.DAYS_FROM_DOCUMENT_DATE, 60, Decimal(0), 0),
    ("EOM30", "30 days from end of month", DueBasis.DAYS_FROM_END_OF_MONTH, 30, Decimal(0), 0),
    ("2/10N30", "2% within 10 days, net 30", DueBasis.DAYS_FROM_DOCUMENT_DATE, 30, Decimal(2), 10),
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
            rounding_difference_account_id=accounts[ACCOUNT_ROUNDING_DIFFERENCE].id,
            realized_fx_gain_account_id=accounts[ACCOUNT_FX_GAIN].id,
            realized_fx_loss_account_id=accounts[ACCOUNT_FX_LOSS].id,
            settlement_discount_granted_account_id=accounts[ACCOUNT_DISCOUNT_GRANTED].id,
            settlement_discount_received_account_id=accounts[ACCOUNT_DISCOUNT_RECEIVED].id,
            post_dated_receivable_account_id=accounts[ACCOUNT_POST_DATED_RECEIVABLE].id,
            post_dated_payable_account_id=accounts[ACCOUNT_POST_DATED_PAYABLE].id,
            ar_control_account_id=accounts[ACCOUNT_AR_CONTROL].id,
            ap_control_account_id=accounts[ACCOUNT_AP_CONTROL].id,
        )
    )
    db.flush()
    return accounts


def seed_subledger_transaction_types(
    db: Session, company: Company, accounts: dict[str, GLAccount]
) -> list[GLTransactionType]:
    types = [
        GLTransactionType(
            company_id=company.id,
            module=module,
            code=code,
            name=name,
            default_gl_account_id=accounts[account_code].id if account_code else None,
            is_active=True,
        )
        for module, code, name, account_code in SUBLEDGER_TRANSACTION_TYPES
    ]
    db.add_all(types)
    db.flush()
    return types


def seed_payment_terms(db: Session, company: Company) -> list[PaymentTerms]:
    terms = [
        PaymentTerms(
            company_id=company.id,
            code=code,
            name=name,
            due_basis=basis,
            due_days=due_days,
            discount_percent=discount_pct,
            discount_days=discount_days,
            is_active=True,
        )
        for code, name, basis, due_days, discount_pct, discount_days in DEFAULT_PAYMENT_TERMS
    ]
    db.add_all(terms)
    db.flush()
    return terms


def seed_ageing_buckets(db: Session, company: Company) -> AgeingBucketSet:
    """The 30/60/90/120+ default set, aged on due date."""
    bucket_set = AgeingBucketSet(
        company_id=company.id,
        code="STD",
        name="Standard 30/60/90/120+",
        basis=AgeingBasis.DUE_DATE,
        is_default=True,
        is_active=True,
    )
    db.add(bucket_set)
    db.flush()
    db.add_all(
        [
            AgeingBucket(
                company_id=company.id,
                bucket_set_id=bucket_set.id,
                sequence=index,
                label=label,
                from_days=from_days,
                to_days=to_days,
            )
            for index, (label, from_days, to_days) in enumerate(DEFAULT_AGEING_BUCKETS)
        ]
    )
    db.flush()
    return bucket_set


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
    seed_subledger_transaction_types(db, company, accounts)
    seed_payment_terms(db, company)
    seed_ageing_buckets(db, company)
    seed_fiscal_year(db, company, year=fiscal_year)
    seed_document_sequences(db, company)
    return seed_roles(db, company)
