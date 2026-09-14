"""Landed cost — Importation Split (P6 decision 9, step 4).

**Decision 9, clause by clause.** The decision is a paragraph of prose; this is the checklist
that says every sentence of it reached the code, and names what would fail if it stopped being
true. Read it as the step's own acceptance list.

1.  *The tables.* `landed_cost_documents` / `landed_cost_lines` — date, description, reference,
    base-currency `amount`, `basis`, status, entry link, lines carrying the target and its
    share. `models/order_entry.py` and migration `0021`; proved to apply from zero and come
    back down by `make migrate-check`.
2.  *Target GRN lines, **any supplier, any GRN**.* `_resolve_targets` checks the receipt exists
    and is unreversed, and deliberately does **not** check the supplier the way the three-way
    match does — one freight bill routinely covers consignments from several.
    `test_one_allocation_carries_a_stocked_and_a_stockless_target_together` spreads one
    allocation across two receipts in two warehouses.
3.  *Optional **informational** source links.* `source_document_id` and
    `source_cashbook_line_id`. Nothing reads them and nothing is derived from them, so there is
    nothing to pin — that is the point of them, and `LandedCostDocument` says so.
4.  *`LCA-` numbers.* `DocType.LANDED_COST` with `_ENTRY` as its only claimant — an allocation
    of nothing never becomes a document, so there is no valueless case to hold a number of its
    own. `tests/kernel/test_sequence_registry.py` and the gapless clause of
    `assert_ledger_invariants`.
5.  *Shares = amount x weight / Σ weights, half-up to base decimals.* `preview_shares`;
    `test_shares_sum_to_the_amount_at_zero_decimals` and `..._at_two_decimals`, across all
    three bases.
6.  *The rounding residue on the **last** target line, so Σ shares == amount exactly.*
    `preview_shares`, last branch. `test_the_residue_lands_on_the_last_line`,
    `test_a_fully_allocated_clearing_account_is_zero`, and clause 8 of
    `assert_order_invariants`, which asserts the sum for every posted document.
7.  *`weight` basis reads `items.weight_per_base_unit` and refuses a target without one.*
    `_weight_of`; `test_a_weight_basis_refuses_an_item_with_no_weight`.
8.  *A target whose warehouse still holds the item: `revalue_stock()` with the clearing account
    as contra, and the average rises from this posting onward while earlier issues are never
    restated.* `post_landed_cost`;
    `test_an_allocation_raises_inventory_and_clears_the_clearing_account` and
    `test_an_allocation_after_a_partial_sale_restates_nothing_earlier`.
9.  *A target whose location holds **none** of the item takes its share to COGS instead, on the
    same entry.* The `stockless_account_id` branch in `stock.py::_plan`;
    `test_a_stockless_target_sends_its_share_to_cogs_on_the_same_entry`.
10. *"If `revalue_stock()` cannot carry a stockless target line, extend it minimally under its
    existing tests rather than post a second entry — **and say so**."* See the note below.
    `test_a_revaluation_of_an_empty_location_is_still_refused_without_the_opt_in`, beside P5's
    own `test_a_revaluation_of_a_location_holding_no_stock_is_refused`, which is untouched.
11. *Reversal through `reverse_stock_posting()` at the original values.* `reverse_landed_cost`;
    `test_a_reversal_takes_the_allocation_back_out` and
    `test_a_stockless_allocation_reverses_too`.

**Saying so** (clause 10). `revalue_stock()` could not carry a stockless target: it refused an
empty location outright with `nothing_to_revalue`. It was extended — one branch in `_plan`,
gated on a new `StockLine.stockless_account_id` that no P5 caller sets, so the refusal stays
the default and P5's test of it still passes unchanged. The test had to move *into* the service
because "does this location still hold any" is only answerable under the costing lock; a caller
reading the balance first could have it emptied underneath it between the read and the posting.

**Two states, and no third.** `LandedCostStatus` is `posted` and `reversed`. No draft — shares
are struck against the receipts and the stock position as they stand, and a document that
waited would post shares computed against a position that had moved. No `partially_allocated`
either: an allocation is all of its amount or none of it, which is what clause 6 makes true and
what the clearing invariant proves.

**Reversal ordering.** `reverse_landed_cost` is a multi-step caller with no savepoint, held to
the step-3 rule by `test_a_refused_landed_cost_reversal_leaves_nothing_behind` — which also
records why the obvious plan for that test does not work. See its docstring.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import masters as inventory_masters
from app.inventory import stock as stock_service
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.sequences import DocType
from app.models.currency import Currency
from app.models.fiscal import PeriodStatus
from app.models.inventory import GoodsReceivedNoteLine, NegativeStockPolicy, StockMove
from app.models.journal import JournalEntry, JournalLine, JournalStatus
from app.models.order_entry import LandedCostBasis, LandedCostStatus
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind
from app.order_entry import grn as grn_service
from app.order_entry import landed_cost as landed_cost_service
from app.subledger import documents as documents_service
from tests.inventory.invariants import assert_stock_invariants
from tests.kernel.invariants import assert_ledger_invariants
from tests.order_entry.conftest import APRIL, MARCH, OrderEntry
from tests.order_entry.invariants import assert_order_invariants
from tests.subledger.invariants import assert_subledger_invariants

ZERO = Decimal(0)


# --- Helpers -------------------------------------------------------------------------------


def _assert_everything(db: Session, company_id: int) -> None:
    assert_ledger_invariants(db, company_id)
    assert_subledger_invariants(db, company_id)
    assert_stock_invariants(db, company_id)
    assert_order_invariants(db, company_id)


def _use_a_two_decimal_base(db: Session, fixture: OrderEntry) -> None:
    base = db.scalar(
        select(Currency).where(
            Currency.company_id == fixture.company_id, Currency.is_base.is_(True)
        )
    )
    base.decimal_places = 2
    db.flush()


def _receive(
    db: Session,
    fixture: OrderEntry,
    quantity: str,
    unit_cost: str,
    *,
    warehouse_id: int | None = None,
    item_id: int | None = None,
) -> GoodsReceivedNoteLine:
    """One receipt of one line, and the line it produced."""
    grn, _ = grn_service.post_grn(
        db,
        fixture.company_id,
        grn_service.GrnInput(
            partner_id=fixture.supplier.id,
            grn_date=MARCH,
            description="Import consignment",
            warehouse_id=warehouse_id or fixture.main.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=item_id or fixture.stock_item.id,
                    quantity=Decimal(quantity),
                    unit_cost=Decimal(unit_cost),
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return grn.lines[0]


def _sell(db: Session, fixture: OrderEntry, quantity: str, unit_price: str = "2000"):  # noqa: ANN202
    return documents_service.post_document(
        db,
        fixture.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=fixture.customer.id,
            document_date=MARCH,
            description="Sale",
            lines=(
                documents_service.LineInput(
                    item_id=fixture.stock_item.id,
                    quantity=Decimal(quantity),
                    unit_price=Decimal(unit_price),
                    warehouse_id=fixture.main.id,
                ),
            ),
        ),
        actor=fixture.owner,
    )


def _allocate(
    db: Session,
    fixture: OrderEntry,
    amount: str,
    grn_line_ids: tuple[int, ...],
    *,
    basis: LandedCostBasis = LandedCostBasis.QUANTITY,
    on=MARCH,  # noqa: ANN001
):  # noqa: ANN202
    return landed_cost_service.post_landed_cost(
        db,
        fixture.company_id,
        landed_cost_service.LandedCostInput(
            cost_date=on,
            description="Freight and clearing",
            amount=Decimal(amount),
            basis=basis,
            grn_line_ids=grn_line_ids,
        ),
        actor=fixture.owner,
    )[0]


def _book_to_clearing(db: Session, fixture: OrderEntry, amount: str, on=MARCH):  # noqa: ANN001, ANN202
    """A forwarder's invoice with a GL line on the clearing account — how a cost gets there.

    Deliberately an ordinary AP invoice with an ordinary GL line: the clearing account is a
    **plain** account (decision 5), and the whole design depends on any document being able to
    post to it without knowing what it is for.
    """
    return documents_service.post_document(
        db,
        fixture.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=fixture.supplier.id,
            document_date=on,
            description="Freight",
            lines=(
                documents_service.LineInput(
                    gl_account_id=fixture.settings.landed_cost_clearing_account_id,
                    quantity=Decimal(1),
                    unit_price=Decimal(amount),
                ),
            ),
        ),
        actor=fixture.owner,
    )


def _balance(db: Session, fixture: OrderEntry, code: str) -> Decimal:
    """An account's base-currency balance, summed over its posted lines."""
    rows = db.scalars(
        select(JournalLine.base_amount)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == fixture.company_id,
            JournalLine.gl_account_id == fixture.accounts[code].id,
            JournalEntry.status == JournalStatus.POSTED,
        )
    ).all()
    return sum(rows, ZERO)


