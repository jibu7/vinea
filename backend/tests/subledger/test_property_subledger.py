"""The compounding-error property (P4 review, item 12).

One Hypothesis test drives a *random sequence* of post / allocate / unallocate / reverse in
RWF and USD at moving exchange rates, and asserts the whole of `assert_subledger_invariants`
plus `assert_ledger_invariants` **after every step**. Checking only the end state hides an
error that is introduced by one operation and masked by the next; this is the test that
catches it.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.money import quantum, round_amount
from app.models.currency import Currency, ExchangeRate
from app.models.partner import PartnerRole
from app.models.subledger import Allocation, DocumentKind, DocumentStatus, PartnerDocument
from app.subledger import allocations as allocations_service
from app.subledger import documents as documents_service
from app.subledger.openitems import verify_open_items
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.conftest import MARCH, Subledger
from tests.subledger.invariants import assert_subledger_invariants

# Operations the machine can attempt. Illegal moves (nothing to allocate, a document that is
# already allocated) are skipped, not failed — the point is the invariants, not the plumbing.
OPERATIONS = ("invoice", "credit_note", "settlement", "allocate", "unallocate", "reverse")

CURRENCIES = ("RWF", "USD")
RATES = (Decimal(1250), Decimal("1300.5"), Decimal(1410))
HUNDRED = Decimal(100)


@dataclass
class _Clock:
    """Every step happens one day later, so allocations never predate their documents and a
    reversal is never dated before the thing it reverses."""

    day: int = 0
    rates_set: set[date] = field(default_factory=set)

    def tick(self) -> date:
        self.day += 1
        return MARCH + timedelta(days=self.day)


def _ensure_rate(db: Session, sub: Subledger, on: date, rate: Decimal, clock: _Clock) -> None:
    if on in clock.rates_set:
        return
    exists = db.scalar(
        select(ExchangeRate.id).where(
            ExchangeRate.company_id == sub.company_id,
            ExchangeRate.currency_id == sub.ledger.cur("USD"),
            ExchangeRate.valid_from == on,
        )
    )
    if exists is None:
        db.add(
            ExchangeRate(
                company_id=sub.company_id,
                currency_id=sub.ledger.cur("USD"),
                valid_from=on,
                rate=rate,
            )
        )
        db.flush()
    clock.rates_set.add(on)


def _post(
    db: Session,
    sub: Subledger,
    *,
    kind: DocumentKind,
    on: date,
    currency: str,
    amount: Decimal,
) -> None:
    ledger = sub.ledger
    data = documents_service.DocumentInput(
        kind=kind,
        partner_id=sub.customer.id,
        document_date=on,
        description=f"{kind.value} {on}",
        currency_id=ledger.cur(currency),
        lines=()
        if kind == DocumentKind.SETTLEMENT
        else (
            documents_service.LineInput(unit_price=amount, gl_account_id=ledger.acct("4100")),
        ),
        amount=amount if kind == DocumentKind.SETTLEMENT else None,
        cash_account_id=ledger.acct("1120") if kind == DocumentKind.SETTLEMENT else None,
    )
    documents_service.post_document(
        db, sub.company_id, PartnerRole.AR, data, actor=ledger.owner
    )


def _open_documents(db: Session, sub: Subledger) -> list[PartnerDocument]:
    return list(
        db.scalars(
            select(PartnerDocument)
            .where(
                PartnerDocument.company_id == sub.company_id,
                PartnerDocument.status == DocumentStatus.POSTED,
                PartnerDocument.open_amount > 0,
            )
            .order_by(PartnerDocument.id)
        )
    )


def _try_allocate(db: Session, sub: Subledger, on: date, magnitude: int) -> None:
    """`magnitude` also picks what *fraction* of the largest possible slice to take.

    Always allocating the maximum settles a document in as few slices as the documents allow,
    which never exercises base-currency rounding across many uneven slices — and that is where
    the settlement residual lives (`_settlement_residuals`). Taking a ragged fraction is what
    makes a document close on its third or fourth partial instead of its first.
    """
    documents = _open_documents(db, sub)
    debits = [d for d in documents if d.direction == 1 and d.document_date <= on]
    credits = [d for d in documents if d.direction == -1 and d.document_date <= on]
    for debit in debits:
        for credit in credits:
            if debit.currency_id != credit.currency_id:
                continue
            largest = min(debit.open_amount, credit.open_amount)
            if largest <= 0:
                continue
            places = db.get(Currency, debit.currency_id).decimal_places
            portion = round_amount(largest * Decimal(magnitude % 100 + 1) / HUNDRED, places)
            amount = min(largest, max(portion, quantum(places)))
            if amount <= 0:
                continue
            allocations_service.allocate(
                db,
                sub.company_id,
                PartnerRole.AR,
                partner_id=sub.customer.id,
                allocation_date=on,
                pairs=[
                    allocations_service.PairInput(
                        debit_document_id=debit.id,
                        credit_document_id=credit.id,
                        amount=amount,
                    )
                ],
                actor=sub.ledger.owner,
            )
            return


def _try_unallocate(db: Session, sub: Subledger, on: date) -> None:
    reversed_ids = set(
        db.scalars(
            select(Allocation.reverses_allocation_id).where(
                Allocation.company_id == sub.company_id,
                Allocation.reverses_allocation_id.is_not(None),
            )
        )
    )
    candidate = db.scalars(
        select(Allocation)
        .where(
            Allocation.company_id == sub.company_id,
            Allocation.reverses_allocation_id.is_(None),
            Allocation.allocation_date <= on,
        )
        .order_by(Allocation.id)
    ).all()
    for allocation in candidate:
        if allocation.id in reversed_ids:
            continue
        allocations_service.unallocate(
            db, allocation, on_date=on, reason="property test", actor=sub.ledger.owner
        )
        return


def _try_reverse(db: Session, sub: Subledger, on: date) -> None:
    for document in db.scalars(
        select(PartnerDocument)
        .where(
            PartnerDocument.company_id == sub.company_id,
            PartnerDocument.status == DocumentStatus.POSTED,
            PartnerDocument.document_date <= on,
        )
        .order_by(PartnerDocument.id)
    ):
        if document.open_amount != document.total_amount or document.matured_entry_id:
            continue
        documents_service.reverse_document(
            db, document, on_date=on, reason="property test", actor=sub.ledger.owner
        )
        return


@pytest.mark.slow
@given(
    steps=st.lists(
        st.tuples(
            st.sampled_from(OPERATIONS),
            st.sampled_from(CURRENCIES),
            st.integers(min_value=1, max_value=500),
            st.sampled_from(RATES),
        ),
        min_size=4,
        max_size=14,
    )
)
def test_the_subledger_survives_an_arbitrary_sequence(
    db: Session, subledger: Subledger, steps: list[tuple[str, str, int, Decimal]]
) -> None:
    clock = _Clock()
    try:
        for operation, currency, magnitude, rate in steps:
            on = clock.tick()
            amount = (
                Decimal(magnitude)
                if currency == "RWF"
                else Decimal(magnitude).scaleb(-2).quantize(Decimal("0.01"))
            )
            if amount <= 0:
                continue
            if currency == "USD":
                _ensure_rate(db, subledger, on, rate, clock)
            try:
                if operation in ("invoice", "credit_note", "settlement"):
                    _post(
                        db,
                        subledger,
                        kind=DocumentKind(
                            {"invoice": "invoice", "credit_note": "credit_note"}.get(
                                operation, "settlement"
                            )
                        ),
                        on=on,
                        currency=currency,
                        amount=amount,
                    )
                elif operation == "allocate":
                    _try_allocate(db, subledger, on, magnitude)
                elif operation == "unallocate":
                    _try_unallocate(db, subledger, on)
                else:
                    _try_reverse(db, subledger, on)
            except (PostingError, LedgerStateError):
                # A refused operation is a legitimate outcome; what matters is that the
                # refusal leaves the ledger exactly as it was.
                db.rollback()

            db.flush()
            assert verify_open_items(db, subledger.company_id) == []
            assert_ledger_invariants(db, subledger.company_id)
            assert_subledger_invariants(db, subledger.company_id)
    finally:
        db.rollback()
