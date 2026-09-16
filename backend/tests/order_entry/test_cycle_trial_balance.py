"""The two cycles, and the trial balance they leave behind (P6 step 9).

**This file exists to be reproduced through the screens.** `frontend/e2e/p6-cycle-tape.spec.ts`
drives the same sequence from the UI on a tenant of its own and asserts the same seven figures
as rendered strings on the Trial balance report. The phase's claim is that the screens post
what the services post, and two tests that agree on a hand-worked table are how it is shown —
not by either one recomputing the other's answer.

**Every figure is worked by hand**, as the step-5 tape's are. The working is beside each row.
A test that derived the expectation the way the service does would pass just as happily over a
service that had been wrong from the start.

    procure to pay
      PO   40 x WINE @ 1 000, exclusive, no tax code
      GRN  receive 25 of them          Dr 1300  25 000   Cr 2350  25 000
      LCA  6 000 freight, by value     Dr 1300   6 000   Cr 1370   6 000
      INV  supplier invoice, matched   Dr 2350  25 000   Cr 2100  25 000
      INV  the freight bill itself     Dr 1370   6 000   Cr 2100   6 000

    order to cash
      SO   10 x WINE @ 2 000
      INV  invoice all ten             Dr 1200  20 000   Cr 4100  20 000
           and the companion           Dr 5100  12 400   Cr 1300  12 400

The average the companion relieves at is **1 240**: 25 units arrived at 25 000 and the freight
added 6 000 to the same 25 units, so (25 000 + 6 000) / 25. Ten of them leave at 12 400. That
figure is the reason this sequence is worth driving through the screens at all — it is the one
number no screen shows and every screen depends on.

No tax code on any line, deliberately. VAT is the step-5 tape's business and it has it; here it
would add two accounts to the comparison without adding a claim, and the point of this file is
the *cycle*, from a promise to a relief of stock at the right cost.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.gl import GLAccount
from app.models.journal import JournalEntry, JournalLine, JournalStatus
from app.models.order_entry import LandedCostBasis
from app.models.partner import PartnerRole, TaxMode
from app.models.subledger import DocumentKind
from app.order_entry import flows as order_flows
from app.order_entry import grn as grn_service
from app.order_entry import landed_cost as landed_cost_service
from app.order_entry import orders as orders_service
from app.subledger import documents as documents_service
from tests.order_entry.conftest import MARCH, OrderEntry

D = Decimal
ZERO = Decimal(0)

#: account code -> (debit, credit) in the base currency, as the trial balance foots them.
#: Quoted verbatim in the step-9 report and asserted, figure for figure, by the e2e.
EXPECTED_TRIAL_BALANCE: dict[str, tuple[Decimal, Decimal]] = {
    # 25 000 received + 6 000 of freight in, 12 400 relieved out.
    "1300": (D(31_000), D(12_400)),
    # Received then billed: the accrual is a round trip and nets to nothing.
    "2350": (D(25_000), D(25_000)),
    # Booked by the freight bill, spread by the allocation: also a round trip.
    "1370": (D(6_000), D(6_000)),
    # The two supplier invoices.
    "2100": (ZERO, D(31_000)),
    "1200": (D(20_000), ZERO),
    "4100": (ZERO, D(20_000)),
    # Ten units at the post-freight average of 1 240.
    "5100": (D(12_400), ZERO),
}


def _trial_balance(db: Session, company_id: int) -> dict[str, tuple[Decimal, Decimal]]:
    """Debit and credit per account, from the posted journal lines.

    Summed here rather than read from `period_balances` on purpose: the cache is a *claim*
    about these lines, and the phase's other tests prove the claim. What this file is about is
    the lines.
    """
    rows = db.execute(
        select(GLAccount.code, JournalLine.base_amount)
        .join(JournalLine, JournalLine.gl_account_id == GLAccount.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalEntry.company_id == company_id,
            JournalEntry.status == JournalStatus.POSTED,
        )
    ).all()
    out: dict[str, list[Decimal]] = {}
    for code, amount in rows:
        cell = out.setdefault(code, [ZERO, ZERO])
        if amount >= ZERO:
            cell[0] += amount
        else:
            cell[1] += -amount
    return {code: (debit, credit) for code, (debit, credit) in out.items()}


def drive_the_two_cycles(db: Session, fixture: OrderEntry) -> None:
    """Procure to pay, then order to cash, through the **services**.

    Exported rather than inlined so the sequence has one definition: the e2e drives the same
    one through the screens, and a divergence between them would make the comparison say
    nothing.
    """
    # --- procure to pay ---------------------------------------------------------------------
    purchase, _ = orders_service.create_purchase_order(
        db,
        fixture.company_id,
        orders_service.PurchaseOrderInput(
            partner_id=fixture.supplier.id,
            order_date=MARCH,
            description="Forty bottles",
            warehouse_id=fixture.main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                orders_service.OrderLineInput(
                    item_id=fixture.stock_item.id, quantity=D(40), unit_price=D(1_000)
                ),
            ),
        ),
        actor=fixture.owner,
    )
    db.flush()

    prepared = order_flows.prepare_receipt_from_purchase_order(db, fixture.company_id, purchase)
    grn, _ = grn_service.post_grn(
        db,
        fixture.company_id,
        grn_service.GrnInput(
            partner_id=fixture.supplier.id,
            grn_date=MARCH,
            description="Twenty-five of the forty",
            warehouse_id=fixture.main.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=fixture.stock_item.id,
                    quantity=D(25),
                    unit_cost=D(1_000),
                    purchase_order_line_id=prepared.grn.lines[0].purchase_order_line_id,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    db.flush()

    landed_cost_service.post_landed_cost(
        db,
        fixture.company_id,
        landed_cost_service.LandedCostInput(
            cost_date=MARCH,
            description="Freight on the consignment",
            amount=D(6_000),
            basis=LandedCostBasis.VALUE,
            grn_line_ids=(grn.lines[0].id,),
        ),
        actor=fixture.owner,
    )
    db.flush()

    # The supplier's own invoice, matched to the receipt: the accrual comes back off.
    documents_service.post_document(
        db,
        fixture.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=fixture.supplier.id,
            document_date=MARCH,
            description="Supplier invoice for the receipt",
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                documents_service.LineInput(
                    item_id=fixture.stock_item.id,
                    quantity=D(25),
                    unit_price=D(1_000),
                    warehouse_id=fixture.main.id,
                    grn_line_id=grn.lines[0].id,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    db.flush()

    # And the forwarder's bill, which is what put the 6 000 on the clearing account in the
    # first place. Booked to 1370 as a plain GL line — the clearing account is deliberately not
    # a control account so that an ordinary supplier invoice can reach it.
    documents_service.post_document(
        db,
        fixture.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=fixture.supplier.id,
            document_date=MARCH,
            description="Freight bill",
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                documents_service.LineInput(
                    gl_account_id=fixture.accounts["1370"].id,
                    description="Freight",
                    quantity=D(1),
                    unit_price=D(6_000),
                ),
            ),
        ),
        actor=fixture.owner,
    )
    db.flush()

    # --- order to cash ----------------------------------------------------------------------
    sale, _ = orders_service.create_sales_order(
        db,
        fixture.company_id,
        orders_service.SalesOrderInput(
            partner_id=fixture.customer.id,
            order_date=MARCH,
            description="Ten bottles",
            warehouse_id=fixture.main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                orders_service.OrderLineInput(
                    item_id=fixture.stock_item.id, quantity=D(10), unit_price=D(2_000)
                ),
            ),
        ),
        actor=fixture.owner,
    )
    db.flush()

    documents_service.post_document(
        db,
        fixture.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=fixture.customer.id,
            document_date=MARCH,
            description="Customer invoice",
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                documents_service.LineInput(
                    item_id=fixture.stock_item.id,
                    quantity=D(10),
                    unit_price=D(2_000),
                    warehouse_id=fixture.main.id,
                    sales_order_line_id=sale.lines[0].id,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    db.flush()


def test_the_two_cycles_leave_this_trial_balance(
    db: Session, order_entry: OrderEntry
) -> None:
    """The seven accounts, worked by hand, against the services.

    The e2e asserts the same seven as rendered strings after driving the same sequence through
    the screens. Neither reads the other's answer: both read this table.
    """
    drive_the_two_cycles(db, order_entry)

    actual = _trial_balance(db, order_entry.company_id)
    assert actual == EXPECTED_TRIAL_BALANCE, actual

    # And it foots, which is the one assertion that would survive every figure being wrong
    # together — so it is stated *after* them rather than instead of them.
    debits = sum(debit for debit, _ in actual.values())
    credits = sum(credit for _, credit in actual.values())
    assert debits == credits == D(94_400)
