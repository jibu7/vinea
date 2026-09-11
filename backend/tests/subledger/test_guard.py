"""P4 step 2 — the posting contract and the control-account guard.

The Posting Engine refuses these first (better messages); the database refuses them again
(SQLSTATE VN007/VN008), which is what makes the rule real rather than a code convention.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.inventory import masters as inventory_masters
from app.kernel import posting
from app.kernel.errors import (
    SQLSTATE_CONTROL_ACCOUNT,
    SQLSTATE_CONTROL_PARTNER,
    PostingError,
    kernel_sqlstate,
)
from app.kernel.events import LineSpec, ManualJournal, PartnerDocumentPosted
from app.kernel.sequences import DocType
from app.models.gl import ControlType, GLAccount
from app.models.inventory import Uom, UomCategory
from app.models.journal import JournalEntry
from tests.kernel.conftest import Ledger, post_simple
from tests.subledger.conftest import MARCH, Subledger


def test_manual_journal_cannot_reach_the_ar_control_account(
    db: Session, ledger: Ledger
) -> None:
    with pytest.raises(PostingError) as excinfo:
        post_simple(db, ledger, debit="1200", credit="2300", amount=Decimal(100), on=MARCH)
    assert excinfo.value.code == "control_account_direct_posting"
    db.rollback()


def test_manual_journal_cannot_reach_the_ap_control_account(
    db: Session, ledger: Ledger
) -> None:
    with pytest.raises(PostingError) as excinfo:
        post_simple(db, ledger, debit="6500", credit="2100", amount=Decimal(100), on=MARCH)
    assert excinfo.value.code == "control_account_direct_posting"
    db.rollback()


def test_a_subledger_event_from_the_wrong_module_is_refused(
    db: Session, subledger: Subledger
) -> None:
    ledger = subledger.ledger
    with pytest.raises(PostingError) as excinfo:
        posting.post(
            db,
            PartnerDocumentPosted(
                module="ap",  # AP module aimed at the AR control account
                doc_type=DocType.AP_INVOICE,
                entry_date=MARCH,
                description="wrong module",
                lines=(
                    LineSpec(
                        amount=Decimal(100),
                        gl_account_id=ledger.acct("1200"),
                        partner_type="customer",
                        partner_id=subledger.customer.id,
                    ),
                    LineSpec(amount=Decimal(-100), gl_account_id=ledger.acct("4100")),
                ),
            ),
            company_id=ledger.company_id,
            actor=ledger.owner,
        )
    assert excinfo.value.code == "control_account_direct_posting"
    db.rollback()


def test_a_control_account_line_must_carry_its_partner(
    db: Session, subledger: Subledger
) -> None:
    ledger = subledger.ledger
    with pytest.raises(PostingError) as excinfo:
        posting.post(
            db,
            PartnerDocumentPosted(
                module="ar",
                doc_type=DocType.AR_INVOICE,
                entry_date=MARCH,
                description="no partner",
                lines=(
                    LineSpec(amount=Decimal(100), gl_account_id=ledger.acct("1200")),
                    LineSpec(amount=Decimal(-100), gl_account_id=ledger.acct("4100")),
                ),
            ),
            company_id=ledger.company_id,
            actor=ledger.owner,
        )
    assert excinfo.value.code == "dimension_required"
    db.rollback()


# --- The database is the authority ---------------------------------------------------------

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
                               partner_type, partner_id, item_id)
    VALUES (:cid, :entry, 1, :account, :branch, :currency, 1, 100, 100, 0,
            :partner_type, :partner_id, :item_id)
    """
)


def _raw_line(
    db: Session,
    subledger: Subledger,
    *,
    module: str,
    partner_type: str | None,
    partner_id: int | None,
    number: str,
    account_code: str = "1200",
    item_id: int | None = None,
) -> None:
    ledger = subledger.ledger
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
            "partner_type": partner_type,
            "partner_id": partner_id,
            "item_id": item_id,
        },
    )


def test_db_refuses_a_control_line_from_the_wrong_module(
    db: Session, subledger: Subledger
) -> None:
    with pytest.raises(DBAPIError) as excinfo:
        _raw_line(
            db,
            subledger,
            module="gl",
            partner_type="customer",
            partner_id=subledger.customer.id,
            number="RAW-1",
        )
    assert kernel_sqlstate(excinfo.value) == SQLSTATE_CONTROL_ACCOUNT
    db.rollback()


def test_db_refuses_a_control_line_without_its_partner(
    db: Session, subledger: Subledger
) -> None:
    with pytest.raises(DBAPIError) as excinfo:
        _raw_line(db, subledger, module="ar", partner_type=None, partner_id=None, number="RAW-2")
    assert kernel_sqlstate(excinfo.value) == SQLSTATE_CONTROL_PARTNER
    db.rollback()


