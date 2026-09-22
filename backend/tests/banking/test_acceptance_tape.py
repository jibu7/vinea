"""The P8 acceptance tape (step 5), row by row, in the RWF-base company.

**Every expected value below is a literal worked by hand**, never a figure computed by the code
under test. Where a row's arithmetic is not obvious the working is in a comment beside it. That is
the whole point of a tape: a test that recomputed the expectation the way the service does would
pass just as happily over a service that had been wrong from the start.

The rows are one sequence, not nineteen independent cases, because the interesting values are the
ones that depend on history — row 8's difference of +37 500 exists only because row 7 imported a
fee and a deposit the ledger lacked, row 12's gain of 14 850 only because rows 5 and 11 left USD
495.00 on the account at 1 320, and row 17's outstanding of −90 000 only because rows 6 and 13
wrote two cheques nobody has presented. So it is one test. When it fails, the row number in the
assertion message is the place to look.

**After every row**: `assert_ledger_invariants`, `assert_subledger_invariants` and
`assert_bank_invariants`. Not `assert_fiscal_invariants` — this company has **no EBM device**, so
nothing here fiscalizes and that suite has nothing to say. The prompt asks for that to be stated
rather than left as a silent omission, and this is the statement.

**The year is pinned to the samples, not to the clock.** `TAPE_YEAR = 2026` because
`tests/banking/samples/*.csv` are dated 2026, and the tape ensures a fiscal year for it rather
than taking `date.today().year` the way `tests/kernel/conftest.py` does. Without that, on 1
January the ledger entries would move to the new year while the statement files stayed in 2026,
every auto-match in rows 7 to 14 would quietly stop matching, and a tape of hand-worked literals
would start reporting a different answer depending on the date it was run — which is the one thing
an acceptance tape may not do.

The setup is the plan's: RWF base (0 dp), USD active with dated rates **1 320 from 1 Sep** and
**1 350 from 30 Sep**; periods Aug-Nov open; accounts `1110` (cash), `1120` (bank), `1121` (bank,
USD, created through the chart of accounts so its `bank_accounts` row appears **by the hook**),
`1130`, `1200`, `2100`, `2190`, `3400`, `4300`, `4350`, `4410`, `6700`, `6950`, `6955`; bank
accounts `BK-RWF` → 1120, `BK-USD` → 1121, `CASH` → 1110; customers C1 and C2; suppliers S1 (bank
details), S2 (bank details, terms `2/10 net 30`) and S3 (no bank details); a rule on `BK-RWF`
matching `ACCOUNT FEE` → payment to `6700`. Posted 1 Sep: `INV-1` C1 118 000, `INV-2` C2 59 000,
`INV-3` C1 USD 500.00 @ 1 320, `INV-4` C2 USD 200.00 @ 1 320, `SIN-1` S1 236 000, `SIN-2` S2
100 000, `SIN-3` S3 50 000, `SIN-4` S1 USD 100.00 @ 1 320.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.banking import accounts as bank_accounts
from app.banking import matching
from app.banking import payment_runs as payment_run_service
from app.banking import reconciliation as reconciliation_service
from app.banking import reports as reports_service
from app.banking import statements as statements_service
from app.core.errors import AppError
from app.kernel import accounts as kernel_accounts
from app.kernel import periods as kernel_periods
from app.kernel import posting
from app.kernel.errors import PostingError
from app.kernel.events import CashbookEntry, CashbookKind, CashbookLineSpec
from app.models.banking import BankAccount, BankMatchKind, BankMatchRule, PaymentRun
from app.models.currency import Currency, ExchangeRate
from app.models.fiscal import AccountingPeriod, FiscalYear, PeriodStatus
from app.models.fiscalization import FxRevaluationRole
from app.models.gl import AccountClass, ControlType, GLAccount
from app.models.job import Job
from app.models.journal import JournalEntry, JournalLine, JournalStatus
from app.models.partner import Partner, PartnerRole, PaymentTerms, TaxMode
from app.models.subledger import (
    Allocation,
    DocumentKind,
    InstrumentType,
    PartnerDocument,
)
from app.models.user import User
from app.subledger import allocations as allocation_service
from app.subledger import documents as documents_service
from app.subledger import masters as partner_masters
from app.subledger import revaluation as revaluation_service
from app.subledger.openitems import recompute_open_amount
from tests.banking.conftest import sample
from tests.banking.invariants import assert_bank_invariants
from tests.conftest import make_tenant
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.invariants import assert_subledger_invariants

#: **Pinned to `tests/banking/samples/*.csv`**, which are dated 2026. See the module docstring.
TAPE_YEAR = 2026

AUG_31 = date(TAPE_YEAR, 8, 31)
SEP_1 = date(TAPE_YEAR, 9, 1)
SEP_3 = date(TAPE_YEAR, 9, 3)
SEP_5 = date(TAPE_YEAR, 9, 5)
SEP_10 = date(TAPE_YEAR, 9, 10)
SEP_12 = date(TAPE_YEAR, 9, 12)
SEP_15 = date(TAPE_YEAR, 9, 15)
SEP_20 = date(TAPE_YEAR, 9, 20)
SEP_26 = date(TAPE_YEAR, 9, 26)
SEP_28 = date(TAPE_YEAR, 9, 28)
SEP_30 = date(TAPE_YEAR, 9, 30)
OCT_1 = date(TAPE_YEAR, 10, 1)
OCT_2 = date(TAPE_YEAR, 10, 2)
OCT_15 = date(TAPE_YEAR, 10, 15)
OCT_31 = date(TAPE_YEAR, 10, 31)
NOV_2 = date(TAPE_YEAR, 11, 2)
NOV_3 = date(TAPE_YEAR, 11, 3)

BOOKING_RATE = Decimal(1320)
MONTH_END_RATE = Decimal(1350)
#: The bank's own rate on row 5's inward transfer, keyed rather than looked up.
BANK_RATE = Decimal(1300)


# --- The tape's own bookkeeping ------------------------------------------------------------------
#
# Expected-vs-actual for every row is collected as it goes and printed at the end, because the
# step-5 report *is* that table. Collecting it costs nothing and means the report quotes the run
# rather than a transcription of it. The shape is P7's, deliberately.

_TABLE: list[tuple[str, str, str, str, str]] = []


def _expect(row: str, label: str, expected, actual) -> None:  # noqa: ANN001
    ok = expected == actual
    _TABLE.append((row, label, _shown(expected), _shown(actual), "ok" if ok else "MISMATCH"))
    assert ok, f"tape row {row}: {label} expected {expected}, got {actual}"


def _shown(value) -> str:  # noqa: ANN001
    text = str(value)
    return text if len(text) <= 58 else f"{text[:55]}..."


@pytest.fixture(scope="module", autouse=True)
def _print_table():  # noqa: ANN202
    yield
    if not _TABLE:
        return
    width = max(len(entry[1]) for entry in _TABLE)
    print("\n[p8 tape] expected vs actual")
    for row, label, expected, actual, status in _TABLE:
        print(
            f"  {row:<4} {label:<{width}}  expected {expected:>20}  "
            f"actual {actual:>20}  {status}"
        )


# --- The setup ----------------------------------------------------------------------------------


@dataclass
class Tape:
    company_id: int
    owner: User
    accounts: dict[str, GLAccount]
    banks: dict[str, BankAccount]
    currencies: dict[str, Currency]
    partners: dict[str, Partner]
    documents: dict[str, object] = field(default_factory=dict)

    def acct(self, code: str) -> int:
        return self.accounts[code].id

    def cur(self, code: str) -> int:
        return self.currencies[code].id

    def bank(self, code: str) -> BankAccount:
        return self.banks[code]


def _ensure_tape_year(db: Session, company_id: int) -> None:
    """A fiscal year covering `TAPE_YEAR`, with August to November open.

    `provision_tenant` seeds the year of the wall clock. When that *is* `TAPE_YEAR` the periods
    already exist and only need opening; when it is not, the year is created — and it cannot
    overlap the seeded one, because they are different calendar years. Either way the tape lands
    on periods it chose rather than on periods the date chose for it.
    """
    covering = db.scalar(
        select(FiscalYear).where(
            FiscalYear.company_id == company_id,
            FiscalYear.start_date <= SEP_1,
            FiscalYear.end_date >= SEP_1,
        )
    )
    if covering is None:
        kernel_periods.create_fiscal_year(
            db,
            company_id,
            name=str(TAPE_YEAR),
            start_date=date(TAPE_YEAR, 1, 1),
            end_date=date(TAPE_YEAR, 12, 31),
            open_through=date(TAPE_YEAR, 12, 1),
        )
        db.flush()
    for period in db.scalars(
        select(AccountingPeriod).where(
            AccountingPeriod.company_id == company_id,
            AccountingPeriod.start_date >= date(TAPE_YEAR, 8, 1),
            AccountingPeriod.end_date <= date(TAPE_YEAR, 11, 30),
        )
    ):
        period.status = PeriodStatus.OPEN
    db.flush()


def _customer(db: Session, tape: Tape, *, name: str, code: str) -> Partner:
    partner = partner_masters.create_partner(
        db, tape.company_id, partner_masters.PartnerInput(name=name, customer_code=code),
        actor=tape.owner,
    )
    partner_masters.upsert_role_settings(
        db,
        tape.company_id,
        partner,
        PartnerRole.AR,
        partner_masters.RoleSettingsInput(
            default_gl_account_id=tape.acct("4100"), tax_mode=TaxMode.EXCLUSIVE
        ),
        actor=tape.owner,
    )
    return partner


def _supplier(
    db: Session,
    tape: Tape,
    *,
    name: str,
    code: str,
    bank_details: bool,
    terms_code: str | None = None,
) -> Partner:
    partner = partner_masters.create_partner(
        db, tape.company_id, partner_masters.PartnerInput(name=name, supplier_code=code),
        actor=tape.owner,
    )
    terms_id = None
    if terms_code is not None:
        terms_id = db.scalars(
            select(PaymentTerms).where(
                PaymentTerms.company_id == tape.company_id, PaymentTerms.code == terms_code
            )
        ).one().id
    partner_masters.upsert_role_settings(
        db,
        tape.company_id,
        partner,
        PartnerRole.AP,
        partner_masters.RoleSettingsInput(
            default_gl_account_id=tape.acct("6990"),
            tax_mode=TaxMode.EXCLUSIVE,
            payment_terms_id=terms_id,
        ),
        actor=tape.owner,
    )
    if bank_details:
        partner_masters.update_partner(
            db,
            partner,
            bank_name="Bank of Kigali",
            bank_account_number=f"00040-{code}-01",
            bank_account_holder=name,
            actor=tape.owner,
        )
    return partner


@pytest.fixture
def tape(db: Session) -> Tape:
    tenant = make_tenant(
        db, company_name="Vinea Tape Ltd", email="tape@vinea.example"
    )
    company_id = tenant.company.id
    from app.db import set_tenant

    set_tenant(db, company_id)
    owner = tenant.user
    _ensure_tape_year(db, company_id)

    currencies = {row.code: row for row in db.scalars(select(Currency))}
    _expect("0", "base currency decimals", 0, currencies["RWF"].decimal_places)
    # 1 320 from 1 September and 1 350 from 30 September — the two dated rates every FX figure
    # in the tape comes from.
    db.add_all(
        [
            ExchangeRate(
                company_id=company_id,
                currency_id=currencies["USD"].id,
                valid_from=SEP_1,
                rate=BOOKING_RATE,
            ),
            ExchangeRate(
                company_id=company_id,
                currency_id=currencies["USD"].id,
                valid_from=SEP_30,
                rate=MONTH_END_RATE,
            ),
        ]
    )
    db.flush()

    accounts = {row.code: row for row in db.scalars(select(GLAccount))}
    tape_state = Tape(
        company_id=company_id,
        owner=owner,
        accounts=accounts,
        banks={},
        currencies=currencies,
        partners={},
    )

    # `1121` is created **through the chart of accounts**, so its `bank_accounts` row must appear
    # by the registration hook rather than by this fixture — the claim clause 6 exists to catch.
    usd_gl = kernel_accounts.create_account(
        db,
        company_id,
        kernel_accounts.AccountInput(
            code="1121",
            name="Bank Account USD",
            class_=AccountClass.ASSET,
            parent_id=accounts["1100"].id,
            control_type=ControlType.BANK,
        ),
        actor=owner,
    )
    bank_accounts.ensure_row(db, usd_gl)
    db.flush()
    tape_state.accounts["1121"] = usd_gl

    rows = {row.code: row for row in db.scalars(select(BankAccount))}
    bank_accounts.update(
        db,
        rows["1120"],
        code="BK-RWF",
        name="Bank of Kigali current account",
        bank_name="Bank of Kigali",
        account_number="00040-0000123-45",
        actor=owner,
    )
    bank_accounts.update(
        db,
        rows["1121"],
        code="BK-USD",
        name="Bank of Kigali USD account",
        currency_id=currencies["USD"].id,
        actor=owner,
    )
    bank_accounts.update(db, rows["1110"], code="CASH", actor=owner)
    db.flush()
    tape_state.banks = {row.code: row for row in db.scalars(select(BankAccount))}

    # The rule row 9's drawer is prefilled from: pattern and account only, which is all the
    # prompt's setup gives it.
    #
    # **Deliberately no `description`.** `prefill_for` returns `rule.description or
    # line.description`, so a rule that names its own narrative shadows the bank's text — and
    # row 9 expects the drawer to open on `MONTHLY ACCOUNT FEE`, which is what the bank printed.
    # A description here would have made the tape disagree with its own prompt, and the first
    # draft of this fixture invented one and did exactly that.
    bank_accounts.create_rule(
        db,
        company_id,
        bank_account_id=tape_state.bank("BK-RWF").id,
        pattern="ACCOUNT FEE",
        gl_account_id=tape_state.acct("6700"),
        actor=owner,
    )

    tape_state.partners = {
        "C1": _customer(db, tape_state, name="Amahoro Retail Ltd", code="C1"),
        "C2": _customer(db, tape_state, name="Kivu Stores Ltd", code="C2"),
        "S1": _supplier(db, tape_state, name="Kigali Timber", code="S1", bank_details=True),
        "S2": _supplier(
            db,
            tape_state,
            name="Musanze Millwork",
            code="S2",
            bank_details=True,
            terms_code="2/10N30",
        ),
        "S3": _supplier(db, tape_state, name="Huye Hardware", code="S3", bank_details=False),
    }
    db.commit()
    return tape_state


# --- Reading the ledger -------------------------------------------------------------------------


def _balance(db: Session, tape: Tape, code: str, *, as_of: date | None = None) -> Decimal:
    """Base-currency balance of an account from its lines. Never a stored column (ADR-04)."""
    statement = (
        select(JournalLine.base_amount)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == tape.company_id,
            JournalLine.gl_account_id == tape.acct(code),
            JournalEntry.status == JournalStatus.POSTED,
        )
    )
    if as_of is not None:
        statement = statement.where(JournalEntry.entry_date <= as_of)
    return sum(db.scalars(statement).all(), Decimal(0))


def _currency_balance(db: Session, tape: Tape, code: str, *, as_of: date) -> Decimal:
    return sum(
        db.scalars(
            select(JournalLine.amount)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(
                JournalLine.company_id == tape.company_id,
                JournalLine.gl_account_id == tape.acct(code),
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.entry_date <= as_of,
            )
        ).all(),
        Decimal(0),
    )


def _partner_open(db: Session, tape: Tape, partner: str, *, as_of: date = NOV_3) -> Decimal:
    """The partner's balance **in the role's own sense** — what the customer owes us, what we owe
    the supplier.

    `signed_base_amount` is signed by the *control account's* side, so AP's is negative when a
    supplier is owed. The prompt's figures are the role's sense (row 6's "S3 open −70 000" is an
    overpayment, not a liability), and `exposure_direction` is the product's own function for that
    turn — the AR/AP partner enquiry reads it the same way. Using it rather than a minus sign
    written here keeps the tape and the screen agreeing about which way round the number goes.
    """
    from app.subledger.common import exposure_direction
    from app.subledger.openitems import open_items_as_of

    role = PartnerRole.AR if partner.startswith("C") else PartnerRole.AP
    balance = sum(
        (
            item.signed_base_amount
            for item in open_items_as_of(db, tape.company_id, role=role, as_of=as_of)
            if item.document.partner_id == tape.partners[partner].id
        ),
        Decimal(0),
    )
    return exposure_direction(role) * balance


def _after_every_row(db: Session, tape: Tape, row: str) -> None:
    db.flush()
    assert_ledger_invariants(db, tape.company_id)
    assert_subledger_invariants(db, tape.company_id)
    assert_bank_invariants(db, tape.company_id)
    _TABLE.append((row, "invariants", "green", "green", "ok"))


def _refuses(db: Session, row: str, label: str, expected_code: str, action) -> Exception:  # noqa: ANN001
    """Provoke a refusal inside a **savepoint**, and return it.

    A plain `db.rollback()` would discard the whole tape: the tenant, the eight documents and
    every row posted so far live in this transaction, and the fixture's `commit` is the only
    boundary behind it. The P6 tape wrote that trap down and this is the same one — a refusal is
    a probe, not a reset.
    """
    step = db.begin_nested()
    try:
        with pytest.raises(AppError) as error:
            action()
        _expect(row, label, expected_code, getattr(error.value, "code", None))
        return error.value
    finally:
        step.rollback()


def _cashbook(
    db: Session,
    tape: Tape,
    *,
    account_code: str,
    amount: Decimal,
    on: date,
    contra: str,
    currency: str = "RWF",
    reference: str | None = None,
    description: str = "cashbook",
) -> JournalEntry:
    entry = posting.post(
        db,
        CashbookEntry(
            entry_date=on,
            description=description,
            reference=reference,
            cash_account_id=tape.acct(account_code),
            kind=CashbookKind.RECEIPT if amount > 0 else CashbookKind.PAYMENT,
            currency_id=tape.cur(currency),
            lines=(CashbookLineSpec(gl_account_id=tape.acct(contra), amount=abs(amount)),),
        ),
        company_id=tape.company_id,
        actor=tape.owner,
    )
    assert entry is not None
    return entry


def _bank_line(db: Session, tape: Tape, entry: JournalEntry, code: str) -> JournalLine:
    return db.scalars(
        select(JournalLine).where(
            JournalLine.entry_id == entry.id,
            JournalLine.gl_account_id == tape.acct(code),
        )
    ).one()


# --- Row 0: the three bank accounts, and the currency rule both ways ----------------------------


def test_row_0_the_three_bank_accounts_are_registered(db: Session, tape: Tape) -> None:
    """Three `bank_accounts` rows, each `kind` equal to its control type, and `1121`'s currency
    set while it still has no lines.

    `1121`'s row exists because the **chart of accounts** created the account and the hook fired
    — the fixture never inserted it. That is the claim invariant clause 6 is written to catch, and
    asserting it here is what makes the clause non-vacuous on this tenant.
    """
    rows = {row.code: row for row in db.scalars(select(BankAccount))}

    _expect("0", "bank_accounts rows", 3, len(rows))
    _expect("0", "BK-RWF kind", "bank", rows["BK-RWF"].kind.value)
    _expect("0", "BK-USD kind", "bank", rows["BK-USD"].kind.value)
    _expect("0", "CASH kind", "cash", rows["CASH"].kind.value)
    _expect("0", "BK-USD currency", "USD", tape.currencies["USD"].code)
    assert rows["BK-USD"].currency_id == tape.cur("USD")
    _expect("0", "BK-RWF account number", "00040-0000123-45", rows["BK-RWF"].account_number)
    _after_every_row(db, tape, "0")


def test_row_0_the_engine_refuses_rwf_on_the_usd_account(db: Session, tape: Tape) -> None:
    """`bank_account_currency_mismatch`, from the **posting engine** — the refusal a user meets,
    with the field error a screen renders."""
    with pytest.raises(PostingError) as error:
        _cashbook(
            db, tape, account_code="1121", amount=Decimal(1000), on=SEP_3, contra="3400"
        )
    _expect("0", "engine refusal", "bank_account_currency_mismatch", error.value.code)
    # The engine prefixes the line index, so the key is `lines.1.currency_id` rather than
    # `currency_id` — which is what a line-level inline error needs to be for the screen to put
    # it on the right row.
    fields = error.value.field_errors or {}
    _expect(
        "0",
        "engine names the line's field",
        ["lines.1.currency_id"],
        [key for key in fields if key.endswith("currency_id")],
    )
    _expect("0", "engine says which currency", ["must be USD"], fields["lines.1.currency_id"])
    db.rollback()


def test_row_0_the_trigger_refuses_rwf_on_the_usd_account(db: Session, tape: Tape) -> None:
    """The same refusal from the **database**, with the engine's check disabled — `VN012`.

    The prompt asks for this proven twice, engine and trigger, one test each, because they are
    two independent guards: a suite that proved only the engine would ship with a broken trigger
    and no way to tell, and the trigger is what makes the rule a property of the *data* rather
    than of whichever code path happens to write it.

    The SQLSTATE is asserted on the cause rather than the code alone. Without that this test
    would pass just as happily with the engine's check running, which is the thing it is
    disabling.
    """
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(posting, "_check_bank_account_currency", lambda *a, **k: None)
    try:
        with pytest.raises(PostingError) as error:
            _cashbook(
                db, tape, account_code="1121", amount=Decimal(1000), on=SEP_3, contra="3400"
            )
        cause = error.value.__cause__
        assert isinstance(cause, DBAPIError)
        _expect("0", "trigger SQLSTATE", "VN012", cause.orig.sqlstate)
        _expect("0", "trigger refusal", "bank_account_currency_mismatch", error.value.code)
    finally:
        monkeypatch.undo()
        db.rollback()


# --- The documents rows 3 to 15 settle ----------------------------------------------------------


def _invoice(
    db: Session,
    tape: Tape,
    *,
    role: PartnerRole,
    partner: str,
    amount: Decimal,
    on: date,
    currency: str = "RWF",
):  # noqa: ANN202
    contra = "4100" if role == PartnerRole.AR else "6990"
    document, _ = documents_service.post_document(
        db,
        tape.company_id,
        role,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=tape.partners[partner].id,
            document_date=on,
            description=f"{partner} {amount}",
            currency_id=tape.cur(currency),
            lines=(
                documents_service.LineInput(unit_price=amount, gl_account_id=tape.acct(contra)),
            ),
        ),
        actor=tape.owner,
    )
    db.flush()
    return document


def _settlement(
    db: Session,
    tape: Tape,
    *,
    role: PartnerRole,
    partner: str,
    amount: Decimal,
    on: date,
    account_code: str,
    instrument: InstrumentType,
    currency: str = "RWF",
    reference: str | None = None,
    exchange_rate: Decimal | None = None,
):  # noqa: ANN202
    document, _ = documents_service.post_document(
        db,
        tape.company_id,
        role,
        documents_service.DocumentInput(
            kind=DocumentKind.SETTLEMENT,
            partner_id=tape.partners[partner].id,
            document_date=on,
            description=f"{partner} settlement",
            reference=reference,
            currency_id=tape.cur(currency),
            exchange_rate=exchange_rate,
            amount=amount,
            cash_account_id=tape.acct(account_code),
            instrument_type=instrument,
        ),
        actor=tape.owner,
    )
    db.flush()
    return document


def _allocate(
    db: Session,
    tape: Tape,
    *,
    role: PartnerRole,
    partner: str,
    debit,  # noqa: ANN001
    credit,  # noqa: ANN001
    amount: Decimal,
    on: date,
):  # noqa: ANN202
    allocation, _ = allocation_service.allocate(
        db,
        tape.company_id,
        role,
        partner_id=tape.partners[partner].id,
        allocation_date=on,
        pairs=[
            allocation_service.PairInput(
                debit_document_id=debit.id, credit_document_id=credit.id, amount=amount
            )
        ],
        actor=tape.owner,
    )
    db.flush()
    return allocation


def _open_documents(db: Session, tape: Tape) -> None:
    """The eight documents posted 1 September, before any bank movement.

    Their base amounts are the tape's: the two USD receivables at 1 320 give 660 000 and
    264 000, and `SIN-4` gives 132 000 — which is what row 12's unrealized loss of 3 000 is
    computed against.
    """
    tape.documents["INV-1"] = _invoice(
        db, tape, role=PartnerRole.AR, partner="C1", amount=Decimal(118000), on=SEP_1
    )
    tape.documents["INV-2"] = _invoice(
        db, tape, role=PartnerRole.AR, partner="C2", amount=Decimal(59000), on=SEP_1
    )
    tape.documents["INV-3"] = _invoice(
        db, tape, role=PartnerRole.AR, partner="C1", amount=Decimal("500.00"), on=SEP_1,
        currency="USD",
    )
    tape.documents["INV-4"] = _invoice(
        db, tape, role=PartnerRole.AR, partner="C2", amount=Decimal("200.00"), on=SEP_1,
        currency="USD",
    )
    tape.documents["SIN-1"] = _invoice(
        db, tape, role=PartnerRole.AP, partner="S1", amount=Decimal(236000), on=SEP_1
    )
    tape.documents["SIN-2"] = _invoice(
        db, tape, role=PartnerRole.AP, partner="S2", amount=Decimal(100000), on=SEP_1
    )
    tape.documents["SIN-3"] = _invoice(
        db, tape, role=PartnerRole.AP, partner="S3", amount=Decimal(50000), on=SEP_1
    )
    tape.documents["SIN-4"] = _invoice(
        db, tape, role=PartnerRole.AP, partner="S1", amount=Decimal("100.00"), on=SEP_1,
        currency="USD",
    )
    db.flush()


# --- The tape ------------------------------------------------------------------------------------


def test_the_acceptance_tape(db: Session, tape: Tape) -> None:  # noqa: PLR0915
    """Rows 1 to 18 in one sequence. Every literal is the prompt's, worked by hand."""
    _open_documents(db, tape)
    _after_every_row(db, tape, "setup")

    # --- Row 1: CB-1 1 000 000 into BK-RWF, CB-2 50 000 into CASH, both 31 Aug ------------------
    cb1 = _cashbook(
        db, tape, account_code="1120", amount=Decimal(1000000), on=AUG_31, contra="3400",
        description="opening float",
    )
    _cashbook(
        db, tape, account_code="1110", amount=Decimal(50000), on=AUG_31, contra="3400",
        description="cash float",
    )
    db.flush()
    _expect("1", "1120 balance", Decimal(1000000), _balance(db, tape, "1120"))
    _expect("1", "1110 balance", Decimal(50000), _balance(db, tape, "1110"))

    summary = {
        row.code: row
        for row in reports_service.cashbook_summary(
            db, tape.company_id, date_from=date(TAPE_YEAR, 8, 1), date_to=AUG_31
        )
    }
    _expect("1", "Cashbooks BK-RWF closing", Decimal(1000000), summary["BK-RWF"].closing_balance)
    _expect("1", "Cashbooks CASH closing", Decimal(50000), summary["CASH"].closing_balance)
    _expect("1", "Cashbooks BK-USD closing", Decimal(0), summary["BK-USD"].closing_balance)
    _after_every_row(db, tape, "1")

    # --- Row 2: BRC-1 on BK-RWF as at 31 Aug, paper mode ---------------------------------------
    brc1 = reconciliation_service.open_reconciliation(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-RWF").id,
        reconciliation_date=AUG_31,
        statement_balance=Decimal(1000000),
        actor=tape.owner,
    )
    db.flush()
    # The lock **before** the tick: the ledger holds 1 000 000 and nothing is reconciled, so the
    # whole balance is outstanding and the difference is the whole balance.
    refusal = _refuses(
        db,
        "2",
        "lock before the tick",
        "reconciliation_difference",
        lambda: reconciliation_service.lock(db, tape.company_id, brc1.id, actor=tape.owner),
    )
    _expect("2", "difference refused", True, "1000000" in str(refusal).replace(" ", ""))

    matching.tick(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-RWF").id,
        journal_line_ids=[_bank_line(db, tape, cb1, "1120").id],
        actor=tape.owner,
    )
    db.flush()
    figures = reconciliation_service.live_figures(db, tape.company_id, brc1)
    _expect("2", "BRC-1 ledger", Decimal(1000000), figures.ledger_balance)
    _expect("2", "BRC-1 outstanding", Decimal(0), figures.outstanding_total)
    _expect("2", "BRC-1 difference", Decimal(0), figures.difference)
    reconciliation_service.lock(db, tape.company_id, brc1.id, actor=tape.owner)
    db.flush()
    _expect("2", "BRC-1 number", "BRC-000001", brc1.number)
    _expect(
        "2",
        "last_reconciled_balance",
        Decimal(1000000),
        tape.bank("BK-RWF").last_reconciled_balance,
    )
    _after_every_row(db, tape, "2")

    # --- Row 3: RCT-1 118 000 and RCT-2 59 000, allocated -------------------------------------
    rct1 = _settlement(
        db, tape, role=PartnerRole.AR, partner="C1", amount=Decimal(118000), on=SEP_3,
        account_code="1120", instrument=InstrumentType.BANK, reference="INV-1 C1",
    )
    _allocate(
        db, tape, role=PartnerRole.AR, partner="C1",
        debit=tape.documents["INV-1"], credit=rct1, amount=Decimal(118000), on=SEP_3,
    )
    rct2 = _settlement(
        db, tape, role=PartnerRole.AR, partner="C2", amount=Decimal(59000), on=SEP_5,
        account_code="1120", instrument=InstrumentType.MOBILE,
    )
    _allocate(
        db, tape, role=PartnerRole.AR, partner="C2",
        debit=tape.documents["INV-2"], credit=rct2, amount=Decimal(59000), on=SEP_5,
    )
    db.flush()
    tape.documents["RCT-1"] = rct1
    tape.documents["RCT-2"] = rct2

    _expect("3", "1120 balance", Decimal(1177000), _balance(db, tape, "1120"))
    _expect("3", "INV-1 open", Decimal(0), recompute_open_amount(db, tape.documents["INV-1"]))
    _expect("3", "INV-2 open", Decimal(0), recompute_open_amount(db, tape.documents["INV-2"]))
    # No statement yet, so both receipts are outstanding — the bank has shown nothing.
    ledger_rows = matching.list_ledger_lines(
        db, tape.company_id, tape.bank("BK-RWF").id, as_of=SEP_30
    )
    outstanding = [row for row in ledger_rows if row.is_outstanding]
    _expect("3", "outstanding lines", 2, len(outstanding))
    _expect(
        "3",
        "outstanding amounts",
        [Decimal(59000), Decimal(118000)],
        sorted(row.amount for row in outstanding),
    )
    _after_every_row(db, tape, "3")

    # --- Row 4: PYR-1 pays S1, S2 (discount) and S3 --------------------------------------------
    lines = [
        payment_run_service.RunLineInput(document_id=tape.documents[code].id)
        for code in ("SIN-1", "SIN-2", "SIN-3")
    ]
    preview = payment_run_service.plan(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-RWF").id,
        payment_date=SEP_10,
        lines=lines,
    )
    by_partner = {supplier.partner_id: supplier for supplier in preview.suppliers}
    s2 = by_partner[tape.partners["S2"].id]
    # 2 % of 100 000, inside the ten-day window from 1 September.
    _expect("4", "S2 discount_available", Decimal(2000), s2.lines[0].discount_available)
    _expect(
        "4",
        "S3 warning",
        ["bank_details_missing"],
        list(by_partner[tape.partners["S3"].id].warnings),
    )
    # 236 000 + (100 000 − 2 000) + 50 000
    _expect("4", "preview total", Decimal(384000), preview.total)

    run = payment_run_service.post_run(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-RWF").id,
        payment_date=SEP_10,
        lines=lines,
        actor=tape.owner,
    )
    db.flush()
    tape.documents["PYR-1"] = run
    _expect("4", "PYR-1 number", "PYR-000001", run.number)
    _expect("4", "PYR-1 total", Decimal(384000), run.total)

    run_lines = payment_run_service.lines_of(db, tape.company_id, run.id)
    settlements = {
        line.partner_id: db.get(PartnerDocument, line.settlement_document_id)
        for line in run_lines
    }
    _expect(
        "4",
        "PMT amounts by supplier",
        [Decimal(50000), Decimal(98000), Decimal(236000)],
        sorted(document.total_amount for document in settlements.values()),
    )
    for document in settlements.values():
        assert document.reference == "PYR-000001"
        assert document.instrument_type == InstrumentType.BANK
        assert document.number.startswith("PMT-")
    _expect("4", "PMT numbers", 3, len({d.number for d in settlements.values()}))

    # ALJ-1: the discount entry — Dr 2100 (the payable comes down) / Cr 4350 (discount received).
    discount_entries = db.scalars(
        select(JournalEntry.id)
        .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
        .where(
            JournalEntry.company_id == tape.company_id,
            JournalLine.gl_account_id == tape.acct("4350"),
        )
        .distinct()
    ).all()
    _expect("4", "ALJ-1 count", 1, len(discount_entries))
    alj1 = {
        code: amount
        for code, amount in db.execute(
            select(GLAccount.code, JournalLine.base_amount)
            .join(
                JournalLine,
                (JournalLine.gl_account_id == GLAccount.id)
                & (JournalLine.company_id == GLAccount.company_id),
            )
            .where(JournalLine.entry_id == discount_entries[0])
        ).all()
    }
    _expect("4", "ALJ-1 Dr 2100", Decimal(2000), alj1["2100"])
    _expect("4", "ALJ-1 Cr 4350", Decimal(-2000), alj1["4350"])

    for code in ("SIN-1", "SIN-2", "SIN-3"):
        _expect("4", f"{code} open", Decimal(0), recompute_open_amount(db, tape.documents[code]))
    # 1 177 000 − 384 000
    _expect("4", "1120 balance", Decimal(793000), _balance(db, tape, "1120"))

    instruction = payment_run_service.instruction_csv(db, tape.company_id, run)
    rows = [line.split(",") for line in instruction.strip().split("\r\n")]
    _expect("4", "instruction rows", 4, len(rows))
    by_code = {row[-1]: row for row in rows[1:]}
    _expect("4", "S3 bank blank", "", by_code["S3"][1])
    _expect("4", "S3 account blank", "", by_code["S3"][2])
    _expect("4", "S3 amount", "50000", by_code["S3"][3])
    _expect("4", "S1 account number", "00040-S1-01", by_code["S1"][2])

    jobs = db.scalars(
        select(Job).where(
            Job.company_id == tape.company_id,
            Job.kind == payment_run_service.REMITTANCE_JOB,
        )
    ).all()
    _expect("4", "remittance jobs", 3, len(jobs))
    _after_every_row(db, tape, "4")

    # --- Row 5: RCT-3 USD into BK-USD, RCT-4 USD into BK-RWF at the bank's rate -----------------
    # **Reference `INV-3`, which is the token the bank's own line carries.** The prompt gives
    # `RCT-3` no reference of its own, but row 11 expects the USD statement's
    # `INWARD TRF C1 INV-3` to match it by `reference` — the strongest of the three rules — so the
    # receipt has to hold something that line contains. `INV-3` is the invoice number the customer
    # quoted, which is exactly what `_reference_tokens` says a receipt's `reference` is for.
    #
    # The first draft used `INV-3 C1` by analogy with `RCT-1` and got an `amount_date` match
    # instead: normalised, `INV3C1` is not inside `INWARDTRFC1INV3`, because the bank prints the
    # two tokens the other way round. Same match, weaker evidence — and the rule is what row 11
    # asserts.
    rct3 = _settlement(
        db, tape, role=PartnerRole.AR, partner="C1", amount=Decimal("500.00"), on=SEP_15,
        account_code="1121", instrument=InstrumentType.BANK, currency="USD",
        reference="INV-3",
    )
    _allocate(
        db, tape, role=PartnerRole.AR, partner="C1",
        debit=tape.documents["INV-3"], credit=rct3, amount=Decimal("500.00"), on=SEP_15,
    )
    # **The bank's own rate, keyed**: 1 300 rather than the 1 320 the table holds. That is what
    # makes row 5 a realized-FX row rather than a plain receipt.
    rct4 = _settlement(
        db, tape, role=PartnerRole.AR, partner="C2", amount=Decimal("200.00"), on=SEP_15,
        account_code="1120", instrument=InstrumentType.BANK, currency="USD",
        exchange_rate=BANK_RATE,
    )
    _allocate(
        db, tape, role=PartnerRole.AR, partner="C2",
        debit=tape.documents["INV-4"], credit=rct4, amount=Decimal("200.00"), on=SEP_15,
    )
    db.flush()
    tape.documents["RCT-3"] = rct3
    tape.documents["RCT-4"] = rct4

    _expect("5", "1121 currency balance", Decimal("500.00"),
            _currency_balance(db, tape, "1121", as_of=SEP_30))
    _expect("5", "1121 base balance", Decimal(660000), _balance(db, tape, "1121"))

    rct4_line = _bank_line(db, tape, db.get(JournalEntry, rct4.journal_entry_id), "1120")
    _expect("5", "RCT-4 amount", Decimal("200.00"), rct4_line.amount)
    # 200.00 x 1 300 — the bank's rate, not the table's.
    _expect("5", "RCT-4 base_amount", Decimal(260000), rct4_line.base_amount)
    base_currency_id = tape.cur("RWF")
    _expect(
        "5",
        "RCT-4 reconciled amount",
        Decimal(260000),
        bank_accounts.reconciled_amount(rct4_line, tape.bank("BK-RWF"), base_currency_id),
    )

    # ALJ-2: realized loss. INV-4 was booked at 1 320 (264 000) and settled at 1 300 (260 000),
    # so 4 000 of receivable was never collected — Dr 6950 / Cr 1200.
    loss_entries = db.scalars(
        select(JournalEntry.id)
        .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
        .where(
            JournalEntry.company_id == tape.company_id,
            JournalLine.gl_account_id == tape.acct("6950"),
        )
        .distinct()
    ).all()
    _expect("5", "ALJ-2 count", 1, len(loss_entries))
    alj2 = {
        code: amount
        for code, amount in db.execute(
            select(GLAccount.code, JournalLine.base_amount)
            .join(
                JournalLine,
                (JournalLine.gl_account_id == GLAccount.id)
                & (JournalLine.company_id == GLAccount.company_id),
            )
            .where(JournalLine.entry_id == loss_entries[0])
        ).all()
    }
    _expect("5", "ALJ-2 Dr 6950", Decimal(4000), alj2["6950"])
    _expect("5", "ALJ-2 Cr 1200", Decimal(-4000), alj2["1200"])
    # 793 000 + 260 000
    _expect("5", "1120 balance", Decimal(1053000), _balance(db, tape, "1120"))
    _expect("5", "INV-3 open", Decimal(0), recompute_open_amount(db, tape.documents["INV-3"]))
    _expect("5", "INV-4 open", Decimal(0), recompute_open_amount(db, tape.documents["INV-4"]))
    _after_every_row(db, tape, "5")

    # --- Row 6: PMT-4 70 000 to S3 by cheque, unallocated --------------------------------------
    pmt4 = _settlement(
        db, tape, role=PartnerRole.AP, partner="S3", amount=Decimal(70000), on=SEP_28,
        account_code="1120", instrument=InstrumentType.CHEQUE, reference="CHQ 101",
    )
    db.flush()
    tape.documents["PMT-4"] = pmt4
    # 1 053 000 − 70 000
    _expect("6", "1120 balance", Decimal(983000), _balance(db, tape, "1120"))
    # A payment on account: S3 is owed nothing and has been paid, so the balance is negative.
    _expect("6", "S3 open", Decimal(-70000), _partner_open(db, tape, "S3"))
    _after_every_row(db, tape, "6")

    # --- Row 7: import generic-bk-rwf-sep.csv, preview then import, then auto-match -------------
    content = sample("generic-bk-rwf-sep.csv")
    view = statements_service.preview(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-RWF").id,
        content=content,
        file_name="generic-bk-rwf-sep.csv",
    )
    _expect("7", "preview rows", 6, len(view.lines))
    # The balance column runs 1 118 000 → 1 090 500, so the opening is the first balance less the
    # first movement: 1 118 000 − 118 000.
    _expect("7", "preview opening", Decimal(1000000), view.opening_balance)
    _expect("7", "preview closing", Decimal(1090500), view.closing_balance)
    _expect("7", "preview skipped", 0, view.skipped_count)
    _expect("7", "preview errors", [], list(view.errors))

    result = statements_service.import_statement(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-RWF").id,
        content=content,
        file_name="generic-bk-rwf-sep.csv",
        actor=tape.owner,
    )
    db.flush()
    bst1 = result.statement
    tape.documents["BST-1"] = bst1
    _expect("7", "BST-1 number", "BST-000001", bst1.number)
    _expect("7", "BST-1 line_count", 6, bst1.line_count)

    auto = matching.auto_match(
        db, tape.company_id, tape.bank("BK-RWF").id, actor=tape.owner
    )
    db.flush()
    lines = statements_service.lines_of(db, tape.company_id, bst1.id)
    states = matching.statement_line_states(db, tape.company_id, bst1.id)
    by_line_no = {line.line_no: line for line in lines}

    def _rule_of(line_no: int):  # noqa: ANN202
        state = states[by_line_no[line_no].id]
        return state.match_rule.value if state.match_rule is not None else None

    # Line 1 quotes `INV-1 C1`, which is RCT-1's own reference — the strongest evidence there is.
    _expect("7", "line 1 rule", "reference", _rule_of(1))
    # Line 2 says `MOMO DEPOSIT 0788`, naming nothing: 59 000 within three days is the guess.
    _expect("7", "line 2 rule", "amount_date", _rule_of(2))
    # Line 3 is the bank's single debit for the run — **one match, three journal members**.
    _expect("7", "line 3 rule", "payment_run", _rule_of(3))
    _expect("7", "line 3 journal members", 3, states[by_line_no[3].id].journal_line_count)
    # Line 5 is the USD receipt, matched on the **base** amount: 260 000, not USD 200.
    _expect("7", "line 5 rule", "amount_date", _rule_of(5))
    # Lines 4 and 6 — the fee and the deposit — are not in the ledger at all yet.
    _expect("7", "line 4 unmatched", None, _rule_of(4))
    _expect("7", "line 6 unmatched", None, _rule_of(6))
    _expect("7", "matches made", 4, len(auto.matched))

    # The same file again: refused on its SHA-256, before a single row is read.
    _refuses(
        db,
        "7",
        "re-import the same file",
        "statement_already_imported",
        lambda: statements_service.import_statement(
            db,
            tape.company_id,
            bank_account_id=tape.bank("BK-RWF").id,
            content=content,
            file_name="generic-bk-rwf-sep.csv",
            actor=tape.owner,
        ),
    )
    _after_every_row(db, tape, "7")

    # --- Row 8: BRC-2 as at 30 Sep, the lock refused, a manual match refused --------------------
    brc2 = reconciliation_service.open_reconciliation(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-RWF").id,
        reconciliation_date=SEP_30,
        actor=tape.owner,
    )
    db.flush()
    tape.documents["BRC-2"] = brc2
    # The statement balance **defaults from the balance column** — nothing was keyed.
    _expect("8", "BRC-2 statement balance", Decimal(1090500), brc2.statement_balance)

    figures = reconciliation_service.live_figures(db, tape.company_id, brc2)
    _expect("8", "BRC-2 ledger", Decimal(983000), figures.ledger_balance)
    # Only PMT-4 is outstanding: the fee and the deposit are on the statement but **not in the
    # ledger at all**, so there is nothing outstanding for them — they are unmatched statement
    # lines, which is a different thing and counted separately.
    _expect("8", "BRC-2 outstanding", Decimal(-70000), figures.outstanding_total)
    _expect("8", "BRC-2 unmatched statement lines", 2, figures.unmatched_statement_count)
    # 1 090 500 − (983 000 + (−70 000)) = 177 500? No: 1 090 500 − 983 000 − 70 000 = 37 500.
    # The deposit (40 000) less the fee (2 500) — exactly what the ledger lacks.
    _expect("8", "BRC-2 difference", Decimal(37500), figures.difference)

    _refuses(
        db,
        "8",
        "lock with lines unmatched",
        "statement_lines_unmatched",
        lambda: reconciliation_service.lock(db, tape.company_id, brc2.id, actor=tape.owner),
    )

    # The manual match row 8 refuses: statement line 6 (the 40 000 deposit) against PMT-4
    # (−70 000). 40 000 − (−70 000) = 110 000 out.
    lines = statements_service.lines_of(db, tape.company_id, tape.documents["BST-1"].id)
    line6 = next(line for line in lines if line.line_no == 6)
    pmt4_line = _bank_line(
        db, tape, db.get(JournalEntry, tape.documents["PMT-4"].journal_entry_id), "1120"
    )
    unbalanced = _refuses(
        db,
        "8",
        "manual match across the two",
        "match_unbalanced",
        lambda: matching.create_match(
            db,
            tape.company_id,
            bank_account_id=tape.bank("BK-RWF").id,
            statement_line_ids=[line6.id],
            journal_line_ids=[pmt4_line.id],
            kind=BankMatchKind.MANUAL,
            rule=BankMatchRule.MANUAL,
            actor=tape.owner,
        ),
    )
    _expect(
        "8",
        "match_unbalanced names 110 000",
        True,
        "110000" in str(unbalanced.field_errors).replace(" ", ""),
    )
    _after_every_row(db, tape, "8")

    # --- Row 9: post the fee and the deposit from their lines, then lock BRC-2 -------------------
    line4 = next(line for line in lines if line.line_no == 4)

    # The drawer opens **prefilled by the rule**: 6700, the line's own amount and date, and the
    # description the bank printed. A suggestion, not a posting — a person still presses Post.
    prefill = matching.prefill_for(db, tape.company_id, line4)
    _expect("9", "prefill account", tape.acct("6700"), prefill.gl_account_id)
    _expect("9", "prefill kind", "payment", prefill.kind)
    _expect("9", "prefill description", "MONTHLY ACCOUNT FEE", prefill.description)
    _expect("9", "prefill amount", Decimal(-2500), line4.amount)
    _expect("9", "prefill date", SEP_12, line4.value_date)

    posted_fee = matching.post_cashbook_from_line(
        db,
        tape.company_id,
        line4.id,
        gl_account_id=prefill.gl_account_id,
        actor=tape.owner,
    )
    db.flush()
    tape.documents["CB-3"] = posted_fee
    _expect("9", "CB-3 match rule", "posted_from_statement", posted_fee.match.rule.value)

    line6 = next(line for line in lines if line.line_no == 6)
    posted_receipt = matching.post_settlement_from_line(
        db,
        tape.company_id,
        line6.id,
        partner_id=tape.partners["C2"].id,
        actor=tape.owner,
    )
    db.flush()
    tape.documents["RCT-5"] = posted_receipt
    _expect("9", "RCT-5 match rule", "posted_from_statement", posted_receipt.match.rule.value)

    # 983 000 − 2 500 + 40 000
    _expect("9", "1120 balance", Decimal(1020500), _balance(db, tape, "1120"))
    figures = reconciliation_service.live_figures(db, tape.company_id, brc2)
    _expect("9", "BRC-2 statement", Decimal(1090500), figures.statement_balance)
    _expect("9", "BRC-2 ledger", Decimal(1020500), figures.ledger_balance)
    _expect("9", "BRC-2 outstanding", Decimal(-70000), figures.outstanding_total)
    _expect("9", "BRC-2 unmatched", 0, figures.unmatched_statement_count)
    _expect("9", "BRC-2 difference", Decimal(0), figures.difference)

    # Keyed 500 short: the difference is −500 and the lock is refused at it.
    wrong = _refuses(
        db,
        "9",
        "lock at a wrong balance",
        "reconciliation_difference",
        lambda: reconciliation_service.lock(
            db,
            tape.company_id,
            brc2.id,
            statement_balance=Decimal(1090000),
            actor=tape.owner,
        ),
    )
    _expect("9", "difference is -500", True, "-500" in str(wrong.field_errors).replace(" ", ""))

    reconciliation_service.lock(db, tape.company_id, brc2.id, actor=tape.owner)
    db.flush()
    _expect("9", "BRC-2 number", "BRC-000002", brc2.number)
    assigned = [
        match
        for match in matching.matches_of(db, tape.company_id, tape.bank("BK-RWF").id)
        if match.reconciliation_id == brc2.id
    ]
    _expect("9", "matches assigned", 6, len(assigned))
    # The prompt writes this `[PMT-4 −70 000]`; `PMT-4` is the tape's name for the fourth
    # payment and `PMT-000004` is what the sequence actually issued, so the literal below pins
    # the numbering as well as the snapshot.
    _expect(
        "9",
        "snapshot",
        [("PMT-000004", Decimal(-70000))],
        [
            (entry["entry_number"], Decimal(entry["amount"]))
            for entry in (brc2.outstanding_snapshot or [])
        ],
    )
    _expect("9", "PMT-4 is the fourth payment", "PMT-000004", tape.documents["PMT-4"].number)
    greatest = db.scalar(
        select(func.max(JournalLine.id)).where(
            JournalLine.company_id == tape.company_id,
            JournalLine.gl_account_id == tape.acct("1120"),
        )
    )
    _expect("9", "high_water_line_id", greatest, brc2.high_water_line_id)
    _expect(
        "9",
        "last_reconciled_balance",
        Decimal(1090500),
        tape.bank("BK-RWF").last_reconciled_balance,
    )
    _after_every_row(db, tape, "9")

    # --- Row 10: unmatching inside a locked reconciliation --------------------------------------
    rct1_line = _bank_line(
        db, tape, db.get(JournalEntry, tape.documents["RCT-1"].journal_entry_id), "1120"
    )
    rct1_match = matching.match_by_journal_line(db, tape.company_id, rct1_line.id)
    assert rct1_match is not None
    locked_refusal = _refuses(
        db,
        "10",
        "unmatch inside a locked BRC",
        "reconciliation_locked",
        lambda: matching.unmatch(db, tape.company_id, rct1_match.id, actor=tape.owner),
    )
    _expect("10", "refusal names BRC-2", True, "BRC-000002" in str(locked_refusal))
    _after_every_row(db, tape, "10")

    # --- Row 11: the USD statement, the fee in USD, BRC-3 ---------------------------------------
    usd_content = sample("generic-bk-usd-sep.csv")
    usd_result = statements_service.import_statement(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-USD").id,
        content=usd_content,
        file_name="generic-bk-usd-sep.csv",
        actor=tape.owner,
    )
    db.flush()
    bst2 = usd_result.statement
    _expect("11", "BST-2 number", "BST-000002", bst2.number)

    matching.auto_match(db, tape.company_id, tape.bank("BK-USD").id, actor=tape.owner)
    db.flush()
    usd_lines = statements_service.lines_of(db, tape.company_id, bst2.id)
    usd_states = matching.statement_line_states(db, tape.company_id, bst2.id)
    first_usd = next(line for line in usd_lines if line.line_no == 1)
    # `INWARD TRF C1 INV-3` contains RCT-3's reference — the bank quoting what the payer typed.
    _expect(
        "11",
        "USD line 1 rule",
        "reference",
        usd_states[first_usd.id].match_rule.value,
    )

    fee_line = next(line for line in usd_lines if line.line_no == 2)
    posted_usd_fee = matching.post_cashbook_from_line(
        db,
        tape.company_id,
        fee_line.id,
        gl_account_id=tape.acct("6700"),
        actor=tape.owner,
    )
    db.flush()
    tape.documents["CB-4"] = posted_usd_fee
    _expect(
        "11", "CB-4 match rule", "posted_from_statement", posted_usd_fee.match.rule.value
    )
    # USD 5.00 at 1 320 — the rate that stood on 15 September.
    cb4_line = _bank_line(db, tape, db.get(JournalEntry, posted_usd_fee.entry_id), "1121")
    _expect("11", "CB-4 base_amount", Decimal(-6600), cb4_line.base_amount)
    _expect("11", "CB-4 amount", Decimal("-5.00"), cb4_line.amount)

    brc3 = reconciliation_service.open_reconciliation(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-USD").id,
        reconciliation_date=SEP_30,
        actor=tape.owner,
    )
    db.flush()
    figures = reconciliation_service.live_figures(db, tape.company_id, brc3)
    # 500.00 − 5.00, in the account's own currency.
    _expect("11", "BRC-3 statement", Decimal("495.00"), figures.statement_balance)
    _expect("11", "BRC-3 ledger", Decimal("495.00"), figures.ledger_balance)
    _expect("11", "BRC-3 outstanding", Decimal(0), figures.outstanding_total)
    _expect("11", "BRC-3 difference", Decimal(0), figures.difference)
    reconciliation_service.lock(db, tape.company_id, brc3.id, actor=tape.owner)
    db.flush()
    tape.documents["BRC-3"] = brc3
    _expect("11", "BRC-3 number", "BRC-000003", brc3.number)
    _after_every_row(db, tape, "11")

    # --- Row 12: FX revaluation, role `all`, 30 Sep at 1 350 ------------------------------------
    view = revaluation_service.preview(
        db, tape.company_id, revaluation_date=SEP_30, role=FxRevaluationRole.ALL
    )
    by_scope: dict[str, list] = {}
    for line in view.lines:
        by_scope.setdefault(line.scope, []).append(line)
    _expect("12", "AR lines", 0, len(by_scope.get("ar", [])))

    bank_line = by_scope["bank"][0]
    _expect("12", "bank line account", "BK-USD", bank_line.bank_account_code)
    _expect("12", "bank open_amount", Decimal("495.00"), bank_line.open_amount)
    # 660 000 − 6 600
    _expect("12", "bank carrying_base", Decimal(653400), bank_line.carrying_base)
    # 495.00 x 1 350
    _expect("12", "bank revalued_base", Decimal(668250), bank_line.revalued_base)
    _expect("12", "bank gain", Decimal(14850), bank_line.difference)

    ap_line = by_scope["ap"][0]
    _expect("12", "AP open", Decimal("-100.00"), ap_line.open_amount)
    _expect("12", "AP carrying", Decimal(-132000), ap_line.carrying_base)
    _expect("12", "AP revalued", Decimal(-135000), ap_line.revalued_base)
    _expect("12", "AP loss", Decimal(-3000), ap_line.difference)

    fxr1 = revaluation_service.post_revaluation(
        db,
        tape.company_id,
        revaluation_date=SEP_30,
        role=FxRevaluationRole.ALL,
        actor=tape.owner,
    )
    db.flush()
    tape.documents["FXR-1"] = fxr1
    posted = {
        code: amount
        for code, amount in db.execute(
            select(GLAccount.code, JournalLine.base_amount)
            .join(
                JournalLine,
                (JournalLine.gl_account_id == GLAccount.id)
                & (JournalLine.company_id == GLAccount.company_id),
            )
            .where(JournalLine.entry_id == fxr1.journal_entry_id)
        ).all()
    }
    _expect("12", "FXR-1 Dr 1130", Decimal(14850), posted["1130"])
    _expect("12", "FXR-1 Cr 4410", Decimal(-14850), posted["4410"])
    _expect("12", "FXR-1 Dr 6955", Decimal(3000), posted["6955"])
    _expect("12", "FXR-1 Cr 2190", Decimal(-3000), posted["2190"])
    _expect("12", "FXR-1 accounts", 4, len(posted))
    _expect("12", "FXR-2 mirror exists", True, fxr1.mirror_entry_id is not None)
    mirror = db.get(JournalEntry, fxr1.mirror_entry_id)
    _expect("12", "FXR-2 date", OCT_1, mirror.entry_date)

    # **Never the bank account itself.** 1121 is where row 5 and row 11 left it.
    _expect("12", "1121 unchanged", Decimal(653400), _balance(db, tape, "1121", as_of=SEP_30))
    _expect("12", "1121 after mirror", Decimal(653400), _balance(db, tape, "1121", as_of=OCT_1))
    # The balance sheet reads the pair: 1121 + 1130 = 495 x 1 350.
    pair = _balance(db, tape, "1121", as_of=SEP_30) + _balance(db, tape, "1130", as_of=SEP_30)
    _expect("12", "1121 + 1130", Decimal(668250), pair)
    _expect("12", "= 495 x 1350", Decimal("495.00") * MONTH_END_RATE, pair)

    # A second run whose scope intersects: `bank` is inside `all`.
    _refuses(
        db,
        "12",
        "second run, role bank",
        "fx_revaluation_exists",
        lambda: revaluation_service.post_revaluation(
            db,
            tape.company_id,
            revaluation_date=SEP_30,
            role=FxRevaluationRole.BANK,
            actor=tape.owner,
        ),
    )
    _after_every_row(db, tape, "12")

    # --- Row 13: PMT-5 dated 26 Sep, posted 2 Oct — the late line -------------------------------
    stored_before = (brc2.ledger_balance, brc2.outstanding_total)
    pmt5 = _settlement(
        db, tape, role=PartnerRole.AP, partner="S1", amount=Decimal(20000), on=SEP_26,
        account_code="1120", instrument=InstrumentType.CHEQUE, reference="CHQ 102",
    )
    db.flush()
    tape.documents["PMT-5"] = pmt5
    # **The locked figures do not move**, however the cheque is dated.
    _expect("13", "BRC-2 stored ledger", Decimal(1020500), brc2.ledger_balance)
    _expect("13", "BRC-2 stored outstanding", Decimal(-70000), brc2.outstanding_total)
    _expect(
        "13",
        "BRC-2 stored unchanged",
        stored_before,
        (brc2.ledger_balance, brc2.outstanding_total),
    )

    report = reports_service.reconciliation_report(db, tape.company_id, brc2.id)
    _expect("13", "Posted after lock count", 1, len(report.posted_after_lock))
    late = report.posted_after_lock[0]
    _expect("13", "late line amount", Decimal(-20000), late.amount)
    _expect("13", "late line is PMT-5", "PMT-000005", late.entry_number)
    # Live: the cheque is in the ledger now, and outstanding with PMT-4.
    _expect("13", "live ledger", Decimal(1000500), report.live.ledger_balance)
    _expect("13", "live outstanding", Decimal(-90000), report.live.outstanding_total)
    _expect("13", "live difference", Decimal(0), report.live.difference)

    detail = reports_service.cashbook_detail(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-RWF").id,
        date_from=SEP_1,
        date_to=SEP_30,
    )
    _expect("13", "Cashbooks opening", Decimal(1000000), detail.opening_balance)
    # 118 000 + 59 000 + 260 000 + 40 000
    _expect("13", "Cashbooks receipts", Decimal(477000), detail.receipts_total)
    # 384 000 + 70 000 + 2 500 + 20 000
    _expect("13", "Cashbooks payments", Decimal(476500), detail.payments_total)
    _expect("13", "Cashbooks closing", Decimal(1000500), detail.closing_balance)
    _expect(
        "13",
        "closing ties to the trial balance",
        True,
        reports_service.base_closing_ties(db, tape.company_id, detail),
    )
    reconciled = {row.entry_number: row.reconciled for row in detail.rows}
    _expect("13", "PMT-4 Reconciled blank", None, reconciled["PMT-000004"])
    _expect("13", "PMT-5 Reconciled blank", None, reconciled["PMT-000005"])
    _expect("13", "RCT-1 Reconciled", "BRC-000002", reconciled[tape.documents["RCT-1"].number])
    _after_every_row(db, tape, "13")

    # --- Row 14: the overlapping October export -------------------------------------------------
    overlap = sample("generic-bk-rwf-overlap-oct.csv")
    overlap_result = statements_service.import_statement(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-RWF").id,
        content=overlap,
        file_name="generic-bk-rwf-overlap-oct.csv",
        actor=tape.owner,
    )
    db.flush()
    bst3 = overlap_result.statement
    tape.documents["BST-3"] = bst3
    _expect("14", "BST-3 number", "BST-000003", bst3.number)
    # The 20 September deposit is already held, by fingerprint — an overlapping export is the
    # normal case, not an error.
    _expect("14", "BST-3 new", 2, overlap_result.new_count)
    _expect("14", "BST-3 skipped", 1, overlap_result.skipped_count)

    matching.auto_match(db, tape.company_id, tape.bank("BK-RWF").id, actor=tape.owner)
    db.flush()
    states = matching.statement_line_states(db, tape.company_id, bst3.id)
    lines3 = statements_service.lines_of(db, tape.company_id, bst3.id)
    rules = {line.line_no: states[line.id].match_rule for line in lines3}
    # `CHQ 101` and `CHQ 102` are the cheques' own references — the bank quoting them back.
    _expect("14", "cheque matches by reference", 2,
            sum(1 for rule in rules.values() if rule is not None and rule.value == "reference"))

    brc4 = reconciliation_service.open_reconciliation(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-RWF").id,
        reconciliation_date=OCT_31,
        actor=tape.owner,
    )
    db.flush()
    tape.documents["BRC-4"] = brc4
    figures = reconciliation_service.live_figures(db, tape.company_id, brc4)
    _expect("14", "BRC-4 statement", Decimal(1000500), figures.statement_balance)
    _expect("14", "BRC-4 ledger", Decimal(1000500), figures.ledger_balance)
    _expect("14", "BRC-4 outstanding", Decimal(0), figures.outstanding_total)
    _expect("14", "BRC-4 difference", Decimal(0), figures.difference)
    reconciliation_service.lock(db, tape.company_id, brc4.id, actor=tape.owner)
    db.flush()
    _expect("14", "BRC-4 number", "BRC-000004", brc4.number)

    # **The history does not change**: BRC-2 still lists PMT-5 under *Posted after lock*, even
    # though the cheque is matched and reconciled now.
    report = reports_service.reconciliation_report(db, tape.company_id, brc2.id)
    _expect("14", "BRC-2 still lists PMT-5 late", 1, len(report.posted_after_lock))
    _expect("14", "BRC-2 late line", "PMT-000005", report.posted_after_lock[0].entry_number)
    _after_every_row(db, tape, "14")

    # --- Row 15: PYR-2 pays SIN-5, then is reversed ---------------------------------------------
    sin5 = _invoice(
        db, tape, role=PartnerRole.AP, partner="S2", amount=Decimal(30000), on=NOV_2
    )
    tape.documents["SIN-5"] = sin5
    # **The discount is declined here, and that is the prompt's own reading.** S2's terms are
    # `2/10 net 30`, `SIN-5` is dated 2 November and `PYR-2` pays it on the 3rd — one day in, so
    # P4 offers 2 % of 30 000. Row 4 says "with the discount taken" in as many words *because it
    # is a choice*; row 15 says nothing and gives `PMT-6` as **30 000**, the undiscounted amount.
    # So the run declines it, which is what the per-line toggle exists for.
    #
    # The first draft left the default on and got 29 400 — the same run, correctly discounted,
    # disagreeing with the prompt by 600. The offer is asserted below so the toggle is shown
    # doing something rather than silently agreeing with a zero.
    offer = payment_run_service.plan(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-RWF").id,
        payment_date=NOV_3,
        lines=[payment_run_service.RunLineInput(document_id=sin5.id)],
    )
    _expect("15", "discount on offer", Decimal(600), offer.suppliers[0].lines[0].discount_available)

    run2 = payment_run_service.post_run(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-RWF").id,
        payment_date=NOV_3,
        lines=[
            payment_run_service.RunLineInput(document_id=sin5.id, take_discount=False)
        ],
        actor=tape.owner,
    )
    db.flush()
    tape.documents["PYR-2"] = run2
    run2_lines = payment_run_service.lines_of(db, tape.company_id, run2.id)
    pmt6 = db.get(PartnerDocument, run2_lines[0].settlement_document_id)
    _expect("15", "PMT-6 number", "PMT-000006", pmt6.number)
    _expect("15", "PMT-6 amount", Decimal(30000), pmt6.total_amount)
    # 1 000 500 − 30 000
    _expect("15", "1120 balance", Decimal(970500), _balance(db, tape, "1120"))

    # Reversing the member on its own is refused — a bulk transfer is one banking act.
    member_refusal = _refuses(
        db,
        "15",
        "reverse PMT-6 alone",
        "payment_run_member",
        lambda: documents_service.reverse_document(
            db, pmt6, on_date=NOV_3, reason="wrong beneficiary", actor=tape.owner
        ),
    )
    _expect("15", "refusal names the run", True, "PYR-000002" in str(member_refusal))

    payment_run_service.reverse_run(
        db,
        tape.company_id,
        run2.id,
        reason="the transfer was recalled",
        on_date=NOV_3,
        actor=tape.owner,
    )
    db.flush()
    _expect("15", "PYR-2 status", "reversed", run2.status.value)
    _expect("15", "PMT-6 status", "reversed", pmt6.status.value)
    _expect("15", "SIN-5 open", Decimal(30000), recompute_open_amount(db, sin5))
    _expect("15", "1120 back to", Decimal(1000500), _balance(db, tape, "1120"))
    # ALC-9 mirrors ALC-8: the allocation is reversed, not deleted.
    reversal = db.scalar(
        select(Allocation).where(
            Allocation.company_id == tape.company_id,
            Allocation.reverses_allocation_id == run2_lines[0].allocation_id,
        )
    )
    _expect("15", "ALC-9 mirrors ALC-8", True, reversal is not None)

    # **These two come after the reversal, and the prompt says so by placement.** It scopes
    # "before the reversal" to the `payment_run_member` refusal alone and lists these next —
    # which is the only order that works: while `PYR-2` still stands `SIN-5` is fully paid, so a
    # run over it is refused `document_not_open` rather than `payment_exceeds_open`. The first
    # draft probed them early and got exactly that. Now `SIN-5` is open 30 000 again, and 40 000
    # exceeds it.
    _refuses(
        db,
        "15",
        "PYR-3 over the open amount",
        "payment_exceeds_open",
        lambda: payment_run_service.post_run(
            db,
            tape.company_id,
            bank_account_id=tape.bank("BK-RWF").id,
            payment_date=NOV_3,
            lines=[
                payment_run_service.RunLineInput(
                    document_id=sin5.id, amount=Decimal(40000)
                )
            ],
            actor=tape.owner,
        ),
    )
    # No number was claimed: the next run to post takes PYR-000003.
    _expect(
        "15",
        "PYR- run unbroken",
        2,
        db.scalar(
            select(func.count()).select_from(PaymentRun).where(
                PaymentRun.company_id == tape.company_id
            )
        ),
    )
    # A run from the till: a payment run moves money through a bank.
    _refuses(
        db,
        "15",
        "a run from CASH",
        "payment_run_needs_bank",
        lambda: payment_run_service.post_run(
            db,
            tape.company_id,
            bank_account_id=tape.bank("CASH").id,
            payment_date=NOV_3,
            lines=[payment_run_service.RunLineInput(document_id=sin5.id)],
            actor=tape.owner,
        ),
    )
    _after_every_row(db, tape, "15")

    # --- Row 16: reopen BRC-2 (refused) and BRC-4 (allowed) --------------------------------------
    _refuses(
        db,
        "16",
        "reopen BRC-2",
        "reconciliation_not_latest",
        lambda: reconciliation_service.reopen(
            db, tape.company_id, brc2.id, reason="a fee was restated", actor=tape.owner
        ),
    )

    brc4_matches = [
        match
        for match in matching.matches_of(db, tape.company_id, tape.bank("BK-RWF").id)
        if match.reconciliation_id == brc4.id
    ]
    _expect("16", "BRC-4 matches before reopen", 2, len(brc4_matches))

    reconciliation_service.reopen(
        db, tape.company_id, brc4.id, reason="the bank restated October", actor=tape.owner
    )
    db.flush()
    _expect("16", "BRC-4 status", "open", brc4.status.value)
    _expect("16", "BRC-4 snapshot cleared", None, brc4.outstanding_snapshot)
    # The matches **stand** — withdrawing the proof is not withdrawing the assertions that went
    # into it — but they lose their reconciliation, so they are free to be unmatched and to join
    # whatever locks next.
    still_there = [
        match
        for match in matching.matches_of(db, tape.company_id, tape.bank("BK-RWF").id)
        if match.id in {m.id for m in brc4_matches}
    ]
    _expect("16", "BRC-4 matches stand", 2, len(still_there))
    _expect(
        "16",
        "and carry no reconciliation",
        [None, None],
        [match.reconciliation_id for match in still_there],
    )
    # The cache falls back to BRC-2 — the latest still locked on this account. BRC-3 is
    # BK-USD's and untouched.
    _expect("16", "last_reconciled_at", SEP_30, tape.bank("BK-RWF").last_reconciled_at)
    _expect(
        "16",
        "last_reconciled_balance",
        Decimal(1090500),
        tape.bank("BK-RWF").last_reconciled_balance,
    )
    _expect("16", "BRC-3 untouched", "locked", tape.documents["BRC-3"].status.value)
    _after_every_row(db, tape, "16")

    # --- Row 17: void BST-3, re-import it, lock BRC-4 -------------------------------------------
    _refuses(
        db,
        "17",
        "void a matched statement",
        "statement_has_matches",
        lambda: statements_service.void(
            db, tape.company_id, bst3.id, reason="the bank resent it", actor=tape.owner
        ),
    )

    for match in still_there:
        matching.unmatch(db, tape.company_id, match.id, actor=tape.owner)
    db.flush()
    statements_service.void(
        db, tape.company_id, bst3.id, reason="the bank resent it", actor=tape.owner
    )
    db.flush()
    _expect("17", "BST-3 status", "void", bst3.status.value)
    # Its lines are out of every listing.
    _expect(
        "17",
        "void lines gone from the pane",
        0,
        len(
            [
                line
                for line in db.scalars(
                    matching.unmatched_statement_lines(
                        db, tape.company_id, tape.bank("BK-RWF")
                    )
                )
                if line.statement_id == bst3.id
            ]
        ),
    )

    # Without the evidence, the two cheques are unpresented again — which is the right answer.
    figures = reconciliation_service.live_figures(db, tape.company_id, brc4)
    _expect("17", "BRC-4 outstanding", Decimal(-90000), figures.outstanding_total)
    _expect("17", "BRC-4 difference", Decimal(-90000), figures.difference)
    _refuses(
        db,
        "17",
        "lock without the evidence",
        "reconciliation_difference",
        lambda: reconciliation_service.lock(db, tape.company_id, brc4.id, actor=tape.owner),
    )

    # The same file again — and **not** refused: a void statement's hash and fingerprints block
    # nothing.
    again = statements_service.import_statement(
        db,
        tape.company_id,
        bank_account_id=tape.bank("BK-RWF").id,
        content=overlap,
        file_name="generic-bk-rwf-overlap-oct.csv",
        actor=tape.owner,
    )
    db.flush()
    _expect("17", "re-import number", "BST-000004", again.statement.number)
    _expect("17", "re-import new", 2, again.new_count)
    _expect("17", "re-import skipped", 1, again.skipped_count)

    matching.auto_match(db, tape.company_id, tape.bank("BK-RWF").id, actor=tape.owner)
    db.flush()
    states = matching.statement_line_states(db, tape.company_id, again.statement.id)
    rules = [
        state.match_rule.value
        for state in states.values()
        if state.match_rule is not None
    ]
    _expect("17", "both cheques by reference", ["reference", "reference"], sorted(rules))

    reconciliation_service.lock(db, tape.company_id, brc4.id, actor=tape.owner)
    db.flush()
    # **The same row, the same number** — a reopen and a re-lock is not a new reconciliation.
    _expect("17", "BRC-4 number again", "BRC-000004", brc4.number)
    _expect("17", "BRC-4 difference", Decimal(0), brc4.difference)
    _after_every_row(db, tape, "17")
