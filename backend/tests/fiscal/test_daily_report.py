"""X and Z (decision 11): the day a device had, counted from what the authority signed.

The arithmetic is the fixture's invoice, worked by hand. `helpers.invoice` sells 10 × 2 000
exclusive at 18 %, so:

  * net 20 000, tax 3 600, **gross 23 600** — all of it class B at 18 %.

A credit note for 2 of those units is 2 × 2 000 = 4 000 net, 720 tax, **gross 4 720**, also
class B. Those two receipts are the whole day in the tests below, which is what makes every
figure checkable without a helper recomputing it.
"""

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fiscal import daily, drainer, registry
from app.kernel.errors import LedgerStateError
from app.models.fiscalization import FiscalDailyReport, FiscalReceipt, FiscalReceiptType
from app.models.subledger import PartnerDocument
from app.subledger import documents as documents_service
from tests.fiscal import helpers
from tests.fiscal.conftest import FiscalPosting

GROSS = Decimal("23600.00")
TAX = Decimal("3600.00")
TAXABLE = Decimal("23600.00")


def _later(seconds: int = 2) -> datetime:
    """A moment past the activation second.

    Every close in this file names its own instant. A receipt's stamp has a second's
    resolution, so a day's bounds do too (`_floor_second`), and a test that activated a device
    and closed its day inside one millisecond would be asking for a range of zero length —
    which `close_day` refuses, rightly. Real days are hours; these are seconds.
    """
    return datetime.now(UTC) + timedelta(seconds=seconds)


def _drain_everything(db: Session, fixture: FiscalPosting, client: httpx.Client) -> None:
    drainer.drain_company(db, fixture.company_id, client=client, max_rows_per_device=50)
    db.flush()