def test_db_refuses_a_control_line_with_the_wrong_partner_type(
    db: Session, subledger: Subledger
) -> None:
    with pytest.raises(DBAPIError) as excinfo:
        _raw_line(
            db,
            subledger,
            module="ar",
            partner_type="supplier",
            partner_id=subledger.supplier.id,
            number="RAW-3",
        )
    assert kernel_sqlstate(excinfo.value) == SQLSTATE_CONTROL_PARTNER
    db.rollback()


def test_gl_entries_keep_their_module(db: Session, ledger: Ledger) -> None:
    entry = post_simple(
        db, ledger, debit="6500", credit="2300", amount=Decimal(100), on=MARCH
    )
    db.commit()
    stored = db.scalar(select(JournalEntry.module).where(JournalEntry.id == entry.id))
    assert stored == "gl"
    assert isinstance(entry.entry_date, date)


def test_manual_journal_still_names_bank_accounts_distinctly(
    db: Session, ledger: Ledger
) -> None:
    """Bank/cash keeps its own error code, so the P3 UI mapping stays valid."""
    with pytest.raises(PostingError) as excinfo:
        posting.post(
            db,
            ManualJournal(
                entry_date=MARCH,
                description="cash by hand",
                lines=(
                    LineSpec(amount=Decimal(100), gl_account_id=ledger.acct("1120")),
                    LineSpec(amount=Decimal(-100), gl_account_id=ledger.acct("2300")),
                ),
            ),
            company_id=ledger.company_id,
            actor=ledger.owner,
        )
    assert excinfo.value.code == "control_account_manual_posting"
    db.rollback()


def test_the_inventory_item_check_guards_the_stock_account(
    db: Session, subledger: Subledger
) -> None:
    """`1300 Inventory` is an `inventory` control account in every tenant, and it is postable —
    so the item-dimension rule added in 0009 has a live account to fire on. No manual journal
    can reach it: the registry pairs `inventory` with module `inv` alone, so the module check
    refuses the line first, at both layers.

    Until P5 the rule read `journal_lines.item_id` as a nullable bigint pointing at nothing,
    and this test recorded that any id satisfied it. P5 step 1 gives `items` its table, so the
    dimension is now a real foreign key: an INV line still needs an item, and the item it
    names has to exist."""
    ledger = subledger.ledger
    stock = db.scalar(
        select(GLAccount).where(
            GLAccount.company_id == ledger.company_id, GLAccount.code == "1300"
        )
    )
    assert stock is not None
    assert stock.control_type == ControlType.INVENTORY and stock.is_postable
    category = db.scalars(
        select(UomCategory).where(
            UomCategory.company_id == ledger.company_id, UomCategory.code == "COUNT"
        )
    ).one()
    base_uom = db.scalars(
        select(Uom).where(Uom.company_id == ledger.company_id, Uom.category_id == category.id)
    ).one()

    # Posting Engine: refused for the module, never for the missing item.
    with pytest.raises(PostingError) as excinfo:
        post_simple(db, ledger, debit="1300", credit="2300", amount=Decimal(100), on=MARCH)
    assert excinfo.value.code == "control_account_direct_posting"
    db.rollback()

    # Database: the same order. VN007 (module), not VN008 (dimension).
    with pytest.raises(DBAPIError) as excinfo:
        _raw_line(
            db,
            subledger,
            module="gl",
            partner_type=None,
            partner_id=None,
            number="RAW-INV-1",
            account_code="1300",
        )
    assert kernel_sqlstate(excinfo.value) == SQLSTATE_CONTROL_ACCOUNT
    db.rollback()

    # Reach past the module check the only way there is — claim to be `inv` — and the item
    # rule is there, reading `item_id`, waiting for P5.
    with pytest.raises(DBAPIError) as excinfo:
        _raw_line(
            db,
            subledger,
            module="inv",
            partner_type=None,
            partner_id=None,
            number="RAW-INV-2",
            account_code="1300",
        )
    assert kernel_sqlstate(excinfo.value) == SQLSTATE_CONTROL_PARTNER
    assert "requires an item" in str(excinfo.value)
    db.rollback()

    # An item id that names nothing is refused by the foreign key 0012 adds — the dimension
    # is a reference now, not a number.
    with pytest.raises(DBAPIError) as excinfo:
        _raw_line(
            db,
            subledger,
            module="inv",
            partner_type=None,
            partner_id=None,
            number="RAW-INV-3",
            account_code="1300",
            item_id=2_000_000_000,
        )
    assert "fk_journal_lines_item" in str(excinfo.value)
    db.rollback()

    # And it is satisfied by a real item.
    item = inventory_masters.create_item(
        db,
        ledger.company_id,
        inventory_masters.ItemInput(
            code="GUARD-001",
            name="Guard item",
            uom_category_id=category.id,
            base_uom_id=base_uom.id,
        ),
        actor=ledger.owner,
    )
    db.flush()
    _raw_line(
        db,
        subledger,
        module="inv",
        partner_type=None,
        partner_id=None,
        number="RAW-INV-4",
        account_code="1300",
        item_id=item.id,
    )
    db.rollback()
