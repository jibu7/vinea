"""The P6 acceptance tape (step 5), row by row, in the RWF-base company.

**Every expected value below is a literal worked by hand**, never a figure computed by the
code under test. Where a row's arithmetic is not obvious the working is in a comment beside
it. That is the whole point of a tape: a test that recomputed the expectation the way the
service does would pass just as happily over a service that had been wrong from the start.

The rows are one sequence, not fourteen independent cases, because the interesting values are
the ones that depend on history — row 9's average of 1 111.1 exists only because rows 2 and 6
received at 1 000 and row 4 sold 30 before the freight landed, and row 11's flush exists only
because row 10 left exactly 20 behind. So it is one test. When it fails, the row number in the
assertion message is the place to look.

**After every row**: all four invariant suites, the accrual proof (accrual == Σ unmatched, per
branch), and the clearing proof (booked − allocated). The two listings are asked the same
questions the invariants ask the ledger, so a reporting path that drifted from the posting
path shows up here rather than on a screen.

The setup is the plan's: stock item X (selling 2 000 excl., VAT 18 % both ways), kit K = 2 x X
(3 500 excl.), warehouse Main, supplier S, forwarder F, customer C, `negative_stock_policy`
`block`, `backorder_policy` `allow` — both of which are what a fresh Rwanda seed already sets,
so the tape runs against the shipped defaults rather than a configuration invented for it.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.inventory import masters as inventory_masters
from app.inventory import stock as stock_service
from app.kernel import posting
from app.kernel.errors import LedgerStateError
from app.kernel.events import CashbookEntry, CashbookKind, CashbookLineSpec
from app.models.currency import Currency, ExchangeRate
from app.models.gl import BackorderPolicy
from app.models.inventory import (
    GrnStatus,
    ItemType,
    NegativeStockPolicy,
)
from app.models.journal import JournalEntry, JournalLine, JournalStatus
from app.models.order_entry import (
    LandedCostBasis,
    PurchaseOrderStatus,
    SalesOrderStatus,
)
from app.models.partner import PartnerRole, TaxMode
from app.models.subledger import DocumentKind, PartnerDocumentLine
from app.order_entry import enquiries as oe_enquiries
from app.order_entry import grn as grn_service
from app.order_entry import kits
from app.order_entry import landed_cost as landed_cost_service
from app.order_entry import orders as orders_service
from app.order_entry import quantities as order_quantities
from app.subledger import documents as documents_service
from app.subledger import masters as partner_masters
from tests.inventory.invariants import assert_stock_invariants
from tests.kernel.conftest import YEAR, Ledger
from tests.kernel.invariants import assert_ledger_invariants
from tests.order_entry.conftest import OrderEntry
from tests.order_entry.invariants import assert_order_invariants, verify_order_statuses
from tests.subledger.invariants import assert_subledger_invariants

ZERO = Decimal(0)
D = Decimal

#: The tape runs on one date so that nothing in it turns on a dated rate except the USD leg,
#: which seeds two rates of its own and says which each row uses.
TAPE_DAY = date(YEAR, 3, 10)
#: The USD leg's two dates — a receipt and an invoice at different rates, which is the whole
#: of what U1-U3 exist to prove.
USD_RECEIPT_DAY = date(YEAR, 3, 11)
USD_INVOICE_DAY = date(YEAR, 3, 12)


# --- The tape's fixture -------------------------------------------------------------------


@dataclass
class Tape:
    """The plan's setup, plus the two things the base fixture has no reason to carry: a
    forwarder to bill freight, and a USD-priced item for U1-U3."""

    oe: OrderEntry
    forwarder: object
    usd_item: object
    usd: Currency
    #: Row 8's freight line is zero-rated — the forwarder's invoice carries no VAT the tape
    #: needs to account for, and the row exists to prove a GL line can reach the clearing
    #: account, not to exercise tax.
    zero_rated_tax_code_id: int = 0

    @property
    def db_company(self) -> int:
        return self.oe.company_id


@pytest.fixture
def tape(db: Session, ledger: Ledger, order_entry: OrderEntry) -> Tape:
    oe = order_entry
    settings = oe.settings
    # The tape's policies are the seeded ones; asserted rather than set, so that a change to
    # the seed pack breaks here instead of quietly changing what the tape proves.
    assert settings.negative_stock_policy == NegativeStockPolicy.BLOCK
    assert settings.backorder_policy == BackorderPolicy.ALLOW

    # VAT 18 % both ways on X and on the kit, which the base fixture leaves unset because most
    # of its tests are about quantities rather than tax.
    vat_out = ledger.tax_codes["VAT-OUT-18"].id
    vat_in = ledger.tax_codes["VAT-IN-18"].id
    for item in (oe.stock_item, oe.kit_item):
        item.default_sales_tax_code_id = vat_out
        item.default_purchase_tax_code_id = vat_in
    oe.stock_item.default_purchase_tax_code_id = vat_in

    # Customer C has **no credit limit** — the tape's three invoices to C would otherwise have
    # to argue with the P4 credit check, which is not what any of these rows is about.
    oe.customer.credit_limit = None

    forwarder = partner_masters.create_partner(
        db,
        oe.company_id,
        partner_masters.PartnerInput(name="Kivu Freight Forwarders", supplier_code="SUPP-F"),
        actor=oe.owner,
    )
    usd = db.scalar(
        select(Currency).where(Currency.company_id == oe.company_id, Currency.code == "USD")
    )
    usd_item = inventory_masters.create_item(
        db,
        oe.company_id,
        inventory_masters.ItemInput(
            code="CORK-U",
            name="Imported corks",
            uom_category_id=oe.inventory.count.id,
            base_uom_id=oe.each.id,
            item_type=ItemType.STOCK,
            selling_price=Decimal(0),
            sales_account_id=oe.accounts["4100"].id,
            cogs_account_id=oe.accounts["5100"].id,
            default_purchase_tax_code_id=vat_in,
        ),
        actor=oe.owner,
    )
    # **The dated rates U2 and U3 turn on** (decision 14). The GRN values at the rate on its
    # own date and freezes that; the invoice books at its own, and the gap between the two is
    # the PPV of U3 — not an FX difference on the payable, which stays with P4's allocation.
    db.add_all(
        [
            ExchangeRate(
                company_id=oe.company_id,
                currency_id=usd.id,
                valid_from=USD_RECEIPT_DAY,
                rate=Decimal(1320),
            ),
            ExchangeRate(
                company_id=oe.company_id,
                currency_id=usd.id,
                valid_from=USD_INVOICE_DAY,
                rate=Decimal(1310),
            ),
        ]
    )
    db.flush()
    return Tape(
        oe=oe,
        forwarder=forwarder,
        usd_item=usd_item,
        usd=usd,
        zero_rated_tax_code_id=ledger.tax_codes["VAT-ZERO"].id,
    )


# --- Reading the ledger back --------------------------------------------------------------


def _entry_amounts(db: Session, tape: Tape, entry_id: int) -> dict[str, Decimal]:
    """Account code → Σ base_amount on one entry. Positive is a debit."""
    codes = {account.id: code for code, account in tape.oe.accounts.items()}
    out: dict[str, Decimal] = {}
    for line in db.scalars(select(JournalLine).where(JournalLine.entry_id == entry_id)):
        code = codes[line.gl_account_id]
        out[code] = out.get(code, ZERO) + line.base_amount
    return {code: amount for code, amount in out.items() if amount != ZERO}


def _balance(db: Session, tape: Tape, code: str) -> Decimal:
    """An account's base-currency balance, summed over its posted lines."""
    rows = db.scalars(
        select(JournalLine.base_amount)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == tape.oe.company_id,
            JournalLine.gl_account_id == tape.oe.accounts[code].id,
            JournalEntry.status == JournalStatus.POSTED,
        )
    ).all()
    return sum(rows, ZERO)


