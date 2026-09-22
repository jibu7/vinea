"""Payment runs, decision 7, with the acceptance tape's own literals.

The run under test is the tape's row 4 — three suppliers, one taking a 2 % discount inside its
window and one with no bank details — and the figures here are the figures the tape will
reproduce at step 5. They are worked by hand and written as constants; nothing in this file
asks the code under test what the answer should be.

    SIN-1  S1  236 000                        paid in full          236 000
    SIN-2  S2  100 000  terms 2/10 net 30     less 2 000 discount    98 000
    SIN-3  S3   50 000  (no bank details)     paid in full           50 000
                                              run total             384 000

The bank opens at 1 177 000 and closes at 793 000, and the discount posts Dr 2100 / Cr 4350
2 000 — the settlement discount P4 computes, taken by P4, on an allocation this module asked
for and did not write.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.banking import matching
from app.banking import payment_runs as payment_run_service
from app.banking import reconciliation as reconciliation_service
from app.kernel.errors import LedgerStateError
from app.models.banking import (
    BankMatchRule,
    PaymentRun,
    PaymentRunStatus,
)
from app.models.job import Job
from app.models.journal import JournalEntry, JournalLine
from app.models.partner import PartnerRole
from app.models.subledger import (
    DocumentKind,
    DocumentStatus,
    InstrumentType,
    PartnerDocument,
)
from app.subledger import documents as documents_service
from app.subledger.openitems import recompute_open_amount
from tests.banking.conftest import Banking, ap_invoice, ap_supplier, cashbook, key_statement
from tests.banking.invariants import assert_bank_invariants
from tests.kernel.conftest import YEAR
from tests.subledger.invariants import assert_subledger_invariants

SEP_1 = date(YEAR, 9, 1)
SEP_10 = date(YEAR, 9, 10)
SEP_30 = date(YEAR, 9, 30)

OPENING = Decimal(1177000)
S1_INVOICE = Decimal(236000)
S2_INVOICE = Decimal(100000)
S3_INVOICE = Decimal(50000)
S2_DISCOUNT = Decimal(2000)
S2_CASH = Decimal(98000)
RUN_TOTAL = Decimal(384000)
CLOSING = Decimal(793000)


class Run:
    """The three suppliers and their three invoices, before any run is posted."""

    def __init__(self, db: Session, banking: Banking) -> None:
        cashbook(db, banking, account_code="1120", amount=OPENING, on=SEP_1)
        self.s1 = ap_supplier(db, banking, name="Kigali Timber", code="S1")
        self.s2 = ap_supplier(
            db, banking, name="Musanze Millwork", code="S2", terms_code="2/10N30"
        )
        self.s3 = ap_supplier(
            db, banking, name="Huye Hardware", code="S3", bank_details=False
        )
        self.sin1 = ap_invoice(db, banking, self.s1, amount=S1_INVOICE, on=SEP_1)
        self.sin2 = ap_invoice(db, banking, self.s2, amount=S2_INVOICE, on=SEP_1)
        self.sin3 = ap_invoice(db, banking, self.s3, amount=S3_INVOICE, on=SEP_1)
        db.commit()

    def lines(self) -> list[payment_run_service.RunLineInput]:
        return [
            payment_run_service.RunLineInput(document_id=document.id)
            for document in (self.sin1, self.sin2, self.sin3)
        ]


@pytest.fixture
def run(db: Session, banking: Banking) -> Run:
    return Run(db, banking)


def _post(db: Session, banking: Banking, run: Run) -> PaymentRun:
    posted = payment_run_service.post_run(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        payment_date=SEP_10,
        lines=run.lines(),
        actor=banking.owner,
    )
    db.flush()
    return posted


def _tick_the_opening(db: Session, banking: Banking) -> None:
    """The opening 1 177 000 is a ledger line this statement does not show, so it is
    outstanding and the reconciliation cannot reach zero. Ticking it is what a person does with
    a line they know cleared before the statement began — paper mode, decision 5."""
    row = banking.bank("BK-RWF")
    outstanding = list(
        db.scalars(matching.unmatched_journal_lines(db, banking.company_id, row))
    )
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=row.id,
        journal_line_ids=[line.id for line in outstanding],
        actor=banking.owner,
    )
    db.flush()


def _post_lines(db: Session, banking: Banking, lines: list) -> PaymentRun:
    posted = payment_run_service.post_run(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        payment_date=SEP_10,
        lines=lines,
        actor=banking.owner,
    )
    db.flush()
    return posted


def _balance(db: Session, banking: Banking, code: str = "1120") -> Decimal:
    """Derived from the lines, never from a column — ADR-04, and the only reading this file
    trusts about what the bank account holds."""
    return sum(
        db.scalars(
            select(JournalLine.base_amount).where(
                JournalLine.company_id == banking.company_id,
                JournalLine.gl_account_id == banking.ledger.acct(code),
            )
        ).all(),
        Decimal(0),
    )


# --- The preview -----------------------------------------------------------------------------


def test_the_preview_shows_the_discount_the_missing_details_and_the_total(
    db: Session, banking: Banking, run: Run
) -> None:
    """Every figure the step-4 screen puts in front of a person before they press Post."""
    preview = payment_run_service.plan(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        payment_date=SEP_10,
        lines=run.lines(),
    )

    assert preview.total == RUN_TOTAL
    assert preview.discount_total == S2_DISCOUNT
    by_partner = {supplier.partner_id: supplier for supplier in preview.suppliers}

    s2 = by_partner[run.s2.id]
    assert s2.lines[0].discount_available == S2_DISCOUNT
    assert s2.lines[0].amount == S2_INVOICE, "the line settles the invoice in full"
    assert s2.lines[0].cash_amount == S2_CASH, "and 98 000 is what leaves the bank"
    assert s2.total == S2_CASH

    assert "bank_details_missing" in by_partner[run.s3.id].warnings
    assert by_partner[run.s1.id].warnings == (), "S1 has details and no open credits"
    assert by_partner[run.s1.id].total == S1_INVOICE


def test_a_declined_discount_pays_the_invoice_in_full(
    db: Session, banking: Banking, run: Run
) -> None:
    """`take_discount` is the only thing the toggle does — the discount is still *available*,
    and the preview keeps saying so, which is what lets the screen show what was given up."""
    preview = payment_run_service.plan(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        payment_date=SEP_10,
        lines=[
            payment_run_service.RunLineInput(document_id=run.sin2.id, take_discount=False)
        ],
    )
    line = preview.suppliers[0].lines[0]
    assert line.discount_available == S2_DISCOUNT
    assert line.discount_amount == Decimal(0)
    assert preview.total == S2_INVOICE


def test_the_discount_window_is_a_fact_of_the_payment_date(
    db: Session, banking: Banking, run: Run
) -> None:
    """2/10 on a 1 September invoice is gone by the 30th, and the preview says so rather than
    offering a discount the allocation would refuse."""
    preview = payment_run_service.plan(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        payment_date=SEP_30,
        lines=[payment_run_service.RunLineInput(document_id=run.sin2.id)],
    )
    assert preview.suppliers[0].lines[0].discount_available == Decimal(0)
    assert preview.total == S2_INVOICE


# --- Posting ---------------------------------------------------------------------------------


def test_the_run_posts_one_settlement_and_one_allocation_per_supplier(
    db: Session, banking: Banking, run: Run
) -> None:
    """The reading of the plan's "batch supplier payments → single bank line", asserted: three
    ordinary P4 payments, each with its own number, its own entry and the run's reference."""
    posted = _post(db, banking, run)

    assert posted.number == "PYR-000001"
    assert posted.reference == "PYR-000001"
    assert posted.total == RUN_TOTAL
    assert posted.status == PaymentRunStatus.POSTED

    lines = payment_run_service.lines_of(db, banking.company_id, posted.id)
    assert len(lines) == 3
    settlements = [
        db.get(PartnerDocument, line.settlement_document_id) for line in lines
    ]
    paid = {
        document.partner_id: document.total_amount for document in settlements
    }
    assert paid == {run.s1.id: S1_INVOICE, run.s2.id: S2_CASH, run.s3.id: S3_INVOICE}
    for document in settlements:
        assert document.kind == DocumentKind.SETTLEMENT
        assert document.role == PartnerRole.AP
        assert document.reference == "PYR-000001"
        assert document.instrument_type == InstrumentType.BANK
        assert document.cash_account_id == banking.bank("BK-RWF").gl_account_id
        assert document.number.startswith("PMT-")

    # Every invoice closed, and the bank down by the run's total and not a franc more.
    for invoice in (run.sin1, run.sin2, run.sin3):
        assert recompute_open_amount(db, invoice) == Decimal(0)
    assert _balance(db, banking) == CLOSING

    assert_subledger_invariants(db, banking.company_id)
    assert_bank_invariants(db, banking.company_id)


