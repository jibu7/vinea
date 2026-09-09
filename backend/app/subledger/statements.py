"""Customer / supplier statements, rendered server-side to PDF (P4 decision 10).

Two variants: **open item** (what is still outstanding, aged) and **activity** (every
document in a date range with a running balance). Both are produced as jobs — there is no
synchronous PDF endpoint — from an HTML template that mirrors the P3 print stylesheet.
"""

from datetime import date
from decimal import Decimal
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.currency import Currency
from app.models.job import Job
from app.models.partner import PartnerRole
from app.services.jobs import handler
from app.subledger import enquiries
from app.subledger.ageing import age_analysis
from app.subledger.documents import DOCUMENT_MATRIX

STATEMENT_JOB = "partner_statement"

# Deliberately close to the P3 print stylesheet: neutral, dense, no screen chrome.
STYLESHEET = """
@page { size: A4; margin: 16mm 14mm; }
body { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; font-size: 9.5pt;
       color: #111; }
h1 { font-size: 15pt; margin: 0 0 2mm; }
h2 { font-size: 11pt; margin: 6mm 0 2mm; border-bottom: 0.4mm solid #111;
     padding-bottom: 1mm; }
.meta { color: #555; margin-bottom: 4mm; }
table { width: 100%; border-collapse: collapse; margin-bottom: 4mm; }
th { text-align: left; border-bottom: 0.4mm solid #111; padding: 1.4mm 1mm; font-size: 8.5pt;
     text-transform: uppercase; letter-spacing: 0.04em; }
td { padding: 1.2mm 1mm; border-bottom: 0.2mm solid #ddd; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tfoot td { font-weight: 600; border-top: 0.4mm solid #111; border-bottom: none; }
.partner { page-break-after: always; }
.partner:last-child { page-break-after: auto; }
"""


def _money(amount: Decimal, places: int) -> str:
    quantised = amount.quantize(Decimal(1).scaleb(-places))
    return f"{quantised:,.{places}f}"


def _kind_label(role: PartnerRole, kind: str) -> str:
    for (matrix_role, matrix_kind), spec in DOCUMENT_MATRIX.items():
        if matrix_role == role and str(matrix_kind) == str(kind):
            return spec.label
    return str(kind)