def _last_entry_id(db: Session, fixture: OrderEntry) -> int | None:
    return db.scalar(
        select(JournalEntry.id)
        .where(JournalEntry.company_id == fixture.company_id)
        .order_by(JournalEntry.id.desc())
        .limit(1)
    )


def _amounts_by_account(db: Session, fixture: OrderEntry, entry_id: int) -> dict[str, Decimal]:
    code_by_id = {account.id: code for code, account in fixture.accounts.items()}
    totals: dict[str, Decimal] = {}
    for line in db.scalars(select(JournalLine).where(JournalLine.entry_id == entry_id)):
        code = code_by_id[line.gl_account_id]
        totals[code] = totals.get(code, ZERO) + line.base_amount
    return totals


# --- The split -------------------------------------------------------------------------------


def test_the_tape_row_9_split(db: Session, order_entry: OrderEntry) -> None:
    """The acceptance tape's row 9, worked by hand: 7 777 by quantity over 60 and 40.

    60/100 of 7 777 is 4 666.2, which rounds to 4 666 in a zero-decimal base; the last line
    takes the rest, 3 111. Together 7 777 — which is the number that has to come off the
    clearing account, not 4 666 + 3 111 computed independently (4 666 + 3 111 = 7 777 here, but
    see `test_the_residue_lands_on_the_last_line` for where independent rounding fails).
    """
    first = _receive(db, order_entry, "60", "1000")
    second = _receive(db, order_entry, "40", "1000")

    shares = landed_cost_service.preview_shares(
        db,
        order_entry.company_id,
        amount=Decimal(7777),
        basis=LandedCostBasis.QUANTITY,
        grn_line_ids=(first.id, second.id),
    )

    assert [share.share for share in shares] == [Decimal(4666), Decimal(3111)]
    assert sum(share.share for share in shares) == Decimal(7777)


