"""P4 step 4 — allocations, realized FX, settlement discounts and unallocation."""

from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError, PostingError
from app.models.journal import JournalLine
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind
from app.subledger import allocations as allocations_service
from app.subledger.openitems import verify_open_items
from tests.kernel.conftest import USD_RATE, Ledger
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.conftest import MARCH, Subledger, set_terms
from tests.subledger.invariants import assert_subledger_invariants
from tests.subledger.test_documents import post_invoice, post_settlement


def _allocate(
    db: Session,
    sub: Subledger,
    *,
    debit,  # noqa: ANN001
    credit,  # noqa: ANN001
    amount: Decimal,
    discount: Decimal = Decimal(0),
    on=MARCH,  # noqa: ANN001
    role: PartnerRole = PartnerRole.AR,
):  # noqa: ANN201
    allocation, _ = allocations_service.allocate(
        db,
        sub.company_id,
        role,
        partner_id=debit.partner_id,
        allocation_date=on,
        pairs=[
            allocations_service.PairInput(
                debit_document_id=debit.id,
                credit_document_id=credit.id,
                amount=amount,
                discount_amount=discount,
            )
        ],
        actor=sub.ledger.owner,
    )
    return allocation


def test_full_settlement_in_base_currency_closes_both_sides(
    db: Session, subledger: Subledger
) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(50000))
    receipt = post_settlement(db, subledger, amount=Decimal(50000))
    db.commit()

    allocation = _allocate(
        db, subledger, debit=invoice, credit=receipt, amount=Decimal(50000)
    )
    db.commit()

    assert invoice.open_amount == Decimal(0)
    assert receipt.open_amount == Decimal(0)
    # No rate difference, no discount: nothing to post.
    assert allocation.journal_entry_id is None
    assert verify_open_items(db, subledger.company_id) == []
    assert_ledger_invariants(db, subledger.company_id)
    assert_subledger_invariants(db, subledger.company_id)


def test_part_payment_leaves_the_balance_open(db: Session, subledger: Subledger) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(50000))
    receipt = post_settlement(db, subledger, amount=Decimal(20000))
    db.commit()
    _allocate(db, subledger, debit=invoice, credit=receipt, amount=Decimal(20000))
    db.commit()
    assert invoice.open_amount == Decimal(30000)
    assert receipt.open_amount == Decimal(0)
    assert_subledger_invariants(db, subledger.company_id)


def test_realized_fx_posts_at_allocation(db: Session, subledger: Subledger) -> None:
    """USD invoice at 1300.5, receipt at 1250: the base-currency difference is a loss and
    posts against the control account, so the subledger keeps reconciling."""
    ledger = subledger.ledger
    later = MARCH + timedelta(days=20)
    _set_usd_rate(db, ledger, later, Decimal(1250))

    invoice, _ = post_invoice(
        db, subledger, amount=Decimal("100.00"), currency="USD", on=MARCH
    )
    receipt = post_settlement(
        db, subledger, amount=Decimal("100.00"), currency="USD", on=later
    )
    db.commit()

    allocation = _allocate(
        db, subledger, debit=invoice, credit=receipt, amount=Decimal("100.00"), on=later
    )
    db.commit()

    assert allocation.journal_entry_id is not None
    lines = db.scalars(
        select(JournalLine).where(JournalLine.entry_id == allocation.journal_entry_id)
    ).all()
    expected_loss = Decimal(100) * USD_RATE - Decimal(100) * Decimal(1250)
    loss = next(line for line in lines if line.gl_account_id == ledger.acct("6950"))
    control = next(
        line for line in lines if line.gl_account_id == invoice.control_account_id
    )
    assert loss.base_amount == expected_loss.quantize(Decimal(1))
    assert control.base_amount == -expected_loss.quantize(Decimal(1))
    assert control.partner_id == subledger.customer.id
    assert invoice.open_amount == Decimal(0) and receipt.open_amount == Decimal(0)
    assert_ledger_invariants(db, subledger.company_id)
    assert_subledger_invariants(db, subledger.company_id)


