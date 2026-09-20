"""Filing a return, reversing it, and what a late entry does to the next one.

The arithmetic is the `month` fixture's, worked by hand in `test_vat_return.py` and repeated
here only where a figure is asserted: output VAT 3 600, input VAT 9 000, so the net is a
**credit** of 5 400. The settlement entry is the interesting part — it has to move exactly what
was declared off the VAT accounts and leave the difference on one account an accountant reads,
and it has to do that without its own lines becoming tax on the next return.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel import posting
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import LineSpec, ManualJournal, ReversalRequested
from app.models.fiscalization import VatReturn, VatReturnStatus
from app.models.gl import GLAccount
from app.models.journal import JournalEntry, JournalLine
from app.tax import vat
from tests.fiscal.conftest import FiscalPosting
from tests.kernel.conftest import YEAR
from tests.kernel.invariants import assert_ledger_invariants

from .test_vat_return import APRIL_FROM, APRIL_TO, MARCH_FROM, MARCH_TO, month  # noqa: F401

#: A date inside March that is *after* the fixture's postings, so an entry dated here is late by
#: arrival rather than by date — which is the only thing that makes it late.
LATE_IN_MARCH = date(YEAR, 3, 20)


def _settlement_of(db: Session, filed: VatReturn) -> dict[str, Decimal]:
    """The settlement entry as {account code: base amount}."""
    assert filed.journal_entry_id is not None
    rows = db.execute(
        select(GLAccount.code, JournalLine.base_amount)
        .join(
            JournalLine,
            (JournalLine.gl_account_id == GLAccount.id)
            & (JournalLine.company_id == GLAccount.company_id),
        )
        .where(
            JournalLine.company_id == filed.company_id,
            JournalLine.entry_id == filed.journal_entry_id,
        )
    )
    return {code: amount for code, amount in rows}


def _accounts(db: Session, company_id: int, *codes: str) -> dict[str, int]:
    return {
        row.code: row.id
        for row in db.execute(
            select(GLAccount.code, GLAccount.id).where(
                GLAccount.company_id == company_id, GLAccount.code.in_(codes)
            )
        )
    }


def _backdated_sale(db: Session, fixture: FiscalPosting, *, on: date = LATE_IN_MARCH) -> None:
    """A sale of 1 000 + 180 VAT posted by hand into the month, the way a correction arrives.

    Three lines because a manual journal is taken as keyed: the engine derives a tax line for a
    *document*, not for a journal somebody wrote. That is also what makes this a faithful late
    entry — it is shaped exactly like the postings the return already reads.
    """
    accounts = _accounts(db, fixture.company_id, "4100", "2200", "1500")
    code = fixture.tax_codes["VAT-OUT-18"]
    posting.post(
        db,
        ManualJournal(
            entry_date=on,
            description="Invoice missed at the month end",
            lines=(
                LineSpec(amount=Decimal("1180"), gl_account_id=accounts["1500"]),
                LineSpec(
                    amount=Decimal("-1000"),
                    gl_account_id=accounts["4100"],
                    tax_code_id=code.id,
                    tax_amount=Decimal("-180"),
                ),
                LineSpec(
                    amount=Decimal("-180"),
                    gl_account_id=accounts["2200"],
                    tax_code_id=code.id,
                ),
            ),
        ),
        company_id=fixture.company_id,
        actor=fixture.owner,
    )
    db.flush()


def test_filing_moves_what_was_declared_and_nets_the_rest_to_one_account(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    filed = vat.file_return(
        db,
        month.company_id,
        period_from=MARCH_FROM,
        period_to=MARCH_TO,
        actor=month.owner,
    )
    db.flush()

    assert filed.number == "VATR-000001"
    assert filed.status is VatReturnStatus.POSTED
    assert filed.output_vat == Decimal("3600.000000")
    assert filed.input_vat == Decimal("9000.000000")
    assert filed.net_payable == Decimal("-5400.000000")

    # Declaring output VAT credited 2200, so settling it debits 2200 by the same 3 600; input
    # VAT was debited, so it is credited. The residue is the net, and it lands on 2250 as a
    # debit because this month is a credit position — one account, either sign.
    assert _settlement_of(db, filed) == {
        "2200": Decimal("3600.000000"),
        "1400": Decimal("-9000.000000"),
        "2250": Decimal("5400.000000"),
    }

    # And the figures are a snapshot, not a view: the row carries what was submitted.
    assert filed.figures["sections"]["sales_standard_vat"] == "3600.000000"
    assert filed.figures["sections"]["net_payable"] == "-5400.000000"
    assert filed.high_water_entry_id > 0


def test_the_settlement_entry_is_not_tax_on_the_next_return(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    """The lines sit on the VAT accounts carrying the same codes. Only the module says they are
    a payment rather than tax — so if that discrimination broke, April would declare March's
    settlement as its own output VAT."""
    vat.file_return(
        db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO, actor=month.owner
    )
    db.flush()

    april = vat.compute(db, month.company_id, period_from=APRIL_FROM, period_to=APRIL_TO)
    assert april.output_vat == Decimal(0)
    assert april.input_vat == Decimal(0)

    # It is not invisible, though: over a range that contains it, it is an untagged movement.
    march = vat.compute(db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO)
    tie = next(tie for tie in march.ties if tie.code == "2200")
    assert tie.declared_in_range == Decimal("-3600.000000")
    assert [line.base_amount for line in tie.untagged] == [Decimal("3600.000000")]


def test_a_range_may_not_overlap_a_filed_return(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    vat.file_return(
        db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO, actor=month.owner
    )
    db.flush()

    # By a single day at the end, which is the boundary the predicate has to get right.
    with pytest.raises(LedgerStateError) as raised:
        vat.file_return(
            db,
            month.company_id,
            period_from=MARCH_TO,
            period_to=APRIL_TO,
            actor=month.owner,
        )
    assert raised.value.code == "vat_period_filed"

    # A range that starts the day after is fine.
    filed = vat.file_return(
        db, month.company_id, period_from=APRIL_FROM, period_to=APRIL_TO, actor=month.owner
    )
    assert filed.number == "VATR-000002"


def test_a_late_entry_is_declared_on_the_next_return_and_the_filed_one_never_moves(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    """Decision (a) of the step-4 note: the filed period is closed, so a return that only
    *listed* a late entry would leave that tax declared to nobody."""
    filed = vat.file_return(
        db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO, actor=month.owner
    )
    db.flush()
    as_filed = dict(filed.figures)

    _backdated_sale(db, month)

    # The filed return is untouched — both the stored snapshot and a recomputation of it.
    assert filed.figures == as_filed
    assert filed.output_vat == Decimal("3600.000000")

    april = vat.compute(db, month.company_id, period_from=APRIL_FROM, period_to=APRIL_TO)
    assert april.sales_standard == (Decimal("1000.000000"), Decimal("180.000000"))
    assert april.output_vat == Decimal("180.000000")

    late = april.late_entries
    assert {row.entry_date for row in late} == {LATE_IN_MARCH}
    assert {row.filed_return_number for row in late} == {"VATR-000001"}
    assert sum(row.tax for row in late) == Decimal("180.000000")

    # The tie still adds up over April's own range: the account did not move in April, the
    # return declares 180, and the late total is what accounts for the difference.
    tie = next(tie for tie in april.ties if tie.code == "2200")
    assert tie.movement == Decimal(0)
    assert tie.late_total == Decimal("-180.000000")
    assert tie.reconciled


def test_an_entry_dated_in_the_month_but_posted_before_filing_is_not_late(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    """The mark is about arrival, not about the date. An entry that existed when the return was
    filed was reported by it, however late in the month it is dated."""
    _backdated_sale(db, month)
    filed = vat.file_return(
        db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO, actor=month.owner
    )
    db.flush()

    # It was declared in March, where it belongs.
    assert filed.output_vat == Decimal("3780.000000")  # 3 600 + 180
    april = vat.compute(db, month.company_id, period_from=APRIL_FROM, period_to=APRIL_TO)
    assert april.late_entries == ()
    assert april.output_vat == Decimal(0)


def test_reversing_a_return_reopens_its_range(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    filed = vat.file_return(
        db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO, actor=month.owner
    )
    db.flush()
    settlement_id = filed.journal_entry_id

    reversed_return = vat.reverse_return(
        db, month.company_id, filed.id, reason="Filed against the wrong month", actor=month.owner
    )
    db.flush()

    assert reversed_return.status is VatReturnStatus.REVERSED
    assert reversed_return.reversal_entry_id is not None
    # The figures stay: a submission that was withdrawn is still a submission.
    assert reversed_return.output_vat == Decimal("3600.000000")

    reversal = db.get(JournalEntry, reversed_return.reversal_entry_id)
    assert reversal is not None
    assert reversal.reverses_entry_id == settlement_id

    # The range is open again, and re-filing it declares the same figures as before — the
    # settlement and its reversal cancel, so neither shows up as tax.
    again = vat.file_return(
        db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO, actor=month.owner
    )
    db.flush()
    # 000002 went to the reversing entry: it is a `VAT` entry like the settlement it undoes,
    # and the run counts every one of them.
    assert again.number == "VATR-000003"
    assert again.output_vat == Decimal("3600.000000")
    assert again.input_vat == Decimal("9000.000000")


def test_a_return_cannot_be_reversed_twice(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    filed = vat.file_return(
        db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO, actor=month.owner
    )
    db.flush()
    vat.reverse_return(db, month.company_id, filed.id, reason="Wrong month", actor=month.owner)
    db.flush()

    with pytest.raises(LedgerStateError) as raised:
        vat.reverse_return(db, month.company_id, filed.id, reason="Again", actor=month.owner)
    assert raised.value.code == "vat_return_reversed"


def test_a_nil_return_holds_its_number_and_posts_no_entry(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    """A range with no VAT movement declares nothing, so there is nothing to settle. It still
    takes a number, which is why `vat_returns` is a claimant on the `VAT` run — without it the
    gapless checker would read the nil return as a hole."""
    filed = vat.file_return(
        db, month.company_id, period_from=APRIL_FROM, period_to=APRIL_TO, actor=month.owner
    )
    db.flush()

    assert filed.number == "VATR-000001"
    assert filed.journal_entry_id is None
    assert filed.output_vat == Decimal(0)
    assert filed.input_vat == Decimal(0)
    assert filed.net_payable == Decimal(0)

    # And it is a real filing: the range is closed against a second one.
    with pytest.raises(LedgerStateError) as raised:
        vat.file_return(
            db, month.company_id, period_from=APRIL_FROM, period_to=APRIL_TO, actor=month.owner
        )
    assert raised.value.code == "vat_period_filed"


def test_filing_twice_under_one_idempotency_key_files_once(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    first = vat.file_return(
        db,
        month.company_id,
        period_from=MARCH_FROM,
        period_to=MARCH_TO,
        actor=month.owner,
        idempotency_key="file-march",
        idempotency_hash="hash-march",
    )
    db.flush()
    again = vat.file_return(
        db,
        month.company_id,
        period_from=MARCH_FROM,
        period_to=MARCH_TO,
        actor=month.owner,
        idempotency_key="file-march",
        idempotency_hash="hash-march",
    )

    assert again.id == first.id
    assert db.scalar(select(VatReturn).where(VatReturn.company_id == month.company_id)) is not None
    assert (
        len(list(db.scalars(select(VatReturn).where(VatReturn.company_id == month.company_id))))
        == 1
    )

    # A different request under the same key is a mistake worth naming.
    with pytest.raises(LedgerStateError) as raised:
        vat.file_return(
            db,
            month.company_id,
            period_from=APRIL_FROM,
            period_to=APRIL_TO,
            actor=month.owner,
            idempotency_key="file-march",
            idempotency_hash="hash-april",
        )
    assert raised.value.code == "idempotency_key_reused"


def test_a_range_that_ends_before_it_starts_is_refused(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    with pytest.raises(PostingError) as raised:
        vat.compute(db, month.company_id, period_from=MARCH_TO, period_to=MARCH_FROM)
    assert raised.value.code == "vat_period_invalid"


def test_a_filed_return_leaves_the_vat_run_gapless(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    """The proof of the one-number-per-filing rule.

    A filing writes two rows that could each hold a number — the `vat_returns` row and its
    settlement entry — and the `VAT` run has exactly one claimant for each case: `_ENTRY` for a
    return that posted, `_NIL_VAT_RETURN` (narrowed to `journal_entry_id IS NULL`) for one that
    did not. Claiming a number for the row *and* letting the entry claim another leaves a number
    no row holds, which is a hole in the run — and this is the assertion that says so.
    """
    vat.file_return(
        db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO, actor=month.owner
    )
    db.flush()
    assert_ledger_invariants(db, month.company_id)

    # And again over a range that posts nothing, so the nil claimant is the one holding it.
    vat.file_return(
        db, month.company_id, period_from=APRIL_FROM, period_to=APRIL_TO, actor=month.owner
    )
    db.flush()
    assert_ledger_invariants(db, month.company_id)


def test_a_late_entry_is_declared_once_and_not_by_every_return_after_it(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    """The regression `tests/tax/test_property_vat.py` found, pinned as an example.

    `_late` selected entries dated in a filed range whose id was above that return's high-water
    mark. True the month after, and true *forever* — so a correction posted into a closed month
    was declared again by every subsequent return for the life of the company. Here: March is
    filed, an entry arrives into March, April declares it, and **May must not**.

    Kept as an example beside the property because CI runs Hypothesis at `max_examples=1`; the
    property earns its keep in the nightly, and this is what fails in the pull request.
    """
    vat.file_return(
        db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO, actor=month.owner
    )
    db.flush()
    _backdated_sale(db, month)  # 1 000 net, 180 VAT, into the month already filed

    april = vat.file_return(
        db, month.company_id, period_from=APRIL_FROM, period_to=APRIL_TO, actor=month.owner
    )
    db.flush()
    # April picks it up, once, as a late entry dated in March.
    assert april.output_vat == Decimal("180.000000")
    assert april.figures["late_entries"], "April is the return that should declare it"

    may = vat.compute(
        db,
        month.company_id,
        period_from=date(YEAR, 5, 1),
        period_to=date(YEAR, 5, 31),
    )
    assert may.late_entries == (), "April already declared it; May must not see it again"
    assert may.output_vat == Decimal(0)


def _as_read(view: vat.VatReturnView) -> dict:
    """The return as a reader sees it: the figures **and** the explanation beside them."""
    return {
        "sections": view.as_filed()["sections"],
        "ties": [
            {
                "account": tie.code,
                "movement": str(tie.movement),
                "declared_in_range": str(tie.declared_in_range),
                "difference": str(tie.difference),
                "reconciled": tie.reconciled,
                "untagged": [
                    (line.entry_number, str(line.base_amount)) for line in tie.untagged
                ],
            }
            for tie in view.ties
        ],
    }


def test_filing_and_reversing_leaves_the_return_it_found(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    """Filing and then reversing must hand the range back **as it was**, list included.

    Both acts post into the range they are about: the settlement moves 2200, 1400 and 2250 on a
    date inside the month, and the reversal posts its mirror. Those are movements on the very
    accounts the tie reads, so "the figures come back" is not enough — two rows that net to zero
    still leave a reader an explanation that did not exist before, and a tie that balances over
    them is self-consistent and wrong.

    So this compares the whole read, sections and untagged lists together, before the first
    filing and after the last reversal.
    """
    before = _as_read(
        vat.compute(db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO)
    )

    filed = vat.file_return(
        db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO, actor=month.owner
    )
    db.flush()
    vat.reverse_return(
        db, month.company_id, filed.id, reason="Handing the range back", actor=month.owner
    )
    db.flush()

    after = _as_read(
        vat.compute(db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO)
    )

    assert after["sections"] == before["sections"], "the figures a re-filing would declare"
    assert after["ties"] == before["ties"], (
        "the tie a reader sees, explanation included — a settlement and its own reversal "
        "cancel in the totals and must not survive as two untagged rows"
    )


def test_a_reversed_ordinary_journal_still_lists_as_two_rows(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    """The **asymmetry**, asserted rather than left for a reader to discover.

    `_untagged()` drops a reversed settlement together with its mirror, and drops nothing else.
    A reversed *ordinary* journal on a VAT account still lists as two rows netting to zero — and
    that is the intended behaviour, not an oversight the settlement rule forgot to extend.

    The difference is what the two entries *are*. A settlement is the return's own act: it exists
    because a return was filed, it moves exactly what that return declared, and withdrawing it
    means the filing never stood. Nothing happened, so nothing is reported. A journal somebody
    keyed onto a VAT account and later reversed is two decisions a person made, and an
    accountant reading the tie is entitled to see both — decision 12's rule is that the report
    *names* what it cannot declare, and "somebody posted this and took it back" is exactly the
    kind of thing it exists to name.

    This is also the sensitivity guard for the `module == tax` half of the filter: without it,
    every reversed pair would vanish from the list and this test fails.
    """
    accounts = _accounts(db, month.company_id, "2200", "1500")
    keyed = posting.post(
        db,
        ManualJournal(
            entry_date=LATE_IN_MARCH,
            description="VAT accrual keyed by hand",
            lines=(
                LineSpec(amount=Decimal("-500"), gl_account_id=accounts["2200"]),
                LineSpec(amount=Decimal("500"), gl_account_id=accounts["1500"]),
            ),
        ),
        company_id=month.company_id,
        actor=month.owner,
    )
    db.flush()
    posting.post(
        db,
        ReversalRequested(
            entry_date=LATE_IN_MARCH,
            entry_id=keyed.id,
            reason="Keyed against the wrong account",
        ),
        company_id=month.company_id,
        actor=month.owner,
    )
    db.flush()

    tie = next(
        tie
        for tie in vat.compute(
            db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO
        ).ties
        if tie.code == "2200"
    )

    # Both rows, netting to zero, both named. The account did not move and the tie still
    # reconciles — what changed is that a reader can see what happened.
    assert [line.base_amount for line in tie.untagged] == [
        Decimal("-500.000000"),
        Decimal("500.000000"),
    ]
    assert tie.difference == Decimal("0.000000")
    assert tie.reconciled
