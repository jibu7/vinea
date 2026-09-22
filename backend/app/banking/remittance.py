"""The remittance advice: what one supplier was paid, and against what (P8 decision 7).

A job rather than a synchronous endpoint, the shape P4's partner statements already use
(ADR-10): one PDF per supplier, the bytes in the row, seven-day retention. A run over forty
suppliers is forty renders, and WeasyPrint is not something to hold a request open for.

The advice is a *reading* of rows the run already wrote — it computes nothing the ledger does
not already say, and it is regenerable from the run at any time inside the retention window,
which is why nothing about it is stored beyond the artifact.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.banking import payment_runs
from app.models.banking import PaymentRun
from app.models.company import Company
from app.models.currency import Currency
from app.models.job import Job
from app.models.partner import Partner
from app.models.subledger import PartnerDocument
from app.services.jobs import handler

# Deliberately the P4 statement stylesheet: a supplier receiving both should not be able to
# tell that two different phases produced them.
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
"""


def _money(amount: Decimal, places: int) -> str:
    return f"{amount.quantize(Decimal(1).scaleb(-places)):,.{places}f}"


def render_remittance_html(
    db: Session, company_id: int, *, run: PaymentRun, partner_id: int
) -> str:
    """One supplier's advice: the invoices paid, the discount taken on each, and the payment.

    The invoice **numbers** are on it because that is what the supplier reconciles against —
    an advice quoting only a total is a payment the supplier's own AR clerk has to guess at.
    """
    from html import escape

    company = db.get(Company, company_id)
    partner = db.get(Partner, partner_id)
    currency = db.get(Currency, run.currency_id)
    assert company is not None and partner is not None and currency is not None
    places = currency.decimal_places

    lines = [
        line
        for line in payment_runs.lines_of(db, company_id, run.id)
        if line.partner_id == partner_id
    ]
    numbers = dict(
        db.execute(
            select(PartnerDocument.id, PartnerDocument.number).where(
                PartnerDocument.company_id == company_id,
                PartnerDocument.id.in_([line.document_id for line in lines] or [0]),
            )
        ).all()
    )
    settlement_number = db.scalar(
        select(PartnerDocument.number).where(
            PartnerDocument.company_id == company_id,
            PartnerDocument.id == (lines[0].settlement_document_id if lines else 0),
        )
    )

    rows = "".join(
        "<tr>"
        f"<td>{escape(numbers.get(line.document_id, ''))}</td>"
        f"<td class='num'>{_money(line.amount + line.discount_amount, places)}</td>"
        f"<td class='num'>{_money(line.discount_amount, places)}</td>"
        f"<td class='num'>{_money(line.amount, places)}</td>"
        "</tr>"
        for line in lines
    )
    total = sum((line.amount for line in lines), Decimal(0))
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Remittance advice {escape(run.number)}</title>"
        f"<style>{STYLESHEET}</style></head><body>"
        f"<h1>Remittance advice</h1>"
        f"<div class='meta'>{escape(company.name)} &middot; {escape(run.number)} &middot; "
        f"{run.payment_date:%d %b %Y}"
        + (f" &middot; {escape(settlement_number)}" if settlement_number else "")
        + "</div>"
        f"<h2>{escape(partner.name)}</h2>"
        "<table><thead><tr><th>Invoice</th><th class='num'>Invoiced</th>"
        "<th class='num'>Discount</th><th class='num'>Paid</th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        "<tfoot><tr><td>Total paid</td><td class='num'></td><td class='num'></td>"
        f"<td class='num'>{currency.code} {_money(total, places)}</td></tr></tfoot>"
        "</table></body></html>"
    )


@handler(payment_runs.REMITTANCE_JOB)
def run_remittance_job(db: Session, job: Job) -> None:
    params = job.params or {}
    run = payment_runs.get(db, job.company_id, int(params["run_id"]))
    partner_id = int(params["partner_id"])
    html = render_remittance_html(db, job.company_id, run=run, partner_id=partner_id)
    from app.subledger.statements import render_pdf

    job.artifact = render_pdf(html)
    job.artifact_name = f"remittance-{run.number}-{partner_id}.pdf"
    job.artifact_content_type = "application/pdf"
    job.result = {
        "run_id": run.id,
        "run_number": run.number,
        "partner_id": partner_id,
        "bytes": len(job.artifact or b""),
    }