def test_the_residue_lands_on_the_last_line(db: Session, order_entry: OrderEntry) -> None:
    """Three equal targets and an amount that does not divide by three.

    100 over three equal weights is 33.33 each in a zero-decimal base — 33, 33, 33, summing to
    99. The last line takes 34 instead, so the shares sum to 100 and the clearing account
    reaches zero. Without the rule the account is left holding a franc that no later allocation
    can ever take off, which is the defect the residue rule exists to prevent and the reason
    clause 8 of `assert_order_invariants` asserts the sum rather than trusting it.
    """
    lines = [_receive(db, order_entry, "10", "100") for _ in range(3)]

    shares = landed_cost_service.preview_shares(
        db,
        order_entry.company_id,
        amount=Decimal(100),
        basis=LandedCostBasis.QUANTITY,
        grn_line_ids=tuple(line.id for line in lines),
    )

    assert [share.share for share in shares] == [Decimal(33), Decimal(33), Decimal(34)]
    assert sum(share.share for share in shares) == Decimal(100)


@pytest.mark.parametrize("basis", list(LandedCostBasis))
@pytest.mark.parametrize("amount", ["7777", "100", "1", "1000000", "333"])
def test_shares_sum_to_the_amount_at_zero_decimals(
    db: Session, order_entry: OrderEntry, basis: LandedCostBasis, amount: str
) -> None:
    """Decision 9's first promise, on every basis, in the RWF-base company.

    Weights chosen so none of the three bases agrees with another: different quantities, at
    different costs, of items with different weights. A split that happened to be exact on one
    basis would say nothing about the others.
    """
    inventory_masters.update_item(
        db,
        order_entry.stock_item,
        weight_per_base_unit=Decimal("0.75"),
        actor=order_entry.owner,
    )
    lines = [
        _receive(db, order_entry, "7", "137"),
        _receive(db, order_entry, "13", "991"),
        _receive(db, order_entry, "3", "17"),
    ]

    shares = landed_cost_service.preview_shares(
        db,
        order_entry.company_id,
        amount=Decimal(amount),
        basis=basis,
        grn_line_ids=tuple(line.id for line in lines),
    )

    assert sum(share.share for share in shares) == Decimal(amount), (
        f"{basis.value} split of {amount} summed to {sum(share.share for share in shares)}"
    )
    for share in shares:
        assert share.share == share.share.quantize(Decimal(1)), (
            f"{share.share} is not a whole franc in a zero-decimal base"
        )


