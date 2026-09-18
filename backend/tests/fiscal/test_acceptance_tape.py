"""The P7 acceptance tape (step 5), row by row, in the RWF-base company.

**Every expected value below is a literal worked by hand**, never a figure computed by the
code under test. Where a row's arithmetic is not obvious the working is in a comment beside it.
That is the whole point of a tape: a test that recomputed the expectation the way the service
does would pass just as happily over a service that had been wrong from the start.

The rows are one sequence, not nineteen independent cases, because the interesting values are
the ones that depend on history — row 9's Z exists only because rows 1 to 8 signed six
receipts, row 10's return ties only because rows 1 to 7 posted the tax it declares, and row 13's
gain of 1 416 exists only because row 4 booked USD 47.20 at 1 320. So it is one test. When it
fails, the row number in the assertion message is the place to look.

**After every row**: `assert_ledger_invariants`, `assert_subledger_invariants`,
`assert_stock_invariants`, `assert_order_invariants` and `assert_fiscal_invariants`.

The setup is the plan's: company `TIN 999000099`, VAT-registered, device D00 on branch Main
(`bhf_id 00`, profile `vsdc`, sandbox); items X (stock, `VAT-OUT-18`/B, 2 000 excl., cost
1 000), S (service, B, 10 000 excl.), E (stock, `VAT-EXEMPT`/A, 1 000, cost 500), Z (stock,
`VAT-ZERO`/C, 5 000, cost 3 000), all with a class code and base UoM `U`; customer C (TIN
`100000001`, phone, credit terms), walk-in W (no TIN), supplier S1 (TIN `100000002`); policy
`block`; opening stock by GRN-1.

The sandbox answers as RRA would — it validates the header buckets against the items and the
tax against the taxable amount, keeps the receipt counters, and refuses a duplicate `invcNo` —
so what the adapter is proved against here is a server that checks, not a double that agrees.
"""

import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.fiscal import daily as daily_service
from app.fiscal import devices as device_service
from app.fiscal import drainer, printing, registry
from app.fiscal import feed as feed_service
from app.fiscal import items as fiscal_items
from app.fiscal import outbox as outbox_service
from app.inventory import masters as inventory_masters
from app.inventory import stock as stock_service
from app.kernel import posting
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import ManualJournal
from app.kernel.posting import LineSpec
from app.models.company import Branch, Company
from app.models.currency import ExchangeRate
from app.models.fiscalization import (
    FiscalCode,
    FiscalDeviceStatus,
    FiscalEnvironment,
    FiscalFeedDecision,
    FiscalItem,
    FiscalOutboxKind,
    FiscalOutboxStatus,
    FiscalProfile,
    FiscalPurchaseFeedRow,
    FiscalReceipt,
    FiscalReceiptType,
    FxRevaluationRole,
    PaymentMethod,
)
from app.models.gl import GLSettings, NegativeStockPolicy
from app.models.inventory import ItemType
from app.models.journal import JournalEntry, JournalLine, JournalStatus
from app.models.partner import PartnerRole, TaxMode
from app.models.subledger import DocumentKind
from app.models.tax import TaxCode
from app.order_entry import grn as grn_service
from app.schemas.fiscal import device_read
from app.subledger import documents as documents_service
from app.subledger import masters as partner_masters
from app.subledger import revaluation as revaluation_service
from app.tax import vat as vat_service
from tests.fiscal.conftest import SANDBOX_URL
from tests.fiscal.invariants import assert_fiscal_invariants
from tests.inventory.conftest import build_inventory
from tests.inventory.invariants import assert_stock_invariants
from tests.kernel.conftest import YEAR, build_ledger
from tests.kernel.invariants import assert_ledger_invariants
from tests.order_entry.invariants import assert_order_invariants
from tests.subledger.invariants import assert_subledger_invariants

D = Decimal
ZERO = Decimal(0)

#: The tape's day, and the month the VAT return covers.
TAPE_DAY = date(YEAR, 3, 10)
MARCH_FROM = date(YEAR, 3, 1)
MARCH_TO = date(YEAR, 3, 31)
APRIL_FROM = date(YEAR, 4, 1)
APRIL_TO = date(YEAR, 4, 30)
#: Row 12's backdated correction, and row 14's reversal — both later in March than the tape's
#: own day, so they are "into the month" rather than into a closed one.
LATE_DAY = date(YEAR, 3, 20)
REVERSAL_DAY = date(YEAR, 3, 25)

#: The two USD rates the tape turns on: row 4 books at 1 320 and row 13 revalues at 1 350.
USD_BOOKING_RATE = D(1320)
USD_MONTH_END_RATE = D(1350)

PURCHASE_CODE = "AB12CD"
PURCHASE_CODE_USD = "CD34EF"
#: §4.16 code 06, "Refund" — RRA's own name for the ordinary case; 07 is "Cancellation".
REFUND_REASON = "06"
CANCELLATION_REASON = "07"

#: §4.15 movement codes, and the only place this file names one.
PURCHASE_IN = "02"
RETURN_IN = "03"
SALE_OUT = "11"


# --- The tape's fixture -----------------------------------------------------------------------


@dataclass
class Tape:
    """The plan's setup. Its own tenant, because the tape asserts absolute counters — receipt
    `1/1 NS`, `invc_no 1`, `sarNo 1` — and a shared fixture's history would move all of them."""

    company_id: int
    owner: object
    device: object
    accounts: dict
    tax_codes: dict[str, TaxCode]
    currencies: dict
    main_warehouse: object
    main_branch: Branch
    x: object
    s: object
    e: object
    z: object
    customer: object
    walk_in: object
    supplier: object

    def acct(self, code: str) -> int:
        return self.accounts[code].id


@pytest.fixture
def tape(db: Session, sandbox_client: httpx.Client) -> Tape:
    ledger = build_ledger(
        db, company_name="Rugari Wines Ltd (tape)", email="owner+p7tape@rugari.example"
    )
    inventory = build_inventory(db, ledger)
    company_id = ledger.company_id
    owner = ledger.owner

    company = db.get(Company, company_id)
    company.tin = "999000099"
    company.vat_registered = True

    settings = db.scalars(select(GLSettings).where(GLSettings.company_id == company_id)).one()
    assert settings.negative_stock_policy == NegativeStockPolicy.BLOCK, (
        "the tape runs on the shipped Rwanda default, not on a policy invented for it"
    )

    tax_codes = {
        row.code: row
        for row in db.scalars(select(TaxCode).where(TaxCode.company_id == company_id))
    }
    accounts = ledger.accounts
    # Base UoM `U` — the authority's quantity unit for "piece" (§4.6).
    inventory.each.fiscal_quantity_unit = "U"

    def _item(code: str, name: str, kind: ItemType, price: Decimal, tax: str) -> object:
        row = inventory_masters.create_item(
            db,
            company_id,
            inventory_masters.ItemInput(
                code=code,
                name=name,
                uom_category_id=inventory.count.id,
                base_uom_id=inventory.each.id,
                item_type=kind,
                selling_price=price,
                sales_account_id=accounts["4100"].id,
                cogs_account_id=accounts["5100"].id if kind is ItemType.STOCK else None,
                purchase_account_id=accounts["6990"].id if kind is ItemType.SERVICE else None,
                default_sales_tax_code_id=tax_codes[tax].id,
            ),
            actor=owner,
        )
        row.fiscal_class_code = "5059020800"
        return row

    x = _item("X", "Rugari Red 750ml", ItemType.STOCK, D(2000), "VAT-OUT-18")
    s = _item("S", "Delivery charge", ItemType.SERVICE, D(10_000), "VAT-OUT-18")
    e = _item("E", "Exempt sundry", ItemType.STOCK, D(1000), "VAT-EXEMPT")
    z = _item("Z", "Zero-rated export pack", ItemType.STOCK, D(5000), "VAT-ZERO")

    # `payment_method` is keyed on each document rather than defaulted from terms: the tape
    # names it per row ("credit", "cash"), and that is what the step-7 screen sends.
    customer = partner_masters.create_partner(
        db,
        company_id,
        partner_masters.PartnerInput(
            name="Umucyo Traders Ltd",
            customer_code="C",
            tin="100000001",
            phone="+250788000001",
        ),
        actor=owner,
    )
    walk_in = partner_masters.create_partner(
        db,
        company_id,
        partner_masters.PartnerInput(name="Walk-in customer", customer_code="W"),
        actor=owner,
    )
    supplier = partner_masters.create_partner(
        db,
        company_id,
        partner_masters.PartnerInput(
            name="Kigali Glass", supplier_code="S1", tin="100000002"
        ),
        actor=owner,
    )

    db.add_all(
        [
            ExchangeRate(
                company_id=company_id,
                currency_id=ledger.cur("USD"),
                valid_from=TAPE_DAY,
                rate=USD_BOOKING_RATE,
            ),
            ExchangeRate(
                company_id=company_id,
                currency_id=ledger.cur("USD"),
                valid_from=MARCH_TO,
                rate=USD_MONTH_END_RATE,
            ),
        ]
    )
    db.flush()

    main_branch = db.scalars(
        select(Branch).where(Branch.company_id == company_id, Branch.is_main.is_(True))
    ).one()
    device = device_service.register_device(
        db,
        company_id,
        branch_id=main_branch.id,
        profile=FiscalProfile.VSDC,
        environment=FiscalEnvironment.TEST,
        base_url=SANDBOX_URL,
        dvc_srl_no="SDC-SERIAL-TAPE",
        bhf_id="00",
        actor=owner,
    )
    db.flush()

    return Tape(
        company_id=company_id,
        owner=owner,
        device=device,
        accounts=accounts,
        tax_codes=tax_codes,
        currencies=ledger.currencies,
        main_warehouse=inventory.main,
        main_branch=main_branch,
        x=x,
        s=s,
        e=e,
        z=z,
        customer=customer,
        walk_in=walk_in,
        supplier=supplier,
    )


