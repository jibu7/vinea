"""P4 step 5 — ageing, statements, listings and enquiries."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.job import JobStatus
from app.models.partner import AgeingBasis, PartnerRole
from app.services import jobs as jobs_service
from app.subledger import allocations as allocations_service
from app.subledger import documents as documents_service
from app.subledger import enquiries as enquiries_service
from app.subledger.ageing import age_analysis
from app.subledger.openitems import open_items_as_of
from app.subledger.statements import STATEMENT_JOB, render_statement_html
from tests.subledger.conftest import MARCH, Subledger
from tests.subledger.invariants import assert_subledger_invariants
from tests.subledger.test_documents import post_invoice, post_settlement


def test_ageing_buckets_match_the_open_items(db: Session, subledger: Subledger) -> None:
    old, _ = post_invoice(db, subledger, amount=Decimal(10000), on=MARCH)
    recent, _ = post_invoice(
        db, subledger, amount=Decimal(4000), on=MARCH + timedelta(days=60)
    )
    db.commit()

    as_of = MARCH + timedelta(days=95)
    report = age_analysis(db, subledger.company_id, PartnerRole.AR, as_of=as_of)
    assert report.bucket_set.basis == AgeingBasis.DUE_DATE
    row = report.rows[0]
    assert row.total == Decimal(14000)
    assert report.grand_total == Decimal(14000)
    # Old invoice: due MARCH+30, so 65 days overdue → the 61–90 bucket.
    by_label = {cell.label: cell.amount for cell in row.buckets}
    assert by_label["61 - 90"] == Decimal(10000)
    assert by_label["Current"] == Decimal(4000)
    assert sum(cell.amount for cell in report.totals) == Decimal(14000)


def test_ageing_is_historical_not_a_snapshot(db: Session, subledger: Subledger) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(10000), on=MARCH)
    later = MARCH + timedelta(days=40)
    receipt = post_settlement(db, subledger, amount=Decimal(10000), on=later)
    db.commit()
    allocations_service.allocate(
        db,
        subledger.company_id,
        PartnerRole.AR,
        partner_id=subledger.customer.id,
        allocation_date=later,
        pairs=[
            allocations_service.PairInput(
                debit_document_id=invoice.id,
                credit_document_id=receipt.id,
                amount=Decimal(10000),
            )
        ],
        actor=subledger.ledger.owner,
    )
    db.commit()

    # As of a date *before* the allocation the invoice was still fully open.
    before = age_analysis(
        db, subledger.company_id, PartnerRole.AR, as_of=MARCH + timedelta(days=10)
    )
    assert before.grand_total == Decimal(10000)
    after = age_analysis(db, subledger.company_id, PartnerRole.AR, as_of=later)
    assert after.grand_total == Decimal(0)


def test_open_items_as_of_ignores_later_allocations(
    db: Session, subledger: Subledger
) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(9000), on=MARCH)
    later = MARCH + timedelta(days=15)
    receipt = post_settlement(db, subledger, amount=Decimal(4000), on=later)
    db.commit()
    allocations_service.allocate(
        db,
        subledger.company_id,
        PartnerRole.AR,
        partner_id=subledger.customer.id,
        allocation_date=later,
        pairs=[
            allocations_service.PairInput(
                debit_document_id=invoice.id,
                credit_document_id=receipt.id,
                amount=Decimal(4000),
            )
        ],
        actor=subledger.ledger.owner,
    )
    db.commit()

    early = open_items_as_of(
        db, subledger.company_id, role=PartnerRole.AR, as_of=MARCH + timedelta(days=1)
    )
    assert [item.open_amount for item in early] == [Decimal(9000)]
    now = open_items_as_of(db, subledger.company_id, role=PartnerRole.AR, as_of=later)
    assert sum(item.signed_base_amount for item in now) == Decimal(5000)


def test_partner_enquiry_runs_a_balance_and_links_to_the_journal(
    db: Session, subledger: Subledger
) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(10000))
    receipt = post_settlement(db, subledger, amount=Decimal(3000))
    db.commit()
    enquiry = enquiries_service.partner_enquiry(
        db,
        subledger.company_id,
        PartnerRole.AR,
        subledger.customer.id,
        as_of=MARCH + timedelta(days=1),
    )
    assert [entry.document.id for entry in enquiry.entries] == [invoice.id, receipt.id]
    assert enquiry.entries[-1].running_base == Decimal(7000)
    assert enquiry.balance_base == Decimal(10000) - Decimal(3000)
    assert all(entry.document.journal_entry_id for entry in enquiry.entries)


def test_transaction_listing_pages(db: Session, subledger: Subledger) -> None:
    for _ in range(5):
        post_invoice(db, subledger, amount=Decimal(1000))
    db.commit()
    first, cursor = documents_service.list_documents(
        db, subledger.company_id, role=PartnerRole.AR, limit=2
    )
    assert len(first) == 2 and cursor is not None
    second, _ = documents_service.list_documents(
        db, subledger.company_id, role=PartnerRole.AR, limit=10, cursor=cursor
    )
    assert len(second) == 3


def test_allocation_listing(db: Session, subledger: Subledger) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(8000))
    receipt = post_settlement(db, subledger, amount=Decimal(8000))
    db.commit()
    allocations_service.allocate(
        db,
        subledger.company_id,
        PartnerRole.AR,
        partner_id=subledger.customer.id,
        allocation_date=MARCH,
        pairs=[
            allocations_service.PairInput(
                debit_document_id=invoice.id,
                credit_document_id=receipt.id,
                amount=Decimal(8000),
            )
        ],
        actor=subledger.ledger.owner,
    )
    db.commit()
    entries = enquiries_service.partner_allocations(
        db, subledger.company_id, PartnerRole.AR, partner_id=subledger.customer.id
    )
    assert len(entries) == 1
    assert entries[0].debit_number == invoice.number
    assert entries[0].credit_number == receipt.number


def test_statement_html_renders_open_items(db: Session, subledger: Subledger) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(12345))
    db.commit()
    html = render_statement_html(
        db,
        subledger.company_id,
        PartnerRole.AR,
        partner_ids=[subledger.customer.id],
        as_of=MARCH + timedelta(days=1),
    )
    assert invoice.number in html
    assert "Amahoro Retail Ltd" in html
    assert "12,345" in html


def test_statement_job_produces_a_pdf(db: Session, subledger: Subledger) -> None:
    post_invoice(db, subledger, amount=Decimal(5000))
    db.commit()
    job = jobs_service.enqueue(
        db,
        subledger.company_id,
        STATEMENT_JOB,
        {
            "role": "ar",
            "partner_ids": [subledger.customer.id],
            "as_of": (MARCH + timedelta(days=1)).isoformat(),
            "variant": "open_item",
        },
        actor=subledger.ledger.owner,
    )
    db.commit()

    jobs_service.run_job(job.id, subledger.company_id, subledger.ledger.owner.id)
    db.expire_all()
    refreshed = jobs_service.get_job(db, subledger.company_id, job.id)
    assert refreshed.status == JobStatus.SUCCEEDED, refreshed.error
    assert refreshed.artifact is not None and refreshed.artifact[:4] == b"%PDF"
    assert refreshed.artifact_content_type == "application/pdf"
    assert_subledger_invariants(db, subledger.company_id)


def test_activity_statement_variant(db: Session, subledger: Subledger) -> None:
    post_invoice(db, subledger, amount=Decimal(2500))
    post_settlement(db, subledger, amount=Decimal(500))
    db.commit()
    html = render_statement_html(
        db,
        subledger.company_id,
        PartnerRole.AR,
        partner_ids=[subledger.customer.id],
        as_of=MARCH + timedelta(days=1),
        variant="activity",
    )
    assert "Balance" in html and "2,500" in html