def test_the_discount_posts_through_p4_to_2100_and_4350(
    db: Session, banking: Banking, run: Run
) -> None:
    """`ALJ-1`: Dr 2100 2 000 / Cr 4350 2 000. This module asked for the allocation; P4 decided
    what the discount was worth and wrote the entry."""
    _post(db, banking, run)

    control = banking.ledger.acct("2100")
    received = banking.ledger.acct("4350")
    discount_entries = db.scalars(
        select(JournalEntry.id)
        .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
        .where(
            JournalEntry.company_id == banking.company_id,
            JournalLine.gl_account_id == received,
        )
        .distinct()
    ).all()
    assert len(discount_entries) == 1, "one allocation posted a discount, and only one"

    lines = {
        line.gl_account_id: line.base_amount
        for line in db.scalars(
            select(JournalLine).where(JournalLine.entry_id == discount_entries[0])
        )
    }
    assert lines[control] == S2_DISCOUNT, "the payable comes down by the discount"
    assert lines[received] == -S2_DISCOUNT, "and discount received is the income"


def test_three_remittance_jobs_are_queued_one_per_supplier(
    db: Session, banking: Banking, run: Run
) -> None:
    posted = _post(db, banking, run)
    jobs = list(
        db.scalars(
            select(Job).where(
                Job.company_id == banking.company_id,
                Job.kind == payment_run_service.REMITTANCE_JOB,
            )
        )
    )
    assert len(jobs) == 3
    assert {job.params["partner_id"] for job in jobs} == {
        run.s1.id,
        run.s2.id,
        run.s3.id,
    }
    assert {job.params["run_id"] for job in jobs} == {posted.id}


