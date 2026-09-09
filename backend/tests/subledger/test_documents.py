"""P4 step 3 — partner documents: the role/kind matrix, tax, numbering, credit limits,
post-dated instruments and reversal."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError
from app.models.journal import JournalEntry, JournalLine
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind, InstrumentType
from app.subledger import documents as documents_service
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.conftest import MARCH, Subledger
from tests.subledger.invariants import assert_subledger_invariants


def post_invoice(
    db: Session,
    sub: Subledger,
    *,
    role: PartnerRole = PartnerRole.AR,
    amount: Decimal = Decimal(100000),
    on: date = MARCH,
    currency: str = "RWF",
    exchange_rate: Decimal | None = None,
    tax_code: str | None = None,
    kind: DocumentKind = DocumentKind.INVOICE,
    permissions: set[str] | None = None,
    idempotency_key: str | None = None,
):  # noqa: ANN201
    ledger = sub.ledger
    document, replayed = documents_service.post_document(
        db,
        sub.company_id,
        role,
        documents_service.DocumentInput(
            kind=kind,
            partner_id=(sub.customer if role == PartnerRole.AR else sub.supplier).id,
            document_date=on,
            description="Consulting services",
            currency_id=ledger.cur(currency),
            exchange_rate=exchange_rate,
            lines=(
                documents_service.LineInput(
                    unit_price=amount,
                    gl_account_id=ledger.acct("4100" if role == PartnerRole.AR else "6990"),
                    tax_code_id=ledger.tax_codes[tax_code].id if tax_code else None,
                ),
            ),
        ),
        actor=ledger.owner,
        permissions=permissions,
        idempotency_key=idempotency_key,
    )
    return document, replayed


def post_settlement(
    db: Session,
    sub: Subledger,
    *,
    role: PartnerRole = PartnerRole.AR,
    amount: Decimal,
    on: date = MARCH,
    currency: str = "RWF",
    exchange_rate: Decimal | None = None,
    maturity_date: date | None = None,
):  # noqa: ANN201
    ledger = sub.ledger
    document, _ = documents_service.post_document(
        db,
        sub.company_id,
        role,
        documents_service.DocumentInput(
            kind=DocumentKind.SETTLEMENT,
            partner_id=(sub.customer if role == PartnerRole.AR else sub.supplier).id,
            document_date=on,
            description="Settlement",
            currency_id=ledger.cur(currency),
            exchange_rate=exchange_rate,
            amount=amount,
            cash_account_id=ledger.acct("1120"),
            instrument_type=InstrumentType.CHEQUE if maturity_date else InstrumentType.BANK,
            maturity_date=maturity_date,
        ),
        actor=ledger.owner,
    )
    return document


def test_ar_invoice_posts_to_the_control_account(db: Session, subledger: Subledger) -> None:
    document, replayed = post_invoice(db, subledger)
    db.commit()

    assert replayed is False
    assert document.role == PartnerRole.AR
    assert document.direction == 1
    assert document.number.startswith("INV-")
    assert document.total_amount == Decimal(100000)
    assert document.open_amount == Decimal(100000)
    assert document.due_date == MARCH + timedelta(days=30)

    lines = db.scalars(
        select(JournalLine).where(JournalLine.entry_id == document.journal_entry_id)
    ).all()
    control = next(
        line for line in lines if line.gl_account_id == document.control_account_id
    )
    assert control.amount == Decimal(100000)
    assert control.partner_type == "customer" and control.partner_id == subledger.customer.id
    assert_ledger_invariants(db, subledger.company_id)
    assert_subledger_invariants(db, subledger.company_id)


def test_ap_invoice_is_the_mirror_image(db: Session, subledger: Subledger) -> None:
    document, _ = post_invoice(db, subledger, role=PartnerRole.AP)
    db.commit()

    assert document.direction == -1
    assert document.number.startswith("SIN-")
    control = db.scalar(
        select(JournalLine).where(
            JournalLine.entry_id == document.journal_entry_id,
            JournalLine.gl_account_id == document.control_account_id,
        )
    )
    assert control.amount == Decimal(-100000)
    assert control.partner_type == "supplier"
    assert_subledger_invariants(db, subledger.company_id)


@pytest.mark.parametrize(
    ("role", "kind", "prefix", "direction"),
    [
        (PartnerRole.AR, DocumentKind.INVOICE, "INV-", 1),
        (PartnerRole.AR, DocumentKind.CREDIT_NOTE, "CRN-", -1),
        (PartnerRole.AP, DocumentKind.INVOICE, "SIN-", -1),
        (PartnerRole.AP, DocumentKind.CREDIT_NOTE, "DBN-", 1),
    ],
)
def test_the_role_kind_matrix(
    db: Session, subledger: Subledger, role: PartnerRole, kind: DocumentKind,
    prefix: str, direction: int,
) -> None:
    document, _ = post_invoice(db, subledger, role=role, kind=kind, amount=Decimal(5000))
    db.commit()
    assert document.number.startswith(prefix)
    assert document.direction == direction
    assert_subledger_invariants(db, subledger.company_id)


def test_tax_is_computed_per_line_half_up(db: Session, subledger: Subledger) -> None:
    document, _ = post_invoice(
        db, subledger, amount=Decimal(100000), tax_code="VAT-OUT-18"
    )
    db.commit()
    assert document.net_amount == Decimal(100000)
    assert document.tax_amount == Decimal(18000)
    assert document.total_amount == Decimal(118000)
    assert_ledger_invariants(db, subledger.company_id)
    assert_subledger_invariants(db, subledger.company_id)


def test_numbering_is_gapless_per_document_type(db: Session, subledger: Subledger) -> None:
    numbers = []
    for _ in range(3):
        document, _ = post_invoice(db, subledger, amount=Decimal(1000))
        numbers.append(document.number)
    db.commit()
    assert numbers == ["INV-000001", "INV-000002", "INV-000003"]
    assert_ledger_invariants(db, subledger.company_id)


def test_idempotency_key_replays_the_same_document(db: Session, subledger: Subledger) -> None:
    first, replayed_first = post_invoice(db, subledger, idempotency_key="key-1")
    db.commit()
    second, replayed_second = post_invoice(db, subledger, idempotency_key="key-1")
    db.commit()
    assert replayed_first is False and replayed_second is True
    assert first.id == second.id
    assert db.scalar(select(JournalEntry.id).where(JournalEntry.number == "INV-000002")) is None


def test_a_same_day_instrument_goes_straight_to_bank(
    db: Session, subledger: Subledger
) -> None:
    ledger = subledger.ledger
    receipt = post_settlement(db, subledger, amount=Decimal(20000), maturity_date=MARCH)
    db.commit()
    accounts = {
        line.gl_account_id
        for line in db.scalars(
            select(JournalLine).where(JournalLine.entry_id == receipt.journal_entry_id)
        )
    }
    assert ledger.acct("1120") in accounts


def test_a_document_cannot_be_reversed_twice(db: Session, subledger: Subledger) -> None:
    document, _ = post_invoice(db, subledger, amount=Decimal(30000))
    db.commit()
    documents_service.reverse_document(
        db, document, on_date=MARCH, reason="oops", actor=subledger.ledger.owner
    )
    db.commit()
    with pytest.raises(LedgerStateError) as excinfo:
        documents_service.reverse_document(
            db, document, on_date=MARCH, reason="again", actor=subledger.ledger.owner
        )
    assert excinfo.value.code == "document_already_reversed"
    db.rollback()


def test_a_partner_without_the_role_is_refused(db: Session, subledger: Subledger) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        documents_service.post_document(
            db,
            subledger.company_id,
            PartnerRole.AP,
            documents_service.DocumentInput(
                kind=DocumentKind.INVOICE,
                partner_id=subledger.customer.id,  # a customer, not a supplier
                document_date=MARCH,
                description="wrong role",
                lines=(
                    documents_service.LineInput(
                        unit_price=Decimal(100),
                        gl_account_id=subledger.ledger.acct("6990"),
                    ),
                ),
            ),
            actor=subledger.ledger.owner,
        )
    assert excinfo.value.code == "partner_role_missing"
    db.rollback()


def test_a_closed_period_still_blocks_a_subledger_document(
    db: Session, subledger: Subledger
) -> None:
    from app.models.fiscal import PeriodStatus

    march_period = next(
        p for p in subledger.ledger.periods if p.start_date <= MARCH <= p.end_date
    )
    march_period.status = PeriodStatus.CLOSED
    db.flush()
    with pytest.raises(Exception) as excinfo:
        post_invoice(db, subledger)
    assert getattr(excinfo.value, "code", "") == "period_not_open"
    db.rollback()