@pytest.mark.parametrize("basis", list(LandedCostBasis))
@pytest.mark.parametrize("amount", ["7777.77", "100.01", "0.03", "1", "333.33"])
def test_shares_sum_to_the_amount_at_two_decimals(
    db: Session, order_entry: OrderEntry, basis: LandedCostBasis, amount: str
) -> None:
    """The same promise where the rounding is finer and the residues are smaller — which is
    where a split that leaned on a zero-decimal accident would come apart."""
    _use_a_two_decimal_base(db, order_entry)
    inventory_masters.update_item(
        db,
        order_entry.stock_item,
        weight_per_base_unit=Decimal("0.75"),
        actor=order_entry.owner,
    )
    lines = [
        _receive(db, order_entry, "7", "137"),
        _receive(db, order_entry, "13", "991"),
        _receive(db, order_entry, "3", "17"),
    ]

    shares = landed_cost_service.preview_shares(
        db,
        order_entry.company_id,
        amount=Decimal(amount),
        basis=basis,
        grn_line_ids=tuple(line.id for line in lines),
    )

    assert sum(share.share for share in shares) == Decimal(amount)


def test_a_weight_basis_refuses_an_item_with_no_weight(
    db: Session, order_entry: OrderEntry
) -> None:
    """`weight_missing`, refused at the target rather than treated as a weight of zero.

    A zero weight would take a zero share and push its cost silently onto the lines that did
    carry one — a wrong valuation with nothing downstream able to detect it.
    """
    line = _receive(db, order_entry, "10", "100")

    with pytest.raises(LedgerStateError) as excinfo:
        landed_cost_service.preview_shares(
            db,
            order_entry.company_id,
            amount=Decimal(500),
            basis=LandedCostBasis.WEIGHT,
            grn_line_ids=(line.id,),
        )

    assert excinfo.value.code == "weight_missing"


def test_a_basis_on_which_everything_weighs_nothing_is_refused(
    db: Session, order_entry: OrderEntry
) -> None:
    """A set of receipts booked at no cost has no `value` to split across. Undefined rather
    than equal — guessing would put the whole amount somewhere arbitrary."""
    line = _receive(db, order_entry, "10", "0")

    with pytest.raises(LedgerStateError) as excinfo:
        landed_cost_service.preview_shares(
            db,
            order_entry.company_id,
            amount=Decimal(500),
            basis=LandedCostBasis.VALUE,
            grn_line_ids=(line.id,),
        )

    assert excinfo.value.code == "no_basis_weight"


def test_a_target_cannot_appear_twice(db: Session, order_entry: OrderEntry) -> None:
    """Two shares against one line is not one share twice the size: the two would round
    separately, and the residue rule has one last line, not two."""
    line = _receive(db, order_entry, "10", "100")

    with pytest.raises(LedgerStateError) as excinfo:
        landed_cost_service.preview_shares(
            db,
            order_entry.company_id,
            amount=Decimal(500),
            basis=LandedCostBasis.QUANTITY,
            grn_line_ids=(line.id, line.id),
        )

    assert excinfo.value.code == "duplicate_target"


def test_the_preview_is_what_gets_posted(db: Session, order_entry: OrderEntry) -> None:
    """One function computes both, so this is a test that nobody has since given the posting
    arithmetic of its own — which is the way a preview comes to show one set of shares and
    write another."""
    first = _receive(db, order_entry, "60", "1000")
    second = _receive(db, order_entry, "40", "1000")
    previewed = landed_cost_service.preview_shares(
        db,
        order_entry.company_id,
        amount=Decimal(7777),
        basis=LandedCostBasis.QUANTITY,
        grn_line_ids=(first.id, second.id),
    )

    document = _allocate(db, order_entry, "7777", (first.id, second.id))

    assert [line.share for line in document.lines] == [share.share for share in previewed]
    _assert_everything(db, order_entry.company_id)


# --- Posting ---------------------------------------------------------------------------------


def test_an_allocation_raises_inventory_and_clears_the_clearing_account(
    db: Session, order_entry: OrderEntry
) -> None:
    """The tape's row 9 end to end: two revaluation moves, one entry, `1300 +7 777 /
    1370 −7 777`, and an average that rises from this posting onward."""
    _book_to_clearing(db, order_entry, "7777")
    first = _receive(db, order_entry, "60", "1000")
    second = _receive(db, order_entry, "40", "1000")

    document = _allocate(db, order_entry, "7777", (first.id, second.id))

    assert document.number.startswith("LCA-")
    assert document.status == LandedCostStatus.POSTED
    amounts = _amounts_by_account(db, order_entry, document.journal_entry_id)
    assert amounts["1300"] == Decimal(7777)
    assert amounts["1370"] == Decimal(-7777)

    moves = db.scalars(
        select(StockMove).where(StockMove.journal_entry_id == document.journal_entry_id)
    ).all()
    assert len(moves) == 2, "one revaluation move per stocked target"
    assert all(move.quantity == ZERO for move in moves), "a revaluation moves value, not quantity"

    position = stock_service.location_balance(
        db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
    )
    assert (position.quantity, position.value) == (Decimal(100), Decimal(107_777))
    _assert_everything(db, order_entry.company_id)