# --- The tape's own bookkeeping ---------------------------------------------------------------
#
# Expected-vs-actual for every row is collected as it goes and printed at the end, because the
# step-5 report is that table. Collecting it costs nothing and means the report quotes the run
# rather than a transcription of it.

_TABLE: list[tuple[str, str, str, str, str]] = []


def _expect(row: str, label: str, expected, actual) -> None:  # noqa: ANN001
    ok = expected == actual
    _TABLE.append((row, label, _shown(expected), _shown(actual), "ok" if ok else "MISMATCH"))
    assert ok, f"tape row {row}: {label} expected {expected}, got {actual}"


def _shown(value) -> str:  # noqa: ANN001
    """What goes in the printed table. Long structures are elided: one row of it is a frozen
    return's whole snapshot, and a report nobody can read is a report."""
    text = str(value)
    return text if len(text) <= 60 else f"{text[:57]}..."


@pytest.fixture(scope="module", autouse=True)
def _print_table():  # noqa: ANN202
    yield
    if not _TABLE:
        return
    width = max(len(entry[1]) for entry in _TABLE)
    print("\n[p7 tape] expected vs actual")
    for row, label, expected, actual, status in _TABLE:
        print(
            f"  {row:<4} {label:<{width}}  expected {expected:>18}  "
            f"actual {actual:>18}  {status}"
        )


def _after_every_row(db: Session, tape: Tape, row: str) -> None:
    db.flush()
    company_id = tape.company_id
    assert_ledger_invariants(db, company_id)
    assert_subledger_invariants(db, company_id)
    assert_stock_invariants(db, company_id)
    assert_order_invariants(db, company_id)
    assert_fiscal_invariants(db, company_id)
    _TABLE.append((row, "invariants", "green", "green", "ok"))


# --- Reading the run --------------------------------------------------------------------------


def _rows(db: Session, tape: Tape, kind: FiscalOutboxKind | None = None) -> list:
    query = select(outbox_service.FiscalOutboxRow).where(
        outbox_service.FiscalOutboxRow.company_id == tape.company_id
    )
    if kind is not None:
        query = query.where(outbox_service.FiscalOutboxRow.kind == kind)
    return list(db.scalars(query.order_by(outbox_service.FiscalOutboxRow.sequence_no)))


def _row_for(db: Session, tape: Tape, document, kind: FiscalOutboxKind):  # noqa: ANN001, ANN202
    found = [
        row
        for row in _rows(db, tape, kind)
        if row.source_doc_id == document.id
        and row.source_doc_type
        in (outbox_service.DOCUMENT_SOURCE, outbox_service.REVERSAL_SOURCE)
    ]
    assert found, f"{document.number} raised no {kind} row"
    return found[-1]


def _movements_of(db: Session, tape: Tape, document) -> list:  # noqa: ANN001
    """The `stock_io` rows this **partner document** raised.

    Matched on the source *type* as well as the id, deliberately: a goods-received note and a
    partner document are separate tables with separate id sequences, and the first draft of
    this file matched on the id alone — which made GRN-1's movement answer for INV-1's and read
    `sarNo 1` where the tape expects 2.
    """
    return [
        row
        for row in _rows(db, tape, FiscalOutboxKind.STOCK_IO)
        if row.source_doc_id == document.id
        and row.source_doc_type
        in (outbox_service.DOCUMENT_SOURCE, outbox_service.REVERSAL_SOURCE)
    ]


def _receipt_of(db: Session, tape: Tape, document, receipt_type=None):  # noqa: ANN001, ANN202
    query = select(FiscalReceipt).where(
        FiscalReceipt.company_id == tape.company_id,
        FiscalReceipt.document_id == document.id,
    )
    if receipt_type is not None:
        query = query.where(FiscalReceipt.receipt_type == receipt_type)
    return db.scalars(query.order_by(FiscalReceipt.tot_rcpt_no.desc())).first()


def _counter(receipt: FiscalReceipt) -> str:
    return f"{receipt.rcpt_no}/{receipt.tot_rcpt_no} {receipt.receipt_type}"


def _on_hand(db: Session, tape: Tape, item) -> Decimal:  # noqa: ANN001
    return stock_service.location_balance(
        db, tape.company_id, item.id, tape.main_warehouse.id
    )


def _master_quantity(db: Session, tape: Tape, item) -> Decimal:  # noqa: ANN001
    """The last on-hand snapshot the authority was told for this item."""
    masters = [
        row
        for row in _rows(db, tape, FiscalOutboxKind.STOCK_MASTER)
        if row.payload.get("itemCd") == _item_code(db, tape, item)
    ]
    assert masters, f"{item.code} has no stock-master row"
    return D(str(masters[-1].payload["rsdQty"]))


def _item_code(db: Session, tape: Tape, item) -> str:  # noqa: ANN001
    row = db.scalar(
        select(FiscalItem).where(
            FiscalItem.company_id == tape.company_id, FiscalItem.item_id == item.id
        )
    )
    return row.item_cd if row else ""


def _balance(db: Session, tape: Tape, code: str) -> Decimal:
    rows = db.scalars(
        select(JournalLine.base_amount)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == tape.company_id,
            JournalLine.gl_account_id == tape.acct(code),
            JournalEntry.status == JournalStatus.POSTED,
        )
    ).all()
    return sum(rows, ZERO)


def _balance_on(db: Session, tape: Tape, code: str, on: date) -> Decimal:
    """An account's balance **as at** a date — what a revaluation's own day holds before its
    mirror the next morning takes it back out."""
    rows = db.scalars(
        select(JournalLine.base_amount)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == tape.company_id,
            JournalLine.gl_account_id == tape.acct(code),
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date <= on,
        )
    ).all()
    return sum(rows, ZERO)


def _mode(client: httpx.Client, mode: str, code: str | None = None) -> None:
    body = {"mode": mode}
    if code is not None:
        body["code"] = code
    client.post("/_sandbox/mode", json=body)


def _drain(db: Session, tape: Tape, client: httpx.Client, *, at: datetime | None = None) -> None:
    drainer.drain_company(
        db,
        tape.company_id,
        now=at or datetime.now(UTC),
        client=client,
        max_rows_per_device=60,
    )
    db.flush()


def _post(db: Session, tape: Tape, role: PartnerRole, **kwargs):  # noqa: ANN003, ANN202
    document, _ = documents_service.post_document(
        db,
        tape.company_id,
        role,
        documents_service.DocumentInput(tax_mode=TaxMode.EXCLUSIVE, **kwargs),
        actor=tape.owner,
    )
    return document


def _line_of(document, item_id: int):  # noqa: ANN001, ANN202
    return next(line for line in document.lines if line.item_id == item_id)


# --- The tape -----------------------------------------------------------------------------