@pytest.fixture
def a_day(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> FiscalPosting:
    """One sale, fiscalized end to end, so the device holds exactly one receipt."""
    helpers.receive(fiscal_posting, db)
    fiscal_posting.invoice = helpers.invoice(fiscal_posting, db)  # type: ignore[attr-defined]
    db.flush()
    _drain_everything(db, fiscal_posting, sandbox_client)
    return fiscal_posting


def test_an_x_counts_what_the_authority_signed(db: Session, a_day: FiscalPosting) -> None:
    view = daily.x_report(db, a_day.company_id, a_day.device.id)

    assert view.kind == "X"
    assert view.figures.ns_count == 1
    assert view.figures.ns_gross == GROSS
    assert view.figures.nr_count == 0
    assert view.figures.net_gross == GROSS
    # One class, B, at the standard rate — the exempt and zero buckets stay absent rather than
    # printing as zeros (§7.22–7.23: a rate that was not used is not on the report).
    assert set(view.figures.classes) == {"B"}
    assert view.figures.classes["B"].taxable_ns == TAXABLE
    assert view.figures.classes["B"].tax_ns == TAX
    assert view.figures.total_tax == TAX


def test_an_x_changes_nothing(db: Session, a_day: FiscalPosting) -> None:
    """An X is a question. Asking it twice must leave no trace and give one answer."""
    first = daily.x_report(db, a_day.company_id, a_day.device.id)
    second = daily.x_report(db, a_day.company_id, a_day.device.id)

    assert first.figures.as_dict() == second.figures.as_dict()
    assert db.scalars(
        select(FiscalDailyReport).where(FiscalDailyReport.company_id == a_day.company_id)
    ).all() == []


def test_a_z_stores_the_day_and_takes_a_number(db: Session, a_day: FiscalPosting) -> None:
    report = daily.close_day(
        db, a_day.company_id, a_day.device.id, actor=a_day.owner, now=_later()
    )
    db.flush()

    assert report.number.startswith("Z-")
    assert report.report_no == 1
    assert report.figures["ns_count"] == 1
    assert report.figures["ns_gross"] == str(GROSS)
    assert report.figures["classes"]["B"]["tax_ns"] == str(TAX)
    assert report.queued_rows == 0


def test_the_x_after_a_close_is_empty(db: Session, a_day: FiscalPosting) -> None:
    """The tape's row 9: after the close, the device's day starts again at zero."""
    daily.close_day(
        db, a_day.company_id, a_day.device.id, actor=a_day.owner, now=_later()
    )
    db.flush()

    after = daily.x_report(db, a_day.company_id, a_day.device.id)
    assert after.figures.ns_count == 0
    assert after.figures.ns_gross == Decimal(0)
    assert after.figures.classes == {}


def test_a_second_z_covers_only_what_came_after_the_first(
    db: Session, a_day: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The closes tile the device's life: no receipt is counted twice, and none is missed.

    The clock is injected rather than read, because a receipt's stamp has a second's resolution
    and a test that closed twice in the same millisecond would be asking which of two days an
    instant belongs to when both bounds are the same instant. Real days are hours apart; this
    one is two seconds, which is enough to be a boundary.
    """
    # The timeline, in whole seconds, because that is the resolution `sdcDateTime` has:
    #
    #   S     the device is activated and the sale is rung up  (both in `a_day`)
    #   S+1   the first Z is taken — after the sale, so it contains it
    #   S+2   the refund is rung up — after the first Z, so it belongs to the second
    #   S+2   the second Z is taken
    #
    # Two receipts issued in the *same* second cannot be put on opposite sides of a boundary by
    # any rule, so the test waits rather than pretending otherwise. The sandbox stamps from the
    # wall clock, as a device does, which is why the wait is real.
    first = daily.close_day(
        db, a_day.company_id, a_day.device.id, actor=a_day.owner, now=_later(1)
    )
    db.flush()

    time.sleep(2.05)

    assert (
        len(
            db.scalars(
                select(FiscalReceipt).where(FiscalReceipt.company_id == a_day.company_id)
            ).all()
        )
        == 1
    )

    invoice: PartnerDocument = a_day.invoice  # type: ignore[attr-defined]
    helpers.credit_note(
        a_day,
        db,
        lines=(
            documents_service.LineInput(
                item_id=a_day.stock_item.id,
                quantity=Decimal(2),
                unit_price=Decimal(2000),
                tax_code_id=a_day.tax_codes["VAT-OUT-18"].id,
                returns_line_id=helpers.line_of(invoice).id,
            ),
        ),
    )
    db.flush()
    _drain_everything(db, a_day, sandbox_client)

    second = daily.close_day(
        db, a_day.company_id, a_day.device.id, actor=a_day.owner, now=datetime.now(UTC)
    )
    db.flush()

    assert second.report_no == 2
    assert second.from_at == first.to_at
    # Only the refund is in the second close.
    assert second.figures["ns_count"] == 0
    assert second.figures["nr_count"] == 1
    assert second.figures["nr_gross"] == "4720.00"
    assert second.figures["classes"]["B"]["tax_nr"] == "720.00"


def test_a_queued_row_is_not_a_receipt_and_the_z_says_how_many(
    db: Session, a_day: FiscalPosting
) -> None:
    """Decision 11's last clause. A sale still in the queue has no receipt, so it is no part of
    the day's totals — and a Z that closed over one records it rather than hiding it."""
    helpers.invoice(a_day, db)  # never drained
    db.flush()

    report = daily.close_day(
        db, a_day.company_id, a_day.device.id, actor=a_day.owner, now=_later()
    )
    db.flush()

    # Still one receipt: the undrained sale contributed nothing to the takings.
    assert report.figures["ns_count"] == 1
    assert report.figures["ns_gross"] == str(GROSS)
    # But the day is on record as having closed over unsent rows.
    assert report.queued_rows > 0
    assert report.figures["queued_rows"] == report.queued_rows


def test_a_device_that_never_activated_has_no_day(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    fiscal_posting.device.activated_at = None
    db.flush()

    with pytest.raises(LedgerStateError) as raised:
        daily.x_report(db, fiscal_posting.company_id, fiscal_posting.device.id)
    assert raised.value.code == "fiscal_device_not_active"


def test_the_range_is_half_open_so_a_receipt_falls_in_exactly_one_z(
    db: Session, a_day: FiscalPosting
) -> None:
    """The boundary that decides whether a receipt is double-counted or lost."""
    receipt = db.scalars(
        select(FiscalReceipt).where(FiscalReceipt.company_id == a_day.company_id)
    ).one()
    moment = receipt.sdc_datetime

    # A range ending exactly at the receipt's instant contains it...
    inside = daily.compute(
        db,
        a_day.company_id,
        a_day.device.id,
        from_at=moment - timedelta(seconds=1),
        to_at=moment,
    )
    assert inside.ns_count == 1

    # ...and the next range, which starts there, does not.
    after = daily.compute(
        db,
        a_day.company_id,
        a_day.device.id,
        from_at=moment,
        to_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    assert after.ns_count == 0


def test_a_z_totals_equal_the_sum_of_its_own_receipts(
    db: Session, a_day: FiscalPosting
) -> None:
    """The build order's own wording: a Z whose totals equal Σ of its receipts.

    Asserted against the stored payloads rather than against the literals above, so it holds
    whatever the fixture sells — this is the property, and the literals are the worked example.
    """
    report = daily.close_day(
        db, a_day.company_id, a_day.device.id, actor=a_day.owner, now=_later()
    )
    db.flush()

    covered = [
        receipt
        for receipt in db.scalars(
            select(FiscalReceipt).where(FiscalReceipt.company_id == a_day.company_id)
        )
        if report.from_at <= receipt.sdc_datetime <= report.to_at
    ]
    assert covered, "the fixture issued a receipt; the close must cover it"

    sales = [r for r in covered if r.receipt_type is FiscalReceiptType.NORMAL_SALE]
    refunds = [r for r in covered if r.receipt_type is FiscalReceiptType.NORMAL_REFUND]

    assert report.figures["ns_count"] == len(sales)
    assert report.figures["nr_count"] == len(refunds)
    assert Decimal(report.figures["ns_gross"]) == sum(
        (Decimal(str(r.request["totAmt"])) for r in sales), Decimal(0)
    )
    assert Decimal(report.figures["total_tax"]) == sum(
        (Decimal(str(r.request["totTaxAmt"])) for r in covered), Decimal(0)
    )
    # **Quantities, not lines, and split NS from NR** — §19.1's items figure is "how many
    # things were sold today". `totItemCnt` is the receipt's line count and stays on the
    # receipt (CIS §7.27); a Z that reported it would say 4 for an invoice of eighteen bottles.
    assert Decimal(report.figures["items_ns"]) == sum(
        (
            Decimal(str(line["qty"]))
            for r in sales
            for line in r.request.get("itemList", [])
        ),
        Decimal(0),
    )
    assert Decimal(report.figures["items_nr"]) == sum(
        (
            Decimal(str(line["qty"]))
            for r in refunds
            for line in r.request.get("itemList", [])
        ),
        Decimal(0),
    )


def test_the_day_reports_discounts_and_follows_the_receipt_not_the_ledger(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """Two requirements at once, because one case proves both.

    §19.1 prints the day's **discounts**, so a Z that could not state them would fail the
    checkpoint sheet — decision 11 lists them beside the copies and the item count.

    And a discounted line is exactly where the ledger and the receipt part company. The wire's
    taxable amount is `splyAmt − dcAmt`, extended from the two-decimal **inclusive** price,
    while the ledger holds the posted gross of an exclusive line; on round, undiscounted prices
    the two coincide, which is why an undiscounted fixture proves nothing about which one a Z
    follows. A Z states what the authority signed, so the figures below are asserted against
    the stored payload — the thing RRA actually received — and not against `journal_lines`.
    """
    helpers.receive(fiscal_posting, db)
    helpers.invoice(
        fiscal_posting,
        db,
        lines=(
            documents_service.LineInput(
                item_id=fiscal_posting.stock_item.id,
                quantity=Decimal(7),
                unit_price=Decimal("333.33"),
                discount_percent=Decimal("12.5"),
                tax_code_id=fiscal_posting.tax_codes["VAT-OUT-18"].id,
            ),
        ),
    )
    db.flush()
    _drain_everything(db, fiscal_posting, sandbox_client)

    view = daily.x_report(db, fiscal_posting.company_id, fiscal_posting.device.id)
    receipt = db.scalars(
        select(FiscalReceipt).where(FiscalReceipt.company_id == fiscal_posting.company_id)
    ).one()
    declared = receipt.request

    # The discount is on the day, and it is the one the receipt carries.
    expected_discount = sum(
        (Decimal(str(line["dcAmt"])) for line in declared["itemList"]), Decimal(0)
    )
    assert expected_discount > 0, "a 12.5% line must declare a discount"
    assert view.figures.discounts == expected_discount

    # And every money figure is the receipt's, to the franc.
    assert view.figures.ns_gross == Decimal(str(declared["totAmt"]))
    assert view.figures.classes["B"].taxable_ns == Decimal(str(declared["taxblAmtB"]))
    assert view.figures.classes["B"].tax_ns == Decimal(str(declared["taxAmtB"]))
    assert view.figures.total_tax == Decimal(str(declared["totTaxAmt"]))


def test_a_payload_that_declared_no_receipt_is_no_part_of_a_till(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """A stock or item row is in the outbox and in no day. The adapter says so, rather than a
    neutral module guessing from a field it should not be reading."""
    adapter = registry.adapter_for("RW")

    assert adapter.normalize_declared_totals(None) is None
    assert adapter.normalize_declared_totals({"sarNo": 1, "itemList": []}) is None
    assert adapter.normalize_declared_totals({"rcptTyCd": "S", "totAmt": "10.00"}) is not None


# --- The three fiscal-day endpoints answer ---------------------------------------------------


@pytest.fixture
def signed_in_owner(client, db: Session, a_day: FiscalPosting):  # noqa: ANN001, ANN201
    """The fixture's own owner, through the real login, so the permissions are the real ones."""
    db.commit()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": a_day.owner.email, "password": "correct horse battery staple"},
    )
    assert response.status_code == 200, response.text
    return a_day


def test_the_x_report_endpoint_renders_the_day(client, signed_in_owner: FiscalPosting) -> None:  # noqa: ANN001
    """Rule 13 in the small: assert a **figure**, not that the route compiles."""
    response = client.get(f"/api/v1/fiscal/devices/{signed_in_owner.device.id}/x-report")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "X"
    assert body["figures"]["ns_count"] == 1
    assert Decimal(body["figures"]["ns_gross"]) == GROSS
    # The figures decision 11 lists and the first pass omitted.
    assert "discounts" in body["figures"]
    assert "declared_less_posted" in body["figures"]


def test_closing_the_day_over_the_api_stores_a_z_and_lists_it(
    client, signed_in_owner: FiscalPosting  # noqa: ANN001
) -> None:
    """Over HTTP there is no injected clock, and there should not be — a caller does not get to
    say when the day ended. So the test waits out the activation second instead, the same
    resolution limit `_floor_second` documents: a device activated and closed inside one second
    is an empty range, and `fiscal_z_empty_range` is the right answer to it (409)."""
    time.sleep(1.05)

    closed = client.post(
        f"/api/v1/fiscal/devices/{signed_in_owner.device.id}/close-day",
        headers={"Idempotency-Key": "close-day-api"},
    )

    assert closed.status_code == 201, closed.text
    body = closed.json()
    assert body["kind"] == "Z"
    assert body["number"].startswith("Z-")
    assert body["figures"]["ns_count"] == 1

    listing = client.get(f"/api/v1/fiscal/devices/{signed_in_owner.device.id}/z-reports")
    assert listing.status_code == 200, listing.text
    assert [row["number"] for row in listing.json()] == [body["number"]]
