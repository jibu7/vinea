"""The fiscal receipts listing, which is a **tie** and not a listing.

A listing of receipts already exists — `GET /fiscal/receipts`, the enquiry somebody holding a
piece of paper searches. This is the report an accountant closes a month with, and its job is
to put two independently-derived figures beside each other and then name every document that is
on one side and not the other. A screen that only printed receipts would answer a question
nobody was asking: the receipts are not in doubt, the *agreement* is.

**The two sides come from different places on purpose.** The declared side reads
`fiscal_receipts.request` through the adapter — the same path `daily.compute` takes, so a day's
listing and that day's Z are the same arithmetic over the same rows and must agree to the cent.
The ledger side reads `partner_documents` and knows nothing about RRA. Agreement is then
evidence; a tie whose two sides came out of one query would agree whatever had gone wrong.

**Awkward prices throughout**, which is step 2's census bias caught the hard way: round,
undiscounted prices are exactly the ones on which the wire and the ledger cannot disagree, so a
tie proved on 2 000 × 10 proves nothing about the residue it exists to surface. Every figure
below is worked by hand from a price that does not divide.
"""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import set_actor, set_tenant
from app.fiscal import drainer
from app.fiscal import enquiries as enquiry_service
from app.models.fiscalization import FiscalReceiptType
from app.models.subledger import DocumentKind
from app.subledger import documents as documents_service
from tests.fiscal.conftest import FiscalPosting
from tests.fiscal.helpers import MARCH, credit_note, invoice, line_of, receive

PASSWORD = "correct horse battery staple"
D = Decimal

#: 1 499 excl. at 18 %. The two sides round it under **different rules**, which is the whole
#: reason this price and not 2 000:
#:
#: * the ledger rounds per line to the currency's places, and RWF has **none** (rule 6) —
#:   4 497 net, 809.46 → **809** VAT, **5 306** gross for three of them;
#: * the wire carries a VAT-inclusive unit price at **two** decimals (decision 6) — 1 768.82,
#:   extended 1 768.82 × 3 = **5 306.46**.
#:
#: Forty-six centimes apart on one invoice, and neither figure is wrong. On 2 000 × 10 they
#: agree exactly and this report has nothing to show — which is step 2's census bias in one
#: line: round prices are precisely the ones that hide the residue.
AWKWARD = D("1499")

#: The documents are dated **today** because that is when the device signs them, and a
#: fixture that dated its sales in March would put every receipt on one side of the tie and
#: every document on the other — a true answer to a question nobody asked. `SIGNED_ELSEWHEN`
#: is the straddle, kept deliberately: one sale dated in March and signed today.
TODAY = date.today()
SIGNED_ELSEWHEN = MARCH


def _sale(fixture: FiscalPosting, db: Session, *, quantity: str, price: Decimal, **kwargs):  # noqa: ANN202
    return invoice(
        fixture,
        db,
        lines=(
            documents_service.LineInput(
                item_id=fixture.stock_item.id,
                quantity=D(quantity),
                unit_price=price,
                tax_code_id=fixture.tax_codes["VAT-OUT-18"].id,
            ),
        ),
        **kwargs,
    )