def test_a_fully_allocated_clearing_account_is_zero(
    db: Session, order_entry: OrderEntry
) -> None:
    """Decision 9's second promise, and the Definition of Done's.

    Booked in two pieces from two documents, allocated in two pieces on two bases, over
    **three** targets whose weights divide neither amount evenly — so both allocations leave a
    rounding residue and the residue rule is what carries the account to zero rather than to
    within a franc or two of it. Picked deliberately: an earlier draft used two targets and
    amounts that happened to divide, and it passed with the residue rule disabled.

    7 777 by quantity over 10 / 10 / 10 is 2 592.33 each: rounding all three gives 7 776, and
    the residue rule makes the last 2 593. 5 000 by value over received values 1 000 / 1 200 /
    1 700 (total 3 900) is 1 282.05 / 1 538.46 / 2 179.49: rounding all three gives 4 999, and
    the last takes 2 180. Independently rounded, the two allocations would leave 1 + 1 = 2 on
    the clearing account, and no later allocation could ever take it off.
    """
    _book_to_clearing(db, order_entry, "7777")
    _book_to_clearing(db, order_entry, "5000")
    first = _receive(db, order_entry, "10", "100")
    second = _receive(db, order_entry, "10", "120")
    third = _receive(db, order_entry, "10", "170")
    assert _balance(db, order_entry, "1370") == Decimal(12_777)

    by_quantity = _allocate(db, order_entry, "7777", (first.id, second.id, third.id))
    by_value = _allocate(
        db, order_entry, "5000", (first.id, second.id, third.id), basis=LandedCostBasis.VALUE
    )

    # The shares each sum to their amount, and neither set is what independent rounding gives.
    assert [line.share for line in by_quantity.lines] == [
        Decimal(2592),
        Decimal(2592),
        Decimal(2593),
    ]
    assert [line.share for line in by_value.lines] == [
        Decimal(1282),
        Decimal(1538),
        Decimal(2180),
    ]
    assert _balance(db, order_entry, "1370") == ZERO
    _assert_everything(db, order_entry.company_id)


def test_an_allocation_after_a_partial_sale_restates_nothing_earlier(
    db: Session, order_entry: OrderEntry
) -> None:
    """Decision 9's third promise, and the tape's row 9 clause "row 4's COGS unchanged".

    100 in at 1 000. Sell 30, so COGS takes 30 000. Then allocate 7 777 of freight across the
    receipt. The average rises for what is left and the 30 000 already posted stays 30 000 —
    P5 decision 4's rule that a revaluation is effective from its posting onward and never
    reaches back. Restating it would mean rewriting a posted journal entry, which rule 3
    forbids outright; the point of the test is that nothing tries to do it by other means.
    """
    _book_to_clearing(db, order_entry, "7777")
    line = _receive(db, order_entry, "100", "1000")
    _sell(db, order_entry, "30")
    cogs_before = _balance(db, order_entry, "5100")
    assert cogs_before == Decimal(30_000)

    _allocate(db, order_entry, "7777", (line.id,))

    assert _balance(db, order_entry, "5100") == Decimal(30_000), (
        "the freight reached back into a sale that had already posted"
    )
    position = stock_service.location_balance(
        db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
    )
    # 70 left, worth 70 000 + 7 777.
    assert (position.quantity, position.value) == (Decimal(70), Decimal(77_777))
    assert _balance(db, order_entry, "1370") == ZERO
    _assert_everything(db, order_entry.company_id)


# --- The stockless target ---------------------------------------------------------------------


