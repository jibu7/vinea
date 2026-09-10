"""The AR/AP role matrix (P4 review, item 2).

Every posting path that exists on both sides of the subledger is asserted **once**, over a
`role` parameter, with the expected signs *derived* from `DOCUMENT_MATRIX[(role, INVOICE)]
.direction` rather than written out twice. Duplicating AR and AP tests by hand is what let
the AP settlement-discount sign bug through: the AR case was covered, the AP case was
covered for FX only, and nothing forced the pair to stay in step.

What is genuinely per-role is the *account* each side posts to — 1200/2100, 6960/4350,
1250/2150 are different GL accounts, not a sign. Those are the only per-role facts in
`ROLE_SPECS`; everything downstream of them is computed.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.errors import PostingError
from app.kernel.money import round_amount
from app.models.audit import AuditLog
from app.models.journal import JournalLine
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind, DocumentStatus, PartnerDocument
from app.subledger import allocations as allocations_service
from app.subledger import documents as documents_service
from app.subledger import enquiries
from app.subledger.common import exposure_direction
from app.subledger.documents import DOCUMENT_MATRIX
from tests.kernel.conftest import USD_RATE
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.conftest import MARCH, Subledger, set_credit_limit, set_terms
from tests.subledger.invariants import assert_subledger_invariants, control_balance
from tests.subledger.test_allocations import _set_usd_rate
from tests.subledger.test_documents import post_invoice, post_settlement

ZERO = Decimal(0)
FX_GAIN = "4400"
FX_LOSS = "6950"


@dataclass(frozen=True)
class RoleSpec:
    """The per-role facts that are *not* derivable: which GL account each leg lands on."""

    role: PartnerRole
    control: str
    discount: str
    post_dated: str
    override_permission: str

    @property
    def invoice_direction(self) -> int:
        """+1 for an AR invoice (debit the customer), -1 for a supplier invoice (credit the
        supplier). Every sign in this module is this number times something."""
        return DOCUMENT_MATRIX[(self.role, DocumentKind.INVOICE)].direction

    def partner(self, sub: Subledger):  # noqa: ANN201
        return sub.customer if self.role == PartnerRole.AR else sub.supplier


ROLE_SPECS = (
    RoleSpec(PartnerRole.AR, "1200", "6960", "1250", "ar:credit_limit_override"),
    RoleSpec(PartnerRole.AP, "2100", "4350", "2150", "ap:credit_limit_override"),
)
ROLES = pytest.mark.parametrize("spec", ROLE_SPECS, ids=lambda s: s.role.value)


def _lines(db: Session, entry_id: int) -> list[JournalLine]:
    return list(db.scalars(select(JournalLine).where(JournalLine.entry_id == entry_id)))


def _line_on(db: Session, entry_id: int, account_id: int) -> JournalLine:
    return next(line for line in _lines(db, entry_id) if line.gl_account_id == account_id)


def _allocate(
    db: Session,
    sub: Subledger,
    spec: RoleSpec,
    *,
    first: PartnerDocument,
    second: PartnerDocument,
    amount: Decimal,
    discount: Decimal = ZERO,
    on=MARCH,  # noqa: ANN001
):  # noqa: ANN201
    """An allocation always matches the +1 document against the -1 one; which of the two is
    the invoice is exactly what flips between AR and AP."""
    debit, credit = (first, second) if first.direction == 1 else (second, first)
    assert (debit.direction, credit.direction) == (1, -1)
    allocation, _ = allocations_service.allocate(
        db,
        sub.company_id,
        spec.role,
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


# --- Realized FX: gain and loss, on both sides ------------------------------------------------


@ROLES
@pytest.mark.parametrize(
    "later_rate", [Decimal(1250), Decimal(1410)], ids=["usd_weakens", "usd_strengthens"]
)
def test_realized_fx_signs_follow_the_invoice_direction(
    db: Session, subledger: Subledger, spec: RoleSpec, later_rate: Decimal
) -> None:
    """Both documents convert at their own booking rate; the base difference is realized FX.
    The same rate movement is a loss on one side of the subledger and a gain on the other,
    and that is not a special case — it is `invoice.direction` times the same number. Four
    cases (AR/AP × up/down) from one body, so a sign can never be fixed on one side only."""
    ledger = subledger.ledger
    later = MARCH + timedelta(days=20)
    _set_usd_rate(db, ledger, later, later_rate)

    invoice, _ = post_invoice(
        db, subledger, role=spec.role, amount=Decimal("100.00"), currency="USD", on=MARCH
    )
    settlement = post_settlement(
        db, subledger, role=spec.role, amount=Decimal("100.00"), currency="USD", on=later
    )
    db.commit()
    assert invoice.direction == spec.invoice_direction
    assert settlement.direction == -spec.invoice_direction

    allocation = _allocate(
        db,
        subledger,
        spec,
        first=invoice,
        second=settlement,
        amount=Decimal("100.00"),
        on=later,
    )
    db.commit()

    hundred = Decimal("100.00")
    expected = spec.invoice_direction * (
        round_amount(hundred * USD_RATE, 0) - round_amount(hundred * later_rate, 0)
    )
    assert expected != ZERO, "the fixture must actually move the rate"
    # A positive base movement on the P&L leg is a debit, i.e. a loss.
    account = FX_LOSS if expected > ZERO else FX_GAIN
    assert allocation.journal_entry_id is not None
    fx = _line_on(db, allocation.journal_entry_id, ledger.acct(account))
    control = _line_on(db, allocation.journal_entry_id, ledger.acct(spec.control))
    assert fx.base_amount == expected
    assert control.base_amount == -expected
    assert control.partner_id == spec.partner(subledger).id
    assert control_balance(
        db, subledger.company_id, ledger.acct(spec.control), as_of=later
    ) == ZERO
    assert_ledger_invariants(db, subledger.company_id)
    assert_subledger_invariants(db, subledger.company_id)


# --- Settlement discount ----------------------------------------------------------------------


@ROLES
def test_settlement_discount_signs_follow_the_invoice_direction(
    db: Session, subledger: Subledger, spec: RoleSpec
) -> None:
    """AR grants a discount (an expense, debit); AP receives one (income, credit). The
    difference is `invoice.direction`, and the control account takes the negation — get that
    wrong and the control account is out by *twice* the discount, which is the bug this
    matrix exists to make impossible."""
    ledger = subledger.ledger
    set_terms(db, subledger, spec.role, subledger.discount_terms.id)
    db.commit()

    invoice, _ = post_invoice(db, subledger, role=spec.role, amount=Decimal(100000))
    settlement = post_settlement(db, subledger, role=spec.role, amount=Decimal(98000))
    db.commit()

    allocation = _allocate(
        db,
        subledger,
        spec,
        first=invoice,
        second=settlement,
        amount=Decimal(98000),
        discount=Decimal(2000),
    )
    db.commit()

    expected = spec.invoice_direction * Decimal(2000)
    discount = _line_on(db, allocation.journal_entry_id, ledger.acct(spec.discount))
    control = _line_on(db, allocation.journal_entry_id, ledger.acct(spec.control))
    assert discount.base_amount == expected
    assert control.base_amount == -expected
    assert invoice.open_amount == ZERO and settlement.open_amount == ZERO
    assert control_balance(
        db, subledger.company_id, ledger.acct(spec.control), as_of=MARCH
    ) == ZERO
    assert_ledger_invariants(db, subledger.company_id)
    assert_subledger_invariants(db, subledger.company_id)


# --- Credit note / debit note -----------------------------------------------------------------


@ROLES
def test_a_credit_note_settles_an_invoice_on_both_sides(
    db: Session, subledger: Subledger, spec: RoleSpec
) -> None:
    """`(ar, credit_note)` is a customer credit note and `(ap, credit_note)` is a return to
    supplier — opposite signs, one code path."""
    ledger = subledger.ledger
    invoice, _ = post_invoice(db, subledger, role=spec.role, amount=Decimal(40000))
    note, _ = post_invoice(
        db, subledger, role=spec.role, kind=DocumentKind.CREDIT_NOTE, amount=Decimal(40000)
    )
    db.commit()
    assert note.direction == -spec.invoice_direction

    _allocate(db, subledger, spec, first=invoice, second=note, amount=Decimal(40000))
    db.commit()

    assert invoice.open_amount == ZERO and note.open_amount == ZERO
    assert control_balance(
        db, subledger.company_id, ledger.acct(spec.control), as_of=MARCH
    ) == ZERO
    assert_ledger_invariants(db, subledger.company_id)
    assert_subledger_invariants(db, subledger.company_id)


# --- Reversal ---------------------------------------------------------------------------------


@ROLES
def test_reversal_unwinds_the_open_item_and_the_control_account(
    db: Session, subledger: Subledger, spec: RoleSpec
) -> None:
    ledger = subledger.ledger
    invoice, _ = post_invoice(db, subledger, role=spec.role, amount=Decimal(30000))
    db.commit()
    control = ledger.acct(spec.control)
    assert control_balance(
        db, subledger.company_id, control, as_of=MARCH
    ) == spec.invoice_direction * Decimal(30000)

    documents_service.reverse_document(
        db, invoice, on_date=MARCH, reason="raised in error", actor=ledger.owner
    )
    db.commit()

    assert invoice.status == DocumentStatus.REVERSED
    assert invoice.open_amount == ZERO and invoice.reversed_on == MARCH
    assert control_balance(db, subledger.company_id, control, as_of=MARCH) == ZERO
    assert_ledger_invariants(db, subledger.company_id)
    assert_subledger_invariants(db, subledger.company_id)


# --- Unallocation -----------------------------------------------------------------------------


@ROLES
def test_unallocation_returns_the_control_account_to_where_it_was(
    db: Session, subledger: Subledger, spec: RoleSpec
) -> None:
    """With an FX difference there is a real journal entry to reverse, so this exercises the
    frozen-base reversal on both sides rather than the do-nothing path."""
    ledger = subledger.ledger
    later = MARCH + timedelta(days=20)
    _set_usd_rate(db, ledger, later, Decimal(1250))
    invoice, _ = post_invoice(
        db, subledger, role=spec.role, amount=Decimal("100.00"), currency="USD", on=MARCH
    )
    settlement = post_settlement(
        db, subledger, role=spec.role, amount=Decimal("100.00"), currency="USD", on=later
    )
    db.commit()
    control = ledger.acct(spec.control)
    before = control_balance(db, subledger.company_id, control, as_of=later)

    allocation = _allocate(
        db, subledger, spec, first=invoice, second=settlement, amount=Decimal("100.00"), on=later
    )
    db.commit()
    assert allocation.journal_entry_id is not None

    reversal = allocations_service.unallocate(
        db, allocation, on_date=later, reason="matched in error", actor=ledger.owner
    )
    db.commit()

    assert reversal.reverses_allocation_id == allocation.id
    assert control_balance(db, subledger.company_id, control, as_of=later) == before
    assert invoice.open_amount == Decimal("100.00")
    assert settlement.open_amount == Decimal("100.00")
    assert_ledger_invariants(db, subledger.company_id)
    assert_subledger_invariants(db, subledger.company_id)


# --- Post-dated instruments -------------------------------------------------------------------


@ROLES
def test_a_post_dated_instrument_waits_in_its_own_account_until_maturity(
    db: Session, subledger: Subledger, spec: RoleSpec
) -> None:
    """A post-dated cheque is not cash. It sits in 1250 (receivable) or 2150 (payable) and
    only crosses to the bank when it matures — the same two-step on both sides, against
    mirror-image accounts."""
    ledger = subledger.ledger
    maturity = MARCH + timedelta(days=45)
    instrument = post_settlement(
        db, subledger, role=spec.role, amount=Decimal(20000), maturity_date=maturity
    )
    db.commit()

    accounts = {line.gl_account_id for line in _lines(db, instrument.journal_entry_id)}
    assert ledger.acct(spec.post_dated) in accounts
    assert ledger.acct("1120") not in accounts, "not in the bank until it matures"
    assert instrument.is_pending_instrument

    matured = documents_service.mature_instruments(
        db, subledger.company_id, as_of=maturity, actor=ledger.owner, role=spec.role
    )
    db.commit()
    assert [document.id for document in matured] == [instrument.id]

    transfer = _lines(db, instrument.matured_entry_id)
    assert {line.gl_account_id for line in transfer} == {
        ledger.acct(spec.post_dated),
        ledger.acct("1120"),
    }
    # The instrument's own direction says which way the cash moves: a receipt debits the
    # bank, a payment credits it.
    bank = next(line for line in transfer if line.gl_account_id == ledger.acct("1120"))
    assert bank.base_amount == -instrument.direction * Decimal(20000)
    assert sum(line.base_amount for line in transfer) == ZERO
    assert_ledger_invariants(db, subledger.company_id)
    assert_subledger_invariants(db, subledger.company_id)


@ROLES
def test_an_instrument_is_listed_before_it_is_due_not_only_once_it_is(
    db: Session, subledger: Subledger, spec: RoleSpec
) -> None:
    """`pending_instruments` answers "what would a run move today?" — which is the wrong
    question for a screen. A cheque a month out has to be visible the day it is banked, or
    the only way to learn it exists is to run maturity and watch what happens."""
    maturity = MARCH + timedelta(days=45)
    instrument = post_settlement(
        db, subledger, role=spec.role, amount=Decimal(20000), maturity_date=maturity
    )
    db.commit()

    assert documents_service.pending_instruments(
        db, subledger.company_id, as_of=MARCH, role=spec.role
    ) == []
    outstanding = documents_service.outstanding_instruments(
        db, subledger.company_id, role=spec.role
    )
    assert [document.id for document in outstanding] == [instrument.id]

    # Once matured it leaves both lists: the cash has landed, there is nothing left to bank.
    documents_service.mature_instruments(
        db, subledger.company_id, as_of=maturity, actor=subledger.ledger.owner, role=spec.role
    )
    db.commit()
    assert documents_service.outstanding_instruments(
        db, subledger.company_id, role=spec.role
    ) == []


@ROLES
def test_exposure_direction_matches_the_invoice_row(
    db: Session, subledger: Subledger, spec: RoleSpec
) -> None:
    """`exposure_direction` is declared in `common` because `documents` imports that module
    and not the reverse, which makes it a second statement of a fact the document matrix
    already holds. This is the seam where they would drift apart."""
    assert exposure_direction(spec.role) == spec.invoice_direction


@ROLES
def test_credit_headroom_falls_as_the_partner_owes_more_in_both_roles(
    db: Session, subledger: Subledger, spec: RoleSpec
) -> None:
    """Headroom is the limit less what is outstanding *in the role's own sense*. AP balances
    are negative — the payable sits on the credit side — so subtracting one straight from the
    limit made owing a supplier increase their headroom, on the AP enquiry and on the partner
    typeahead that reads it."""
    set_credit_limit(db, subledger, role=spec.role, limit=Decimal(100_000))
    partner = spec.partner(subledger)
    before = enquiries.partner_enquiry(
        db, subledger.company_id, spec.role, partner.id, as_of=MARCH
    )
    assert before.credit_available == Decimal(100_000)

    post_invoice(db, subledger, role=spec.role, amount=Decimal(30_000))
    db.commit()

    after = enquiries.partner_enquiry(
        db, subledger.company_id, spec.role, partner.id, as_of=MARCH
    )
    assert after.exposure_base == Decimal(30_000), "outstanding, in the role's own sense"
    assert after.credit_available == Decimal(70_000)


# --- Credit limit -----------------------------------------------------------------------------


@ROLES
def test_the_credit_limit_blocks_the_post_and_the_override_clears_it(
    db: Session, subledger: Subledger, spec: RoleSpec
) -> None:
    """Exposure is what the partner relationship is worth in the role's own sense: what
    customers owe us, what we owe suppliers. Both are `invoice.direction × Σ signed open
    items`, and a document that *increases* it is the one whose direction matches the
    invoice's — a receipt or a credit note reduces exposure and is never checked."""
    set_credit_limit(db, subledger, spec.role, Decimal(50000))
    with pytest.raises(PostingError) as excinfo:
        post_invoice(db, subledger, role=spec.role, amount=Decimal(60000))
    assert excinfo.value.code == "credit_limit_exceeded"
    assert "10000" in excinfo.value.message, "the message names the excess"
    db.rollback()

    set_credit_limit(db, subledger, spec.role, Decimal(50000))
    document, _ = post_invoice(
        db,
        subledger,
        role=spec.role,
        amount=Decimal(60000),
        permissions={spec.override_permission},
    )
    db.commit()
    assert document.total_amount == Decimal(60000)

    override = db.scalars(
        select(AuditLog).where(AuditLog.action == "partner_document.credit_limit_override")
    ).all()
    assert len(override) == 1
    assert override[0].after["role"] == spec.role.value
    assert Decimal(override[0].after["excess"]) == Decimal(10000)
    assert Decimal(override[0].after["exposure"]) == Decimal(60000)