def test_realized_fx_on_the_ap_side_is_a_gain(db: Session, subledger: Subledger) -> None:
    ledger = subledger.ledger
    later = MARCH + timedelta(days=20)
    _set_usd_rate(db, ledger, later, Decimal(1250))

    invoice, _ = post_invoice(
        db, subledger, role=PartnerRole.AP, amount=Decimal("100.00"), currency="USD", on=MARCH
    )
    payment = post_settlement(
        db, subledger, role=PartnerRole.AP, amount=Decimal("100.00"), currency="USD", on=later
    )
    db.commit()
    allocation, _ = allocations_service.allocate(
        db,
        subledger.company_id,
        PartnerRole.AP,
        partner_id=subledger.supplier.id,
        allocation_date=later,
        pairs=[
            allocations_service.PairInput(
                debit_document_id=payment.id,
                credit_document_id=invoice.id,
                amount=Decimal("100.00"),
            )
        ],
        actor=ledger.owner,
    )
    db.commit()
    lines = db.scalars(
        select(JournalLine).where(JournalLine.entry_id == allocation.journal_entry_id)
    ).all()
    assert any(line.gl_account_id == ledger.acct("4400") for line in lines), "exchange gain"
    assert_subledger_invariants(db, subledger.company_id)


def test_settlement_discount_posts_at_allocation(db: Session, subledger: Subledger) -> None:
    ledger = subledger.ledger
    set_terms(db, subledger, PartnerRole.AR, subledger.discount_terms.id)
    db.commit()

    invoice, _ = post_invoice(db, subledger, amount=Decimal(100000))
    receipt = post_settlement(db, subledger, amount=Decimal(98000))
    db.commit()
    assert invoice.payment_terms_id == subledger.discount_terms.id

    allocation = _allocate(
        db,
        subledger,
        debit=invoice,
        credit=receipt,
        amount=Decimal(98000),
        discount=Decimal(2000),
    )
    db.commit()

    assert invoice.open_amount == Decimal(0)
    lines = db.scalars(
        select(JournalLine).where(JournalLine.entry_id == allocation.journal_entry_id)
    ).all()
    discount = next(line for line in lines if line.gl_account_id == ledger.acct("6960"))
    assert discount.base_amount == Decimal(2000)
    assert_ledger_invariants(db, subledger.company_id)
    assert_subledger_invariants(db, subledger.company_id)


def test_settlement_discount_on_the_ap_side_is_income(
    db: Session, subledger: Subledger
) -> None:
    """The mirror of the AR case: we pay less than we owe, so the supplier's control account
    is *debited* by the discount and the difference is income. Getting this sign wrong leaves
    the AP control account out by twice the discount."""
    ledger = subledger.ledger
    set_terms(db, subledger, PartnerRole.AP, subledger.discount_terms.id)
    db.commit()

    invoice, _ = post_invoice(
        db, subledger, role=PartnerRole.AP, amount=Decimal(100000)
    )
    payment = post_settlement(
        db, subledger, role=PartnerRole.AP, amount=Decimal(98000)
    )
    db.commit()

    allocation, _ = allocations_service.allocate(
        db,
        subledger.company_id,
        PartnerRole.AP,
        partner_id=subledger.supplier.id,
        allocation_date=MARCH,
        pairs=[
            allocations_service.PairInput(
                debit_document_id=payment.id,
                credit_document_id=invoice.id,
                amount=Decimal(98000),
                discount_amount=Decimal(2000),
            )
        ],
        actor=ledger.owner,
    )
    db.commit()

    assert invoice.open_amount == Decimal(0) and payment.open_amount == Decimal(0)
    lines = db.scalars(
        select(JournalLine).where(JournalLine.entry_id == allocation.journal_entry_id)
    ).all()
    income = next(line for line in lines if line.gl_account_id == ledger.acct("4350"))
    control = next(
        line for line in lines if line.gl_account_id == invoice.control_account_id
    )
    assert income.base_amount == Decimal(-2000), "discount received is income (credit)"
    assert control.base_amount == Decimal(2000), "AP control is debited by the discount"
    assert _control_balance(db, subledger, invoice.control_account_id) == Decimal(0)
    assert_ledger_invariants(db, subledger.company_id)
    assert_subledger_invariants(db, subledger.company_id)


def test_a_discount_beyond_the_terms_is_refused(db: Session, subledger: Subledger) -> None:
    set_terms(db, subledger, PartnerRole.AR, subledger.discount_terms.id)
    db.commit()
    invoice, _ = post_invoice(db, subledger, amount=Decimal(100000))
    receipt = post_settlement(db, subledger, amount=Decimal(90000))
    db.commit()
    with pytest.raises(PostingError) as excinfo:
        _allocate(
            db,
            subledger,
            debit=invoice,
            credit=receipt,
            amount=Decimal(90000),
            discount=Decimal(10000),
        )
    assert excinfo.value.code == "discount_exceeds_terms"
    db.rollback()