def test_the_remittance_advice_names_the_invoice_and_the_amount(
    db: Session, banking: Banking, run: Run
) -> None:
    """Rendered as HTML rather than PDF: what the step-9 e2e reads back with `pdftotext` is
    this text, and asserting it here keeps the claim away from WeasyPrint's version."""
    from app.banking.remittance import render_remittance_html

    posted = _post(db, banking, run)
    html = render_remittance_html(
        db, banking.company_id, run=posted, partner_id=run.s2.id
    )

    assert run.sin2.number in html
    assert "98,000" in html, "what was paid"
    assert "2,000" in html, "and the discount that closed the rest"
    assert "Musanze Millwork" in html


def test_the_instruction_file_carries_the_supplier_with_no_bank_details(
    db: Session, banking: Banking, run: Run
) -> None:
    """One row per beneficiary, S3's account fields empty.

    The row is there **because** the payment posted: hiding it would hide the one beneficiary
    the accountant has to key into the bank's portal by hand.
    """
    posted = _post(db, banking, run)
    body = payment_run_service.instruction_csv(db, banking.company_id, posted)
    rows = [line.split(",") for line in body.strip().split("\r\n")]

    assert rows[0][0] == "beneficiary"
    assert len(rows) == 4, "a header and three beneficiaries"
    by_code = {row[-1]: row for row in rows[1:]}
    assert by_code["S1"][1] == "Bank of Kigali"
    assert by_code["S1"][2] == "00040-S1-01"
    assert by_code["S1"][3] == "236000", "at the franc's own scale, not NUMERIC(20,6)"
    assert by_code["S2"][3] == "98000", "the cash, not the invoice"
    assert by_code["S3"][1] == "", "no bank"
    assert by_code["S3"][2] == "", "no account number"
    assert by_code["S3"][3] == "50000", "and the amount all the same"