@pytest.fixture
def tied(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> FiscalPosting:
    """A day with all three shapes of row the tie exists to tell apart.

    On both sides, dated today and signed today:

    * **INV-A** 3 × 1 499 — ledger 5 306, wire 5 306.46
    * **INV-B** 7 × 1 499 — ledger 12 382, wire 12 381.74
    * **CRN-A** 1 × 1 499 against INV-A — ledger 1 769, wire 1 768.82, `NR`

    On the receipts side alone:

    * **INV-OLD** 2 × 1 499 dated in March and signed today — ledger 3 538, wire 3 537.64.
      The month-end straddle, and the shape a refund of an older invoice also takes.

    On the ledger side alone:

    * **INV-QUEUED** 5 × 1 499 — ledger 8 844, posted after the drain and left in the queue.
    """
    receive(fiscal_posting, db, quantity="500")
    first = _sale(fiscal_posting, db, quantity="3", price=AWKWARD, document_date=TODAY)
    _sale(fiscal_posting, db, quantity="7", price=AWKWARD, document_date=TODAY)
    credit_note(
        fiscal_posting,
        db,
        refund_of_document_id=first.id,
        document_date=TODAY,
        lines=(
            documents_service.LineInput(
                item_id=fiscal_posting.stock_item.id,
                quantity=D(1),
                unit_price=AWKWARD,
                returns_line_id=line_of(first).id,
            ),
        ),
    )
    _sale(fiscal_posting, db, quantity="2", price=AWKWARD, document_date=SIGNED_ELSEWHEN)
    drainer.drain_company(
        db, fiscal_posting.company_id, client=sandbox_client, max_rows_per_device=200
    )
    # Posted after the drain and left there: a sale the ledger knows about and RRA does not.
    _sale(fiscal_posting, db, quantity="5", price=AWKWARD, document_date=TODAY)
    db.commit()
    set_tenant(db, fiscal_posting.company_id)
    set_actor(db, fiscal_posting.owner.id)
    return fiscal_posting


def _listing(db: Session, fixture: FiscalPosting, *, date_from=TODAY, date_to=TODAY):  # noqa: ANN202
    return enquiry_service.receipt_listing(
        db, fixture.company_id, fixture.device.id, date_from=date_from, date_to=date_to
    )


def test_the_counters_and_the_totals_are_what_the_receipts_declared(
    db: Session, tied: FiscalPosting
) -> None:
    """Three sales and one refund signed today, and the figures are the **wire's** own.

    Worked by hand at 1 768.82 inclusive per bottle:
      * INV-A 3 → 5 306.46, VAT 809.46
      * INV-B 7 → 12 381.74, VAT 1 888.74
      * INV-OLD 2 → 3 537.64, VAT 539.64  (dated in March, signed today)
      * NS 3 receipts, 21 225.84 gross, 3 237.84 VAT
      * CRN-A 1 → 1 768.82, VAT 269.82 · NR 1 receipt
      * declared net = 21 225.84 − 1 768.82 = 19 457.02

    Not one of these is a figure the ledger holds. That is the point: these are the numbers on
    the paper an inspector is holding.
    """
    view = _listing(db, tied)

    assert (view.ns_count, view.nr_count) == (3, 1)
    assert view.ns_gross == D("21225.84")
    assert view.ns_tax == D("3237.84")
    assert view.nr_gross == D("1768.82")
    assert view.nr_tax == D("269.82")
    assert view.declared_net == D("19457.02")


def test_the_ledger_side_is_the_documents_counted_the_ledgers_own_way(
    db: Session, tied: FiscalPosting
) -> None:
    """The sales ledger for the same day, in whole francs because RWF has no smaller unit.

    Four documents are dated today: INV-A 5 306, INV-B 12 382, INV-QUEUED 8 844 and the credit
    note 1 769. INV-OLD is dated in March and is not in this range — the receipts side has it
    and this side does not, which is the asymmetry the next test names.
    """
    view = _listing(db, tied)

    assert (view.ledger_invoice_count, view.ledger_credit_note_count) == (3, 1)
    assert view.ledger_invoice_total == D("26532")
    assert view.ledger_credit_note_total == D("1769")
    assert view.ledger_net == D("24763")


def test_the_tie_reconciles_rather_than_balances(
    db: Session, tied: FiscalPosting
) -> None:
    """The whole report in one assertion, and the word is **reconciled** (decision 12's word).

    Declared 19 457.02 against a ledger of 24 763 is a difference of −5 305.98, and a screen
    that stopped there would have handed an accountant a morning's work. Adding back the one
    document the ledger has and RRA does not (INV-QUEUED, 8 844) and taking out the one RRA has
    and the ledger's range does not (INV-OLD, 3 537.64) leaves **0.38** — which is the rounding
    residue and nothing else:

        INV-A +0.46 · INV-B −0.26 · CRN-A +0.18  (a refund, so its residue adds back)

    0.38 is not a defect and never will be; it is two correct rounding rules meeting. The
    report's job is to get a reader to it in one step instead of three days.
    """
    view = _listing(db, tied)
    assert view.difference == D("-5305.98")

    queued = sum(row.base_total_amount for row in view.only_in_ledger)
    elsewhere = sum(row.declared_gross for row in view.only_on_receipts)
    assert view.difference + queued - elsewhere == D("0.38")


def test_a_sale_the_queue_still_holds_is_named_with_the_word_that_fixes_it(
    db: Session, tied: FiscalPosting
) -> None:
    """The accountant's real question: *which* document, and what is it waiting on?

    A total that disagrees is a morning's work. The same total with the document named and the
    word `queued` against it is a drain — and `failed` would be somebody's decision instead,
    which is why the status is carried through rather than flattened to "missing".
    """
    view = _listing(db, tied)

    assert [(row.number, row.reason) for row in view.only_in_ledger] == [
        (view.only_in_ledger[0].number, "queued")
    ]
    assert view.only_in_ledger[0].base_total_amount == D("8844")
    assert view.only_in_ledger[0].kind is DocumentKind.INVOICE


def test_a_receipt_signed_outside_its_documents_range_says_dated_outside(
    db: Session, tied: FiscalPosting
) -> None:
    """A receipt belongs to the day the device **signed** it; a document to the day it is dated.

    They part company at every month end, and again on every refund of an older invoice — RRA
    cannot un-sign a sale, so the `NR` is signed today against a document dated then
    (decision 3). INV-OLD is that shape: signed today, dated in March, and on the receipts side
    of today's range alone.
    """
    view = _listing(db, tied)

    assert [(row.document_date, row.outside_range) for row in view.only_on_receipts] == [
        (SIGNED_ELSEWHEN, "dated_outside")
    ]
    assert view.only_on_receipts[0].declared_gross == D("3537.64")


def test_every_row_carries_the_declaration_and_the_posting_side_by_side(
    db: Session, tied: FiscalPosting
) -> None:
    """Both numbers on the row, because a reader wants both rather than somebody's subtraction.

    INV-A declared 5 306.46 and posted 5 306. A row that showed one of them would be a row an
    accountant could not reconcile against whichever system they had open; a row that showed
    the difference alone would hide which side each figure came from.
    """
    view = _listing(db, tied)

    row = next(r for r in view.receipts if r.declared_gross == D("5306.46"))
    assert row.posted_base_total == D("5306")
    assert row.receipt_type is FiscalReceiptType.NORMAL_SALE
    assert row.receipt_number.endswith("NS")
    assert row.outside_range is None


def test_the_listing_reaches_a_signed_in_accountant_over_http(
    client: TestClient, db: Session, tied: FiscalPosting
) -> None:
    """Opened with data behind it and a figure asserted off what came back (rule 13).

    A 200 with an empty body would prove the route compiles and nothing else — which is exactly
    how step 4 shipped a revaluation detail that returned 500 to every caller.
    """
    response = client.post(
        "/api/v1/auth/login", json={"email": tied.owner.email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text

    response = client.get(
        "/api/v1/fiscal/receipts/listing",
        params={
            "device_id": tied.device.id,
            "date_from": TODAY.isoformat(),
            "date_to": TODAY.isoformat(),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ns_count"] == 3
    assert D(body["ns_gross"]) == D("21225.84")
    assert D(body["ledger_net"]) == D("24763")
    assert D(body["difference"]) == D("-5305.98")
    assert body["sdc_id"] == tied.device.sdc_id
    assert [row["reason"] for row in body["only_in_ledger"]] == ["queued"]
    assert [row["outside_range"] for row in body["only_on_receipts"]] == ["dated_outside"]
