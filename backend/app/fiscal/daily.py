"""The day's fiscal report: X and Z (decision 11).

**An X is a question, a Z is an act.** X is the same computation as Z over the range from the
last Z to now, and is never stored — asking what the day looks like so far must not change what
the day is. Z is a stored close: it takes a number from the device's `FZR` run, freezes its
figures, and becomes the `from_at` of the next one.

**A receipt decides what is in the day; the ledger decides what it is worth.** A Z states what
the authority signed, so the population of a day is `fiscal_receipts` and nothing else — a sale
with no receipt is no part of it, however posted it is. The money, though, is read off the
documents those receipts were issued against, exactly as decision 11 words it ("computed from
`fiscal_receipts` **and their documents**") and exactly as the VAT return of decision 12 reads
its own figures. Two reasons. It ties to the ledger, which is the property every other figure
in this phase has. And it keeps a revenue authority's field names out of a module above the
adapter — `tests/fiscal/test_boundary.py` refuses them, rightly: this same computation has to
serve a second country whose payload spells none of it alike, and the four tax classes reach it
through `tax_codes.fiscal_tax_type`, which is Vinea's own mapping rather than anybody's wire
format.

**Pending queue rows are not receipts.** A sale still queued has no receipt and is no part of
the day's totals, so a Z records how many rows were still waiting when it was taken. A Z that
closed over an unsent sale says so on its face rather than silently understating the day.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from starlette.requests import Request

from app.core.errors import NotFoundError
from app.kernel.errors import LedgerStateError
from app.kernel.sequences import DocType, claim_number
from app.models.fiscalization import (
    FiscalDailyReport,
    FiscalDevice,
    FiscalDeviceStatus,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalReceipt,
    FiscalReceiptType,
)
from app.models.journal import JournalLine
from app.models.subledger import PartnerDocument
from app.models.tax import TaxCode
from app.models.user import User
from app.services.audit import record_audit

#: The wire resolution. Every amount on a receipt is `NUMBER 18,2`, so a day's totals are held
#: and stored at two places — otherwise a figure's *string* form depends on how a JSON number
#: happened to round-trip out of JSONB, and a stored Z would not compare equal to itself.
CENTS = Decimal("0.01")
MONEY_ZERO = Decimal("0.00")


@dataclass
class ClassTotals:
    """One tax class, split by receipt type — §19.1 prints the two apart."""

    taxable_ns: Decimal = MONEY_ZERO
    tax_ns: Decimal = MONEY_ZERO
    taxable_nr: Decimal = MONEY_ZERO
    tax_nr: Decimal = MONEY_ZERO
    rate: Decimal = MONEY_ZERO

    def as_dict(self) -> dict[str, str]:
        return {
            "taxable_ns": str(self.taxable_ns),
            "tax_ns": str(self.tax_ns),
            "taxable_nr": str(self.taxable_nr),
            "tax_nr": str(self.tax_nr),
            "rate": str(self.rate),
        }


@dataclass
class DailyFigures:
    """The §19.1 content of a day, computed from the receipts in a range."""

    ns_count: int = 0
    ns_gross: Decimal = MONEY_ZERO
    nr_count: int = 0
    nr_gross: Decimal = MONEY_ZERO
    items_count: int = 0
    copies_count: int = 0
    copies_gross: Decimal = MONEY_ZERO
    queued_rows: int = 0
    classes: dict[str, ClassTotals] = field(default_factory=dict)
    by_payment_method: dict[str, Decimal] = field(default_factory=dict)

    @property
    def total_tax(self) -> Decimal:
        return sum(
            (totals.tax_ns + totals.tax_nr for totals in self.classes.values()), MONEY_ZERO
        )

    @property
    def net_gross(self) -> Decimal:
        """Sales less refunds — what the day actually took."""
        return self.ns_gross - self.nr_gross

    def as_dict(self) -> dict:
        return {
            "ns_count": self.ns_count,
            "ns_gross": str(self.ns_gross),
            "nr_count": self.nr_count,
            "nr_gross": str(self.nr_gross),
            "net_gross": str(self.net_gross),
            "total_tax": str(self.total_tax),
            "items_count": self.items_count,
            "copies_count": self.copies_count,
            "copies_gross": str(self.copies_gross),
            "queued_rows": self.queued_rows,
            "classes": {name: totals.as_dict() for name, totals in sorted(self.classes.items())},
            "by_payment_method": {
                code: str(amount) for code, amount in sorted(self.by_payment_method.items())
            },
        }


@dataclass(frozen=True)
class DailyReportView:
    """An X or a Z, in the same shape — they differ by whether anybody stored it."""

    device_id: int
    kind: str
    from_at: datetime
    to_at: datetime
    figures: DailyFigures
    number: str | None = None
    report_no: int | None = None


def x_report(
    db: Session, company_id: int, device_id: int, *, now: datetime | None = None
) -> DailyReportView:
    """The day so far. Computed, shown, and forgotten."""
    device = _device(db, company_id, device_id)
    from_at, include_from = _opened_at(db, company_id, device)
    to_at = _floor_second(now or datetime.now(UTC))
    return DailyReportView(
        device_id=device.id,
        kind="X",
        from_at=from_at,
        to_at=to_at,
        figures=compute(
            db,
            company_id,
            device.id,
            from_at=from_at,
            to_at=to_at,
            include_from=include_from,
        ),
    )


def close_day(
    db: Session,
    company_id: int,
    device_id: int,
    *,
    actor: User,
    now: datetime | None = None,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> FiscalDailyReport:
    """Take the Z. Never commits.

    The range runs from the last Z (or the device's activation, for the first one) to now, so
    the closes of a device tile its whole life with no gap and no overlap — which is what makes
    "the sum of the Zs" a statement about the device rather than about the days somebody
    happened to close.
    """
    device = _device(db, company_id, device_id)
    from_at, include_from = _opened_at(db, company_id, device)
    to_at = _floor_second(now or datetime.now(UTC))
    if to_at <= from_at:
        raise LedgerStateError(
            "The day was already closed at this instant; nothing has happened since",
            code="fiscal_z_empty_range",
            field_errors={"device_id": ["already closed"]},
        )

    figures = compute(
        db,
        company_id,
        device.id,
        from_at=from_at,
        to_at=to_at,
        include_from=include_from,
    )
    claimed = claim_number(db, company_id, DocType.FISCAL_Z_REPORT, device.branch_id)
    report = FiscalDailyReport(
        company_id=company_id,
        device_id=device.id,
        report_no=claimed.sequence_no,
        number=claimed.number,
        from_at=from_at,
        to_at=to_at,
        figures=figures.as_dict(),
        queued_rows=figures.queued_rows,
        closed_by=actor.id,
        closed_at=to_at,
    )
    db.add(report)
    db.flush()

    record_audit(
        db,
        company_id=company_id,
        action="fiscal_daily_report.close",
        entity="fiscal_daily_report",
        entity_id=report.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={
            "number": report.number,
            "device_id": device.id,
            "from_at": from_at.isoformat(),
            "to_at": to_at.isoformat(),
            "ns_count": figures.ns_count,
            "nr_count": figures.nr_count,
            "queued_rows": figures.queued_rows,
        },
        request=request,
    )
    return report


def compute(
    db: Session,
    company_id: int,
    device_id: int,
    *,
    from_at: datetime,
    to_at: datetime,
    include_from: bool = False,
) -> DailyFigures:
    """The §19.1 arithmetic over the receipts a device issued in a range.

    Closed at the end and normally open at the start (`from_at <` … `<= to_at`), so a receipt
    is counted by exactly one Z however finely the day is sliced: a close's `to_at` becomes the
    next day's `from_at`, and the receipt that landed on the boundary belongs to the earlier of
    the two.

    `include_from` is for the one range with no earlier day to belong to — a device's **first**,
    which opens at its activation. That bound is floored to the second (see `_floor_second`), so
    a receipt issued in the activation second sits exactly *on* it, and an exclusive start would
    drop the first sale a device ever made.
    """
    opens = (
        FiscalReceipt.sdc_datetime >= from_at
        if include_from
        else FiscalReceipt.sdc_datetime > from_at
    )
    receipts = list(
        db.scalars(
            select(FiscalReceipt)
            .where(
                FiscalReceipt.company_id == company_id,
                FiscalReceipt.device_id == device_id,
                opens,
                FiscalReceipt.sdc_datetime <= to_at,
            )
            .order_by(FiscalReceipt.id)
        )
    )

    documents = _documents_of(db, company_id, [receipt.document_id for receipt in receipts])
    classes = _class_totals_by_document(db, company_id, list(documents.values()))

    figures = DailyFigures()
    for receipt in receipts:
        document = documents.get(receipt.document_id)
        if document is None:
            continue
        _absorb(figures, receipt, document, classes.get(document.id, {}))

    figures.queued_rows = _queued_rows(db, company_id, device_id)
    return figures


def _absorb(
    figures: DailyFigures,
    receipt: FiscalReceipt,
    document: PartnerDocument,
    classes: dict[str, tuple[Decimal, Decimal, Decimal]],
) -> None:
    """Add one signed receipt, and the document it was issued against, to the day.

    **Why the document and not the payload.** A Z has to agree with what the authority signed,
    and the receipt is what says a document *was* signed and when — which is why the population
    of a day is `fiscal_receipts` and nothing else. But the money is read off the ledger, the
    way decision 11 says ("computed from `fiscal_receipts` **and their documents**") and the way
    every other figure in this phase is. Reading it off the stored request instead would put a
    revenue authority's field names in a module above the adapter, which
    `test_no_rra_field_name_appears_in_code_outside_the_rwanda_package` refuses — and rightly:
    the same Z has to be computable for a second country whose payload spells none of it alike.
    """
    gross = _money(document.base_total_amount)
    is_refund = receipt.receipt_type is FiscalReceiptType.NORMAL_REFUND

    if is_refund:
        figures.nr_count += 1
        figures.nr_gross += gross
    else:
        figures.ns_count += 1
        figures.ns_gross += gross
    # The document's own lines. A kit travels to the authority as its parent and its components
    # do not appear on the receipt (decision 3), so a document carrying one counts more lines
    # here than the receipt printed. Stated rather than hidden: the day's item count is what was
    # sold, and the receipt's is what was declared — step 5's tape is where the two are read
    # side by side against a real kit.
    figures.items_count += len(document.lines)

    # A copy is a print, not a receipt: it is counted and its value noted, and it is no part of
    # the day's takings (decision 11 — only one sale was ever declared).
    if receipt.copy_count:
        figures.copies_count += receipt.copy_count
        figures.copies_gross += gross * receipt.copy_count

    for name, (taxable, tax, rate) in classes.items():
        totals = figures.classes.setdefault(name, ClassTotals())
        totals.rate = rate
        if is_refund:
            totals.taxable_nr += taxable
            totals.tax_nr += tax
        else:
            totals.taxable_ns += taxable
            totals.tax_ns += tax

    # Refunds reduce what was taken by the method the original was paid by, which is how a till
    # reconciles: the money went back out the way it came in.
    if document.payment_method is not None:
        method = str(document.payment_method)
        signed = -gross if is_refund else gross
        figures.by_payment_method[method] = (
            figures.by_payment_method.get(method, MONEY_ZERO) + signed
        )


def _documents_of(
    db: Session, company_id: int, document_ids: Sequence[int]
) -> dict[int, PartnerDocument]:
    if not document_ids:
        return {}
    return {
        document.id: document
        for document in db.scalars(
            select(PartnerDocument)
            .where(
                PartnerDocument.company_id == company_id,
                PartnerDocument.id.in_(list(document_ids)),
            )
            .options(selectinload(PartnerDocument.lines))
        )
    }


def _class_totals_by_document(
    db: Session, company_id: int, documents: Sequence[PartnerDocument]
) -> dict[int, dict[str, tuple[Decimal, Decimal, Decimal]]]:
    """Per document, per tax class: (taxable, tax, rate), all in base currency.

    The split is the VAT return's — a line on the code's own tax account **is** the tax, every
    other line carrying the code is base — and the class is `tax_codes.fiscal_tax_type`, which
    is Vinea's own mapping onto the four classes rather than anybody's field name.

    `taxable` is the **gross**: decision 6 sends the VAT-inclusive amount as the taxable one, so
    a class's taxable figure is its net plus its tax. Amounts are taken in absolute value —
    a sale is a credit and a refund is a debit, and a day's report states both as magnitudes
    with the sign carried by which bucket they land in.
    """
    entry_ids = [document.journal_entry_id for document in documents]
    if not entry_ids:
        return {}
    by_entry = {document.journal_entry_id: document.id for document in documents}

    rows = db.execute(
        select(
            JournalLine.entry_id,
            JournalLine.gl_account_id,
            JournalLine.base_amount,
            TaxCode.gl_account_id.label("tax_account_id"),
            TaxCode.fiscal_tax_type,
            TaxCode.rate_pct,
        )
        .join(
            TaxCode,
            (TaxCode.id == JournalLine.tax_code_id)
            & (TaxCode.company_id == JournalLine.company_id),
        )
        .where(
            JournalLine.company_id == company_id,
            JournalLine.entry_id.in_(entry_ids),
        )
    )

    totals: dict[int, dict[str, tuple[Decimal, Decimal, Decimal]]] = {}
    for row in rows:
        if row.fiscal_tax_type is None:
            continue
        document_id = by_entry[row.entry_id]
        name = str(row.fiscal_tax_type)
        taxable, tax, _ = totals.setdefault(document_id, {}).get(
            name, (MONEY_ZERO, MONEY_ZERO, MONEY_ZERO)
        )
        amount = _money(abs(row.base_amount))
        # Both halves of a taxed line add to the taxable figure, because the wire's taxable
        # amount is the gross; only the tax-account half adds to the tax.
        taxable += amount
        if row.tax_account_id is not None and row.gl_account_id == row.tax_account_id:
            tax += amount
        totals[document_id][name] = (taxable, tax, _money(row.rate_pct))
    return totals


def _queued_rows(db: Session, company_id: int, device_id: int) -> int:
    """Rows this device is still holding — anything not `sent` or `cancelled`."""
    terminal = [
        status for status in FiscalOutboxStatus if status.is_terminal
    ]
    return int(
        db.scalar(
            select(func.count(FiscalOutboxRow.id)).where(
                FiscalOutboxRow.company_id == company_id,
                FiscalOutboxRow.device_id == device_id,
                FiscalOutboxRow.status.not_in(terminal),
            )
        )
        or 0
    )


def reports_of(
    db: Session, company_id: int, device_id: int, *, limit: int = 50
) -> Sequence[FiscalDailyReport]:
    return list(
        db.scalars(
            select(FiscalDailyReport)
            .where(
                FiscalDailyReport.company_id == company_id,
                FiscalDailyReport.device_id == device_id,
            )
            .order_by(FiscalDailyReport.report_no.desc())
            .limit(limit)
        )
    )


def _device(db: Session, company_id: int, device_id: int) -> FiscalDevice:
    device = db.scalar(
        select(FiscalDevice).where(
            FiscalDevice.company_id == company_id, FiscalDevice.id == device_id
        )
    )
    if device is None:
        raise NotFoundError("Fiscal device not found")
    if device.status is FiscalDeviceStatus.PENDING:
        raise LedgerStateError(
            "A device that has never been activated has no day to report",
            code="fiscal_device_not_active",
            field_errors={"device_id": ["not activated"]},
        )
    return device


def _opened_at(db: Session, company_id: int, device: FiscalDevice) -> tuple[datetime, bool]:
    """Where this day starts: the last Z's `to_at`, or the device's activation.

    Activation rather than creation, because a device that was registered in March and
    activated in June issued nothing in between, and a first Z whose range began at
    registration would claim to cover months that could hold no receipt.
    """
    last = db.scalar(
        select(FiscalDailyReport.to_at)
        .where(
            FiscalDailyReport.company_id == company_id,
            FiscalDailyReport.device_id == device.id,
        )
        .order_by(FiscalDailyReport.report_no.desc())
        .limit(1)
    )
    if last is not None:
        return last, False
    if device.activated_at is not None:
        return _floor_second(device.activated_at), True
    raise LedgerStateError(
        "The device has no activation time to open its first day from",
        code="fiscal_device_not_active",
        field_errors={"device_id": ["not activated"]},
    )


def _floor_second(moment: datetime) -> datetime:
    """A day's bounds are expressed at the resolution the authority's timestamps have.

    `sdcDateTime` is `yyyyMMddHHmmss` — whole seconds — so a receipt issued in the same second
    a device was activated parses to an instant fractionally *before* `activated_at`, and a
    first day opening at the microsecond would drop it.

    Both bounds are floored, so the boundary sits on the same grid the stamps do. A close taken
    at 10:30:07.4 ends at 10:30:07, which *includes* a receipt stamped 10:30:07 and excludes
    everything after — and the next day opens at that same instant, exclusively. Without it, a
    receipt issued in the second a Z was taken would fall after that Z's `to_at` and before the
    next one's `from_at`: a receipt in no day at all.

    A consequence worth stating: two closes inside one second are an empty range, which
    `close_day` refuses (`fiscal_z_empty_range`) and the table's `to_at > from_at` check would
    refuse anyway. A day has a second's resolution because a receipt does.
    """
    return moment.replace(microsecond=0)


def _money(value: object) -> Decimal:
    """An amount at the wire's resolution.

    `NUMBER 18,2` is what a receipt carries, so a day's figures are held and stored at two
    places. Without it a total's *string* form would depend on the exponent a `NUMERIC(20,6)`
    column happened to hand back, and a stored Z would not compare equal to itself."""
    if value is None or value == "":
        return MONEY_ZERO
    return Decimal(str(value)).quantize(CENTS)