# --- The refusals ------------------------------------------------------------------------------


def test_a_run_from_a_cash_account_is_refused(
    db: Session, banking: Banking, run: Run
) -> None:
    with pytest.raises(LedgerStateError) as error:
        payment_run_service.post_run(
            db,
            banking.company_id,
            bank_account_id=banking.bank("CASH").id,
            payment_date=SEP_10,
            lines=run.lines(),
            actor=banking.owner,
        )
    assert error.value.code == "payment_run_needs_bank"


def test_paying_more_than_is_open_is_refused_and_claims_no_number(
    db: Session, banking: Banking, run: Run
) -> None:
    """The tape's row 15: `payment_exceeds_open`, **no number claimed**.

    That second half is the whole reason `plan()` is a separate function. A refusal discovered
    after `claim_number` would leave a hole in the `PYR-` run that an auditor cannot explain.
    """
    with pytest.raises(LedgerStateError) as error:
        payment_run_service.post_run(
            db,
            banking.company_id,
            bank_account_id=banking.bank("BK-RWF").id,
            payment_date=SEP_10,
            lines=[
                payment_run_service.RunLineInput(
                    document_id=run.sin3.id, amount=S3_INVOICE + Decimal(20000)
                )
            ],
            actor=banking.owner,
        )
    assert error.value.code == "payment_exceeds_open"
    db.rollback()

    # The number is still there to be claimed, which is what "no number claimed" means.
    posted = _post(db, banking, run)
    assert posted.number == "PYR-000001"


def test_an_invoice_settled_meanwhile_fails_the_run(
    db: Session, banking: Banking, run: Run
) -> None:
    """`document_not_open`: the selection was made when the invoice was open and somebody paid
    it in the meantime. Nothing posts — not even the two suppliers whose invoices are fine."""
    payment_run_service.post_run(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        payment_date=SEP_10,
        lines=[payment_run_service.RunLineInput(document_id=run.sin3.id)],
        actor=banking.owner,
    )
    db.flush()

    with pytest.raises(LedgerStateError) as error:
        payment_run_service.post_run(
            db,
            banking.company_id,
            bank_account_id=banking.bank("BK-RWF").id,
            payment_date=SEP_10,
            lines=run.lines(),
            actor=banking.owner,
        )
    assert error.value.code == "document_not_open"

    runs = list(
        db.scalars(select(PaymentRun).where(PaymentRun.company_id == banking.company_id))
    )
    assert len(runs) == 1, "the second run posted nothing at all"


def test_an_invoice_in_another_currency_is_not_selectable(
    db: Session, banking: Banking, run: Run
) -> None:
    """A payment leaves a bank account in the currency that account holds, and P4 allocates
    within one currency — so a USD invoice is not on an RWF run's list and not payable by it."""
    usd_invoice = ap_invoice(
        db, banking, run.s1, amount=Decimal(100), on=SEP_1, currency="USD"
    )
    db.flush()

    listed = payment_run_service.selectable_documents(
        db, banking.company_id, bank_account_id=banking.bank("BK-RWF").id, on=SEP_10
    )
    assert usd_invoice.id not in {document.document_id for document in listed}

    with pytest.raises(LedgerStateError) as error:
        payment_run_service.plan(
            db,
            banking.company_id,
            bank_account_id=banking.bank("BK-RWF").id,
            payment_date=SEP_10,
            lines=[payment_run_service.RunLineInput(document_id=usd_invoice.id)],
        )
    assert error.value.code == "payment_run_currency_mismatch"


def test_the_selection_lists_open_invoices_with_their_discount(
    db: Session, banking: Banking, run: Run
) -> None:
    listed = {
        document.document_id: document
        for document in payment_run_service.selectable_documents(
            db, banking.company_id, bank_account_id=banking.bank("BK-RWF").id, on=SEP_10
        )
    }
    assert set(listed) == {run.sin1.id, run.sin2.id, run.sin3.id}
    assert listed[run.sin2.id].discount_available == S2_DISCOUNT
    assert listed[run.sin1.id].discount_available == Decimal(0)
    assert listed[run.sin3.id].open_amount == S3_INVOICE
    assert listed[run.sin3.id].supplier_code == "S3"