def test_the_acceptance_tape(  # noqa: PLR0915
    db: Session, tape: Tape, sandbox_client: httpx.Client
) -> None:
    company_id = tape.company_id
    owner = tape.owner
    device = tape.device
    adapter = registry.adapter_for("RW", client=sandbox_client)

    # --- Row 0: initialize D00, sync, register X/S/E/Z, GRN-1 -------------------------------
    device_service.initialize_device(
        db, company_id, device, actor=owner, client=sandbox_client
    )
    _expect("0", "device status", FiscalDeviceStatus.ACTIVE, device.status)
    _expect("0", "sdc_id", "SDC010000005", device.sdc_id)
    _expect("0", "mrc_no", "WIS01006230", device.mrc_no)
    # Keys unreadable through the API: the response model has no field for one, so a key
    # cannot be serialised however the device is loaded.
    rendered = device_read(device).model_dump()
    _expect("0", "keys on the wire", [], sorted(k for k in rendered if k.endswith("_key")))
    _expect("0", "device holds keys", True, rendered["has_keys"])

    device_service.sync_codes(db, company_id, device, actor=owner, client=sandbox_client)
    device_service.sync_item_classes(
        db, company_id, device, actor=owner, client=sandbox_client
    )
    tax_class_codes = sorted(
        row.code
        for row in db.scalars(
            select(FiscalCode).where(
                FiscalCode.company_id == company_id, FiscalCode.code_class == "04"
            )
        )
    )
    _expect("0", "code class 04", ["A", "B", "C", "D"], tax_class_codes)

    # Registered in the order the tape names them, because `item_cd`'s last seven characters
    # are a company-wide sequence and the tape asserts each one.
    for item in (tape.x, tape.s, tape.e, tape.z):
        fiscal_items.ensure_registered_for_report(
            db, company_id, device=device, adapter=adapter, item=item, actor=owner
        )
    db.flush()
    _expect("0", "item_cd X", "RW2NTXU0000001", _item_code(db, tape, tape.x))
    _expect("0", "item_cd S", "RW3NTXU0000002", _item_code(db, tape, tape.s))
    _expect("0", "item_cd E", "RW2NTXU0000003", _item_code(db, tape, tape.e))
    _expect("0", "item_cd Z", "RW2NTXU0000004", _item_code(db, tape, tape.z))

    grn_service.post_grn(
        db,
        company_id,
        grn_service.GrnInput(
            partner_id=tape.supplier.id,
            grn_date=TAPE_DAY,
            description="GRN-1 opening stock",
            warehouse_id=tape.main_warehouse.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=tape.x.id, quantity=D(100), unit_cost=D(1000)
                ),
                grn_service.GrnLineInput(
                    item_id=tape.e.id, quantity=D(50), unit_cost=D(500)
                ),
                grn_service.GrnLineInput(
                    item_id=tape.z.id, quantity=D(20), unit_cost=D(3000)
                ),
            ),
        ),
        actor=owner,
    )
    db.flush()
    _drain(db, tape, sandbox_client)

    item_rows = _rows(db, tape, FiscalOutboxKind.ITEM)
    _expect("0", "item rows", 4, len(item_rows))
    _expect(
        "0",
        "item rows sent",
        [FiscalOutboxStatus.SENT] * 4,
        [row.status for row in item_rows],
    )
    movements = _rows(db, tape, FiscalOutboxKind.STOCK_IO)
    _expect("0", "stock_io sarNo", 1, movements[0].sar_no)
    _expect("0", "stock_io sarTyCd", PURCHASE_IN, movements[0].payload["sarTyCd"])
    _expect("0", "master X", D(100), _master_quantity(db, tape, tape.x))
    _expect("0", "master E", D(50), _master_quantity(db, tape, tape.e))
    _expect("0", "master Z", D(20), _master_quantity(db, tape, tape.z))
    _after_every_row(db, tape, "0")

    # --- Row 1: INV-1 to C, exclusive, credit, purchase code AB12CD ------------------------
    # 10 x X @ 2 000 = 20 000 + 3 600 · 1 x S @ 10 000 = 10 000 + 1 800
    # 5 x E @ 1 000  =  5 000 +     0 · 2 x Z @  5 000 = 10 000 +     0
    # net 45 000 · tax 5 400 · gross 50 400
    inv1 = _post(
        db,
        tape,
        PartnerRole.AR,
        kind=DocumentKind.INVOICE,
        partner_id=tape.customer.id,
        document_date=TAPE_DAY,
        description="INV-1",
        purchase_code=PURCHASE_CODE,
        payment_method=PaymentMethod.CREDIT,
        lines=(
            documents_service.LineInput(
                item_id=tape.x.id, quantity=D(10), unit_price=D(2000)
            ),
            documents_service.LineInput(
                item_id=tape.s.id, quantity=D(1), unit_price=D(10_000)
            ),
            documents_service.LineInput(
                item_id=tape.e.id, quantity=D(5), unit_price=D(1000)
            ),
            documents_service.LineInput(
                item_id=tape.z.id, quantity=D(2), unit_price=D(5000)
            ),
        ),
    )
    db.flush()
    _expect("1", "INV-1 net", D(45_000), inv1.net_amount)
    _expect("1", "INV-1 tax", D(5_400), inv1.tax_amount)
    _expect("1", "INV-1 gross", D(50_400), inv1.total_amount)

    sale1 = _row_for(db, tape, inv1, FiscalOutboxKind.SALE)
    _expect("1", "sale row status", FiscalOutboxStatus.QUEUED, sale1.status)
    _expect("1", "sale invc_no", 1, sale1.invc_no)
    payload = sale1.payload
    _expect("1", "bucket A", D("5000.00"), D(str(payload["taxblAmtA"])))
    _expect("1", "bucket B", D("35400.00"), D(str(payload["taxblAmtB"])))
    _expect("1", "tax B", D("5400.00"), D(str(payload["taxAmtB"])))
    _expect("1", "bucket C", D("10000.00"), D(str(payload["taxblAmtC"])))
    _expect("1", "bucket D", D("0.00"), D(str(payload["taxblAmtD"])))
    _expect("1", "totTaxblAmt", D("50400.00"), D(str(payload["totTaxblAmt"])))
    _expect("1", "totTaxAmt", D("5400.00"), D(str(payload["totTaxAmt"])))
    lines = {line["itemCd"]: line for line in payload["itemList"]}
    line_x = lines[_item_code(db, tape, tape.x)]
    _expect("1", "X prc", D("2360.00"), D(str(line_x["prc"])))
    _expect("1", "X splyAmt", D("23600.00"), D(str(line_x["splyAmt"])))
    _expect("1", "X taxAmt", D("3600.00"), D(str(line_x["taxAmt"])))
    line_s = lines[_item_code(db, tape, tape.s)]
    _expect("1", "S prc", D("11800.00"), D(str(line_s["prc"])))
    _expect("1", "S taxAmt", D("1800.00"), D(str(line_s["taxAmt"])))
    line_e = lines[_item_code(db, tape, tape.e)]
    _expect("1", "E prc", D("1000.00"), D(str(line_e["prc"])))
    _expect("1", "E taxAmt", D("0.00"), D(str(line_e["taxAmt"])))
    line_z = lines[_item_code(db, tape, tape.z)]
    _expect("1", "Z prc", D("5000.00"), D(str(line_z["prc"])))
    _expect("1", "Z taxAmt", D("0.00"), D(str(line_z["taxAmt"])))
    _expect("1", "custTin", "100000001", payload["custTin"])
    _expect("1", "prcOrdCd", PURCHASE_CODE, payload["prcOrdCd"])
    _expect("1", "pmtTyCd", "02", payload["pmtTyCd"])

    with pytest.raises(LedgerStateError) as refused:
        printing.receipt_block(db, company_id, inv1.id)
    _expect("1", "print before the receipt", "fiscal_receipt_pending", refused.value.code)

    _drain(db, tape, sandbox_client)
    receipt1 = _receipt_of(db, tape, inv1)
    _expect("1", "INV-1 receipt", "1/1 NS", _counter(receipt1))
    block = printing.receipt_block(db, company_id, inv1.id)
    _expect("1", "print after the receipt", "1/1 NS", block.receipt_number)

    sale_movement = _movements_of(db, tape, inv1)[0]
    _expect("1", "stock_io sarNo", 2, sale_movement.sar_no)
    _expect("1", "stock_io sarTyCd", SALE_OUT, sale_movement.payload["sarTyCd"])
    _expect("1", "master X", D(90), _master_quantity(db, tape, tape.x))
    _expect("1", "master E", D(45), _master_quantity(db, tape, tape.e))
    _expect("1", "master Z", D(18), _master_quantity(db, tape, tape.z))
    _after_every_row(db, tape, "1")

    # --- Row 2: INV-2 to W, cash, 3 x X @ 2 000 less 10 % ----------------------------------
    # net 3 x 2 000 x 0.9 = 5 400 · tax 18 % = 972 · gross 6 372
    # On the wire the line is extended from the **inclusive** price: prc 2 360.00,
    # splyAmt 3 x 2 360 = 7 080, and dcAmt is the residue 7 080 − 6 372 = 708.
    inv2 = _post(
        db,
        tape,
        PartnerRole.AR,
        kind=DocumentKind.INVOICE,
        partner_id=tape.walk_in.id,
        document_date=TAPE_DAY,
        description="INV-2",
        payment_method=PaymentMethod.CASH,
        lines=(
            documents_service.LineInput(
                item_id=tape.x.id,
                quantity=D(3),
                unit_price=D(2000),
                discount_percent=D(10),
            ),
        ),
    )
    db.flush()
    _expect("2", "INV-2 net", D(5_400), inv2.net_amount)
    _expect("2", "INV-2 tax", D(972), inv2.tax_amount)
    _expect("2", "INV-2 gross", D(6_372), inv2.total_amount)
    sale2 = _row_for(db, tape, inv2, FiscalOutboxKind.SALE)
    line = sale2.payload["itemList"][0]
    _expect("2", "prc", D("2360.00"), D(str(line["prc"])))
    _expect("2", "splyAmt", D("7080.00"), D(str(line["splyAmt"])))
    _expect("2", "dcRt", D("10.00"), D(str(line["dcRt"])))
    _expect("2", "dcAmt", D("708.00"), D(str(line["dcAmt"])))
    _expect("2", "taxblAmt", D("6372.00"), D(str(line["taxblAmt"])))
    _expect("2", "taxAmt", D("972.00"), D(str(line["taxAmt"])))
    _expect("2", "pmtTyCd", "01", sale2.payload["pmtTyCd"])
    # A walk-in has no TIN, and the payload leaves the field out rather than sending null —
    # the same fact on the wire, so the assertion reads it with `.get`.
    _expect("2", "custTin", None, sale2.payload.get("custTin"))

    _drain(db, tape, sandbox_client)
    _expect("2", "INV-2 receipt", "2/2 NS", _counter(_receipt_of(db, tape, inv2)))
    _expect("2", "master X", D(87), _master_quantity(db, tape, tape.x))
    _after_every_row(db, tape, "2")

    # --- Row 3: CRN-1 against INV-1's X line, 2 x X, reason 06 -----------------------------
    # net 2 x 2 000 = 4 000 · tax 720 · gross 4 720
    # The companion returns the stock at the cost it left at: 2 x 1 000.
    crn1 = _post(
        db,
        tape,
        PartnerRole.AR,
        kind=DocumentKind.CREDIT_NOTE,
        partner_id=tape.customer.id,
        document_date=TAPE_DAY,
        description="CRN-1",
        purchase_code=PURCHASE_CODE,
        payment_method=PaymentMethod.CREDIT,
        refund_of_document_id=inv1.id,
        refund_reason=REFUND_REASON,
        lines=(
            documents_service.LineInput(
                item_id=tape.x.id,
                quantity=D(2),
                unit_price=D(2000),
                returns_line_id=_line_of(inv1, tape.x.id).id,
            ),
        ),
    )
    db.flush()
    _expect("3", "CRN-1 net", D(4_000), crn1.net_amount)
    _expect("3", "CRN-1 tax", D(720), crn1.tax_amount)
    _expect("3", "CRN-1 gross", D(4_720), crn1.total_amount)
    refund1 = _row_for(db, tape, crn1, FiscalOutboxKind.REFUND)
    _expect("3", "rcptTyCd", "R", refund1.payload["rcptTyCd"])
    _expect("3", "orgInvcNo", 1, refund1.payload["orgInvcNo"])
    _expect("3", "rfdRsnCd", REFUND_REASON, refund1.payload["rfdRsnCd"])
    _expect("3", "refund invc_no", 3, refund1.invc_no)

    _drain(db, tape, sandbox_client)
    _expect("3", "CRN-1 receipt", "1/3 NR", _counter(_receipt_of(db, tape, crn1)))
    _expect("3", "stock_io sarTyCd", RETURN_IN, _movements_of(db, tape, crn1)[0].payload["sarTyCd"])
    _expect("3", "master X", D(89), _master_quantity(db, tape, tape.x))
    _after_every_row(db, tape, "3")

    # --- Row 3b: print INV-1 again — a COPY, and no second call to RRA ---------------------
    before = len(_rows(db, tape))
    copy = printing.record_copy(db, company_id, inv1.id, actor=owner)
    db.flush()
    _expect("3b", "copy layout", True, copy.is_copy)
    _expect("3b", "copy_count", 1, copy.copy_count)
    _expect("3b", "same SDC block", receipt1.sdc_id, copy.sdc_id)
    _expect("3b", "same receipt number", "1/1 NS", copy.receipt_number)
    _expect("3b", "no new outbox row", before, len(_rows(db, tape)))
    _after_every_row(db, tape, "3b")

    # --- Row 4: INV-3 to C in USD at 1 320, code CD34EF, 20 x X @ 2.00 ---------------------
    # USD 40.00 net · 7.20 tax · 47.20 gross
    # base 40 x 1 320 = 52 800 · 7.20 x 1 320 = 9 504 · 62 304
    # the wire is RWF: prc = 2.00 x 1.18 x 1 320 = 3 115.20
    inv3 = _post(
        db,
        tape,
        PartnerRole.AR,
        kind=DocumentKind.INVOICE,
        partner_id=tape.customer.id,
        document_date=TAPE_DAY,
        description="INV-3",
        purchase_code=PURCHASE_CODE_USD,
        payment_method=PaymentMethod.CREDIT,
        currency_id=tape.currencies["USD"].id,
        exchange_rate=USD_BOOKING_RATE,
        lines=(
            documents_service.LineInput(
                item_id=tape.x.id, quantity=D(20), unit_price=D("2.00")
            ),
        ),
    )
    db.flush()
    _expect("4", "INV-3 net USD", D("40.00"), inv3.net_amount)
    _expect("4", "INV-3 tax USD", D("7.20"), inv3.tax_amount)
    _expect("4", "INV-3 gross USD", D("47.20"), inv3.total_amount)
    _expect("4", "INV-3 base", D("62304"), inv3.base_total_amount)
    sale3 = _row_for(db, tape, inv3, FiscalOutboxKind.SALE)
    _expect("4", "prc RWF", D("3115.20"), D(str(sale3.payload["itemList"][0]["prc"])))
    _expect("4", "bucket B", D("62304.00"), D(str(sale3.payload["taxblAmtB"])))
    _expect("4", "tax B", D("9504.00"), D(str(sale3.payload["taxAmtB"])))

    _drain(db, tape, sandbox_client)
    _expect("4", "INV-3 receipt", "3/4 NS", _counter(_receipt_of(db, tape, inv3)))
    _expect("4", "master X", D(69), _master_quantity(db, tape, tape.x))
    _after_every_row(db, tape, "4")

    # --- Row 5: the sandbox is down; INV-4 survives three backoff steps --------------------
    # net 10 000 · tax 1 800 · gross 11 800.
    #
    # **The tape's "+0, +1 min, +5 min" are the backoff *waits*, not absolute offsets**, and
    # the expected outcome is what says so: the schedule is 1 → 5 → 15 minutes (decision 4),
    # so a row attempted at +0 is next due at +1, and one attempted at +1 is next due at +6.
    # A third drain at an absolute +5 would find nothing due and leave two attempts behind,
    # which is not "attempts 3 · next_attempt_at +15 min". So the drains are cumulative:
    # +0, +1, +6 — and the fifteen minutes are measured from the third.
    _mode(sandbox_client, "down")
    inv4 = _post(
        db,
        tape,
        PartnerRole.AR,
        kind=DocumentKind.INVOICE,
        partner_id=tape.walk_in.id,
        document_date=TAPE_DAY,
        description="INV-4",
        payment_method=PaymentMethod.CASH,
        lines=(
            documents_service.LineInput(
                item_id=tape.s.id, quantity=D(1), unit_price=D(10_000)
            ),
        ),
    )
    db.flush()
    _expect("5", "INV-4 net", D(10_000), inv4.net_amount)
    _expect("5", "INV-4 tax", D(1_800), inv4.tax_amount)
    _expect("5", "INV-4 gross", D(11_800), inv4.total_amount)

    start = datetime.now(UTC)
    third_attempt_at = start + timedelta(minutes=6)
    for offset in (0, 1, 6):
        _drain(db, tape, sandbox_client, at=start + timedelta(minutes=offset))
    sale4 = _row_for(db, tape, inv4, FiscalOutboxKind.SALE)
    _expect("5", "attempts", 3, sale4.attempts)
    _expect("5", "status", FiscalOutboxStatus.QUEUED, sale4.status)
    _expect(
        "5",
        "next attempt in minutes",
        15,
        round((sale4.next_attempt_at - third_attempt_at).total_seconds() / 60),
    )
    with pytest.raises(LedgerStateError) as refused:
        printing.receipt_block(db, company_id, inv4.id)
    _expect("5", "print while queued", "fiscal_receipt_pending", refused.value.code)

    _mode(sandbox_client, "up")
    _drain(db, tape, sandbox_client, at=start + timedelta(minutes=30))
    _expect("5", "INV-4 receipt", "4/5 NS", _counter(_receipt_of(db, tape, inv4)))
    _after_every_row(db, tape, "5")

    # --- Row 6: the answer is lost; the row is verified and a receipt attached -------------
    # `accept_then_timeout` registers the sale and then fails to answer, which is the only
    # honest way to reach `unknown`: RRA is holding a sale Vinea has no receipt for, and a
    # resend would be the duplicate the whole policy exists to prevent.
    _mode(sandbox_client, "accept_then_timeout")
    inv5 = _post(
        db,
        tape,
        PartnerRole.AR,
        kind=DocumentKind.INVOICE,
        partner_id=tape.walk_in.id,
        document_date=TAPE_DAY,
        description="INV-5",
        payment_method=PaymentMethod.CASH,
        lines=(
            documents_service.LineInput(
                item_id=tape.e.id, quantity=D(1), unit_price=D(1000)
            ),
        ),
    )
    db.flush()
    _drain(db, tape, sandbox_client)
    sale5 = _row_for(db, tape, inv5, FiscalOutboxKind.SALE)
    _expect("6", "row after the lost answer", FiscalOutboxStatus.UNKNOWN, sale5.status)
    _expect(
        "6",
        "device queue blocked",
        sale5.id,
        outbox_service.head_row(db, company_id, device.id).id,
    )

    _mode(sandbox_client, "up")
    held = sandbox_client.get("/_sandbox/ledger").json()
    ledger_of_device = next(iter(held.values()))
    # **Six, not five.** The tape's row reads "lastSaleInvcNo 5 >= 5", and its own numbers say
    # otherwise: sales and refunds share the `FIS` run (row 3's credit note is `invc_no 3`), so
    # after five invoices and one credit note the device holds 6 — which is also what row 6's
    # own expected receipt `5/6 NS` says, `rcptNo 5` within NS over `totRcptNo 6` across types.
    # The rule the row is about is `held >= ours`, and it is asserted below on the outcome.
    _expect("6", "lastSaleInvcNo", 6, ledger_of_device["last_sale_invc_no"])
    _expect("6", "row invc_no", 6, sale5.invc_no)
    drainer.verify_with_device(
        db, company_id, device, sale5, actor=owner, client=sandbox_client
    )
    _expect("6", "after verify", FiscalOutboxStatus.NEEDS_RECEIPT, sale5.status)

    drainer.attach_receipt(
        db,
        company_id,
        device,
        sale5,
        fields=ledger_of_device["sales"][str(sale5.invc_no)],
        note="read off MyRRA on 10 March",
        actor=owner,
        client=sandbox_client,
    )
    db.flush()
    _expect("6", "after attach", FiscalOutboxStatus.SENT, sale5.status)
    _expect("6", "INV-5 receipt", "5/6 NS", _counter(_receipt_of(db, tape, inv5)))

    _drain(db, tape, sandbox_client)
    _expect("6", "queue resumes", None, outbox_service.head_row(db, company_id, device.id))
    _expect(
        "6",
        "stock_io sarTyCd",
        SALE_OUT,
        _movements_of(db, tape, inv5)[0].payload["sarTyCd"],
    )
    _expect("6", "master E", D(44), _master_quantity(db, tape, tape.e))
    _after_every_row(db, tape, "6")

    # --- Row 7: SIN-1 from S1, 50 x X @ 1 000, unmatched ------------------------------------
    # net 50 000 · tax 9 000 · gross 59 000. Unmatched, so the goods arrive on the invoice and
    # the companion receives them — which is the case the stock report has something to say
    # about.
    sin1 = _post(
        db,
        tape,
        PartnerRole.AP,
        kind=DocumentKind.INVOICE,
        partner_id=tape.supplier.id,
        document_date=TAPE_DAY,
        description="SIN-1",
        reference="S1-0001",
        payment_method=PaymentMethod.CREDIT,
        lines=(
            documents_service.LineInput(
                item_id=tape.x.id,
                quantity=D(50),
                unit_price=D(1000),
                tax_code_id=tape.tax_codes["VAT-IN-18"].id,
            ),
        ),
    )
    db.flush()
    _expect("7", "SIN-1 net", D(50_000), sin1.net_amount)
    _expect("7", "SIN-1 tax", D(9_000), sin1.tax_amount)
    _expect("7", "SIN-1 gross", D(59_000), sin1.total_amount)
    purchase1 = _row_for(db, tape, sin1, FiscalOutboxKind.PURCHASE)
    _expect("7", "regTyCd", "M", purchase1.payload["regTyCd"])
    _expect("7", "pchsTyCd", "N", purchase1.payload["pchsTyCd"])
    _expect("7", "rcptTyCd", "P", purchase1.payload["rcptTyCd"])
    _expect("7", "purchase invcNo", 1, purchase1.invc_no)
    _expect("7", "bucket B", D("59000.00"), D(str(purchase1.payload["taxblAmtB"])))
    _expect("7", "tax B", D("9000.00"), D(str(purchase1.payload["taxAmtB"])))

    _drain(db, tape, sandbox_client)
    movement7 = _movements_of(db, tape, sin1)[0]
    _expect("7", "stock_io sarTyCd", PURCHASE_IN, movement7.payload["sarTyCd"])
    _expect("7", "stock_io qty", D("50.00"), D(str(movement7.payload["itemList"][0]["qty"])))
    _expect("7", "stock_io prc", D("1000.00"), D(str(movement7.payload["itemList"][0]["prc"])))
    _expect("7", "master X", D(119), _master_quantity(db, tape, tape.x))
    _after_every_row(db, tape, "7")

    # --- Row 8: the authority's feed, accepted ---------------------------------------------
    # A purchase somebody else registered against this taxpayer. Accepting it confirms RRA's
    # own record; it raises no AP document, because the feed is not a keying shortcut.
    feed_service.fetch(db, company_id, device, actor=owner, client=sandbox_client)
    db.flush()
    feed_row = db.scalars(
        select(FiscalPurchaseFeedRow).where(FiscalPurchaseFeedRow.company_id == company_id)
    ).one()
    _expect("8", "feed spplrTin", "100000003", feed_row.spplr_tin)
    _expect("8", "feed spplrInvcNo", 77, feed_row.spplr_invc_no)
    _expect("8", "feed taxable B", D("11800.00"), feed_row.total_taxable_amount)
    _expect("8", "feed tax B", D("1800.00"), feed_row.total_tax_amount)

    documents_before = db.scalar(
        select(func.count())
        .select_from(documents_service.PartnerDocument)
        .where(documents_service.PartnerDocument.company_id == company_id)
    )
    decision = feed_service.accept(db, company_id, feed_row, actor=owner)
    db.flush()
    _expect("8", "decision", FiscalFeedDecision.ACCEPTED, feed_row.decision)
    confirmation = decision.confirmation
    _expect("8", "regTyCd", "A", confirmation.payload["regTyCd"])
    _expect("8", "pchsSttsCd", "02", confirmation.payload["pchsSttsCd"])
    _expect("8", "spplrInvcNo", 77, confirmation.payload["spplrInvcNo"])
    _expect(
        "8",
        "watermark advanced",
        True,
        bool(device.watermarks.get("purchases")),
    )
    _expect(
        "8",
        "no AP document",
        documents_before,
        db.scalar(
            select(func.count())
            .select_from(documents_service.PartnerDocument)
            .where(documents_service.PartnerDocument.company_id == company_id)
        ),
    )
    _drain(db, tape, sandbox_client)
    _after_every_row(db, tape, "8")

    # --- Row 9: close the day → Z-1 for D00 -------------------------------------------------
    # Six receipts were signed today. Worked by hand from what each declared:
    #   NS  INV-1 50 400 · INV-2 6 372 · INV-3 62 304 · INV-4 11 800 · INV-5 1 000 = 131 876
    #   NR  CRN-1 4 720
    #   B   NS 35 400 + 6 372 + 62 304 + 11 800 = 115 876, tax 5 400 + 972 + 9 504 + 1 800
    #                                                          = 17 676 · NR 4 720 / 720
    #   A   NS 5 000 (INV-1's E) + 1 000 (INV-5) = 6 000
    #   C   NS 10 000 (INV-1's Z)
    #   credit  INV-1 50 400 + INV-3 62 304 = 112 704 · cash  6 372 + 11 800 + 1 000 = 19 172
    #   copies  1 print of INV-1, 50 400
    zed = daily_service.close_day(db, company_id, device.id, actor=owner)
    db.flush()
    figures = zed.figures
    _expect("9", "Z number", "Z-000001", zed.number)
    _expect("9", "NS count", 5, figures["ns_count"])
    _expect("9", "NS gross", D("131876.00"), D(figures["ns_gross"]))
    _expect("9", "NR count", 1, figures["nr_count"])
    _expect("9", "NR gross", D("4720.00"), D(figures["nr_gross"]))
    _expect("9", "B taxable NS", D("115876.00"), D(figures["classes"]["B"]["taxable_ns"]))
    _expect("9", "B tax NS", D("17676.00"), D(figures["classes"]["B"]["tax_ns"]))
    _expect("9", "B taxable NR", D("4720.00"), D(figures["classes"]["B"]["taxable_nr"]))
    _expect("9", "B tax NR", D("720.00"), D(figures["classes"]["B"]["tax_nr"]))
    _expect("9", "A taxable NS", D("6000.00"), D(figures["classes"]["A"]["taxable_ns"]))
    _expect("9", "C taxable NS", D("10000.00"), D(figures["classes"]["C"]["taxable_ns"]))
    _expect("9", "credit", D("112704.00"), D(figures["by_payment_method"]["credit"]))
    _expect("9", "cash", D("19172.00"), D(figures["by_payment_method"]["cash"]))
    _expect("9", "copies count", 1, figures["copies_count"])
    _expect("9", "copies gross", D("50400.00"), D(figures["copies_gross"]))
    # §19.1 prints the day's discounts. One line in the day carried one: row 2's 10 % off
    # three bottles, `splyAmt 7 080 − taxblAmt 6 372`. Everything else was undiscounted, so
    # the day's figure is that line's and nothing else.
    _expect("9", "discounts", D("708.00"), D(figures["discounts"]))
    # 10 + 1 + 5 + 2 (INV-1) + 3 (INV-2) + 20 (INV-3) + 1 (INV-4) + 1 (INV-5) = 43 sold,
    # against 2 returned on CRN-1.
    _expect("9", "items sold", D("43.00"), D(figures["items_ns"]))
    _expect("9", "items returned", D("2.00"), D(figures["items_nr"]))
    _expect(
        "9",
        "refunds by method",
        D("4720.00"),
        D(figures["refunds_by_payment_method"]["credit"]),
    )

    after = daily_service.x_report(db, company_id, device.id)
    _expect("9", "X after the close — NS", 0, after.figures.ns_count)
    _expect("9", "X after the close — NR", 0, after.figures.nr_count)
    _expect("9", "X after the close — gross", D("0.00"), after.figures.ns_gross)
    _expect("9", "X after the close — items", D("0.00"), after.figures.items_ns)
    _after_every_row(db, tape, "9")

    # --- Row 10: the VAT return for March ---------------------------------------------------
    # Standard-rated sales, from the ledger rather than from the wire:
    #   INV-1 X 20 000 + S 10 000 · INV-2 5 400 · INV-3 52 800 · INV-4 10 000 · CRN-1 −4 000
    #   = 94 200, VAT 5 400 + 972 + 9 504 + 1 800 − 720 = 16 956
    #   zero-rated 10 000 (INV-1's Z) · exempt 5 000 (INV-1's E) + 1 000 (INV-5) = 6 000
    #   purchases 50 000 / 9 000 (SIN-1) · imports 0 · net payable 16 956 − 9 000 = 7 956
    march = vat_service.compute(db, company_id, period_from=MARCH_FROM, period_to=MARCH_TO)
    _expect("10", "standard sales base", D(94_200), march.sales_standard[0])
    _expect("10", "standard sales VAT", D(16_956), march.sales_standard[1])
    _expect("10", "zero-rated sales", D(10_000), march.sales_zero_rated)
    _expect("10", "exempt sales", D(6_000), march.sales_exempt)
    _expect("10", "standard purchases base", D(50_000), march.purchases_standard[0])
    _expect("10", "standard purchases VAT", D(9_000), march.purchases_standard[1])
    _expect("10", "imports", D(0), march.purchases_imports[1])
    _expect("10", "net payable", D(7_956), march.net_payable)

    ties = {tie.code: tie for tie in march.ties}
    # 2200 is a liability credited on declaration, so its movement is negative; 1400 is an
    # asset debited on the input side. The tie holds when the movement is entirely declared —
    # `difference` zero with nothing untagged beneath it.
    _expect("10", "2200 movement", D(-16_956), ties["2200"].movement)
    _expect("10", "2200 difference", D(0), ties["2200"].difference)
    _expect("10", "1400 movement", D(9_000), ties["1400"].movement)
    _expect("10", "1400 difference", D(0), ties["1400"].difference)
    _expect("10", "untagged", [], [row for tie in march.ties for row in tie.untagged])
    _expect("10", "every tie reconciled", True, all(tie.reconciled for tie in march.ties))
    _after_every_row(db, tape, "10")

    # --- Row 11: file it --------------------------------------------------------------------
    # The settlement moves the declared liability off the two VAT accounts onto 2250:
    #   Dr 2200 +16 956 · Cr 1400 −9 000 · Cr 2250 −7 956
    before_2200 = _balance(db, tape, "2200")
    before_1400 = _balance(db, tape, "1400")
    # "the last entry" is the last one that had **arrived when the return was computed**. The
    # settlement the filing itself posts is a later id and is deliberately below the mark: it
    # is not something the return declares, and a mark above it would make the next return read
    # it as a late entry.
    last_entry_id = db.scalar(
        select(func.max(JournalEntry.id)).where(JournalEntry.company_id == company_id)
    )
    filed = vat_service.file_return(
        db, company_id, period_from=MARCH_FROM, period_to=MARCH_TO, actor=owner
    )
    db.flush()
    _expect("11", "return number", "VATR-000001", filed.number)
    _expect("11", "2200 settlement", D(16_956), _balance(db, tape, "2200") - before_2200)
    _expect("11", "1400 settlement", D(-9_000), _balance(db, tape, "1400") - before_1400)
    _expect("11", "2250 settlement", D(-7_956), _balance(db, tape, "2250"))
    _expect("11", "high water", last_entry_id, filed.high_water_entry_id)
    as_filed = dict(filed.figures)

    # **Beyond the brief's row, and free here**: a filed range may not be filed over again.
    # `test_a_range_may_not_overlap_a_filed_return` pins the one-day boundary; this asserts it
    # on the state the tape has already built, so the refusal is proved against a return with
    # real figures behind it rather than against an empty month.
    with pytest.raises(LedgerStateError) as overlapping:
        vat_service.file_return(
            db,
            company_id,
            period_from=date(YEAR, 3, 15),
            period_to=date(YEAR, 4, 15),
            actor=owner,
        )
    _expect("11", "an overlapping range", "vat_period_filed", overlapping.value.code)
    _after_every_row(db, tape, "11")

    # --- Row 12: a backdated correction into the filed month --------------------------------
    # Dr 1500 1 180 · Cr 4100 1 000 (VAT-OUT-18) · Cr 2200 180. Three lines because a manual
    # journal is taken as keyed: the engine derives a tax line for a *document*, not for a
    # journal somebody wrote.
    code = tape.tax_codes["VAT-OUT-18"]
    posting.post(
        db,
        ManualJournal(
            entry_date=LATE_DAY,
            description="Invoice missed at the month end",
            lines=(
                LineSpec(amount=D(1180), gl_account_id=tape.acct("1500")),
                LineSpec(
                    amount=D(-1000),
                    gl_account_id=tape.acct("4100"),
                    tax_code_id=code.id,
                    tax_amount=D(-180),
                ),
                LineSpec(
                    amount=D(-180), gl_account_id=tape.acct("2200"), tax_code_id=code.id
                ),
            ),
        ),
        company_id=company_id,
        actor=owner,
    )
    db.flush()
    _expect("12", "filed return unchanged", as_filed, dict(filed.figures))
    _expect("12", "filed output VAT", D(16_956), D(as_filed["sections"]["output_vat"]))

    april = vat_service.compute(db, company_id, period_from=APRIL_FROM, period_to=APRIL_TO)
    # One journal entry, listed **per line** — `_late` puts the base on the revenue line and
    # the tax on the tax-account line, the same shape `_accumulate` reads, so the row count is
    # two and the figures the tape names are their totals.
    _expect("12", "late entries", 1, len({row.entry_id for row in april.late_entries}))
    _expect("12", "late base", D(1_000), sum((row.base for row in april.late_entries), ZERO))
    _expect("12", "late tax", D(180), sum((row.tax for row in april.late_entries), ZERO))
    _expect(
        "12",
        "late return named",
        {"VATR-000001"},
        {row.filed_return_number for row in april.late_entries},
    )
    _expect("12", "2200 on the trial balance", D(-180), _balance(db, tape, "2200") - (
        before_2200 + D(16_956)
    ))
    _after_every_row(db, tape, "12")

    # --- Row 13: the FX revaluation at month end --------------------------------------------
    # INV-3 is open at USD 47.20, booked at 1 320 → carrying 62 304. At 1 350 it is 63 720,
    # so the unrealized gain is 1 416: Dr 1290, Cr 4410. Never 1200 — the control account's
    # balance is Σ open items at booking rates, which is P4's invariant.
    run = revaluation_service.post_revaluation(
        db,
        company_id,
        revaluation_date=MARCH_TO,
        role=FxRevaluationRole.AR,
        actor=owner,
    )
    db.flush()
    before_1200 = _balance(db, tape, "1200")
    lines = revaluation_service.lines_of(db, company_id, run.id)
    _expect("13", "revaluation lines", 1, len(lines))
    _expect("13", "open amount", D("47.20"), lines[0].open_amount)
    _expect("13", "carrying", D(62_304), lines[0].carrying_base)
    _expect("13", "revalued", D(63_720), lines[0].revalued_base)
    _expect("13", "gain", D(1_416), lines[0].difference)
    _expect("13", "run number", "FXR-000001", run.number)
    _expect("13", "1290 at month end", D(1_416), _balance_on(db, tape, "1290", MARCH_TO))
    _expect("13", "4410 at month end", D(-1_416), _balance_on(db, tape, "4410", MARCH_TO))
    _expect("13", "1290 after the mirror", D(0), _balance(db, tape, "1290"))
    _expect("13", "4410 after the mirror", D(0), _balance(db, tape, "4410"))
    _expect("13", "1200 untouched", before_1200, _balance(db, tape, "1200"))

    with pytest.raises(LedgerStateError) as refused:
        revaluation_service.post_revaluation(
            db,
            company_id,
            revaluation_date=MARCH_TO,
            role=FxRevaluationRole.AR,
            actor=owner,
        )
    _expect("13", "a second run", "fx_revaluation_exists", refused.value.code)
    _after_every_row(db, tape, "13")

    # --- Row 14: reverse INV-4, reason 07 ---------------------------------------------------
    # RRA cannot un-sign a sale, so the reversal of a signed invoice owes a **refund**: the P4
    # reversal posts, and a second receipt is issued against the same document.
    #
    # **A real second first.** `sdcDateTime` is `yyyyMMddHHmmss`, so a fiscal day is bounded to
    # the second and Z-2 opens *exclusively* at Z-1's `to_at`. A receipt the sandbox stamps
    # inside that same second falls in neither Z — the resolution limit `_floor_second`
    # documents — so the tape waits it out rather than pretending it does not exist.
    time.sleep(1.05)
    documents_service.reverse_document(
        db,
        inv4,
        on_date=REVERSAL_DAY,
        reason="keyed twice",
        refund_reason=CANCELLATION_REASON,
        actor=owner,
    )
    db.flush()
    refund4 = _row_for(db, tape, inv4, FiscalOutboxKind.REFUND)
    _expect("14", "rcptTyCd", "R", refund4.payload["rcptTyCd"])
    _expect("14", "orgInvcNo", 5, refund4.payload["orgInvcNo"])
    _expect("14", "rfdRsnCd", CANCELLATION_REASON, refund4.payload["rfdRsnCd"])
    _drain(db, tape, sandbox_client)
    _expect(
        "14",
        "reversal receipt",
        "2/7 NR",
        _counter(_receipt_of(db, tape, inv4, FiscalReceiptType.NORMAL_REFUND)),
    )

    # A second Z, one second after the first so the two ranges tile rather than overlap — a
    # fiscal day has a second's resolution because `sdcDateTime` does.
    zed2 = daily_service.close_day(
        db,
        company_id,
        device.id,
        actor=owner,
        now=datetime.now(UTC) + timedelta(seconds=1),
    )
    db.flush()
    _expect("14", "Z-2 number", "Z-000002", zed2.number)
    _expect("14", "Z-2 NR count", 1, zed2.figures["nr_count"])
    _expect("14", "Z-2 NR gross", D("11800.00"), D(zed2.figures["nr_gross"]))
    _expect("14", "Z-2 NS count", 0, zed2.figures["ns_count"])
    _after_every_row(db, tape, "14")

    # --- Row 15: a refund cannot be refunded ------------------------------------------------
    with pytest.raises(LedgerStateError) as refused:
        documents_service.reverse_document(
            db, crn1, on_date=REVERSAL_DAY, reason="undo the credit note", actor=owner
        )
    _expect("15", "reversing a signed refund", "fiscal_refund_irreversible", refused.value.code)
    _after_every_row(db, tape, "15")

    # --- Row 16: a TIN with no purchase code ------------------------------------------------
    with pytest.raises(PostingError) as refused_code:
        _post(
            db,
            tape,
            PartnerRole.AR,
            kind=DocumentKind.INVOICE,
            partner_id=tape.customer.id,
            document_date=TAPE_DAY,
            description="INV-6",
            payment_method=PaymentMethod.CREDIT,
            lines=(
                documents_service.LineInput(
                    item_id=tape.x.id, quantity=D(1), unit_price=D(2000)
                ),
            ),
        )
    _expect("16", "no purchase code", "purchase_code_required", refused_code.value.code)
    _expect(
        "16",
        "refused on the field",
        ["purchase_code"],
        sorted(refused_code.value.field_errors),
    )
    _after_every_row(db, tape, "16")

    # --- Row 17: RRA refuses the sale, and the reversal cancels the row ---------------------
    # `884` is an unknown TIN: a refusal RRA will give again, so the row is `failed` and does
    # not back off. Everything behind it on the device waits, which is the point.
    _mode(sandbox_client, "reject", "884")
    inv7 = _post(
        db,
        tape,
        PartnerRole.AR,
        kind=DocumentKind.INVOICE,
        partner_id=tape.customer.id,
        document_date=TAPE_DAY,
        description="INV-7",
        purchase_code=PURCHASE_CODE,
        payment_method=PaymentMethod.CREDIT,
        lines=(
            documents_service.LineInput(
                item_id=tape.x.id, quantity=D(1), unit_price=D(2000)
            ),
        ),
    )
    db.flush()
    _drain(db, tape, sandbox_client)
    sale7 = _row_for(db, tape, inv7, FiscalOutboxKind.SALE)
    _expect("17", "row status", FiscalOutboxStatus.FAILED, sale7.status)
    _expect("17", "result code", "884", sale7.last_result_cd)
    _expect(
        "17",
        "queue blocked",
        sale7.id,
        outbox_service.head_row(db, company_id, device.id).id,
    )

    _mode(sandbox_client, "up")
    refunds_before = len(_rows(db, tape, FiscalOutboxKind.REFUND))
    documents_service.reverse_document(
        db, inv7, on_date=REVERSAL_DAY, reason="RRA refused the TIN", actor=owner
    )
    db.flush()
    _expect("17", "row cancelled", FiscalOutboxStatus.CANCELLED, sale7.status)
    _expect(
        "17",
        "no refund queued",
        refunds_before,
        len(_rows(db, tape, FiscalOutboxKind.REFUND)),
    )
    _expect("17", "reversal posted", "reversed", str(inv7.status))
    _drain(db, tape, sandbox_client)
    _expect("17", "queue resumes", None, outbox_service.head_row(db, company_id, device.id))
    _after_every_row(db, tape, "17")

    # --- Row 18: negative stock cannot be allowed while a device is live --------------------
    # CIS §7.30: no receipt for goods the stock does not hold, and `block` is what makes that
    # true. The refusal belongs on the screen that moves the setting, not only on activation.
    with pytest.raises(device_service.FiscalSetupError) as refused_policy:
        inventory_masters.update_inventory_defaults(
            db,
            company_id,
            {"negative_stock_policy": NegativeStockPolicy.ALLOW.value},
            actor=owner,
        )
    _expect("18", "allowing negative stock", "fiscal_requires_block", refused_policy.value.code)
    _after_every_row(db, tape, "18")


