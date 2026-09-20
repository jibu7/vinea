"""The day's fiscal report: X and Z (decision 11).

**An X is a question, a Z is an act.** X is the same computation as Z over the range from the
last Z to now, and is never stored — asking what the day looks like so far must not change what
the day is. Z is a stored close: it takes a number from the device's `FZR` run, freezes its
figures, and becomes the `from_at` of the next one.

**A Z states what the authority signed.** Both the population of a day and its figures come
from `fiscal_receipts` — the receipts say which documents are in the day and when, and the
payloads they were issued against say what was declared. Not the ledger: on a discounted line
the wire's taxable amount is `splyAmt − dcAmt`, derived from the two-decimal inclusive price,
and **not** the posted gross (`rwanda/builders.py` says so where it computes them). The two
agree on round, undiscounted prices and part company everywhere else, so a Z read off
`journal_lines` would be a summary that disagreed with the receipts it summarises. The VAT
return of decision 12 reads the ledger because a return declares what was *posted*; a Z reads
the receipts because a Z reports what was *declared*. Both are right; they are not the same
question.

**And no field name of the authority's appears here.** The translation is the adapter's, through
`normalize_declared_totals` — the read half of `normalize_receipt`, added for this — so the same
computation serves a second country whose payload spells none of it alike (rule 12, and
`tests/fiscal/test_boundary.py`).

**Pending queue rows are not receipts.** A sale still queued has no receipt and is no part of
the day's totals, so a Z records how many rows were still waiting when it was taken. A Z that
closed over an unsent sale says so on its face rather than silently understating the day.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import NotFoundError
from app.fiscal import registry
from app.fiscal.protocol import DeclaredTotals, FiscalizationAdapter
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
from app.models.subledger import PartnerDocument
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
    #: How many **things** were sold and how many came back — Σ of the receipts' line
    #: quantities, split the way §19.1 splits every other figure on the page. Not the line
    #: count: a four-line invoice of 10 + 1 + 5 + 2 sold eighteen items, and "items" on a day's
    #: report is the question a shopkeeper asks about stock, not about paperwork. (The *line*
    #: count is the receipt's own `ITEMS NUMBER` and stays on the printed receipt, where CIS
    #: §7.27 puts it.)
    items_ns: Decimal = MONEY_ZERO
    items_nr: Decimal = MONEY_ZERO
    copies_count: int = 0
    copies_gross: Decimal = MONEY_ZERO
    #: §19.1 prints the day's discounts, so a Z that could not state them would fail the
    #: checkpoint sheet. Σ of the line discounts the receipts declared.
    discounts: Decimal = MONEY_ZERO
    #: What the same documents are worth **in the ledger**, signed the same way `net_gross` is.
    #: Beside it so the difference below has both sides on the page.
    posted_net: Decimal = MONEY_ZERO
    queued_rows: int = 0
    classes: dict[str, ClassTotals] = field(default_factory=dict)
    #: **Sales by method, and refunds beside them — not netted.** Every other figure on a Z is
    #: split NS from NR, and a payment bucket that quietly nets the two is the one number on
    #: the page a reader cannot take apart again. It also could not be trusted if it were: the
    #: method on the bucket is the *credit note's* own, and a refund keyed cash against an
    #: invoice sold on credit would subtract from a drawer the money never came out of.
    #:
    #: Kept apart, the identity a reader can check is Σ `by_payment_method` == `ns_gross`, and
    #: Σ `refunds_by_payment_method` == `nr_gross`.
    by_payment_method: dict[str, Decimal] = field(default_factory=dict)
    refunds_by_payment_method: dict[str, Decimal] = field(default_factory=dict)

    @property
    def total_tax(self) -> Decimal:
        return sum(
            (totals.tax_ns + totals.tax_nr for totals in self.classes.values()), MONEY_ZERO
        )

    @property
    def net_gross(self) -> Decimal:
        """Sales less refunds — what the day actually took, as declared."""
        return self.ns_gross - self.nr_gross

    @property
    def declared_less_posted(self) -> Decimal:
        """The wire-versus-ledger residue, named rather than left to be discovered.

        A Z reports what was **declared** and a VAT return reports what was **posted**, and on a
        discounted or fractionally priced line those differ: the wire's taxable amount is
        `splyAmt − dcAmt`, extended from a two-decimal inclusive price, while the ledger holds
        the posted gross (P7 decision 6, and `rwanda/builders.py` where it computes them). The
        difference is expected and is nobody's defect — but an accountant reconciling a month of
        Zs against that month's return will find it, and a figure that appears with no name is
        indistinguishable from an error.

        It is stated here, on the document that introduces it, rather than in the return's tie:
        the tie compares a VAT account's movement with the tax lines declared on it, and both
        of those are the ledger's — no wire figure reaches `journal_lines`, so a residue line
        there would be zero in every month and would explain nothing.
        """
        return self.net_gross - self.posted_net

    def as_dict(self) -> dict:
        return {
            "ns_count": self.ns_count,
            "ns_gross": str(self.ns_gross),
            "nr_count": self.nr_count,
            "nr_gross": str(self.nr_gross),
            "net_gross": str(self.net_gross),
            "total_tax": str(self.total_tax),
            "items_ns": str(self.items_ns),
            "items_nr": str(self.items_nr),
            "copies_count": self.copies_count,
            "copies_gross": str(self.copies_gross),
            "discounts": str(self.discounts),
            "posted_net": str(self.posted_net),
            "declared_less_posted": str(self.declared_less_posted),
            "queued_rows": self.queued_rows,
            "classes": {name: totals.as_dict() for name, totals in sorted(self.classes.items())},
            "by_payment_method": {
                code: str(amount) for code, amount in sorted(self.by_payment_method.items())
            },
            "refunds_by_payment_method": {
                code: str(amount)
                for code, amount in sorted(self.refunds_by_payment_method.items())
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
    adapter: FiscalizationAdapter | None = None,
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
    reader = adapter or _adapter_for(db, company_id)
    documents = _documents_of(db, company_id, [receipt.document_id for receipt in receipts])

    figures = DailyFigures()
    for receipt in receipts:
        declared = reader.normalize_declared_totals(receipt.request, receipt.response)
        if declared is None:
            continue
        _absorb(figures, receipt, declared, documents.get(receipt.document_id))

    figures.queued_rows = _queued_rows(db, company_id, device_id)
    return figures


def _absorb(
    figures: DailyFigures,
    receipt: FiscalReceipt,
    declared: DeclaredTotals,
    document: PartnerDocument | None,
) -> None:
    """Add one signed receipt to the day, from what it declared.

    **Why the declaration and not the ledger.** A Z states what the authority signed, and on a
    discounted line the wire's taxable amount is `splyAmt − dcAmt` — derived from the
    two-decimal inclusive price — not the posted gross. The two agree on round, undiscounted
    prices and part company everywhere else, so a Z computed off `journal_lines` would be a
    summary that disagreed with the receipts it summarises. The totals therefore come through
    `normalize_declared_totals`, which is the adapter's job precisely so that no field name of
    the authority's reaches this module (rule 12, and `tests/fiscal/test_boundary.py`).

    The document is consulted for one thing only — **how it was paid** — because
    `payment_method` is a property of the document (decision 7) and is already neutral. A
    receipt whose document has since vanished still counts in the day's takings; it simply
    joins no payment-method bucket.
    """
    is_refund = receipt.receipt_type is FiscalReceiptType.NORMAL_REFUND

    if is_refund:
        figures.nr_count += 1
        figures.nr_gross += declared.gross
    else:
        figures.ns_count += 1
        figures.ns_gross += declared.gross
    if is_refund:
        figures.items_nr += declared.quantity
    else:
        figures.items_ns += declared.quantity
    figures.discounts += declared.discount
    if document is not None:
        posted = _money(document.base_total_amount)
        figures.posted_net += -posted if is_refund else posted

    # A copy is a print, not a receipt: it is counted and its value noted, and it is no part of
    # the day's takings (decision 11 — only one sale was ever declared).
    if receipt.copy_count:
        figures.copies_count += receipt.copy_count
        figures.copies_gross += declared.gross * receipt.copy_count

    for row in declared.classes:
        totals = figures.classes.setdefault(row.tax_class, ClassTotals())
        totals.rate = row.rate
        if is_refund:
            totals.taxable_nr += row.taxable
            totals.tax_nr += row.tax
        else:
            totals.taxable_ns += row.taxable
            totals.tax_ns += row.tax

    # Sales in one bucket, refunds in another. See `DailyFigures.by_payment_method` for why
    # they are not netted: the method on a refund is the credit note's own, so netting would
    # take money out of whichever drawer the *credit note* names rather than the drawer the
    # sale went into — and the reader could not tell afterwards.
    if document is not None and document.payment_method is not None:
        method = str(document.payment_method)
        bucket = figures.refunds_by_payment_method if is_refund else figures.by_payment_method
        bucket[method] = bucket.get(method, MONEY_ZERO) + declared.gross


def _adapter_for(db: Session, company_id: int) -> FiscalizationAdapter:
    """The company's own adapter, because only it can read its own payloads.

    One line, kept as a name because this module reads it twice and because the *reason* it
    takes no transport belongs somewhere — `registry.adapter_for_company` is where, now that
    the receipts listing needs the same thing for the same reason.
    """
    return registry.adapter_for_company(db, company_id)


def _documents_of(
    db: Session, company_id: int, document_ids: Sequence[int]
) -> dict[int, PartnerDocument]:
    if not document_ids:
        return {}
    return {
        document.id: document
        for document in db.scalars(
            select(PartnerDocument).where(
                PartnerDocument.company_id == company_id,
                PartnerDocument.id.in_(list(document_ids)),
            )
        )
    }


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
