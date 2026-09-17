"""The VAT return over a month of postings, with every figure worked by hand.

The literals below are derived in the comments rather than computed by a helper, because a test
that recomputes the thing under test proves only that one expression appears twice. What is
being asserted is arithmetic somebody checked: the rates are RWA's 18 % standard, exempt and
zero, and the amounts are the ones the fixtures post.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel import posting
from app.kernel.events import CashbookEntry, CashbookKind, CashbookLineSpec
from app.models.gl import GLAccount
from app.subledger import documents as documents_service
from app.tax import vat
from tests.fiscal import helpers
from tests.fiscal.conftest import FiscalPosting
from tests.kernel.conftest import YEAR

MARCH_FROM = date(YEAR, 3, 1)
MARCH_TO = date(YEAR, 3, 31)
APRIL_FROM = date(YEAR, 4, 1)
APRIL_TO = date(YEAR, 4, 30)


def _sale_of(fixture: FiscalPosting, code: str, quantity: Decimal, price: Decimal):
    return documents_service.LineInput(
        item_id=fixture.stock_item.id,
        quantity=quantity,
        unit_price=price,
        tax_code_id=fixture.tax_codes[code].id,
    )


@pytest.fixture
def month(db: Session, fiscal_posting: FiscalPosting) -> FiscalPosting:
    """A month of postings whose every VAT figure is checkable by hand.

    Sales, exclusive pricing:
      * standard : 10 × 2 000 = 20 000 base, 18 % = 3 600 VAT
      * exempt   :  5 × 1 000 =  5 000 base, no VAT
      * zero     :  2 × 5 000 = 10 000 base, no VAT
    Purchases, exclusive:
      * standard : 50 × 1 000 = 50 000 base, 18 % = 9 000 VAT

    So output VAT 3 600, input VAT 9 000, and the net is a **credit** of 5 400 — the direction
    that would be quietly wrong if the sign followed the sales side.
    """
    helpers.receive(fiscal_posting, db)
    helpers.invoice(
        fiscal_posting,
        db,
        lines=(
            _sale_of(fiscal_posting, "VAT-OUT-18", Decimal(10), Decimal(2000)),
            _sale_of(fiscal_posting, "VAT-EXEMPT", Decimal(5), Decimal(1000)),
            _sale_of(fiscal_posting, "VAT-ZERO", Decimal(2), Decimal(5000)),
        ),
        document_date=helpers.MARCH,
    )
    helpers.supplier_invoice(fiscal_posting, db, document_date=helpers.MARCH)
    db.flush()
    return fiscal_posting


def test_the_month_ties_to_the_vat_accounts_to_the_franc(
    db: Session, month: FiscalPosting
) -> None:
    view = vat.compute(db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO)

    assert view.sales_standard == (Decimal("20000.000000"), Decimal("3600.000000"))
    assert view.sales_exempt == Decimal("5000.000000")
    assert view.sales_zero_rated == Decimal("10000.000000")
    assert view.purchases_standard == (Decimal("50000.000000"), Decimal("9000.000000"))
    assert view.purchases_imports == (Decimal(0), Decimal(0))
    assert view.output_vat == Decimal("3600.000000")
    assert view.input_vat == Decimal("9000.000000")
    # Output − input: a credit, carried forward rather than payable.
    assert view.net_payable == Decimal("-5400.000000")

    # The tie, per VAT account. Output VAT was credited, so the ledger movement is negative;
    # input VAT was debited, so it is positive. Both are fully declared, so nothing is untagged.
    ties = {tie.code: tie for tie in view.ties}
    assert ties["2200"].movement == Decimal("-3600.000000")
    assert ties["2200"].declared_in_range == Decimal("-3600.000000")
    assert ties["2200"].difference == Decimal(0)
    assert ties["2200"].untagged == ()
    assert ties["2200"].reconciled
    assert ties["1400"].movement == Decimal("9000.000000")
    assert ties["1400"].declared_in_range == Decimal("9000.000000")
    assert ties["1400"].difference == Decimal(0)
    assert ties["1400"].reconciled


def test_an_untagged_movement_is_listed_rather_than_absorbed(
    db: Session, month: FiscalPosting
) -> None:
    """A payment to the authority moves the VAT account and is no part of the return.

    The report's job is to say so line by line. A report that forced the two to agree would be
    hiding the one entry an accountant is looking for.
    """
    _pay_the_authority(db, month, Decimal(1000))

    view = vat.compute(db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO)
    tie = next(tie for tie in view.ties if tie.code == "2200")

    # The account moved by the payment as well; the declared figure did not.
    assert tie.movement == Decimal("-2600.000000")
    assert tie.declared_in_range == Decimal("-3600.000000")
    assert tie.difference == Decimal("1000.000000")
    assert [line.base_amount for line in tie.untagged] == [Decimal("1000.000000")]
    assert tie.reconciled
    # And the return itself is unchanged by it: a payment is not a sale.
    assert view.output_vat == Decimal("3600.000000")


def _pay_the_authority(db: Session, fixture: FiscalPosting, amount: Decimal) -> None:
    """Dr VAT output / Cr bank, tagged with nothing — the ordinary way tax is paid.

    Through the cashbook rather than a manual journal, because `1120` is a bank control account
    and the kernel refuses a manual journal onto one. That is also the realistic path: a payment
    to the authority is a cashbook payment, and it is exactly the movement the tie has to
    surface without the return declaring it.
    """
    accounts = {
        row.code: row.id
        for row in db.execute(
            select(GLAccount.code, GLAccount.id).where(
                GLAccount.company_id == fixture.company_id, GLAccount.code.in_(("2200", "1120"))
            )
        )
    }
    posting.post(
        db,
        CashbookEntry(
            entry_date=helpers.MARCH,
            description="VAT paid to RRA",
            cash_account_id=accounts["1120"],
            kind=CashbookKind.PAYMENT,
            lines=(
                CashbookLineSpec(amount=amount, gl_account_id=accounts["2200"]),
            ),
        ),
        company_id=fixture.company_id,
        actor=fixture.owner,
    )
    db.flush()
