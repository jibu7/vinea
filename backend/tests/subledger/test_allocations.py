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
from app.subledger import allocations as allocations_service
from app.subledger.openitems import open_items_as_of, verify_open_items
from tests.kernel.conftest import Ledger
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.conftest import MARCH, Subledger, set_terms
from tests.subledger.invariants import assert_subledger_invariants, control_balance
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


def test_full_settlement_leaves_no_base_residual_in_the_control_account(
    db: Session, subledger: Subledger
) -> None:
    """Base rounding is not additive. A USD 100.00 invoice at 1250 posts 125 000 RWF, but the
    three receipts that settle it — 33.33 + 33.33 + 33.34 — post 41 663 + 41 663 + 41 675 =
    125 001. Settling the last minor unit in document currency must settle it in base too:
    the final allocation absorbs the residual to the rounding-difference account, so the
    document's open base amount is exactly zero and so is the control account."""
    ledger = subledger.ledger
    _set_usd_rate(db, ledger, MARCH, Decimal(1250))
    db.commit()

    invoice, _ = post_invoice(
        db, subledger, amount=Decimal("100.00"), currency="USD", on=MARCH
    )
    slices = [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
    receipts = [
        post_settlement(db, subledger, amount=amount, currency="USD", on=MARCH)
        for amount in slices
    ]
    db.commit()
    # The premise: the slices really do round to one RWF more than the invoice posted.
    assert invoice.base_total_amount == Decimal(125000)
    assert sum(r.base_total_amount for r in receipts) == Decimal(125001)

    allocations = [
        _allocate(db, subledger, debit=invoice, credit=receipt, amount=amount)
        for receipt, amount in zip(receipts, slices, strict=True)
    ]
    db.commit()

    # Only the allocation that closes the invoice posts anything: the rates match, so there
    # is no realized FX, and the first two allocations leave the invoice open.
    assert [a.journal_entry_id for a in allocations[:2]] == [None, None]
    lines = db.scalars(
        select(JournalLine).where(JournalLine.entry_id == allocations[2].journal_entry_id)
    ).all()
    control = next(line for line in lines if line.gl_account_id == invoice.control_account_id)
    rounding = next(line for line in lines if line.gl_account_id == ledger.acct("6950"))
    assert control.base_amount == Decimal(1) and rounding.base_amount == Decimal(-1)
    assert rounding.description == "Settlement rounding"
    assert control.partner_id == subledger.customer.id

    assert invoice.open_amount == Decimal(0)
    settled = next(
        item
        for item in open_items_as_of(
            db, subledger.company_id, as_of=MARCH, include_settled=True
        )
        if item.document.id == invoice.id
    )
    assert settled.open_base_amount == Decimal(0), "a settled document holds no base residual"
    assert control_balance(
        db, subledger.company_id, invoice.control_account_id, as_of=MARCH
    ) == Decimal(0), "the residual is still sitting in the AR control account"
    assert_ledger_invariants(db, subledger.company_id)
    assert_subledger_invariants(db, subledger.company_id)