# --- The bank's one line, and the run's three (decision 4's second rule) -------------------------


def test_one_statement_line_matches_the_runs_three_ledger_lines(
    db: Session, banking: Banking, run: Run
) -> None:
    """The one-to-many the whole reading turns on, and the rule that was written at step 2 and
    could find nothing until now.

    One debit of 384 000 quoting `PYR-000001`; three `PMT-` lines on `1120`; **one** match with
    three journal members, which balances by construction because the run's total is Σ of its
    settlements.
    """
    posted = _post(db, banking, run)
    db.commit()
    key_statement(
        db,
        banking,
        lines=[(SEP_10, "BULK PAYMENT PYR-000001", -RUN_TOTAL)],
        opening=OPENING,
    )
    db.flush()

    result = matching.auto_match(
        db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
    )
    assert len(result.matched) == 1, "one statement line, one match"
    match = result.matched[0]
    assert match.rule == BankMatchRule.PAYMENT_RUN

    statement_ids, journal_ids = matching.members_of(db, banking.company_id, match.id)
    assert len(statement_ids) == 1
    assert len(journal_ids) == 3, "one per settlement — the run's N ledger lines"

    settlement_entries = {
        db.get(PartnerDocument, line.settlement_document_id).journal_entry_id
        for line in payment_run_service.lines_of(db, banking.company_id, posted.id)
    }
    matched_entries = {
        db.get(JournalLine, journal_line_id).entry_id for journal_line_id in journal_ids
    }
    assert matched_entries == settlement_entries

    assert_bank_invariants(db, banking.company_id)


# --- Reversal ------------------------------------------------------------------------------------


def test_reversing_the_run_restores_every_open_item(
    db: Session, banking: Banking, run: Run
) -> None:
    """The tape's row 15 at three suppliers instead of one: every invoice open again, every
    settlement reversed, the bank back where it started, and the run `reversed`."""
    posted = _post(db, banking, run)
    db.commit()

    payment_run_service.reverse_run(
        db,
        banking.company_id,
        posted.id,
        reason="the transfer was recalled",
        actor=banking.owner,
    )
    db.flush()

    assert posted.status == PaymentRunStatus.REVERSED
    assert posted.reversal_reason == "the transfer was recalled"
    assert recompute_open_amount(db, run.sin1) == S1_INVOICE
    assert recompute_open_amount(db, run.sin2) == S2_INVOICE, "discount and all"
    assert recompute_open_amount(db, run.sin3) == S3_INVOICE
    for line in payment_run_service.lines_of(db, banking.company_id, posted.id):
        settlement = db.get(PartnerDocument, line.settlement_document_id)
        assert settlement.status == DocumentStatus.REVERSED
    assert _balance(db, banking) == OPENING

    assert_subledger_invariants(db, banking.company_id)
    assert_bank_invariants(db, banking.company_id)


def test_reversing_the_run_releases_its_match(
    db: Session, banking: Banking, run: Run
) -> None:
    """The statement line is unmatched again and the reversing entries are outstanding — which
    is the right answer until the bank returns the money or the accountant posts what
    happened."""
    posted = _post(db, banking, run)
    db.commit()
    key_statement(
        db,
        banking,
        lines=[(SEP_10, "BULK PAYMENT PYR-000001", -RUN_TOTAL)],
        opening=OPENING,
    )
    db.flush()
    matching.auto_match(
        db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
    )
    db.flush()
    assert len(matching.matches_of(db, banking.company_id, banking.bank("BK-RWF").id)) == 1

    payment_run_service.reverse_run(
        db, banking.company_id, posted.id, reason="recalled", actor=banking.owner
    )
    db.flush()

    assert matching.matches_of(db, banking.company_id, banking.bank("BK-RWF").id) == []
    unmatched = list(
        db.scalars(
            matching.unmatched_statement_lines(
                db, banking.company_id, banking.bank("BK-RWF")
            )
        )
    )
    assert len(unmatched) == 1, "the bank's line is a line nobody has explained again"
    assert_bank_invariants(db, banking.company_id)