def test_a_stockless_target_sends_its_share_to_cogs_on_the_same_entry(
    db: Session, order_entry: OrderEntry
) -> None:
    """Decision 9's stockless rule, and the tape's row 12.

    The goods this cost was incurred for have been sold. There is no carrying value left to add
    it to, so the share goes to cost of sales — **on the same entry**, because one allocation is
    one document and the clearing account has to clear in one posting. No move is written: a
    zero-quantity move with no effect on carrying value would claim the stock ledger did
    something, and it did not.
    """
    _book_to_clearing(db, order_entry, "5000")
    line = _receive(db, order_entry, "40", "1000")
    _sell(db, order_entry, "40")  # empties Main
    position = stock_service.location_balance(
        db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
    )
    assert position.quantity == ZERO, "the target location has to be empty for this to be the case"

    document = _allocate(db, order_entry, "5000", (line.id,))

    amounts = _amounts_by_account(db, order_entry, document.journal_entry_id)
    assert amounts["5100"] == Decimal(5000), "the share did not reach cost of sales"
    assert amounts["1370"] == Decimal(-5000)
    assert "1300" not in amounts, "a stockless share must not touch the inventory account"

    moves = db.scalars(
        select(StockMove).where(StockMove.journal_entry_id == document.journal_entry_id)
    ).all()
    assert moves == [], "a stockless share writes no move"

    cost_line = document.lines[0]
    assert cost_line.went_to_cogs is True
    assert cost_line.stock_move_id is None
    assert _balance(db, order_entry, "1370") == ZERO
    _assert_everything(db, order_entry.company_id)


def test_one_allocation_carries_a_stocked_and_a_stockless_target_together(
    db: Session, order_entry: OrderEntry
) -> None:
    """The case decision 9 asked `revalue_stock()` to be extended for, rather than split into
    two entries: one document whose shares go partly into stock and partly to cost of sales.

    If the extension had not been made, this would have had to be two postings — and the
    clearing account would then have been cleared by two documents where the operator raised
    one.
    """
    _book_to_clearing(db, order_entry, "1000")
    sold = _receive(db, order_entry, "40", "1000", warehouse_id=order_entry.main.id)
    kept = _receive(db, order_entry, "60", "1000", warehouse_id=order_entry.depot.id)
    _sell(db, order_entry, "40")  # empties Main, leaves the depot alone

    document = _allocate(db, order_entry, "1000", (sold.id, kept.id))

    amounts = _amounts_by_account(db, order_entry, document.journal_entry_id)
    # 40/100 of 1 000 to cost of sales, the remaining 600 into the depot's carrying value.
    assert amounts["5100"] == Decimal(400)
    assert amounts["1300"] == Decimal(600)
    assert amounts["1370"] == Decimal(-1000)
    assert [line.went_to_cogs for line in document.lines] == [True, False]
    assert [line.stock_move_id is None for line in document.lines] == [True, False]
    assert _balance(db, order_entry, "1370") == ZERO
    _assert_everything(db, order_entry.company_id)


def test_a_revaluation_of_an_empty_location_is_still_refused_without_the_opt_in(
    db: Session, order_entry: OrderEntry
) -> None:
    """The other half of the extension: the default did not move.

    `revalue_stock()` refuses an empty location exactly as it did before P6. Only a line that
    names a `stockless_account_id` — which no P5 caller does — takes the new path. Without this
    the extension would read as a relaxation of the P5 rule rather than an opt-in beside it.
    """
    _receive(db, order_entry, "10", "100", warehouse_id=order_entry.main.id)

    with pytest.raises(PostingError) as excinfo:
        stock_service.revalue_stock(
            db,
            order_entry.company_id,
            document=stock_service.StockDocument(
                doc_type=str(DocType.INV_ADJUSTMENT),
                move_date=MARCH,
                description="Write-up",
                transaction_type_id=order_entry.inventory.transaction_types["REVAL"].id,
            ),
            lines=[
                stock_service.StockLine(
                    item_id=order_entry.stock_item.id,
                    warehouse_id=order_entry.depot.id,  # never received anything
                    value=Decimal(75),
                )
            ],
            actor=order_entry.owner,
        )

    assert excinfo.value.code == "nothing_to_revalue"
    db.rollback()


# --- Reversal ----------------------------------------------------------------------------------


def test_a_reversal_takes_the_allocation_back_out(db: Session, order_entry: OrderEntry) -> None:
    """At the original values, so the two sides cancel exactly and the clearing account goes
    back to holding what was booked to it."""
    _book_to_clearing(db, order_entry, "7777")
    line = _receive(db, order_entry, "100", "1000")
    document = _allocate(db, order_entry, "7777", (line.id,))
    assert _balance(db, order_entry, "1370") == ZERO

    landed_cost_service.reverse_landed_cost(
        db, document, on_date=APRIL, reason="Wrong consignment", actor=order_entry.owner
    )

    assert document.status == LandedCostStatus.REVERSED
    assert document.reversal_entry_id is not None
    assert _balance(db, order_entry, "1370") == Decimal(7777), "the cost is unallocated again"
    position = stock_service.location_balance(
        db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
    )
    assert (position.quantity, position.value) == (Decimal(100), Decimal(100_000))
    _assert_everything(db, order_entry.company_id)


