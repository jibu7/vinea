"""Fixtures for the banking suite.

One tenant, three bank accounts and the one shape the phase turns on: `BK-RWF` over `1120` in
the base currency, `CASH` over `1110`, and `BK-USD` over a `1121` created **through the chart
of accounts** so that its master row appears by the hook rather than by the fixture. That last
one is not convenience — "the hook fires on the ordinary create path" is the claim clause 6
exists to catch, and a fixture that inserted the row itself would test nothing about it.
"""

import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.banking import accounts as accounts_service
from app.banking import statements as statements_service
from app.banking.formats import ParsedLine
from app.kernel import accounts as kernel_accounts
from app.kernel import posting
from app.kernel.events import CashbookEntry, CashbookKind, CashbookLineSpec
from app.models.banking import BankAccount
from app.models.gl import AccountClass, ControlType, GLAccount
from app.models.journal import JournalEntry, JournalLine
from app.models.partner import Partner, PartnerRole, TaxMode
from app.subledger import masters as partner_masters
from tests.kernel.conftest import Ledger, build_ledger

# Re-exported the way `tests/fiscal/conftest.py` re-exports it: several tests here are about
# what a tenant looks like *before* any bank account is renamed or given a currency, and they
# need the plain seeded ledger rather than this module's three-account one.
from tests.kernel.conftest import ledger as ledger  # noqa: PLC0414

SAMPLES = Path(__file__).parent / "samples"
#: The owner's real exports and their mappings (precondition (d)). Outside `backend/`, so read
#: through `REPO_ROOT` inside the container, where the repo is mounted at `/repo`.
REAL_SAMPLES = (
    Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[3])
    / "docs"
    / "banking"
    / "samples"
)


def sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


@dataclass
class Banking:
    ledger: Ledger
    accounts: dict[str, BankAccount]
    #: One of each, so the post-from-a-statement-line drawer has a partner to post a receipt
    #: or a payment against. The subledger suite's own pair, built the same way.
    customer: Partner
    supplier: Partner

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

    customer = partner_masters.create_partner(
        db,
        ledger.company_id,
        partner_masters.PartnerInput(name="Amahoro Retail Ltd", customer_code="CUST001"),
        actor=ledger.owner,
    )
    supplier = partner_masters.create_partner(
        db,
        ledger.company_id,
        partner_masters.PartnerInput(name="Rwanda Paper Supplies", supplier_code="SUPP001"),
        actor=ledger.owner,
    )
    for partner, role in ((customer, PartnerRole.AR), (supplier, PartnerRole.AP)):
        partner_masters.upsert_role_settings(
            db,
            ledger.company_id,
            partner,
            role,
            partner_masters.RoleSettingsInput(
                default_gl_account_id=ledger.acct(
                    "4100" if role == PartnerRole.AR else "6990"
                ),
                tax_mode=TaxMode.EXCLUSIVE,
            ),
            actor=ledger.owner,
        )

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
        customer=customer,
        supplier=supplier,
    )


@pytest.fixture
def banking(db: Session) -> Banking:
    return build_banking(db)


# --- Posting helpers the matching and reconciliation suites share --------------------------------
#
# Every one of these goes through the kernel or through P4, because a fixture that wrote a
# journal line itself would be testing the matcher against rows the engine would never produce
# — and `tests/banking/test_boundary.py` exists to say that nothing does.


def cashbook(
    db: Session,
    banking: Banking,
    *,
    account_code: str,
    amount: Decimal,
    on: date,
    contra: str = "3400",
    currency: str = "RWF",
    reference: str | None = None,
    description: str = "opening",
) -> JournalEntry:
    """A cashbook receipt (positive) or payment (negative) on a bank account."""
    entry = posting.post(
        db,
        CashbookEntry(
            entry_date=on,
            description=description,
            reference=reference,
            cash_account_id=banking.ledger.acct(account_code),
            kind=CashbookKind.RECEIPT if amount > 0 else CashbookKind.PAYMENT,
            currency_id=banking.ledger.cur(currency),
            lines=(
                CashbookLineSpec(
                    gl_account_id=banking.ledger.acct(contra), amount=abs(amount)
                ),
            ),
        ),
        company_id=banking.company_id,
        actor=banking.owner,
    )
    assert entry is not None
    return entry