def test_a_run_inside_a_locked_reconciliation_is_not_reversible(
    db: Session, banking: Banking, run: Run
) -> None:
    """`reconciliation_locked`, raised **before the first unallocation**.

    A match inside a locked reconciliation is part of a proof somebody signed. Asking first is
    what keeps a refused reversal from leaving the run half undone — asserted below by the
    invoices still being closed after the refusal.
    """
    posted = _post(db, banking, run)
    db.commit()
    key_statement(
        db,
        banking,
        lines=[(SEP_10, "BULK PAYMENT PYR-000001", -RUN_TOTAL)],
        opening=OPENING,
        closing=CLOSING,
    )
    db.flush()
    matching.auto_match(
        db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
    )
    _tick_the_opening(db, banking)
    reconciliation = reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        reconciliation_date=SEP_30,
        statement_balance=CLOSING,
        actor=banking.owner,
    )
    db.flush()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.flush()

    with pytest.raises(LedgerStateError) as error:
        payment_run_service.reverse_run(
            db, banking.company_id, posted.id, reason="recalled", actor=banking.owner
        )
    assert error.value.code == "reconciliation_locked"

    assert posted.status == PaymentRunStatus.POSTED
    assert recompute_open_amount(db, run.sin1) == Decimal(0), "nothing was unallocated"


def test_a_member_settlement_cannot_be_reversed_on_its_own(
    db: Session, banking: Banking, run: Run
) -> None:
    """`payment_run_member`, on the **document path** — the AP document screen, the API, and
    anything else that reaches `reverse_document`. A bulk transfer is one banking act."""
    posted = _post(db, banking, run)
    db.commit()
    settlement = db.get(
        PartnerDocument,
        payment_run_service.lines_of(db, banking.company_id, posted.id)[
            0
        ].settlement_document_id,
    )

    with pytest.raises(LedgerStateError) as error:
        documents_service.reverse_document(
            db,
            settlement,
            on_date=SEP_10,
            reason="wrong beneficiary",
            actor=banking.owner,
        )
    assert error.value.code == "payment_run_member"
    assert posted.number in str(error.value), "the refusal names the run to reverse"


def test_a_member_of_a_reversed_run_is_an_ordinary_document_again(
    db: Session, banking: Banking, run: Run
) -> None:
    """The guard is about a *posted* run. Once the run is reversed its settlements are already
    reversed, so there is nothing left to protect — and the next run's settlements are the ones
    that matter."""
    posted = _post(db, banking, run)
    db.commit()
    payment_run_service.reverse_run(
        db, banking.company_id, posted.id, reason="recalled", actor=banking.owner
    )
    db.flush()

    settlement = db.get(
        PartnerDocument,
        payment_run_service.lines_of(db, banking.company_id, posted.id)[
            0
        ].settlement_document_id,
    )
    with pytest.raises(LedgerStateError) as error:
        documents_service.reverse_document(
            db, settlement, on_date=SEP_10, reason="again", actor=banking.owner
        )
    assert error.value.code == "document_already_reversed", (
        "not payment_run_member — the run no longer owns it"
    )


def test_reversing_a_run_twice_is_refused(
    db: Session, banking: Banking, run: Run
) -> None:
    posted = _post(db, banking, run)
    db.commit()
    payment_run_service.reverse_run(
        db, banking.company_id, posted.id, reason="recalled", actor=banking.owner
    )
    db.flush()
    with pytest.raises(LedgerStateError) as error:
        payment_run_service.reverse_run(
            db, banking.company_id, posted.id, reason="again", actor=banking.owner
        )
    assert error.value.code == "payment_run_already_reversed"


# --- Fallible leg first, proven at the service level (P6's rule) ---------------------------------