def _position(db: Session, tape: Tape, item_id: int, warehouse_id: int):  # noqa: ANN202
    return stock_service.location_balance(db, tape.oe.company_id, item_id, warehouse_id)


# --- The tape's own bookkeeping -------------------------------------------------------------
#
# Expected-vs-actual for every row is collected as it goes and printed at the end, because the
# step-5 gate report is that table. Collecting it costs nothing and means the report quotes the
# run rather than a transcription of it.

_TABLE: list[tuple[str, str, str, str, str]] = []


def _expect(row: str, label: str, expected, actual) -> None:  # noqa: ANN001
    """Assert one figure and record it for the report table."""
    ok = expected == actual
    _TABLE.append((row, label, str(expected), str(actual), "ok" if ok else "MISMATCH"))
    assert ok, f"tape row {row}: {label} expected {expected}, got {actual}"


@pytest.fixture(scope="module", autouse=True)
def _print_table():  # noqa: ANN202
    yield
    if not _TABLE:
        return
    width = max(len(row[1]) for row in _TABLE)
    print("\n[tape] expected vs actual")
    for row, label, expected, actual, status in _TABLE:
        print(
            f"  {row:<4} {label:<{width}}  expected {expected:>12}  "
            f"actual {actual:>12}  {status}"
        )


def _after_every_row(db: Session, tape: Tape, row: str) -> None:
    """All four suites, plus the two account proofs, after every row of the tape."""
    company_id = tape.oe.company_id
    db.flush()
    assert_ledger_invariants(db, company_id)
    assert_subledger_invariants(db, company_id)
    assert_stock_invariants(db, company_id)
    assert_order_invariants(db, company_id)
    assert verify_order_statuses(db, company_id) == [], f"row {row}: status drift"

    # The accrual tie, asked of the **listing** rather than of the invariant's own query: the
    # reporting path and the posting path have to agree, and this is where a screen that read
    # the wrong column would be caught (rule 13's failure mode, one layer down).
    #
    # **The sign.** The accrual is a liability and a receipt credits it, so the account's
    # balance is negative while goods are outstanding (the invariant suite signs it the same
    # way). The listing shows the positive figure a person reads — "this receipt still has
    # 60 000 to be invoiced" — so the tie is against the negated balance. Stated here rather
    # than absorbed by an `abs()`, because a sign that flips for a reason should say the
    # reason, and one that flips for no reason should fail.
    listing = oe_enquiries.goods_received_listing(db, company_id)
    accrual = _balance(db, tape, "2350")
    assert listing.unmatched_total == -accrual, (
        f"row {row}: goods-received listing says {listing.unmatched_total} unmatched, "
        f"the accrual account says {accrual}"
    )


def _line_of(document, item_id: int):  # noqa: ANN001, ANN202
    """The first posted line on `document` carrying `item_id`."""
    return next(line for line in document.lines if line.item_id == item_id)


def _post(db: Session, tape: Tape, role: PartnerRole, **kwargs):  # noqa: ANN003, ANN202
    document, _ = documents_service.post_document(
        db,
        tape.oe.company_id,
        role,
        documents_service.DocumentInput(tax_mode=TaxMode.EXCLUSIVE, **kwargs),
        actor=tape.oe.owner,
    )
    return document


# --- The tape -------------------------------------------------------------------------------