def test_the_discount_window_closes(db: Session, subledger: Subledger) -> None:
    set_terms(db, subledger, PartnerRole.AR, subledger.discount_terms.id)
    db.commit()
    invoice, _ = post_invoice(db, subledger, amount=Decimal(100000))
    late = MARCH + timedelta(days=25)
    receipt = post_settlement(db, subledger, amount=Decimal(98000), on=late)
    db.commit()
    with pytest.raises(PostingError) as excinfo:
        _allocate(
            db,
            subledger,
            debit=invoice,
            credit=receipt,
            amount=Decimal(98000),
            discount=Decimal(2000),
            on=late,
        )
    assert excinfo.value.code == "discount_exceeds_terms"
    db.rollback()


def test_allocation_cannot_exceed_the_open_amount(db: Session, subledger: Subledger) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(10000))
    receipt = post_settlement(db, subledger, amount=Decimal(50000))
    db.commit()
    with pytest.raises(PostingError) as excinfo:
        _allocate(db, subledger, debit=invoice, credit=receipt, amount=Decimal(20000))
    assert excinfo.value.code == "allocation_exceeds_open_amount"
    db.rollback()


def test_cross_currency_allocation_is_refused(db: Session, subledger: Subledger) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(10000), currency="RWF")
    receipt = post_settlement(db, subledger, amount=Decimal("10.00"), currency="USD")
    db.commit()
    with pytest.raises(LedgerStateError) as excinfo:
        _allocate(db, subledger, debit=invoice, credit=receipt, amount=Decimal(10))
    assert excinfo.value.code == "cross_currency_allocation_unsupported"
    db.rollback()


def test_a_credit_note_settles_an_invoice(db: Session, subledger: Subledger) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(10000))
    note, _ = post_invoice(
        db, subledger, kind=DocumentKind.CREDIT_NOTE, amount=Decimal(4000)
    )
    db.commit()
    _allocate(db, subledger, debit=invoice, credit=note, amount=Decimal(4000))
    db.commit()
    assert invoice.open_amount == Decimal(6000)
    assert note.open_amount == Decimal(0)
    assert_subledger_invariants(db, subledger.company_id)


def test_unallocate_returns_the_ledger_to_the_same_position(
    db: Session, subledger: Subledger
) -> None:
    ledger = subledger.ledger
    later = MARCH + timedelta(days=20)
    _set_usd_rate(db, ledger, later, Decimal(1250))
    invoice, _ = post_invoice(db, subledger, amount=Decimal("100.00"), currency="USD")
    receipt = post_settlement(
        db, subledger, amount=Decimal("100.00"), currency="USD", on=later
    )
    db.commit()
    before = _control_balance(db, subledger, invoice.control_account_id)

    allocation = _allocate(
        db, subledger, debit=invoice, credit=receipt, amount=Decimal("100.00"), on=later
    )
    db.commit()
    reversal = allocations_service.unallocate(
        db, allocation, on_date=later, reason="allocated in error", actor=ledger.owner
    )
    db.commit()

    assert reversal.reverses_allocation_id == allocation.id
    assert invoice.open_amount == Decimal("100.00")
    assert receipt.open_amount == Decimal("100.00")
    assert _control_balance(db, subledger, invoice.control_account_id) == before
    assert_ledger_invariants(db, subledger.company_id)
    assert_subledger_invariants(db, subledger.company_id)


def test_an_allocation_can_only_be_unallocated_once(
    db: Session, subledger: Subledger
) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(10000))
    receipt = post_settlement(db, subledger, amount=Decimal(10000))
    db.commit()
    allocation = _allocate(db, subledger, debit=invoice, credit=receipt, amount=Decimal(10000))
    db.commit()
    allocations_service.unallocate(
        db, allocation, on_date=MARCH, reason="first", actor=subledger.ledger.owner
    )
    db.commit()
    with pytest.raises(LedgerStateError) as excinfo:
        allocations_service.unallocate(
            db, allocation, on_date=MARCH, reason="second", actor=subledger.ledger.owner
        )
    assert excinfo.value.code == "allocation_already_reversed"
    db.rollback()