def test_reversing_a_settlement_before_unallocating_it_is_refused(
    db: Session, banking: Banking, run: Run
) -> None:
    """**The ordering proof.** `reverse_run` unallocates and then reverses; this is what the
    other order does.

    P4's `reverse_document` refuses `document_allocated` while any allocation survives, so a
    reversal-first run would post N reversals and then refuse on the first still-allocated
    settlement — leaving the ledger reversed and the open items not. The refusal is asserted
    here rather than inferred from the comment in `payment_runs.reverse_run`, which is P6's
    rule: an ordering that matters is proven at the service level, not described.
    """
    posted = _post(db, banking, run)
    db.commit()
    settlement = db.get(
        PartnerDocument,
        payment_run_service.lines_of(db, banking.company_id, posted.id)[
            0
        ].settlement_document_id,
    )

    # `in_payment_run=True` is how the run itself gets past `payment_run_member`; with the
    # allocation still standing, the refusal underneath it is the one that fires.
    with pytest.raises(LedgerStateError) as error:
        documents_service.reverse_document(
            db,
            settlement,
            on_date=SEP_10,
            reason="out of order",
            actor=banking.owner,
            in_payment_run=True,
        )
    assert error.value.code == "document_allocated"


def test_the_reversal_refuses_before_it_writes(
    db: Session, banking: Banking, run: Run
) -> None:
    """The other half of the same claim, from the run's side: a reversal that cannot complete
    leaves nothing behind.

    The lock is the refusal that arrives last in wall-clock terms and first in the code, so if
    the ordering were wrong this would find two suppliers unallocated and one not.
    """
    posted = _post(db, banking, run)
    db.commit()
    key_statement(
        db,
        banking,
        lines=[(SEP_10, "BULK PAYMENT PYR-000001", -RUN_TOTAL)],
        opening=OPENING,
        closing=CLOSING,
    )
    db.flush()
    matching.auto_match(
        db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
    )
    _tick_the_opening(db, banking)
    reconciliation = reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        reconciliation_date=SEP_30,
        statement_balance=CLOSING,
        actor=banking.owner,
    )
    db.flush()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.flush()
    before = _balance(db, banking)

    with pytest.raises(LedgerStateError):
        payment_run_service.reverse_run(
            db, banking.company_id, posted.id, reason="recalled", actor=banking.owner
        )

    assert _balance(db, banking) == before
    for invoice in (run.sin1, run.sin2, run.sin3):
        assert recompute_open_amount(db, invoice) == Decimal(0)
    still_matched = [
        match
        for match in matching.matches_of(db, banking.company_id, banking.bank("BK-RWF").id)
        if match.rule == BankMatchRule.PAYMENT_RUN
    ]
    assert len(still_matched) == 1, "the run's match was not released either"
    assert_bank_invariants(db, banking.company_id)


# --- The run a document belongs to ---------------------------------------------------------------


def test_a_settlement_knows_which_run_paid_it(
    db: Session, banking: Banking, run: Run
) -> None:
    """What the AP document detail's "Paid in run PYR-n" reads, and what the reversal guard
    asks. A settlement posted outside a run belongs to none."""
    posted = _post(db, banking, run)
    db.commit()
    member = payment_run_service.lines_of(db, banking.company_id, posted.id)[0]

    found = payment_run_service.run_of_settlement(
        db, banking.company_id, member.settlement_document_id
    )
    assert found is not None
    assert found.number == posted.number

    standalone, _ = documents_service.post_document(
        db,
        banking.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.SETTLEMENT,
            partner_id=run.s3.id,
            document_date=SEP_10 + timedelta(days=1),
            description="cheque",
            amount=Decimal(1000),
            cash_account_id=banking.ledger.acct("1120"),
            instrument_type=InstrumentType.CHEQUE,
        ),
        actor=banking.owner,
    )
    db.flush()
    assert (
        payment_run_service.run_of_settlement(db, banking.company_id, standalone.id) is None
    )