def test_the_acceptance_tape(db: Session, tape: Tape) -> None:  # noqa: PLR0915
    oe = tape.oe
    company_id = oe.company_id
    main = oe.main
    X = oe.stock_item
    K = oe.kit_item

    # --- Row 1: PO-1 to S, 100 x X @ 1 000 -------------------------------------------------
    # net 100 x 1 000 = 100 000 · tax 18 % = 18 000 · gross 118 000
    po1, _ = orders_service.create_purchase_order(
        db,
        company_id,
        orders_service.PurchaseOrderInput(
            partner_id=oe.supplier.id,
            order_date=TAPE_DAY,
            description="PO-1",
            warehouse_id=main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                orders_service.OrderLineInput(
                    item_id=X.id, quantity=D(100), unit_price=D(1000)
                ),
            ),
        ),
        actor=oe.owner,
    )
    _expect("1", "PO-1 net", D(100_000), po1.net_amount)
    _expect("1", "PO-1 tax", D(18_000), po1.tax_amount)
    _expect("1", "PO-1 gross", D(118_000), po1.total_amount)
    _expect("1", "PO-1 status", PurchaseOrderStatus.OPEN, po1.status)
    _expect(
        "1",
        "on order X@Main",
        D(100),
        order_quantities.position(db, company_id, X.id, main.id).on_order,
    )
    _expect(
        "1",
        "entries so far",
        0,
        db.scalar(
            select(func.count())
            .select_from(JournalEntry)
            .where(JournalEntry.company_id == company_id)
        ),
    )
    _after_every_row(db, tape, "1")

    # --- Row 2: GRN-1 vs PO-1, 60 ----------------------------------------------------------
    # 60 @ 1 000 = 60 000 into Main. avg 1 000. Accrual credited 60 000.
    po1_line = po1.lines[0]
    grn1, _ = grn_service.post_grn(
        db,
        company_id,
        grn_service.GrnInput(
            partner_id=oe.supplier.id,
            grn_date=TAPE_DAY,
            description="GRN-1",
            warehouse_id=main.id,
            purchase_order_id=po1.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=X.id,
                    quantity=D(60),
                    unit_cost=D(1000),
                    purchase_order_line_id=po1_line.id,
                ),
            ),
        ),
        actor=oe.owner,
    )
    position = _position(db, tape, X.id, main.id)
    _expect("2", "Main quantity", D(60), position.quantity)
    _expect("2", "Main value", D(60_000), position.value)
    _expect("2", "avg", D(1000), stock_service.item_state(db, company_id, X.id).average)
    _expect(
        "2",
        "GRN-1 entry",
        {"1300": D(60_000), "2350": D(-60_000)},
        _entry_amounts(db, tape, grn1.journal_entry_id),
    )
    _expect("2", "GRN-1 number prefix", "GRN-", grn1.number[:4])
    db.refresh(po1)
    _expect("2", "PO-1 status", PurchaseOrderStatus.PARTIALLY_RECEIVED, po1.status)
    _expect(
        "2",
        "PO-1 received",
        D(60),
        order_quantities.purchase_fulfilment(db, company_id, order_id=po1.id)[
            po1_line.id
        ].fulfilled,
    )
    _expect(
        "2",
        "on order X@Main",
        D(40),
        order_quantities.position(db, company_id, X.id, main.id).on_order,
    )
    _expect("2", "accrual balance", D(-60_000), _balance(db, tape, "2350"))
    _after_every_row(db, tape, "2")

    # --- Row 3: SO-1 to C, 28 x X @ 2 000 + 1 x K @ 3 500 (breakup -> 2 x X) ---------------
    # net 28 x 2 000 + 3 500 = 59 500 · tax 18 % = 10 710 · gross 70 210
    # committed counts stock items only: 28 + 2 = 30, never the kit itself.
    so1, _ = orders_service.create_sales_order(
        db,
        company_id,
        orders_service.SalesOrderInput(
            partner_id=oe.customer.id,
            order_date=TAPE_DAY,
            description="SO-1",
            warehouse_id=main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                orders_service.OrderLineInput(item_id=X.id, quantity=D(28)),
                orders_service.OrderLineInput(item_id=K.id, quantity=D(1)),
            ),
        ),
        actor=oe.owner,
    )
    kit_line = next(line for line in so1.lines if line.item_id == K.id)
    # Breakup against the definition's own default (2 x X): the screen's path, exercised, with
    # the tape's numbers unchanged because the definition is what the tape orders.
    orders_service.breakup_sales_order_line(
        db,
        company_id,
        so1,
        kit_line.id,
        (kits.ComponentInput(item_id=X.id, base_quantity=D(2)),),
        actor=oe.owner,
    )
    db.flush()
    db.refresh(so1)
    _expect("3", "SO-1 net", D(59_500), so1.net_amount)
    _expect("3", "SO-1 tax", D(10_710), so1.tax_amount)
    _expect("3", "SO-1 gross", D(70_210), so1.total_amount)
    p3 = order_quantities.position(db, company_id, X.id, main.id)
    _expect("3", "committed X@Main", D(30), p3.committed)
    _expect("3", "available X@Main", D(30), p3.available)
    _expect("3", "no entry for SO-1", None, getattr(so1, "journal_entry_id", None))
    _after_every_row(db, tape, "3")

    # --- Row 4: invoice SO-1 in full --------------------------------------------------------
    # AR 1200 +70 210 / 4100 -59 500 / 2200 -10 710
    # companion STK-: 30 units at the average of 1 000 -> 5100 +30 000 / 1300 -30 000
    x_line = next(
        line
        for line in so1.lines
        if line.item_id == X.id and line.kit_parent_line_id is None
    )
    component = next(line for line in so1.lines if line.kit_parent_line_id == kit_line.id)
    inv1 = _post(
        db,
        tape,
        PartnerRole.AR,
        kind=DocumentKind.INVOICE,
        partner_id=oe.customer.id,
        document_date=TAPE_DAY,
        description="Invoice SO-1",
        lines=(
            documents_service.LineInput(
                item_id=X.id,
                quantity=D(28),
                unit_price=D(2000),
                warehouse_id=main.id,
                sales_order_line_id=x_line.id,
            ),
            documents_service.LineInput(
                item_id=K.id,
                quantity=D(1),
                unit_price=D(3500),
                warehouse_id=main.id,
                sales_order_line_id=kit_line.id,
                kit_components=(
                    documents_service.LineInput(
                        item_id=X.id,
                        quantity=D(2),
                        warehouse_id=main.id,
                        sales_order_line_id=component.id,
                    ),
                ),
            ),
        ),
    )
    _expect(
        "4",
        "AR entry",
        {"1200": D(70_210), "4100": D(-59_500), "2200": D(-10_710)},
        _entry_amounts(db, tape, inv1.journal_entry_id),
    )
    _expect(
        "4",
        "companion entry",
        {"5100": D(30_000), "1300": D(-30_000)},
        _entry_amounts(db, tape, inv1.stock_entry_id),
    )
    _expect(
        "4",
        "companion number prefix",
        "STK-",
        db.get(JournalEntry, inv1.stock_entry_id).number[:4],
    )
    position = _position(db, tape, X.id, main.id)
    _expect("4", "Main quantity", D(30), position.quantity)
    _expect("4", "Main value", D(30_000), position.value)
    _expect("4", "avg", D(1000), stock_service.item_state(db, company_id, X.id).average)
    db.refresh(so1)
    _expect("4", "SO-1 status", SalesOrderStatus.INVOICED, so1.status)
    fulfilment = order_quantities.sales_fulfilment(db, company_id, order_id=so1.id)
    _expect("4", "SO-1 X invoiced", D(28), fulfilment[x_line.id].fulfilled)
    _expect("4", "SO-1 K invoiced", D(1), fulfilment[kit_line.id].fulfilled)
    _expect(
        "4",
        "committed X@Main",
        ZERO,
        order_quantities.position(db, company_id, X.id, main.id).committed,
    )
    _after_every_row(db, tape, "4")

    # --- Row 5: SO-2 to C, 70 x X @ 2 000 ---------------------------------------------------
    # net 70 x 2 000 = 140 000 · tax 18 % = 25 200 · gross 165 200
    # Main holds 30, so 70 committed leaves available -40 and 40 of the line backordered. The
    # default policy permits it; `backorder_policy = block` refusing this order is asserted
    # outside the tape, in test_the_backorder_policy_refuses_row_5.
    so2, _ = orders_service.create_sales_order(
        db,
        company_id,
        orders_service.SalesOrderInput(
            partner_id=oe.customer.id,
            order_date=TAPE_DAY,
            description="SO-2",
            warehouse_id=main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(orders_service.OrderLineInput(item_id=X.id, quantity=D(70)),),
        ),
        actor=oe.owner,
    )
    so2_line = so2.lines[0]
    _expect("5", "SO-2 net", D(140_000), so2.net_amount)
    _expect("5", "SO-2 tax", D(25_200), so2.tax_amount)
    _expect("5", "SO-2 gross", D(165_200), so2.total_amount)
    p5 = order_quantities.position(db, company_id, X.id, main.id)
    _expect("5", "committed X@Main", D(70), p5.committed)
    _expect("5", "available X@Main", D(-40), p5.available)
    enquiry = oe_enquiries.sales_order_enquiry(db, company_id, so2.id)
    _expect("5", "backordered", D(40), enquiry.lines[0].backordered)
    _after_every_row(db, tape, "5")

    # --- Row 6: GRN-2 vs PO-1, 40 -----------------------------------------------------------
    # Main 60 + 40 = 70 at 1 000 = 70 000. Accrual 60 000 + 40 000 = 100 000.
    grn2, _ = grn_service.post_grn(
        db,
        company_id,
        grn_service.GrnInput(
            partner_id=oe.supplier.id,
            grn_date=TAPE_DAY,
            description="GRN-2",
            warehouse_id=main.id,
            purchase_order_id=po1.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=X.id,
                    quantity=D(40),
                    unit_cost=D(1000),
                    purchase_order_line_id=po1_line.id,
                ),
            ),
        ),
        actor=oe.owner,
    )
    position = _position(db, tape, X.id, main.id)
    _expect("6", "Main quantity", D(70), position.quantity)
    _expect("6", "Main value", D(70_000), position.value)
    _expect("6", "avg", D(1000), stock_service.item_state(db, company_id, X.id).average)
    _expect(
        "6",
        "GRN-2 entry",
        {"1300": D(40_000), "2350": D(-40_000)},
        _entry_amounts(db, tape, grn2.journal_entry_id),
    )
    _expect("6", "accrual balance", D(-100_000), _balance(db, tape, "2350"))
    db.refresh(po1)
    _expect("6", "PO-1 status", PurchaseOrderStatus.RECEIVED, po1.status)
    p6 = order_quantities.position(db, company_id, X.id, main.id)
    _expect("6", "on order X@Main", ZERO, p6.on_order)
    _expect("6", "available X@Main", ZERO, p6.available)
    _after_every_row(db, tape, "6")

    # --- Row 7: supplier invoice INV-A, 60 -> GRN-1 line and 40 -> GRN-2 line, both @ 1 020 --
    # **One invoice, two lines, two receipts** (the plan's row 7).
    # net 100 x 1 020 = 102 000 · tax 18 % = 18 360 · gross 120 360
    # The match relieves the frozen accrual — 60 000 + 40 000 — and the difference between the
    # 102 000 claimed and the 100 000 accrued is price variance: 5300 +2 000.
    grn1_line = grn1.lines[0]
    grn2_line = grn2.lines[0]
    inv_a = _post(
        db,
        tape,
        PartnerRole.AP,
        kind=DocumentKind.INVOICE,
        partner_id=oe.supplier.id,
        document_date=TAPE_DAY,
        description="INV-A",
        lines=(
            documents_service.LineInput(
                item_id=X.id, quantity=D(60), unit_price=D(1020), grn_line_id=grn1_line.id
            ),
            documents_service.LineInput(
                item_id=X.id, quantity=D(40), unit_price=D(1020), grn_line_id=grn2_line.id
            ),
        ),
    )
    _expect("7", "INV-A net", D(102_000), inv_a.net_amount)
    _expect("7", "INV-A tax", D(18_360), inv_a.tax_amount)
    _expect("7", "INV-A gross", D(120_360), inv_a.total_amount)
    _expect(
        "7",
        "AP entry",
        {"2350": D(100_000), "5300": D(2_000), "1400": D(18_360), "2100": D(-120_360)},
        _entry_amounts(db, tape, inv_a.journal_entry_id),
    )
    _expect("7", "accrual balance", ZERO, _balance(db, tape, "2350"))
    # **The posted relief, read back** — `accrual_relieved` is what the match wrote, never a
    # share recomputed from today's quantities (step 2's finding).
    relieved = {
        line.grn_line_id: line.accrual_relieved
        for line in db.scalars(
            select(PartnerDocumentLine).where(PartnerDocumentLine.document_id == inv_a.id)
        )
    }
    _expect("7", "relieved off GRN-1", D(60_000), relieved[grn1_line.id])
    _expect("7", "relieved off GRN-2", D(40_000), relieved[grn2_line.id])
    db.refresh(grn1)
    db.refresh(grn2)
    _expect("7", "GRN-1 status", GrnStatus.MATCHED, grn1.status)
    _expect("7", "GRN-2 status", GrnStatus.MATCHED, grn2.status)
    _expect("7", "companion (none — the goods came on the GRN)", None, inv_a.stock_entry_id)
    _expect("7", "avg", D(1000), stock_service.item_state(db, company_id, X.id).average)
    _after_every_row(db, tape, "7")

    # --- Row 8: supplier invoice INV-F from F, GL line 7 777 zero-rated to 1370 -------------
    # A **GL line**, not an item line: freight arrives on the forwarder's invoice and has to be
    # able to land on the clearing account, which is why 1370 is a plain account (decision 5).
    zero_rated = tape.zero_rated_tax_code_id
    inv_f = _post(
        db,
        tape,
        PartnerRole.AP,
        kind=DocumentKind.INVOICE,
        partner_id=tape.forwarder.id,
        document_date=TAPE_DAY,
        description="INV-F",
        lines=(
            documents_service.LineInput(
                gl_account_id=oe.accounts["1370"].id,
                quantity=D(1),
                unit_price=D(7777),
                tax_code_id=zero_rated,
                description="Ocean freight",
            ),
        ),
    )
    _expect(
        "8",
        "AP entry",
        {"1370": D(7_777), "2100": D(-7_777)},
        _entry_amounts(db, tape, inv_f.journal_entry_id),
    )
    _expect("8", "clearing balance", D(7_777), _balance(db, tape, "1370"))
    _expect("8", "companion (none — no stock line)", None, inv_f.stock_entry_id)
    _after_every_row(db, tape, "8")

    # --- Row 9: LCA-1, 7 777 by quantity across GRN-1 (60) and GRN-2 (40) -------------------
    # 7 777 x 60/100 = 4 666.2 -> 4 666 half-up; the residue rule gives the **last** line the
    # remainder, 7 777 - 4 666 = 3 111, so the shares sum to the amount to the franc.
    # Main then holds 70 000 + 7 777 = 77 777 over 70 units -> 1 111.1 exactly.
    lca1, _ = landed_cost_service.post_landed_cost(
        db,
        company_id,
        landed_cost_service.LandedCostInput(
            cost_date=TAPE_DAY,
            description="LCA-1 ocean freight",
            amount=D(7777),
            basis=LandedCostBasis.QUANTITY,
            grn_line_ids=(grn1_line.id, grn2_line.id),
            source_document_id=inv_f.id,
        ),
        actor=oe.owner,
    )
    shares = sorted(lca1.lines, key=lambda line: line.id)
    _expect("9", "share on GRN-1 line", D(4_666), shares[0].share)
    _expect("9", "share on GRN-2 line", D(3_111), shares[1].share)
    _expect("9", "shares sum to the amount", D(7_777), shares[0].share + shares[1].share)
    _expect(
        "9",
        "revaluation moves",
        2,
        sum(1 for line in lca1.lines if line.stock_move_id is not None),
    )
    _expect(
        "9",
        "LCA-1 entry",
        {"1300": D(7_777), "1370": D(-7_777)},
        _entry_amounts(db, tape, lca1.journal_entry_id),
    )
    _expect("9", "LCA-1 number prefix", "LCA-", lca1.number[:4])
    position = _position(db, tape, X.id, main.id)
    _expect("9", "Main quantity", D(70), position.quantity)
    _expect("9", "Main value", D(77_777), position.value)
    _expect("9", "avg", D("1111.1"), stock_service.item_state(db, company_id, X.id).average)
    _expect("9", "clearing balance", ZERO, _balance(db, tape, "1370"))
    # **Row 4's cost of sales is untouched.** The average rises from this posting onward; the
    # 30 units sold at 1 000 were sold at 1 000 and stay there (decision 9).
    _expect("9", "COGS unchanged", D(30_000), _balance(db, tape, "5100"))
    # `quantity_at_posting` is what the **location** held, so both targets record 70 — the
    # figure the reversal divides the share by, stored because it cannot be recovered later.
    _expect("9", "quantity_at_posting (GRN-1 line)", D(70), shares[0].quantity_at_posting)
    _expect("9", "quantity_at_posting (GRN-2 line)", D(70), shares[1].quantity_at_posting)
    _after_every_row(db, tape, "9")

    # --- Row 10: invoice SO-2 for 50 --------------------------------------------------------
    # AR 50 x 2 000 = 100 000 net · tax 18 000 · gross 118 000
    # companion: 50 at the new average of 1 111.1 = 55 555 exactly.
    # Main 77 777 - 55 555 = 22 222 over 20 units, still 1 111.1.
    inv2 = _post(
        db,
        tape,
        PartnerRole.AR,
        kind=DocumentKind.INVOICE,
        partner_id=oe.customer.id,
        document_date=TAPE_DAY,
        description="Invoice SO-2 part 1",
        lines=(
            documents_service.LineInput(
                item_id=X.id,
                quantity=D(50),
                unit_price=D(2000),
                warehouse_id=main.id,
                sales_order_line_id=so2_line.id,
            ),
        ),
    )
    _expect(
        "10",
        "AR entry",
        {"1200": D(118_000), "4100": D(-100_000), "2200": D(-18_000)},
        _entry_amounts(db, tape, inv2.journal_entry_id),
    )
    _expect(
        "10",
        "companion entry",
        {"5100": D(55_555), "1300": D(-55_555)},
        _entry_amounts(db, tape, inv2.stock_entry_id),
    )
    position = _position(db, tape, X.id, main.id)
    _expect("10", "Main quantity", D(20), position.quantity)
    _expect("10", "Main value", D(22_222), position.value)
    _expect("10", "avg", D("1111.1"), stock_service.item_state(db, company_id, X.id).average)
    db.refresh(so2)
    _expect("10", "SO-2 status", SalesOrderStatus.PARTIALLY_INVOICED, so2.status)
    _expect(
        "10",
        "SO-2 invoiced",
        D(50),
        order_quantities.sales_fulfilment(db, company_id, order_id=so2.id)[so2_line.id].fulfilled,
    )
    _expect(
        "10",
        "committed X@Main",
        D(20),
        order_quantities.position(db, company_id, X.id, main.id).committed,
    )
    _after_every_row(db, tape, "10")

    # --- Row 11: invoice SO-2 for the remaining 20, which empties Main ----------------------
    # AR 20 x 2 000 = 40 000 net · tax 7 200 · gross 47 200
    # **The flush**: the issue takes what the location has left rather than 20 x 1 111.1, so
    # the companion is 22 222 and Main lands on exactly 0 / 0 with no rounding crumb behind.
    inv3 = _post(
        db,
        tape,
        PartnerRole.AR,
        kind=DocumentKind.INVOICE,
        partner_id=oe.customer.id,
        document_date=TAPE_DAY,
        description="Invoice SO-2 part 2",
        lines=(
            documents_service.LineInput(
                item_id=X.id,
                quantity=D(20),
                unit_price=D(2000),
                warehouse_id=main.id,
                sales_order_line_id=so2_line.id,
            ),
        ),
    )
    _expect(
        "11",
        "AR entry",
        {"1200": D(47_200), "4100": D(-40_000), "2200": D(-7_200)},
        _entry_amounts(db, tape, inv3.journal_entry_id),
    )
    _expect(
        "11",
        "companion entry (flush)",
        {"5100": D(22_222), "1300": D(-22_222)},
        _entry_amounts(db, tape, inv3.stock_entry_id),
    )
    position = _position(db, tape, X.id, main.id)
    _expect("11", "Main quantity", ZERO, position.quantity)
    _expect("11", "Main value", ZERO, position.value)
    _expect("11", "avg stays", D("1111.1"), stock_service.item_state(db, company_id, X.id).average)
    db.refresh(so2)
    _expect("11", "SO-2 status", SalesOrderStatus.INVOICED, so2.status)
    _expect(
        "11",
        "SO-2 invoiced",
        D(70),
        order_quantities.sales_fulfilment(db, company_id, order_id=so2.id)[so2_line.id].fulfilled,
    )
    _after_every_row(db, tape, "11")

    # --- Row 12: duty paid by cashbook to 1370, then LCA-2 allocates it ---------------------
    # The duty is a **P3 cashbook payment**, which is only possible because 1370 is a plain
    # account: a control account would refuse it (decision 5, and that is the reason).
    posting.post(
        db,
        CashbookEntry(
            entry_date=TAPE_DAY,
            description="Duty to RRA",
            cash_account_id=oe.accounts["1120"].id,
            kind=CashbookKind.PAYMENT,
            lines=(
                CashbookLineSpec(gl_account_id=oe.accounts["1370"].id, amount=D(5000)),
            ),
        ),
        company_id=company_id,
        actor=oe.owner,
    )
    db.flush()
    _expect("12", "clearing after the payment", D(5_000), _balance(db, tape, "1370"))
    _expect("12", "bank after the payment", D(-5_000), _balance(db, tape, "1120"))

    # LCA-2 targets GRN-2's line, whose location Main now holds **none** of X — row 11 emptied
    # it. The share therefore goes to cost of sales on the same entry, with no move, so the
    # clearing account clears whether or not the goods are still there (decision 9).
    lca2, _ = landed_cost_service.post_landed_cost(
        db,
        company_id,
        landed_cost_service.LandedCostInput(
            cost_date=TAPE_DAY,
            description="LCA-2 import duty",
            amount=D(5000),
            basis=LandedCostBasis.QUANTITY,
            grn_line_ids=(grn2_line.id,),
        ),
        actor=oe.owner,
    )
    _expect(
        "12",
        "LCA-2 entry",
        {"5100": D(5_000), "1370": D(-5_000)},
        _entry_amounts(db, tape, lca2.journal_entry_id),
    )
    _expect(
        "12",
        "moves written",
        0,
        sum(1 for line in lca2.lines if line.stock_move_id is not None),
    )
    _expect("12", "went to COGS", True, lca2.lines[0].went_to_cogs)
    _expect("12", "quantity_at_posting", ZERO, lca2.lines[0].quantity_at_posting)
    _expect("12", "clearing balance", ZERO, _balance(db, tape, "1370"))
    position = _position(db, tape, X.id, main.id)
    _expect("12", "Main quantity", ZERO, position.quantity)
    _expect("12", "Main value", ZERO, position.value)
    _after_every_row(db, tape, "12")

    # --- Row 13: credit note CN-1 for C against row 4's X line, 5 x X -----------------------
    # AR reverses 5 x 2 000: 4100 +10 000 / 2200 +1 800 / 1200 -11 800.
    # The goods come back at **the cost they were issued at** — 1 000, not today's 1 111.1 —
    # because `returns_line_id` names the line they left on (decision 2).
    x_invoice_line = _line_of(inv1, X.id)
    cn1 = _post(
        db,
        tape,
        PartnerRole.AR,
        kind=DocumentKind.CREDIT_NOTE,
        partner_id=oe.customer.id,
        document_date=TAPE_DAY,
        description="CN-1",
        lines=(
            documents_service.LineInput(
                item_id=X.id,
                quantity=D(5),
                unit_price=D(2000),
                warehouse_id=main.id,
                returns_line_id=x_invoice_line.id,
            ),
        ),
    )
    _expect(
        "13",
        "AR entry",
        {"4100": D(10_000), "2200": D(1_800), "1200": D(-11_800)},
        _entry_amounts(db, tape, cn1.journal_entry_id),
    )
    _expect(
        "13",
        "companion entry",
        {"1300": D(5_000), "5100": D(-5_000)},
        _entry_amounts(db, tape, cn1.stock_entry_id),
    )
    position = _position(db, tape, X.id, main.id)
    _expect("13", "Main quantity", D(5), position.quantity)
    _expect("13", "Main value", D(5_000), position.value)
    _expect("13", "avg", D(1000), stock_service.item_state(db, company_id, X.id).average)
    _after_every_row(db, tape, "13")

    # --- Row 14: return to supplier DBN-1 to S, 5 x X @ 1 020, which empties Main -----------
    # net 5 x 1 020 = 5 100 · tax 918 · gross 6 018
    # The companion issues at the average (the flush, since it empties): 5 000 off inventory
    # and onto the accrual. The AP side credits the accrual for **exactly that** 5 000 and
    # sends the difference from the 5 100 claimed to price variance: 5300 -100.
    dbn1 = _post(
        db,
        tape,
        PartnerRole.AP,
        kind=DocumentKind.CREDIT_NOTE,
        partner_id=oe.supplier.id,
        document_date=TAPE_DAY,
        description="DBN-1",
        lines=(
            documents_service.LineInput(
                item_id=X.id, quantity=D(5), unit_price=D(1020), warehouse_id=main.id
            ),
        ),
    )
    _expect("14", "DBN-1 net", D(5_100), dbn1.net_amount)
    _expect("14", "DBN-1 tax", D(918), dbn1.tax_amount)
    _expect("14", "DBN-1 gross", D(6_018), dbn1.total_amount)
    _expect(
        "14",
        "companion entry (flush)",
        {"2350": D(5_000), "1300": D(-5_000)},
        _entry_amounts(db, tape, dbn1.stock_entry_id),
    )
    _expect(
        "14",
        "AP entry",
        {"2100": D(6_018), "1400": D(-918), "2350": D(-5_000), "5300": D(-100)},
        _entry_amounts(db, tape, dbn1.journal_entry_id),
    )
    _expect("14", "accrual balance", ZERO, _balance(db, tape, "2350"))
    position = _position(db, tape, X.id, main.id)
    _expect("14", "Main quantity", ZERO, position.quantity)
    _expect("14", "Main value", ZERO, position.value)
    _after_every_row(db, tape, "14")

    # --- U1: PO-U to S in USD, 10 x Y @ 10.00 ----------------------------------------------
    Y = tape.usd_item
    pou, _ = orders_service.create_purchase_order(
        db,
        company_id,
        orders_service.PurchaseOrderInput(
            partner_id=oe.supplier.id,
            order_date=USD_RECEIPT_DAY,
            description="PO-U",
            warehouse_id=main.id,
            currency_id=tape.usd.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                orders_service.OrderLineInput(
                    item_id=Y.id, quantity=D(10), unit_price=D("10.00")
                ),
            ),
        ),
        actor=oe.owner,
    )
    pou_line = pou.lines[0]
    _expect(
        "U1",
        "on order Y@Main",
        D(10),
        order_quantities.position(db, company_id, Y.id, main.id).on_order,
    )
    _after_every_row(db, tape, "U1")

    # --- U2: GRN-U vs PO-U, 10, at the rate on the GRN date (1 320) ------------------------
    # 10 x 10.00 x 1 320 = 132 000, frozen on the line.
    grnu, _ = grn_service.post_grn(
        db,
        company_id,
        grn_service.GrnInput(
            partner_id=oe.supplier.id,
            grn_date=USD_RECEIPT_DAY,
            description="GRN-U",
            warehouse_id=main.id,
            purchase_order_id=pou.id,
            currency_id=tape.usd.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=Y.id,
                    quantity=D(10),
                    unit_cost=D("10.00"),
                    purchase_order_line_id=pou_line.id,
                ),
            ),
        ),
        actor=oe.owner,
    )
    grnu_line = grnu.lines[0]
    _expect("U2", "frozen base value", D(132_000), grnu_line.value)
    _expect("U2", "rate used", D(1320), grnu.exchange_rate)
    position = _position(db, tape, Y.id, main.id)
    _expect("U2", "Main Y quantity", D(10), position.quantity)
    _expect("U2", "Main Y value", D(132_000), position.value)
    _expect("U2", "avg Y", D(13_200), stock_service.item_state(db, company_id, Y.id).average)
    _expect(
        "U2",
        "GRN-U entry",
        {"1300": D(132_000), "2350": D(-132_000)},
        _entry_amounts(db, tape, grnu.journal_entry_id),
    )
    _after_every_row(db, tape, "U2")

    # --- U3: supplier invoice INV-U, 10 -> GRN-U line @ 10.00, at the rate on its own date --
    # net 100.00 USD at 1 310 = 131 000 base; the match relieves the **frozen** 132 000, so
    # the rate movement lands in price variance: 131 000 - 132 000 = -1 000.
    # tax 18.00 USD x 1 310 = 23 580 · gross 118.00 USD x 1 310 = 154 580.
    inv_u = _post(
        db,
        tape,
        PartnerRole.AP,
        kind=DocumentKind.INVOICE,
        partner_id=oe.supplier.id,
        document_date=USD_INVOICE_DAY,
        description="INV-U",
        currency_id=tape.usd.id,
        lines=(
            documents_service.LineInput(
                item_id=Y.id,
                quantity=D(10),
                unit_price=D("10.00"),
                grn_line_id=grnu_line.id,
            ),
        ),
    )
    _expect("U3", "rate used", D(1310), inv_u.exchange_rate)
    _expect(
        "U3",
        "AP entry",
        {"2350": D(132_000), "5300": D(-1_000), "1400": D(23_580), "2100": D(-154_580)},
        _entry_amounts(db, tape, inv_u.journal_entry_id),
    )
    _expect("U3", "gross in USD", D("118.00"), inv_u.total_amount)
    relieved_u = db.scalar(
        select(PartnerDocumentLine.accrual_relieved).where(
            PartnerDocumentLine.document_id == inv_u.id
        )
    )
    _expect("U3", "relieved off GRN-U", D(132_000), relieved_u)
    _expect("U3", "accrual balance", ZERO, _balance(db, tape, "2350"))
    _after_every_row(db, tape, "U3")


