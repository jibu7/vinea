"""The VAT return's annexes: the sales and purchase listings, as CSV.

Decision 12's last clause. Two things about the shape are worth stating.

**The figures come from the same lines the return does.** An annex whose totals disagreed with
the return it accompanies would be worse than no annex, and the only way to be sure they agree
is to read the same thing: `journal_lines` carrying a tax code, split into base and tax by the
account the line posted to, exactly as `vat.py` does. Nothing here reads
`partner_documents.tax_amount`, which is the document's own currency and would need converting
— the base amounts on the ledger are already what the return declares.

**A document is joined through its entry, original or reversal.** A reversed invoice has two
entries and the return counts both, because they cancel. So the annex attributes each of them
to the document they belong to and lists them as separate rows with their own dates: the row
that was declared and the row that took it back. An annex that showed only the original would
tie to nothing.
"""

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.kernel.money import ZERO
from app.models.fiscalization import FiscalPurchaseFeedRow, FiscalReceipt
from app.models.journal import JournalEntry, JournalLine
from app.models.partner import Partner, PartnerRole
from app.models.subledger import PartnerDocument
from app.models.tax import TaxCode
from app.tax.vat import _SIGN, PURCHASES, SALES, TAX_MODULE

SALES_COLUMNS = (
    "customer_tin",
    "customer_name",
    "document_number",
    "document_date",
    "entry_number",
    "entry_date",
    "invc_no",
    "rcpt_no",
    "sdc_id",
    "base",
    "vat",
)

PURCHASE_COLUMNS = (
    "supplier_tin",
    "supplier_name",
    "document_number",
    "supplier_reference",
    "document_date",
    "entry_number",
    "entry_date",
    "feed_supplier_invoice_no",
    "base",
    "vat",
)


@dataclass(frozen=True)
class AnnexRow:
    """One document-entry pair: what it declared, and how the authority can find it.

    `entry_number` and `entry_date` are on the row rather than only the document's, because a
    reversal is dated when it happened and that is the date the range matched on.
    """

    document_id: int
    document_number: str
    document_date: date
    entry_id: int
    entry_number: str
    entry_date: date
    partner_tin: str | None
    partner_name: str
    reference: str | None
    base: Decimal
    tax: Decimal
    invc_no: int | None = None
    rcpt_no: int | None = None
    sdc_id: str | None = None
    feed_supplier_invoice_no: int | None = None


def sales_rows(
    db: Session, company_id: int, *, period_from: date, period_to: date
) -> list[AnnexRow]:
    """Every AR document that moved a tax-coded line in the range, with its receipt."""
    rows = _rows(db, company_id, PartnerRole.AR, period_from=period_from, period_to=period_to)
    receipts = {
        receipt.id: receipt
        for receipt in db.scalars(
            select(FiscalReceipt).where(FiscalReceipt.company_id == company_id)
        )
    }
    out: list[AnnexRow] = []
    for row in rows:
        receipt = receipts.get(row.fiscal_receipt_id) if row.fiscal_receipt_id else None
        out.append(
            _row(
                row,
                invc_no=receipt.invc_no if receipt else None,
                rcpt_no=receipt.rcpt_no if receipt else None,
                sdc_id=receipt.sdc_id if receipt else None,
            )
        )
    return out


def purchase_rows(
    db: Session, company_id: int, *, period_from: date, period_to: date
) -> list[AnnexRow]:
    """Every AP document that moved a tax-coded line in the range, with the feed's number.

    The feed's `spplrInvcNo` is the authority's own handle on the supplier's sale, so when an
    AP document has been linked to an accepted feed row it goes on the annex beside our own
    reference — that pair is what a desk audit reconciles.
    """
    rows = _rows(db, company_id, PartnerRole.AP, period_from=period_from, period_to=period_to)
    linked = {
        feed.ap_document_id: feed.spplr_invc_no
        for feed in db.scalars(
            select(FiscalPurchaseFeedRow).where(
                FiscalPurchaseFeedRow.company_id == company_id,
                FiscalPurchaseFeedRow.ap_document_id.is_not(None),
            )
        )
    }
    return [_row(row, feed_supplier_invoice_no=linked.get(row.document_id)) for row in rows]


def sales_csv(db: Session, company_id: int, *, period_from: date, period_to: date) -> str:
    return _csv(
        SALES_COLUMNS,
        (
            (
                row.partner_tin or "",
                row.partner_name,
                row.document_number,
                row.document_date.isoformat(),
                row.entry_number,
                row.entry_date.isoformat(),
                row.invc_no if row.invc_no is not None else "",
                row.rcpt_no if row.rcpt_no is not None else "",
                row.sdc_id or "",
                row.base,
                row.tax,
            )
            for row in sales_rows(db, company_id, period_from=period_from, period_to=period_to)
        ),
    )