def bank_line_of(db: Session, banking: Banking, entry: JournalEntry, account_code: str):  # noqa: ANN201
    """The line of this entry that sits on the bank account — what a match points at."""
    return db.scalars(
        select(JournalLine).where(
            JournalLine.entry_id == entry.id,
            JournalLine.gl_account_id == banking.ledger.acct(account_code),
        )
    ).one()


def import_sample(db: Session, banking: Banking, name: str, *, account: str = "BK-RWF"):  # noqa: ANN201
    return statements_service.import_statement(
        db,
        banking.company_id,
        bank_account_id=banking.bank(account).id,
        content=sample(name),
        file_name=name,
        actor=banking.owner,
    )


def key_statement(
    db: Session,
    banking: Banking,
    *,
    account: str = "BK-RWF",
    lines: list[tuple[date, str, Decimal]],
    opening: Decimal = Decimal(0),
    closing: Decimal | None = None,
):  # noqa: ANN201
    """A statement keyed line by line — `(value date, description, credit-positive amount)`.

    The suites use this rather than a CSV wherever the *file* is not what is under test: a
    reconciliation cares about dates, amounts and text, and building a CSV to express three of
    those is a parser test wearing a reconciliation test's clothes.
    """
    parsed = [
        ParsedLine(
            row=index,
            value_date=value_date,
            booking_date=None,
            description=description,
            reference=None,
            amount=amount,
            balance_after=None,
            external_id=None,
            occurrence=sum(
                1
                for earlier in lines[: index - 1]
                if (earlier[0], earlier[1], earlier[2]) == (value_date, description, amount)
            ),
        )
        for index, (value_date, description, amount) in enumerate(lines, start=1)
    ]
    total = sum((amount for _, _, amount in lines), Decimal(0))
    return statements_service.import_manual(
        db,
        banking.company_id,
        bank_account_id=banking.bank(account).id,
        lines=parsed,
        opening_balance=opening,
        closing_balance=opening + total if closing is None else closing,
        actor=banking.owner,
    )


# --- Payment-run helpers (step 3) ----------------------------------------------------------------


def ap_supplier(
    db: Session,
    banking: Banking,
    *,
    name: str,
    code: str,
    bank_details: bool = True,
    terms_code: str | None = None,
) -> Partner:
    """A supplier with, or deliberately without, the bank details a run's instruction file
    reads. `terms_code` is a seeded `payment_terms` row — `2/10N30` is the discount window the
    tape's S2 pays inside."""
    from app.models.partner import PaymentTerms

    partner = partner_masters.create_partner(
        db,
        banking.company_id,
        partner_masters.PartnerInput(name=name, supplier_code=code),
        actor=banking.owner,
    )
    terms_id = None
    if terms_code is not None:
        terms_id = db.scalars(
            select(PaymentTerms).where(
                PaymentTerms.company_id == banking.company_id,
                PaymentTerms.code == terms_code,
            )
        ).one().id
    partner_masters.upsert_role_settings(
        db,
        banking.company_id,
        partner,
        PartnerRole.AP,
        partner_masters.RoleSettingsInput(
            default_gl_account_id=banking.ledger.acct("6990"),
            tax_mode=TaxMode.EXCLUSIVE,
            payment_terms_id=terms_id,
        ),
        actor=banking.owner,
    )
    if bank_details:
        partner_masters.update_partner(
            db,
            partner,
            bank_name="Bank of Kigali",
            bank_account_number=f"00040-{code}-01",
            bank_account_holder=name,
            actor=banking.owner,
        )
    db.flush()
    return partner


def ap_invoice(
    db: Session,
    banking: Banking,
    partner: Partner,
    *,
    amount: Decimal,
    on: date,
    currency: str = "RWF",
):  # noqa: ANN201
    """A posted supplier invoice, through P4 — the only way one exists."""
    from app.models.subledger import DocumentKind
    from app.subledger import documents as documents_service

    document, _ = documents_service.post_document(
        db,
        banking.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=partner.id,
            document_date=on,
            description=f"Supplies from {partner.name}",
            currency_id=banking.ledger.cur(currency),
            lines=(
                documents_service.LineInput(
                    unit_price=amount, gl_account_id=banking.ledger.acct("6990")
                ),
            ),
        ),
        actor=banking.owner,
    )
    db.flush()
    return document