def test_a_stockless_allocation_reverses_too(db: Session, order_entry: OrderEntry) -> None:
    """The cost-of-sales lines come back with the revaluation moves because they are lines on
    the same entry — which is the second thing putting them on one entry buys."""
    _book_to_clearing(db, order_entry, "5000")
    line = _receive(db, order_entry, "40", "1000")
    _sell(db, order_entry, "40")
    document = _allocate(db, order_entry, "5000", (line.id,))
    cogs_after_allocation = _balance(db, order_entry, "5100")

    landed_cost_service.reverse_landed_cost(
        db, document, on_date=APRIL, reason="Duty reassessed", actor=order_entry.owner
    )

    assert _balance(db, order_entry, "5100") == cogs_after_allocation - Decimal(5000)
    assert _balance(db, order_entry, "1370") == Decimal(5000)
    _assert_everything(db, order_entry.company_id)


def test_a_landed_cost_reverses_once(db: Session, order_entry: OrderEntry) -> None:
    _book_to_clearing(db, order_entry, "1000")
    line = _receive(db, order_entry, "10", "100")
    document = _allocate(db, order_entry, "1000", (line.id,))
    landed_cost_service.reverse_landed_cost(
        db, document, on_date=APRIL, reason="First", actor=order_entry.owner
    )

    with pytest.raises(LedgerStateError) as excinfo:
        landed_cost_service.reverse_landed_cost(
            db, document, on_date=APRIL, reason="Second", actor=order_entry.owner
        )

    assert excinfo.value.code == "landed_cost_already_reversed"


def test_a_refused_landed_cost_reversal_leaves_nothing_behind(
    db: Session, order_entry: OrderEntry
) -> None:
    """**The step-3 ordering rule, carried to this step's caller.**

    `reverse_landed_cost` is a *multi-step caller*: it reverses a posting and then writes a
    status, a date and an audit row. It has no savepoint to unwind, so what it needs from the
    thing it calls is that a refusal arrives **before** the first write. The step-3 rule is what
    makes that true — reverse the fallible leg first — and here the fallible leg is the only
    leg: `reverse_stock_posting()` is what can refuse, and everything after it is a column
    assignment that cannot fail.

    Driven with **no savepoint and no rollback**, exactly as
    `test_a_refused_reversal_leaves_nothing_behind` in `test_property_order.py` is, and for the
    reason that test gives: an endpoint would hide the difference, because closing its session
    rolls the whole request back either way.

    **The refusal is a closed period, and it has to be** — which is worth writing down, because
    the obvious plan does not work. A landed cost posts *revaluation* moves, and a revaluation
    carries no quantity: allocating onto stock, selling all of it and then reversing does **not**
    raise `insufficient_stock`, because `reverse_stock_posting`'s negative-stock check compares
    quantities and this reversal moves none. The plan was drafted that way, failed with an empty
    refusal list, and the empty list is the useful fact: under `block` a landed cost is always
    reversible however the goods have moved since. What is left that can refuse is the period
    lock, which is checked inside `posting.reverse` — after the costing locks are taken and
    before a single row is written.
    """
    _book_to_clearing(db, order_entry, "7777")
    line = _receive(db, order_entry, "100", "1000")
    document = _allocate(db, order_entry, "7777", (line.id,))
    _assert_everything(db, order_entry.company_id)

    entries_before = _last_entry_id(db, order_entry)
    april = next(
        period
        for period in order_entry.ledger.periods
        if period.start_date <= APRIL <= period.end_date
    )
    april.status = PeriodStatus.CLOSED
    db.flush()

    refusals: list[str] = []
    try:
        landed_cost_service.reverse_landed_cost(
            db, document, on_date=APRIL, reason="Too late", actor=order_entry.owner
        )
    except (LedgerStateError, PostingError) as refused:
        refusals.append(refused.code)
    db.flush()

    assert refusals == ["period_not_open"], refusals
    # Nothing was written: not the status, not the date, not an entry — so a caller holding
    # this in the middle of a larger unit of work has nothing to unwind.
    assert document.status == LandedCostStatus.POSTED
    assert document.reversed_on is None
    assert document.reversal_entry_id is None
    assert _last_entry_id(db, order_entry) == entries_before, "a refused reversal posted an entry"
    _assert_everything(db, order_entry.company_id)