def purchases_csv(db: Session, company_id: int, *, period_from: date, period_to: date) -> str:
    return _csv(
        PURCHASE_COLUMNS,
        (
            (
                row.partner_tin or "",
                row.partner_name,
                row.document_number,
                row.reference or "",
                row.document_date.isoformat(),
                row.entry_number,
                row.entry_date.isoformat(),
                row.feed_supplier_invoice_no
                if row.feed_supplier_invoice_no is not None
                else "",
                row.base,
                row.tax,
            )
            for row in purchase_rows(db, company_id, period_from=period_from, period_to=period_to)
        ),
    )


def _csv(columns: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """CSV with `\\r\\n` endings — RFC 4180, which is what a spreadsheet and a customs desk
    both expect. Decimals are written by `str`, so a franc stays a franc."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def _row(row: "_Grouped", **extra: object) -> AnnexRow:
    return AnnexRow(
        document_id=row.document_id,
        document_number=row.document_number,
        document_date=row.document_date,
        entry_id=row.entry_id,
        entry_number=row.entry_number,
        entry_date=row.entry_date,
        partner_tin=row.partner_tin,
        partner_name=row.partner_name,
        reference=row.reference,
        base=row.base,
        tax=row.tax,
        **extra,  # type: ignore[arg-type]
    )


def _rows(
    db: Session,
    company_id: int,
    role: PartnerRole,
    *,
    period_from: date,
    period_to: date,
) -> list["_Grouped"]:
    """Base and tax per (document, entry), from the tax-coded lines of that entry.

    The split is the return's: a line on the code's own tax account **is** the tax, every other
    line carrying the code is base. `TAX_MODULE` is excluded for the same reason it is there —
    a settlement line is the payment of tax, not tax.
    """
    sign = _SIGN[SALES if role is PartnerRole.AR else PURCHASES]
    statement = (
        select(
            PartnerDocument.id.label("document_id"),
            PartnerDocument.number.label("document_number"),
            PartnerDocument.document_date,
            PartnerDocument.reference,
            PartnerDocument.fiscal_receipt_id,
            JournalEntry.id.label("entry_id"),
            JournalEntry.number.label("entry_number"),
            JournalEntry.entry_date,
            Partner.tin.label("partner_tin"),
            Partner.name.label("partner_name"),
            JournalLine.gl_account_id,
            JournalLine.base_amount,
            TaxCode.gl_account_id.label("tax_account_id"),
        )
        .join(
            JournalEntry,
            and_(
                JournalEntry.company_id == PartnerDocument.company_id,
                or_(
                    JournalEntry.id == PartnerDocument.journal_entry_id,
                    JournalEntry.id == PartnerDocument.reversal_entry_id,
                ),
            ),
        )
        .join(
            JournalLine,
            and_(
                JournalLine.entry_id == JournalEntry.id,
                JournalLine.company_id == JournalEntry.company_id,
            ),
        )
        .join(
            TaxCode,
            and_(
                TaxCode.id == JournalLine.tax_code_id,
                TaxCode.company_id == JournalLine.company_id,
            ),
        )
        .join(
            Partner,
            and_(
                Partner.id == PartnerDocument.partner_id,
                Partner.company_id == PartnerDocument.company_id,
            ),
        )
        .where(
            PartnerDocument.company_id == company_id,
            PartnerDocument.role == role,
            JournalEntry.module != TAX_MODULE,
            JournalEntry.entry_date >= period_from,
            JournalEntry.entry_date <= period_to,
        )
    )

    grouped: dict[tuple[int, int], dict] = {}
    for row in db.execute(statement):
        key = (row.document_id, row.entry_id)
        bucket = grouped.setdefault(
            key,
            {
                "document_id": row.document_id,
                "document_number": row.document_number,
                "document_date": row.document_date,
                "reference": row.reference,
                "fiscal_receipt_id": row.fiscal_receipt_id,
                "entry_id": row.entry_id,
                "entry_number": row.entry_number,
                "entry_date": row.entry_date,
                "partner_tin": row.partner_tin,
                "partner_name": row.partner_name,
                "base": ZERO,
                "tax": ZERO,
            },
        )
        if row.tax_account_id is not None and row.gl_account_id == row.tax_account_id:
            bucket["tax"] += sign * row.base_amount
        else:
            bucket["base"] += sign * row.base_amount

    return [
        _Grouped(**bucket)
        for _, bucket in sorted(
            grouped.items(), key=lambda item: (item[1]["entry_date"], item[0])
        )
    ]


@dataclass(frozen=True)
class _Grouped:
    """A `Row`-alike so `_row` reads one way for both the query and the aggregate."""

    document_id: int
    document_number: str
    document_date: date
    reference: str | None
    fiscal_receipt_id: int | None
    entry_id: int
    entry_number: str
    entry_date: date
    partner_tin: str | None
    partner_name: str
    base: Decimal
    tax: Decimal