def render_statement_html(
    db: Session,
    company_id: int,
    role: PartnerRole,
    *,
    partner_ids: list[int],
    as_of: date,
    variant: str = "open_item",
    date_from: date | None = None,
) -> str:
    company = db.get(Company, company_id)
    base = db.scalar(select(Currency).where(Currency.company_id == company_id, Currency.is_base))
    assert company is not None and base is not None
    places = base.decimal_places
    ageing = age_analysis(db, company_id, role, as_of=as_of)
    ageing_by_partner = {row.partner_id: row for row in ageing.rows}

    sections: list[str] = []
    for partner_id in partner_ids:
        enquiry = enquiries.partner_enquiry(
            db, company_id, role, partner_id, as_of=as_of, date_from=date_from
        )
        rows: list[str] = []
        if variant == "open_item":
            for item in enquiry.open_items:
                document = item.document
                due = f"{document.due_date:%d/%m/%Y}" if document.due_date else ""
                rows.append(
                    "<tr>"
                    f"<td>{escape(document.number)}</td>"
                    f"<td>{escape(_kind_label(role, document.kind))}</td>"
                    f"<td>{document.document_date:%d/%m/%Y}</td>"
                    f"<td>{due}</td>"
                    f"<td class='num'>{_money(document.total_amount, places)}</td>"
                    f"<td class='num'>{_money(item.signed_base_amount, places)}</td>"
                    "</tr>"
                )
            header = (
                "<tr><th>Document</th><th>Type</th><th>Date</th><th>Due</th>"
                "<th class='num'>Amount</th><th class='num'>Outstanding</th></tr>"
            )
        else:
            for entry in enquiry.entries:
                document = entry.document
                rows.append(
                    "<tr>"
                    f"<td>{escape(document.number)}</td>"
                    f"<td>{escape(_kind_label(role, document.kind))}</td>"
                    f"<td>{document.document_date:%d/%m/%Y}</td>"
                    f"<td>{escape(document.description)}</td>"
                    f"<td class='num'>"
                    f"{_money(document.direction * document.base_total_amount, places)}</td>"
                    f"<td class='num'>{_money(entry.running_base, places)}</td>"
                    "</tr>"
                )
            header = (
                "<tr><th>Document</th><th>Type</th><th>Date</th><th>Description</th>"
                "<th class='num'>Amount</th><th class='num'>Balance</th></tr>"
            )

        ageing_row = ageing_by_partner.get(partner_id)
        ageing_html = ""
        if ageing_row is not None:
            cells = "".join(
                f"<th class='num'>{escape(cell.label)}</th>" for cell in ageing_row.buckets
            )
            values = "".join(
                f"<td class='num'>{_money(cell.amount, places)}</td>"
                for cell in ageing_row.buckets
            )
            ageing_html = (
                f"<h2>Ageing ({escape(ageing.bucket_set.name)})</h2>"
                f"<table><thead><tr>{cells}</tr></thead>"
                f"<tbody><tr>{values}</tr></tbody></table>"
            )

        sections.append(
            f"<section class='partner'>"
            f"<h1>{escape(enquiry.partner.name)}</h1>"
            f"<p class='meta'>{escape(company.name)} &middot; "
            f"{'Customer' if role == PartnerRole.AR else 'Supplier'} statement &middot; "
            f"as of {as_of:%d/%m/%Y} &middot; amounts in {escape(base.code)}</p>"
            f"<table><thead>{header}</thead><tbody>{''.join(rows)}</tbody>"
            f"<tfoot><tr><td colspan='5'>Balance</td>"
            f"<td class='num'>{_money(enquiry.balance_base, places)}</td></tr></tfoot></table>"
            f"{ageing_html}"
            f"</section>"
        )

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Statement {as_of:%Y-%m-%d}</title><style>{STYLESHEET}</style></head>"
        f"<body>{''.join(sections)}</body></html>"
    )


def render_pdf(html: str) -> bytes:
    from weasyprint import HTML

    return HTML(string=html).write_pdf()


@handler(STATEMENT_JOB)
def run_statement_job(db: Session, job: Job) -> None:
    params = job.params or {}
    role = PartnerRole(params["role"])
    as_of = date.fromisoformat(params["as_of"])
    date_from = date.fromisoformat(params["date_from"]) if params.get("date_from") else None
    partner_ids = list(params.get("partner_ids") or [])
    if not partner_ids:
        partner_ids = [
            partner.id
            for partner in _partners_with_activity(db, job.company_id, role, as_of=as_of)
        ]
    html = render_statement_html(
        db,
        job.company_id,
        role,
        partner_ids=partner_ids,
        as_of=as_of,
        variant=params.get("variant", "open_item"),
        date_from=date_from,
    )
    job.artifact = render_pdf(html)
    job.artifact_name = f"statement-{role.value}-{as_of:%Y%m%d}.pdf"
    job.artifact_content_type = "application/pdf"
    job.result = {"partner_count": len(partner_ids), "bytes": len(job.artifact or b"")}


def _partners_with_activity(db: Session, company_id: int, role: PartnerRole, *, as_of: date):  # noqa: ANN202
    from app.models.partner import Partner
    from app.models.subledger import PartnerDocument

    return db.scalars(
        select(Partner)
        .join(
            PartnerDocument,
            (PartnerDocument.partner_id == Partner.id)
            & (PartnerDocument.company_id == Partner.company_id),
        )
        .where(
            Partner.company_id == company_id,
            PartnerDocument.role == role,
            PartnerDocument.document_date <= as_of,
        )
        .distinct()
        .order_by(Partner.name)
    ).all()