# --- Outside the tape ---------------------------------------------------------------------
#
# The plan lists four checks to make beside the tape. Three of them were built with the code
# they guard and are asserted where that code lives, so they are named here rather than
# duplicated:
#
#   * a supplier invoice with a stock line and no GRN link receives at its net unit cost and
#     leaves the accrual at zero in the same transaction —
#     `test_posting_map.py`, `test_a_supplier_invoice_with_no_receipt_behind_it_nets_the
#     _accrual_to_zero`
#   * a service PO line is received by its invoice —
#     `test_orders.py::test_a_service_line_is_received_by_its_invoice`
#   * a GRN with a matched line refuses reversal — `test_match.py`, `grn_matched`
#
# The fourth is tied to a specific tape row, so it belongs beside the tape.


def test_the_backorder_policy_refuses_row_5(db: Session, tape: Tape) -> None:
    """Row 5 under `backorder_policy = block`: the order that the tape takes is refused.

    Row 5 promises 70 against 30 on the shelf. Under the default `allow` that is an ordinary
    backorder and the tape proceeds; under `block` it is `exceeds_available`, refused on the
    line. Both halves are asserted — the refusal, and that the same order succeeds once the
    policy is the tape's — because a test that only proved the refusal would pass just as well
    against a service that refused every order.
    """
    oe = tape.oe
    company_id = oe.company_id
    X = oe.stock_item
    grn_service.post_grn(
        db,
        company_id,
        grn_service.GrnInput(
            partner_id=oe.supplier.id,
            grn_date=TAPE_DAY,
            description="30 on the shelf",
            warehouse_id=oe.main.id,
            lines=(
                grn_service.GrnLineInput(item_id=X.id, quantity=D(30), unit_cost=D(1000)),
            ),
        ),
        actor=oe.owner,
    )
    oe.settings.backorder_policy = BackorderPolicy.BLOCK
    db.flush()

    def take_the_order():  # noqa: ANN202
        return orders_service.create_sales_order(
            db,
            company_id,
            orders_service.SalesOrderInput(
                partner_id=oe.customer.id,
                order_date=TAPE_DAY,
                description="SO-2 under block",
                warehouse_id=oe.main.id,
                tax_mode=TaxMode.EXCLUSIVE,
                lines=(orders_service.OrderLineInput(item_id=X.id, quantity=D(70)),),
            ),
            actor=oe.owner,
        )

    # **A savepoint, not a rollback.** The tenant — company, partners, items — was created in
    # this same transaction, so a plain rollback would discard the fixture along with the
    # refused order. The property machine wrote that reasoning down; this is the same trap.
    step = db.begin_nested()
    with pytest.raises(LedgerStateError) as refused:
        take_the_order()
    assert refused.value.code == "exceeds_available"
    step.rollback()

    # The same order, under the policy the tape runs: taken, and backordered by 40.
    oe.settings.backorder_policy = BackorderPolicy.ALLOW
    db.flush()
    order, _ = take_the_order()
    enquiry = oe_enquiries.sales_order_enquiry(db, company_id, order.id)
    assert enquiry.lines[0].backordered == D(40)