def test_reversing_a_landed_cost_is_never_blocked_by_the_goods_having_gone(
    db: Session, order_entry: OrderEntry
) -> None:
    """The positive half of the note above, asserted rather than left as a remark.

    A revaluation moves value and no quantity, so taking one back can never push a location
    below zero and `block` has nothing to refuse — even when every unit the cost was allocated
    onto has since been sold. If a later change ever makes this raise, the reversal ordering in
    `reverse_landed_cost` stops being merely correct and starts being load-bearing, and the
    test above stops being the only thing standing behind it.
    """
    order_entry.settings.negative_stock_policy = NegativeStockPolicy.BLOCK
    db.flush()
    _book_to_clearing(db, order_entry, "7777")
    line = _receive(db, order_entry, "100", "1000")
    document = _allocate(db, order_entry, "7777", (line.id,))
    _sell(db, order_entry, "100")  # every unit it was allocated onto is gone

    landed_cost_service.reverse_landed_cost(
        db, document, on_date=APRIL, reason="Reassessed", actor=order_entry.owner
    )

    assert document.status == LandedCostStatus.REVERSED
    assert _balance(db, order_entry, "1370") == Decimal(7777)
    _assert_everything(db, order_entry.company_id)


def test_a_reversed_receipt_cannot_take_a_landed_cost(
    db: Session, order_entry: OrderEntry
) -> None:
    """Adding cost to goods that were taken back would leave the allocation holding value
    against a line the accrual proof has already unwound."""
    line = _receive(db, order_entry, "10", "100")
    grn = grn_service.get_grn(db, order_entry.company_id, line.grn_id)
    grn_service.reverse_grn(
        db, grn, on_date=MARCH, reason="Never arrived", actor=order_entry.owner
    )

    with pytest.raises(LedgerStateError) as excinfo:
        _allocate(db, order_entry, "500", (line.id,))

    assert excinfo.value.code == "grn_reversed"


# --- Idempotency and refusals -----------------------------------------------------------------


def test_an_idempotency_key_replays_rather_than_posting_twice(
    db: Session, order_entry: OrderEntry
) -> None:
    _book_to_clearing(db, order_entry, "1000")
    line = _receive(db, order_entry, "10", "100")
    data = landed_cost_service.LandedCostInput(
        cost_date=MARCH,
        description="Freight",
        amount=Decimal(1000),
        basis=LandedCostBasis.QUANTITY,
        grn_line_ids=(line.id,),
    )

    first, replayed_first = landed_cost_service.post_landed_cost(
        db, order_entry.company_id, data, actor=order_entry.owner, idempotency_key="k-1"
    )
    second, replayed_second = landed_cost_service.post_landed_cost(
        db, order_entry.company_id, data, actor=order_entry.owner, idempotency_key="k-1"
    )

    assert (replayed_first, replayed_second) == (False, True)
    assert first.id == second.id
    assert _balance(db, order_entry, "1370") == ZERO
    _assert_everything(db, order_entry.company_id)


def test_an_allocation_of_zero_is_refused(db: Session, order_entry: OrderEntry) -> None:
    """There is no such document. An allocation of nothing has no shares to strike and nothing
    to take off the clearing account."""
    line = _receive(db, order_entry, "10", "100")

    with pytest.raises(LedgerStateError) as excinfo:
        _allocate(db, order_entry, "0", (line.id,))

    assert excinfo.value.code == "invalid_amount"


def test_an_allocation_needs_a_target(db: Session, order_entry: OrderEntry) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        _allocate(db, order_entry, "500", ())

    assert excinfo.value.code == "empty_document"


def test_allocated_per_grn_line_is_a_query_over_unreversed_documents(
    db: Session, order_entry: OrderEntry
) -> None:
    """What a receipt actually ended up costing — the read the step-5 landed-cost listing is
    built on, pinned here rather than shipped untested ahead of its screen.

    A query like every other figure in the phase: reversing the allocation takes its shares
    off this sum by construction, with nothing to correct and nothing to remember.
    """
    _book_to_clearing(db, order_entry, "1000")
    first = _receive(db, order_entry, "60", "1000")
    second = _receive(db, order_entry, "40", "1000")

    document = _allocate(db, order_entry, "1000", (first.id, second.id))
    allocated = landed_cost_service.allocated_per_grn_line(
        db, order_entry.company_id, [first.id, second.id]
    )
    assert allocated == {first.id: Decimal(600), second.id: Decimal(400)}

    landed_cost_service.reverse_landed_cost(
        db, document, on_date=APRIL, reason="Reassessed", actor=order_entry.owner
    )

    assert landed_cost_service.allocated_per_grn_line(
        db, order_entry.company_id, [first.id, second.id]
    ) == {}, "a reversed allocation still counted"
    _assert_everything(db, order_entry.company_id)