def test_auto_allocate_is_oldest_first(db: Session, subledger: Subledger) -> None:
    first, _ = post_invoice(db, subledger, amount=Decimal(10000), on=MARCH)
    second, _ = post_invoice(
        db, subledger, amount=Decimal(10000), on=MARCH + timedelta(days=5)
    )
    receipt = post_settlement(
        db, subledger, amount=Decimal(12000), on=MARCH + timedelta(days=10)
    )
    db.commit()

    pairs = allocations_service.auto_allocate_pairs(
        db,
        subledger.company_id,
        PartnerRole.AR,
        partner_id=subledger.customer.id,
        allocation_date=MARCH + timedelta(days=10),
    )
    assert [(pair.debit_document_id, pair.amount) for pair in pairs] == [
        (first.id, Decimal(10000)),
        (second.id, Decimal(2000)),
    ]
    allocations_service.allocate(
        db,
        subledger.company_id,
        PartnerRole.AR,
        partner_id=subledger.customer.id,
        allocation_date=MARCH + timedelta(days=10),
        pairs=pairs,
        actor=subledger.ledger.owner,
    )
    db.commit()
    assert first.open_amount == Decimal(0)
    assert second.open_amount == Decimal(8000)
    assert receipt.open_amount == Decimal(0)
    assert_subledger_invariants(db, subledger.company_id)


# --- Properties ----------------------------------------------------------------------------


@pytest.mark.slow
@given(
    invoice_amount=st.integers(min_value=100, max_value=1_000_000),
    part=st.integers(min_value=1, max_value=99),
)
def test_open_items_never_go_negative(
    db: Session, subledger: Subledger, invoice_amount: int, part: int
) -> None:
    """Each example flushes but never commits, so the rollback leaves the fixture pristine
    for the next one."""
    try:
        amount = Decimal(invoice_amount)
        paid = max((amount * part / 100).quantize(Decimal(1)), Decimal(1))
        invoice, _ = post_invoice(db, subledger, amount=amount)
        receipt = post_settlement(db, subledger, amount=paid)
        _allocate(db, subledger, debit=invoice, credit=receipt, amount=min(paid, amount))
        assert invoice.open_amount >= Decimal(0)
        assert invoice.open_amount <= invoice.total_amount
        assert verify_open_items(db, subledger.company_id) == []
        assert_subledger_invariants(db, subledger.company_id)
    finally:
        db.rollback()


@pytest.mark.slow
@given(rate=st.integers(min_value=1000, max_value=1600))
def test_full_settlement_realizes_exactly_the_booking_rate_difference(
    db: Session, subledger: Subledger, rate: int
) -> None:
    try:
        ledger = subledger.ledger
        later = MARCH + timedelta(days=20)
        _set_usd_rate(db, ledger, later, Decimal(rate))
        invoice, _ = post_invoice(db, subledger, amount=Decimal("100.00"), currency="USD")
        receipt = post_settlement(
            db, subledger, amount=Decimal("100.00"), currency="USD", on=later
        )
        _allocate(
            db, subledger, debit=invoice, credit=receipt, amount=Decimal("100.00"), on=later
        )

        expected = (Decimal(100) * USD_RATE).quantize(Decimal(1)) - (
            Decimal(100) * Decimal(rate)
        ).quantize(Decimal(1))
        fx_total = sum((line.base_amount for line in _fx_lines(db, subledger)), Decimal(0))
        assert fx_total == expected
        # Fully settled: the control account is back to zero and nothing else moved.
        assert _control_balance(db, subledger, invoice.control_account_id) == Decimal(0)
        assert_subledger_invariants(db, subledger.company_id)
    finally:
        db.rollback()


def _fx_lines(db: Session, sub: Subledger) -> list[JournalLine]:
    ledger = sub.ledger
    return list(
        db.scalars(
            select(JournalLine).where(
                JournalLine.company_id == sub.company_id,
                JournalLine.gl_account_id.in_(
                    [ledger.acct("6950"), ledger.acct("4400")]
                ),
            )
        )
    )


def _control_balance(db: Session, sub: Subledger, account_id: int) -> Decimal:
    from sqlalchemy import func

    return db.scalar(
        select(func.coalesce(func.sum(JournalLine.base_amount), Decimal(0))).where(
            JournalLine.company_id == sub.company_id,
            JournalLine.gl_account_id == account_id,
        )
    )


def _set_usd_rate(db: Session, ledger: Ledger, on, rate: Decimal) -> None:  # noqa: ANN001
    from app.models.currency import ExchangeRate

    db.add(
        ExchangeRate(
            company_id=ledger.company_id,
            currency_id=ledger.cur("USD"),
            valid_from=on,
            rate=rate,
        )
    )
    db.flush()