# --- The same rows under the other route profile ----------------------------------------------


def test_the_tape_runs_under_the_osdc_profile(
    db: Session, tape: Tape, sandbox_client: httpx.Client
) -> None:
    """Rows 0 to 3 again, against a device on the `osdc` profile.

    This is what proves the route table and `normalize_receipt()` rather than one profile's
    happy path: OSDC speaks the same payload vocabulary down **different paths**, adds `cmcKey`
    to every request, and answers a sale with `curRcptNo / totRcptNo / intrlData / rcptSign /
    sdcDateTime` where VSDC answers `rcptNo / vsdcRcptPbctDate / sdcId / mrcNo`. Everything
    above the adapter has to be unable to tell — so the assertions here are the same literals
    the `vsdc` run asserts, read off the same neutral columns.
    """
    company_id = tape.company_id
    owner = tape.owner
    device = tape.device
    device.profile = FiscalProfile.OSDC
    db.flush()
    adapter = registry.adapter_for("RW", client=sandbox_client)

    device_service.initialize_device(
        db, company_id, device, actor=owner, client=sandbox_client
    )
    _expect("osdc 0", "device status", FiscalDeviceStatus.ACTIVE, device.status)
    _expect("osdc 0", "sdc_id", "SDC010000005", device.sdc_id)
    _expect("osdc 0", "mrc_no", "WIS01006230", device.mrc_no)

    device_service.sync_codes(db, company_id, device, actor=owner, client=sandbox_client)
    device_service.sync_item_classes(
        db, company_id, device, actor=owner, client=sandbox_client
    )
    _expect(
        "osdc 0",
        "code class 04",
        ["A", "B", "C", "D"],
        sorted(
            row.code
            for row in db.scalars(
                select(FiscalCode).where(
                    FiscalCode.company_id == company_id, FiscalCode.code_class == "04"
                )
            )
        ),
    )

    for item in (tape.x, tape.s, tape.e, tape.z):
        fiscal_items.ensure_registered_for_report(
            db, company_id, device=device, adapter=adapter, item=item, actor=owner
        )
    db.flush()
    _expect("osdc 0", "item_cd X", "RW2NTXU0000001", _item_code(db, tape, tape.x))
    _expect("osdc 0", "item_cd S", "RW3NTXU0000002", _item_code(db, tape, tape.s))
    _expect("osdc 0", "item_cd E", "RW2NTXU0000003", _item_code(db, tape, tape.e))
    _expect("osdc 0", "item_cd Z", "RW2NTXU0000004", _item_code(db, tape, tape.z))

    grn_service.post_grn(
        db,
        company_id,
        grn_service.GrnInput(
            partner_id=tape.supplier.id,
            grn_date=TAPE_DAY,
            description="GRN-1 opening stock",
            warehouse_id=tape.main_warehouse.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=tape.x.id, quantity=D(100), unit_cost=D(1000)
                ),
                grn_service.GrnLineInput(
                    item_id=tape.e.id, quantity=D(50), unit_cost=D(500)
                ),
                grn_service.GrnLineInput(
                    item_id=tape.z.id, quantity=D(20), unit_cost=D(3000)
                ),
            ),
        ),
        actor=owner,
    )
    db.flush()
    _drain(db, tape, sandbox_client)
    _expect("osdc 0", "master X", D(100), _master_quantity(db, tape, tape.x))
    _after_every_row(db, tape, "osdc 0")

    inv1 = _post(
        db,
        tape,
        PartnerRole.AR,
        kind=DocumentKind.INVOICE,
        partner_id=tape.customer.id,
        document_date=TAPE_DAY,
        description="INV-1",
        purchase_code=PURCHASE_CODE,
        payment_method=PaymentMethod.CREDIT,
        lines=(
            documents_service.LineInput(
                item_id=tape.x.id, quantity=D(10), unit_price=D(2000)
            ),
            documents_service.LineInput(
                item_id=tape.s.id, quantity=D(1), unit_price=D(10_000)
            ),
            documents_service.LineInput(
                item_id=tape.e.id, quantity=D(5), unit_price=D(1000)
            ),
            documents_service.LineInput(
                item_id=tape.z.id, quantity=D(2), unit_price=D(5000)
            ),
        ),
    )
    db.flush()
    _expect("osdc 1", "INV-1 gross", D(50_400), inv1.total_amount)
    sale1 = _row_for(db, tape, inv1, FiscalOutboxKind.SALE)
    _expect("osdc 1", "bucket B", D("35400.00"), D(str(sale1.payload["taxblAmtB"])))
    _expect("osdc 1", "tax B", D("5400.00"), D(str(sale1.payload["taxAmtB"])))
    # The key is added at send, never frozen into the row — decision 2, and the redaction test
    # walks every stored payload for it.
    _expect("osdc 1", "no key in the stored payload", None, sale1.payload.get("cmcKey"))
    _drain(db, tape, sandbox_client)
    _expect("osdc 1", "INV-1 receipt", "1/1 NS", _counter(_receipt_of(db, tape, inv1)))
    _after_every_row(db, tape, "osdc 1")

    inv2 = _post(
        db,
        tape,
        PartnerRole.AR,
        kind=DocumentKind.INVOICE,
        partner_id=tape.walk_in.id,
        document_date=TAPE_DAY,
        description="INV-2",
        payment_method=PaymentMethod.CASH,
        lines=(
            documents_service.LineInput(
                item_id=tape.x.id,
                quantity=D(3),
                unit_price=D(2000),
                discount_percent=D(10),
            ),
        ),
    )
    db.flush()
    _expect("osdc 2", "INV-2 gross", D(6_372), inv2.total_amount)
    _drain(db, tape, sandbox_client)
    _expect("osdc 2", "INV-2 receipt", "2/2 NS", _counter(_receipt_of(db, tape, inv2)))
    _expect("osdc 2", "master X", D(87), _master_quantity(db, tape, tape.x))
    _after_every_row(db, tape, "osdc 2")

    crn1 = _post(
        db,
        tape,
        PartnerRole.AR,
        kind=DocumentKind.CREDIT_NOTE,
        partner_id=tape.customer.id,
        document_date=TAPE_DAY,
        description="CRN-1",
        purchase_code=PURCHASE_CODE,
        payment_method=PaymentMethod.CREDIT,
        refund_of_document_id=inv1.id,
        refund_reason=REFUND_REASON,
        lines=(
            documents_service.LineInput(
                item_id=tape.x.id,
                quantity=D(2),
                unit_price=D(2000),
                returns_line_id=_line_of(inv1, tape.x.id).id,
            ),
        ),
    )
    db.flush()
    _expect("osdc 3", "CRN-1 gross", D(4_720), crn1.total_amount)
    refund1 = _row_for(db, tape, crn1, FiscalOutboxKind.REFUND)
    _expect("osdc 3", "rcptTyCd", "R", refund1.payload["rcptTyCd"])
    _expect("osdc 3", "orgInvcNo", 1, refund1.payload["orgInvcNo"])
    _drain(db, tape, sandbox_client)
    # The OSDC sales response spells its counters `curRcptNo` and stamps `sdcDateTime`; the
    # receipt row below is built from the same neutral `FiscalReceiptData` the VSDC run
    # produced, which is the whole of what `normalize_receipt()` is for.
    _expect("osdc 3", "CRN-1 receipt", "1/3 NR", _counter(_receipt_of(db, tape, crn1)))
    _expect("osdc 3", "master X", D(89), _master_quantity(db, tape, tape.x))
    _after_every_row(db, tape, "osdc 3")
