"""The annexes, and the one property that matters: they add up to the return.

An annex is a listing, so the temptation is to test that it lists things. What is actually worth
asserting is that its totals are the return's — because the two are separate queries over the
same ledger, and a divergence between them is the defect a desk audit finds rather than a test.
"""

import csv
import io
from decimal import Decimal

from sqlalchemy.orm import Session

from app.tax import annexes, vat
from tests.fiscal.conftest import FiscalPosting

from .test_vat_return import MARCH_FROM, MARCH_TO, month  # noqa: F401


def _read(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_the_sales_annex_totals_are_the_returns_sales(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    view = vat.compute(db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO)
    rows = annexes.sales_rows(db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO)

    # The fixture's month: 20 000 standard + 5 000 exempt + 10 000 zero = 35 000 base, and the
    # only VAT on the sales side is the 3 600 on the standard lines.
    assert sum(row.base for row in rows) == Decimal("35000.000000")
    assert sum(row.tax for row in rows) == view.output_vat == Decimal("3600.000000")


def test_the_purchase_annex_totals_are_the_returns_purchases(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    view = vat.compute(db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO)
    rows = annexes.purchase_rows(db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO)

    assert sum(row.base for row in rows) == Decimal("50000.000000")
    assert sum(row.tax for row in rows) == view.input_vat == Decimal("9000.000000")


def test_the_sales_annex_carries_the_customers_tin_and_the_receipt(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    """What the authority reconciles against: who was billed, and which receipt it signed."""
    rows = _read(
        annexes.sales_csv(db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO)
    )
    assert rows, "the month posted an invoice; the annex must list it"

    invoice = rows[0]
    assert invoice["customer_tin"] == month.customer.tin
    assert invoice["document_number"].startswith("INV-")
    assert invoice["base"] == "35000.000000"
    assert invoice["vat"] == "3600.000000"
    # No drain ran in this fixture, so there is no receipt yet — and the annex says so with an
    # empty cell rather than by omitting the sale, which would understate the month.
    assert invoice["invc_no"] == ""
    assert invoice["sdc_id"] == ""


def test_the_purchase_annex_carries_the_suppliers_own_invoice_number(
    db: Session, month: FiscalPosting  # noqa: F811
) -> None:
    rows = _read(
        annexes.purchases_csv(db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO)
    )
    assert rows

    purchase = rows[0]
    # `reference` is the supplier's own number — the handle RRA reconciles the two sides by.
    assert purchase["supplier_reference"] == "77"
    assert purchase["base"] == "50000.000000"
    assert purchase["vat"] == "9000.000000"
    # Nothing has been linked to a feed row, so the authority's own number is blank.
    assert purchase["feed_supplier_invoice_no"] == ""


def test_the_annex_header_is_stable(db: Session, month: FiscalPosting) -> None:  # noqa: F811
    """The columns are an interface: something downstream reads them by name."""
    sales = annexes.sales_csv(db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO)
    purchases = annexes.purchases_csv(
        db, month.company_id, period_from=MARCH_FROM, period_to=MARCH_TO
    )

    assert sales.splitlines()[0] == ",".join(annexes.SALES_COLUMNS)
    assert purchases.splitlines()[0] == ",".join(annexes.PURCHASE_COLUMNS)
    # RFC 4180 line endings, which is what a spreadsheet and a customs desk both expect.
    assert sales.endswith("\r\n")
