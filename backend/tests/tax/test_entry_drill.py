"""From a `VATR-` or `FXR-` journal entry, back to the return or the run that posted it.

P6 taught this lesson on stock and P7 repeats it one module over. A filed VAT return and an FX
revaluation run both post **through the kernel from outside a subledger**, so neither has a row
in `MODULE_DOCUMENT_TABLES`: `tax` has no document table at all, and `gl` is every manual
journal ever posted. Left alone, an accountant who opened `VATR-000001` from the trial balance
got an entry page that named a module, offered a disabled Reverse, and could not say which
return it settled — the P4 failure mode rule 13 is written against, exactly.

**Six entries, and only four of them are named by a column.** Between them the two documents
can produce:

| entry | posted as | `source_doc_type` | named by |
|---|---|---|---|
| the settlement | `VatReturnPosted` | `vat_return` | `vat_returns.journal_entry_id` |
| its reversal | `ReversalRequested` | *none* | `vat_returns.reversal_entry_id` |
| the run | `FxRevalued` | `fx_revaluation` | `fx_revaluations.journal_entry_id` |
| its next-day mirror | `ReversalRequested` | *none* | `fx_revaluations.mirror_entry_id` |
| the counter-entry | `FxRevalued` | `fx_revaluation` | `fx_revaluations.reversal_entry_id` |
| the counter's mirror | `ReversalRequested` | *none* | **nothing** |

`ReversalRequested` carries no `source_doc_type` of its own and the kernel does not copy the
original's, which is why half the table has none. The last row has no column either — reversing
a run mirrors its counter-entry and stores only the counter — so it is resolved through
`reverses_entry_id` on the rule that a mirror belongs wherever its original belongs.

**And the pair.** A VAT return reverses as a true mirror, so the kernel writes
`reverses_entry_id` and both directions already stand. An FX run does not: its counter-entry is
a fresh entry of negated lines, because the run's own next-day mirror already holds the
`reverses_entry_id` slot and `uq_journal_entries_reverses_entry_id` allows one mirror per entry.
So the counter-entry is a reversal that renders with nothing to say about what it reversed, and
`_resolve_p7_document_pair` fills it from the document — **only where the column is null**,
which is the one thing separating it from the landed-cost resolver it is modelled on.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.gl import _entry_read, _module_document
from app.models.currency import ExchangeRate
from app.models.journal import JournalEntry
from app.models.partner import PartnerRole
from app.order_entry import sources as order_sources
from app.subledger import revaluation
from app.tax import vat as vat_service
from tests.fiscal.conftest import FiscalPosting
from tests.kernel.conftest import YEAR
from tests.subledger.conftest import Subledger
from tests.subledger.test_documents import post_invoice

from .test_vat_return import MARCH_FROM, MARCH_TO, month  # noqa: F401

D = Decimal
MARCH_END = date(YEAR, 3, 31)


def _entry(db: Session, entry_id: int) -> JournalEntry:
    return db.get(JournalEntry, entry_id)


def test_the_drill_keys_are_the_strings_the_posters_actually_write() -> None:
    """`sources.py` spells them rather than importing them, so something has to check.

    The keys live on the inventory side of the tree and the constants live in `app/tax/` and
    `app/subledger/`; importing tax into `sources.py` to spell two strings would be a
    dependency bought for nothing. The cost of spelling them is that a rename could part them
    silently, and a resolver that answers `None` for every P7 entry is exactly the failure that
    renders as a blank cell and fails no test. This is that test.
    """
    assert order_sources.VAT_RETURN == vat_service.VAT_RETURN_SOURCE
    assert order_sources.FX_REVALUATION == revaluation.FX_REVALUATION_SOURCE


# --- The VAT return -------------------------------------------------------------------------


@pytest.fixture
def a_filed_return(db: Session, month: FiscalPosting):  # noqa: ANN201, F811
    """One filed return over the `month` fixture, whose figures are worked by hand elsewhere.

    It has to be a month with **tax in it**: `file_return` posts no settlement entry for a
    return that declares nothing, and a fixture that filed an empty month would leave every
    assertion below resolving a null entry id and passing for the wrong reason.
    """
    filed = vat_service.file_return(
        db,
        month.company_id,
        period_from=MARCH_FROM,
        period_to=MARCH_TO,
        actor=month.owner,
    )
    db.flush()
    assert filed.journal_entry_id is not None, "the premise: a return with something in it"
    return filed


def test_a_settlement_entry_drills_to_the_return_it_filed(
    db: Session,
    month: FiscalPosting,  # noqa: F811
    a_filed_return,
) -> None:
    assert a_filed_return.journal_entry_id is not None
    assert _module_document(db, _entry(db, a_filed_return.journal_entry_id)) == (
        a_filed_return.id,
        a_filed_return.number,
        "vat_return",
    )


def test_a_returns_reversal_drills_to_the_return_although_it_carries_no_source(
    db: Session,
    month: FiscalPosting,  # noqa: F811
    a_filed_return,
) -> None:
    """The reversing entry is a `ReversalRequested`, so it has no `source_doc_type` at all.

    It is an entry with a `VATR-` number on the trial balance and, before this, nothing behind
    it. `vat_returns.reversal_entry_id` is what names it.
    """
    vat_service.reverse_return(
        db,
        month.company_id,
        a_filed_return.id,
        reason="Filed against the wrong month",
        actor=month.owner,
    )
    db.flush()
    reversal = _entry(db, a_filed_return.reversal_entry_id)
    assert reversal.source_doc_type is None, "the premise: nothing to resolve from"
    assert _module_document(db, reversal) == (
        a_filed_return.id,
        a_filed_return.number,
        "vat_return",
    )


def test_a_filed_return_pairs_without_the_document(
    db: Session,
    month: FiscalPosting,  # noqa: F811
    a_filed_return,
) -> None:
    """Both directions of a `VATR-` pair come from the **kernel**, and must keep doing so.

    A VAT return reverses through `module_reversal("tax")` as a true `ReversalRequested`, so
    `journal_entries.reverses_entry_id` is written and `_resolve_p7_document_pair` has nothing
    to add. Asserted rather than assumed, because the day that changes is the day the resolver
    silently becomes load-bearing on this side too.
    """
    vat_service.reverse_return(
        db,
        month.company_id,
        a_filed_return.id,
        reason="Filed against the wrong month",
        actor=month.owner,
    )
    db.flush()
    settlement_id = a_filed_return.journal_entry_id
    reversal_id = a_filed_return.reversal_entry_id
    assert _entry(db, reversal_id).reverses_entry_id == settlement_id

    settlement = _entry_read(db, _entry(db, settlement_id))
    reversal = _entry_read(db, _entry(db, reversal_id))
    assert settlement.reversed_by_entry_id == reversal_id
    assert reversal.reverses_entry_id == settlement_id


# --- The FX revaluation ---------------------------------------------------------------------


@pytest.fixture
def a_run(db: Session, subledger: Subledger):  # noqa: ANN201
    """One posted run over an open USD receivable — an entry, and its next-day mirror."""
    post_invoice(
        db,
        subledger,
        role=PartnerRole.AR,
        amount=D("47.20"),
        currency="USD",
        exchange_rate=D(1320),
    )
    db.add(
        ExchangeRate(
            company_id=subledger.company_id,
            currency_id=subledger.ledger.cur("USD"),
            valid_from=MARCH_END,
            rate=D(1350),
        )
    )
    db.flush()
    run = revaluation.post_revaluation(
        db,
        subledger.company_id,
        revaluation_date=MARCH_END,
        role=revaluation.FxRevaluationRole.AR,
        actor=subledger.owner,
    )
    db.flush()
    return run


def test_a_run_and_its_mirror_both_drill_to_the_run(
    db: Session, subledger: Subledger, a_run
) -> None:
    """Two `FXR-` entries on the trial balance, a day apart, and both belong to one run.

    The mirror is the one with nothing of its own: posted as a `ReversalRequested`, so no
    source link, and it is `fx_revaluations.mirror_entry_id` that names it.
    """
    assert a_run.journal_entry_id is not None
    assert a_run.mirror_entry_id is not None
    assert _entry(db, a_run.mirror_entry_id).source_doc_type is None

    expected = (a_run.id, a_run.number, "fx_revaluation")
    assert _module_document(db, _entry(db, a_run.journal_entry_id)) == expected
    assert _module_document(db, _entry(db, a_run.mirror_entry_id)) == expected
    assert _entry(db, a_run.mirror_entry_id).entry_date == MARCH_END + timedelta(days=1)


def test_the_counter_entry_and_its_own_mirror_both_drill_to_the_run(
    db: Session, subledger: Subledger, a_run
) -> None:
    """Reversing a run posts two more `FXR-` entries, and only one of them is stored.

    The counter's mirror is named by no column anywhere — `reverse_revaluation` posts it and
    keeps no id — so it resolves through `reverses_entry_id` back to the counter. Without that
    second hop it is an `FXR-` entry on the trial balance belonging to nothing.
    """
    revaluation.reverse_revaluation(
        db, subledger.company_id, a_run.id, reason="Wrong rate", actor=subledger.owner
    )
    db.flush()
    counter_id = a_run.reversal_entry_id
    assert counter_id is not None
    counters_mirror = db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == subledger.company_id,
            JournalEntry.reverses_entry_id == counter_id,
        )
    )
    assert counters_mirror is not None, "the premise: the counter is itself mirrored"

    expected = (a_run.id, a_run.number, "fx_revaluation")
    assert _module_document(db, _entry(db, counter_id)) == expected
    assert _module_document(db, counters_mirror) == expected


def test_the_counter_entry_says_what_it_reversed_and_the_run_keeps_its_mirror(
    db: Session, subledger: Subledger, a_run
) -> None:
    """The asymmetry, both halves, in one test — because they are one decision.

    The counter-entry's `reverses_entry_id` is **null**: it is a fresh entry of negated lines,
    not a mirror, because the run's entry already has a mirror and can have only one. So the
    document fills that side.

    And the run's entry keeps `reversed_by = its mirror`. That is the true answer to "what took
    this out of the balance sheet" — the next-day frozen-base reversal — and overwriting it
    with the counter-entry would trade one true statement for another and lose the first. This
    is the whole difference from `_resolve_landed_cost_pair`, which fills unconditionally
    because there the kernel's columns are null on both sides.
    """
    revaluation.reverse_revaluation(
        db, subledger.company_id, a_run.id, reason="Wrong rate", actor=subledger.owner
    )
    db.flush()

    counter = _entry(db, a_run.reversal_entry_id)
    assert counter.reverses_entry_id is None, "the premise: the kernel cannot say"
    resolved = _entry_read(db, counter)
    assert resolved.reverses_entry_id == a_run.journal_entry_id
    assert resolved.reverses_entry_number == _entry(db, a_run.journal_entry_id).number

    run_entry = _entry_read(db, _entry(db, a_run.journal_entry_id))
    assert run_entry.reversed_by_entry_id == a_run.mirror_entry_id