@ROLES
def test_a_document_that_reduces_exposure_never_trips_the_credit_limit(
    db: Session, subledger: Subledger, spec: RoleSpec
) -> None:
    set_credit_limit(db, subledger, spec.role, Decimal(0))
    note, _ = post_invoice(
        db, subledger, role=spec.role, kind=DocumentKind.CREDIT_NOTE, amount=Decimal(1000)
    )
    db.commit()
    assert note.direction == -spec.invoice_direction


@ROLES
def test_a_zero_credit_limit_means_no_credit(
    db: Session, subledger: Subledger, spec: RoleSpec
) -> None:
    set_credit_limit(db, subledger, spec.role, Decimal(0))
    with pytest.raises(PostingError) as excinfo:
        post_invoice(db, subledger, role=spec.role, amount=Decimal(1))
    assert excinfo.value.code == "credit_limit_exceeded"
    db.rollback()


# --- The FX exactness property, on both sides -------------------------------------------------


@pytest.mark.slow
@ROLES
@given(rate=st.integers(min_value=1000, max_value=1600))
def test_full_settlement_realizes_exactly_the_booking_rate_difference(
    db: Session, subledger: Subledger, spec: RoleSpec, rate: int
) -> None:
    """Whatever the rate does, a fully settled document leaves its control account at exactly
    zero and the realized FX is exactly the booking-rate difference — no residue, on either
    side. Hypothesis sweeps the closing rate across the booking rate in both directions."""
    try:
        ledger = subledger.ledger
        later = MARCH + timedelta(days=20)
        _set_usd_rate(db, ledger, later, Decimal(rate))
        invoice, _ = post_invoice(
            db, subledger, role=spec.role, amount=Decimal("100.00"), currency="USD", on=MARCH
        )
        settlement = post_settlement(
            db, subledger, role=spec.role, amount=Decimal("100.00"), currency="USD", on=later
        )
        _allocate(
            db,
            subledger,
            spec,
            first=invoice,
            second=settlement,
            amount=Decimal("100.00"),
            on=later,
        )

        hundred = Decimal("100.00")
        expected = spec.invoice_direction * (
            round_amount(hundred * USD_RATE, 0) - round_amount(hundred * Decimal(rate), 0)
        )
        fx_total = sum(
            (
                line.base_amount
                for line in db.scalars(
                    select(JournalLine).where(
                        JournalLine.company_id == subledger.company_id,
                        JournalLine.gl_account_id.in_(
                            [ledger.acct(FX_LOSS), ledger.acct(FX_GAIN)]
                        ),
                    )
                )
            ),
            ZERO,
        )
        assert fx_total == expected
        assert control_balance(
            db, subledger.company_id, ledger.acct(spec.control), as_of=later
        ) == ZERO
        assert_subledger_invariants(db, subledger.company_id)
    finally:
        db.rollback()
