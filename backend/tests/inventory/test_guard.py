"""P5 step 1 — the INV control-account guard, at both layers.

Decision 2 says the inventory accounts are control accounts: only `module='inv'` may post to
them, and every line on one carries an `item_id`. That is not a code convention — it is the
`control_account_modules` registry P4 seeded (`('inventory', 'inv')`), enforced by the
Posting Engine in Python and again by `kernel_check_subledger_line()` in the database.

P5 step 1 is what makes the second half of the rule *reachable*: `items` now exists, so
`item_id` is a real reference rather than a number. These three tests pin the whole rule:

1. a manual journal to 1300 is refused for the module (`control_account_direct_posting`);
2. an `inv` entry on 1300 without an item is refused for the dimension (VN008);
3. the same entry with a real item is accepted.

Layers 2 and 3 were written against raw SQL claiming `module='inv'`, because when step 1
landed no inventory event was postable through the engine. Step 2 makes them postable, and
the engine-level twins are at the bottom of this file. Both stay: the database is the
authority the engine is checked against, so the raw tests keep their meaning — the twins
prove the engine agrees with it, and one of them still needs a hand-built event because the
stock service cannot produce a line without an item.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.inventory import masters
from app.kernel.errors import (
    SQLSTATE_CONTROL_ACCOUNT,
    SQLSTATE_CONTROL_PARTNER,
    PostingError,
    kernel_sqlstate,
)
from app.kernel.sequences import DocType
from app.models.gl import ControlAccountModule, ControlType, GLAccount
from app.models.inventory import INVENTORY_MODULE
from tests.inventory.conftest import Inventory, Stock, receive
from tests.kernel.conftest import post_simple
from tests.subledger.conftest import MARCH

INSERT_ENTRY = text(
    """
    INSERT INTO journal_entries (company_id, number, doc_type, event_type, module, entry_date,
                                 period_id, description, status)
    VALUES (:cid, :number, 'JE', 'manual_journal', :module, :on, :period, 'raw', 'draft')
    RETURNING id
    """
)
INSERT_LINE = text(
    """
    INSERT INTO journal_lines (company_id, entry_id, line_no, gl_account_id, branch_id,
                               currency_id, exchange_rate, amount, base_amount, tax_amount,
                               item_id)
    VALUES (:cid, :entry, 1, :account, :branch, :currency, 1, 100, 100, 0, :item_id)
    """
)


def _raw_line(
    db: Session,
    inventory: Inventory,
    *,
    module: str,
    number: str,
    account_code: str = "1300",
    item_id: int | None = None,
) -> None:
    """One line, written past the Posting Engine, to reach the database's own rules.

    The single-writer guard is opened deliberately: the point is to test the trigger, and the
    trigger is what stands between a determined writer and the ledger.
    """
    ledger = inventory.ledger
    period_id = next(p.id for p in ledger.periods if p.start_date <= MARCH <= p.end_date)
    db.execute(text("SELECT set_config('app.posting_engine', 'on', true)"))
    entry_id = db.execute(
        INSERT_ENTRY,
        {
            "cid": ledger.company_id,
            "number": number,
            "module": module,
            "on": MARCH,
            "period": period_id,
        },
    ).scalar_one()
    db.execute(
        INSERT_LINE,
        {
            "cid": ledger.company_id,
            "entry": entry_id,
            "account": ledger.acct(account_code),
            "branch": ledger.main_branch.id,
            "currency": ledger.base.id,
            "item_id": item_id,
        },
    )


def _item(db: Session, inventory: Inventory, code: str = "GUARD-001"):  # noqa: ANN201
    return masters.create_item(
        db,
        inventory.company_id,
        masters.ItemInput(
            code=code,
            name="Guarded item",
            uom_category_id=inventory.count.id,
            base_uom_id=inventory.each.id,
        ),
        actor=inventory.owner,
    )


def test_the_registry_pairs_the_inventory_control_type_with_the_inv_module_alone(
    db: Session, inventory: Inventory
) -> None:
    """The guard is a registry lookup, not an equality test, so the registry *is* the rule.

    Deny-by-default: a control type listed here at all is reachable only from the modules
    paired with it. `inventory` is paired with `inv` and nothing else, which is why a GL
    journal cannot reach 1300 and why P6's order-entry module will have to register a row of
    its own rather than the guard being loosened for it.
    """
    rows = {
        (row.control_type, row.module)
        for row in db.execute(
            select(ControlAccountModule.control_type, ControlAccountModule.module)
        )
    }

    assert rows == {
        (ControlType.AR, "ar"),
        (ControlType.AP, "ap"),
        (ControlType.INVENTORY, INVENTORY_MODULE),
    }
    assert {module for control, module in rows if control == ControlType.INVENTORY} == {"inv"}


def test_the_inventory_accounts_are_control_accounts_in_a_seeded_tenant(
    db: Session, inventory: Inventory
) -> None:
    accounts = {
        row.code: row
        for row in db.scalars(
            select(GLAccount).where(
                GLAccount.company_id == inventory.company_id,
                GLAccount.code.in_(("1300", "1350")),
            )
        )
    }

    assert accounts["1300"].control_type == ControlType.INVENTORY
    assert accounts["1350"].control_type == ControlType.INVENTORY
    # Postable as well as control: the module posts to them, it is everyone else who cannot.
    assert all(row.is_postable and row.is_control for row in accounts.values())


# --- 1. A manual journal cannot reach the inventory account ---------------------------------


def test_a_manual_journal_to_the_inventory_account_is_refused(
    db: Session, inventory: Inventory
) -> None:
    """Consequence of decision 2, and the reason opening stock cannot arrive as a GL journal:
    it has to come through the inventory journal batch instead."""
    with pytest.raises(PostingError) as excinfo:
        post_simple(
            db, inventory.ledger, debit="1300", credit="2300", amount=Decimal(100), on=MARCH
        )

    assert excinfo.value.code == "control_account_direct_posting"
    db.rollback()


def test_a_manual_journal_to_the_in_transit_account_is_refused(
    db: Session, inventory: Inventory
) -> None:
    """1350 is the other INV account, and it is the one a hand-written "stock in transit"
    correction would reach for. The transfer legs are the only thing that may move it."""
    with pytest.raises(PostingError) as excinfo:
        post_simple(
            db, inventory.ledger, debit="1350", credit="2300", amount=Decimal(100), on=MARCH
        )

    assert excinfo.value.code == "control_account_direct_posting"
    db.rollback()


def test_the_database_refuses_a_gl_line_on_the_inventory_account(
    db: Session, inventory: Inventory
) -> None:
    """The same refusal one layer down, past the Posting Engine: VN007, for the module."""
    with pytest.raises(DBAPIError) as excinfo:
        _raw_line(db, inventory, module="gl", number="RAW-INV-GL")

    assert kernel_sqlstate(excinfo.value) == SQLSTATE_CONTROL_ACCOUNT
    db.rollback()


# --- 2. An inv line on the inventory account needs an item ----------------------------------


def test_an_inventory_line_without_an_item_is_refused(
    db: Session, inventory: Inventory
) -> None:
    """VN008, for the dimension — the rule P4 added and P5 makes real.

    This is what guarantees the item enquiry and the valuation report can reconcile to the GL
    at all: a value sitting on the inventory account with no item behind it would appear in
    the account balance and in no item's history, and `assert_stock_invariants` would have
    nothing to match it against.
    """
    with pytest.raises(DBAPIError) as excinfo:
        _raw_line(db, inventory, module=INVENTORY_MODULE, number="RAW-INV-NOITEM")

    assert kernel_sqlstate(excinfo.value) == SQLSTATE_CONTROL_PARTNER
    assert "requires an item" in str(excinfo.value)
    db.rollback()


def test_an_inventory_line_naming_a_nonexistent_item_is_refused(
    db: Session, inventory: Inventory
) -> None:
    """The dimension is a reference from P5 step 1 on, not just a non-null number."""
    with pytest.raises(DBAPIError) as excinfo:
        _raw_line(
            db,
            inventory,
            module=INVENTORY_MODULE,
            number="RAW-INV-GHOST",
            item_id=2_000_000_000,
        )

    assert "fk_journal_lines_item" in str(excinfo.value)
    db.rollback()


# --- 3. An inv line with a real item is accepted --------------------------------------------


def test_an_inventory_line_with_a_real_item_is_accepted(
    db: Session, inventory: Inventory
) -> None:
    """The positive half. Without it the two refusals above would also pass against a guard
    that simply rejected every inventory line — which is not the rule, and would make the
    whole module unpostable in step 2."""
    item = _item(db, inventory)
    db.flush()

    _raw_line(
        db,
        inventory,
        module=INVENTORY_MODULE,
        number="RAW-INV-OK",
        item_id=item.id,
    )

    written = db.execute(
        text(
            "SELECT l.gl_account_id, l.item_id, e.module FROM journal_lines l "
            "JOIN journal_entries e ON e.id = l.entry_id WHERE e.number = 'RAW-INV-OK'"
        )
    ).one()
    assert written.item_id == item.id
    assert written.module == INVENTORY_MODULE
    assert written.gl_account_id == inventory.ledger.acct("1300")
    db.rollback()


def test_the_in_transit_account_takes_an_inv_line_with_an_item_too(
    db: Session, inventory: Inventory
) -> None:
    """Both INV accounts behave the same way — the transfer legs post to 1350 under exactly
    these rules in step 4."""
    item = _item(db, inventory, code="GUARD-002")
    db.flush()

    _raw_line(
        db,
        inventory,
        module=INVENTORY_MODULE,
        number="RAW-TRN-OK",
        account_code="1350",
        item_id=item.id,
    )

    count = db.execute(
        text("SELECT count(*) FROM journal_entries WHERE number = 'RAW-TRN-OK'")
    ).scalar_one()
    assert count == 1
    db.rollback()


# --- Step 2: the same three rules, now through the Posting Engine ---------------------------
#
# The three tests above reach the database directly, because when they were written nothing
# inventory could post — `StockAdjusted` was a stub and `post()` refused it. Step 2 makes
# `receive_stock()` / `issue_stock()` real, so the guard can be exercised where the product
# actually meets it. Both layers stay: the database is the authority, and these are the proof
# that the engine agrees with it.


def test_the_engine_posts_an_inventory_line_with_its_item(db: Session, stock: Stock) -> None:
    """Layer 3's twin. The item dimension is not something the stock service remembers to
    add — it is on every inventory line by construction, because the line *is* a move."""
    posting = receive(db, stock, quantity=Decimal(4), unit_cost=Decimal(250), on=MARCH)

    inventory_lines = [
        line
        for line in posting.entry.lines
        if line.gl_account_id == stock.inventory.settings.inventory_account_id
    ]
    assert len(inventory_lines) == 1
    assert inventory_lines[0].item_id == stock.item.id
    assert posting.entry.module == INVENTORY_MODULE


def test_the_engine_refuses_an_inventory_line_without_an_item(
    db: Session, stock: Stock
) -> None:
    """Layer 2's twin, at the level the rule is written at: a `module='inv'` event that names
    an INV account and no item is refused by the engine's own dimension check, before the
    trigger ever sees it (`VN008` is what catches it if the engine is wrong).

    Reaching this needs a hand-built event, because the stock service cannot produce a line
    without an item — which is the point, and is why the raw-SQL test above stays.
    """
    from app.kernel import posting as posting_engine
    from app.kernel.events import LineSpec, StockAdjusted

    with pytest.raises(PostingError) as excinfo:
        posting_engine.post(
            db,
            StockAdjusted(
                entry_date=MARCH,
                description="an inventory line with no item",
                doc_type=DocType.INV_ADJUSTMENT,
                lines=(
                    LineSpec(
                        amount=Decimal(100),
                        gl_account_id=stock.inventory.settings.inventory_account_id,
                    ),
                    LineSpec(amount=Decimal(-100), gl_account_id=stock.ledger_account("5200")),
                ),
            ),
            company_id=stock.company_id,
            actor=stock.owner,
        )

    assert excinfo.value.code == "dimension_required"
    assert excinfo.value.field_errors == {"lines.0.item_id": ["item required"]}
    db.rollback()


def test_a_gl_journal_still_cannot_reach_the_inventory_account_now_that_inventory_can(
    db: Session, stock: Stock
) -> None:
    """Layer 1's twin. Making the module postable is exactly the moment this could have been
    loosened by accident — the registry is deny-by-default for everything but `inv`, and
    posting real stock does not change that."""
    receive(db, stock, quantity=Decimal(4), unit_cost=Decimal(250), on=MARCH)

    with pytest.raises(PostingError) as excinfo:
        post_simple(
            db, stock.inventory.ledger, debit="1300", credit="2300", amount=Decimal(50), on=MARCH
        )

    assert excinfo.value.code == "control_account_direct_posting"
    db.rollback()