def test_a_run_naming_the_same_invoice_twice_is_refused_before_it_writes(
    db: Session, banking: Banking, run: Run
) -> None:
    """Two rows open on one invoice, or a retry that appended rather than replaced.

    The plain reading passes both lines — each reads the same open amount — and P4 then refuses
    `allocation_exceeds_open_amount` halfway through the posting, with settlements already
    written. `plan()` tracks what is left *within the run*, so the refusal is
    `payment_exceeds_open` and it arrives before the first write.
    """
    with pytest.raises(LedgerStateError) as error:
        payment_run_service.post_run(
            db,
            banking.company_id,
            bank_account_id=banking.bank("BK-RWF").id,
            payment_date=SEP_10,
            lines=[
                payment_run_service.RunLineInput(document_id=run.sin1.id),
                payment_run_service.RunLineInput(document_id=run.sin1.id),
            ],
            actor=banking.owner,
        )
    assert error.value.code == "payment_exceeds_open"
    db.rollback()
    assert (
        db.scalars(
            select(PaymentRun).where(PaymentRun.company_id == banking.company_id)
        ).all()
        == []
    )


def test_a_run_cannot_be_dated_before_an_invoice_it_pays(
    db: Session, banking: Banking, run: Run
) -> None:
    """`payment_run_before_invoice`, and the reason is an invariant rather than a nicety.

    Allocating a payment dated before its invoice puts the payment on the AP control account on
    a date the invoice is not on it yet, so the control account and the open items disagree at
    every date in between — clause 1 of `assert_subledger_invariants`, which the property
    machine broke on a plan that dated a run before the invoice it paid.
    """
    with pytest.raises(LedgerStateError) as error:
        payment_run_service.plan(
            db,
            banking.company_id,
            bank_account_id=banking.bank("BK-RWF").id,
            payment_date=SEP_1 - timedelta(days=1),
            lines=[payment_run_service.RunLineInput(document_id=run.sin1.id)],
        )
    assert error.value.code == "payment_run_before_invoice"


def test_a_partial_payment_takes_no_discount_and_still_reports_one(
    db: Session, banking: Banking, run: Run
) -> None:
    """The discount buys prompt settlement of the account, not a percentage off an instalment.

    The alternative — P4's figure capped at the line's amount — would post a settlement of
    **zero cash** for a line paying less than the discount on offer (1 000 against a 100 000
    invoice at 2/10), which `post_document` refuses after the `PYR-` number was claimed. So a
    partial line pays exactly what was keyed, and `discount_available` keeps reporting what
    settling in full would be worth.
    """
    preview = payment_run_service.plan(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        payment_date=SEP_10,
        lines=[
            payment_run_service.RunLineInput(document_id=run.sin2.id, amount=Decimal(1000))
        ],
    )
    line = preview.suppliers[0].lines[0]
    assert line.discount_available == S2_DISCOUNT, "what paying in full would be worth"
    assert line.discount_amount == Decimal(0)
    assert line.cash_amount == Decimal(1000)
    assert preview.total == Decimal(1000)

    posted = _post_lines(
        db,
        banking,
        [payment_run_service.RunLineInput(document_id=run.sin2.id, amount=Decimal(1000))],
    )
    assert posted.total == Decimal(1000)
    assert recompute_open_amount(db, run.sin2) == S2_INVOICE - Decimal(1000)


def test_settling_the_last_of_a_part_paid_invoice_takes_the_discount(
    db: Session, banking: Banking, run: Run
) -> None:
    """"In full" is the invoice's **remaining** amount, not its total. An invoice part paid
    outside the run still qualifies when the run clears the rest inside the window."""
    _post_lines(
        db,
        banking,
        [payment_run_service.RunLineInput(document_id=run.sin2.id, amount=Decimal(40000))],
    )
    db.flush()
    assert recompute_open_amount(db, run.sin2) == Decimal(60000)

    preview = payment_run_service.plan(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        payment_date=SEP_10,
        lines=[payment_run_service.RunLineInput(document_id=run.sin2.id)],
    )
    line = preview.suppliers[0].lines[0]
    assert line.open_amount == Decimal(60000)
    assert line.amount == Decimal(60000)
    assert line.discount_amount == S2_DISCOUNT, "2 % of the invoice total, P4's own figure"
    assert line.cash_amount == Decimal(58000)
