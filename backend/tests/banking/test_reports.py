"""Cashbooks, the reconciliation report and the bank-account enquiry (P8 decisions 6 and 10).

**The tie is the subject of this file.** A report over `journal_lines` that merely looked right
would pass a screenshot; what makes Cashbooks the ledger is that its closing balance is asserted
equal to the figure another part of the system already publishes. Two ties, because the two sides
are denominated differently:

* **base closing == the trial balance**, for every account, at any date — `trial_balance` is a
  base-currency report and `closing_base` is Σ `base_amount`, so this is the same sum twice and
  it holds on `BK-RWF` and `BK-USD` alike;
* **currency closing == `period_balances`**, in the account's own currency, **at a period end** —
  the trial balance cannot answer in USD, and the cache is per period so it has no opinion about
  a Tuesday.

The tape's literal is the first one on `BK-RWF`: September opening 1 000 000, receipts 477 000,
payments 476 500, closing 1 000 500 == trial balance `1120` at 30 September.
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.banking import matching, reports
from app.banking import reconciliation as reconciliation_service
from app.kernel import enquiries as kernel_enquiries
from app.models.banking import BankMatchKind, BankMatchRule
from tests.banking.conftest import (
    Banking,
    bank_line_of,
    cashbook,
    key_statement,
)
from tests.kernel.conftest import YEAR

SEP_1 = date(YEAR, 9, 1)
SEP_3 = date(YEAR, 9, 3)
SEP_10 = date(YEAR, 9, 10)
SEP_30 = date(YEAR, 9, 30)
AUG_31 = date(YEAR, 8, 31)


def _tb_figure(db: Session, banking: Banking, code: str, *, as_of: date) -> Decimal:
    """The trial balance's own figure for one account: debit less credit, in base."""
    balance = kernel_enquiries.trial_balance(db, banking.company_id, as_of=as_of)
    row = next(row for row in balance.rows if row.code == code)
    return row.debit - row.credit


# --- The tie -----------------------------------------------------------------------------------


def test_the_base_closing_equals_the_trial_balance(db: Session, banking: Banking) -> None:
    """Decision 6's tie, on a base-currency account, with the tape's own September figures.

    Opening 1 000 000 at 31 August; receipts 477 000 and payments 476 500 in September; closing
    1 000 500 — and that closing is the trial balance figure for `1120`, not a number that
    happens to look like it.
    """
    cashbook(db, banking, account_code="1120", amount=Decimal(1000000), on=AUG_31)
    cashbook(db, banking, account_code="1120", amount=Decimal(477000), on=SEP_3)
    cashbook(db, banking, account_code="1120", amount=Decimal(-476500), on=SEP_10)
    db.commit()

    detail = reports.cashbook_detail(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        date_from=SEP_1,
        date_to=SEP_30,
    )

    assert detail.opening_balance == Decimal(1000000)
    assert detail.receipts_total == Decimal(477000)
    assert detail.payments_total == Decimal(476500)
    assert detail.closing_balance == Decimal(1000500)
    assert detail.closing_base == Decimal(1000500)

    assert _tb_figure(db, banking, "1120", as_of=SEP_30) == Decimal(1000500)
    assert reports.base_closing_ties(db, banking.company_id, detail), (
        "the closing balance is the trial balance's figure, not merely near it"
    )


def test_the_currency_closing_ties_to_period_balances_on_a_foreign_account(
    db: Session, banking: Banking
) -> None:
    """The second tie, where the first cannot reach.

    `BK-USD` closes at USD 495.00 while the trial balance says 653 400 base — the trial balance
    is a base-currency report and cannot answer in USD. `period_balances` carries the currency
    figure per (period, account, branch, currency), so the tie goes there.
    """
    cashbook(
        db, banking, account_code="1121", amount=Decimal("500.00"), on=SEP_3, currency="USD"
    )
    cashbook(
        db, banking, account_code="1121", amount=Decimal("-5.00"), on=SEP_10, currency="USD"
    )
    db.commit()

    detail = reports.cashbook_detail(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-USD").id,
        date_from=SEP_1,
        date_to=SEP_30,
    )

    assert detail.currency_code == "USD"
    assert detail.closing_balance == Decimal("495.00"), "the account's own currency"
    assert detail.closing_base != detail.closing_balance, "and base is a different number"

    assert reports.currency_closing_ties(db, banking.company_id, detail) is True
    assert reports.base_closing_ties(db, banking.company_id, detail), (
        "the base tie holds on a foreign account too"
    )
    assert detail.closing_base == _tb_figure(db, banking, "1121", as_of=SEP_30)


def test_the_currency_tie_declines_a_date_that_is_not_a_period_end(
    db: Session, banking: Banking
) -> None:
    """`None`, not `False`. The cache is per period, so mid-month it has nothing to say — and a
    caller that read `None` as a failure would be asserting something unanswerable.

    This is the reading decision 6 needed making explicit: the base tie holds at any date, the
    currency tie only at a period end.
    """
    cashbook(
        db, banking, account_code="1121", amount=Decimal("500.00"), on=SEP_3, currency="USD"
    )
    db.commit()

    mid_month = reports.cashbook_detail(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-USD").id,
        date_from=SEP_1,
        date_to=SEP_10,
    )
    assert reports.currency_closing_ties(db, banking.company_id, mid_month) is None
    assert reports.base_closing_ties(db, banking.company_id, mid_month), (
        "the base tie is not restricted to a period end"
    )


def test_the_opening_balance_excludes_the_first_day_of_the_range(
    db: Session, banking: Banking
) -> None:
    """The boundary both openings have to agree on. A line dated exactly `date_from` belongs to
    the range, not to the opening — counted in both it would double, and the closing would still
    tie, which is what makes this worth its own test."""
    cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_1)
    db.commit()

    detail = reports.cashbook_detail(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        date_from=SEP_1,
        date_to=SEP_30,
    )
    assert detail.opening_balance == Decimal(0)
    assert detail.opening_base == Decimal(0)
    assert detail.receipts_total == Decimal(1000)
    assert detail.closing_balance == Decimal(1000)


# --- The Reconciled column ----------------------------------------------------------------------


def test_the_reconciled_column_says_brc_matched_or_nothing(
    db: Session, banking: Banking
) -> None:
    """Decision 6's third column, all three of its states in one report.

    It reads `matching.match_by_journal_line` — the same function the GL entry page reads — so a
    line cannot say `BRC-000001` here and *outstanding* there.
    """
    locked_entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    matched_entry = cashbook(db, banking, account_code="1120", amount=Decimal(500), on=SEP_10)
    outstanding_entry = cashbook(db, banking, account_code="1120", amount=Decimal(250), on=SEP_10)
    db.commit()

    row = banking.bank("BK-RWF")
    # One ticked and locked into BRC-000001.
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=row.id,
        journal_line_ids=[bank_line_of(db, banking, locked_entry, "1120").id],
        actor=banking.owner,
    )
    reconciliation = reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=row.id,
        reconciliation_date=SEP_3,
        statement_balance=Decimal(1000),
        actor=banking.owner,
    )
    db.flush()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.flush()
    # One matched but in no locked reconciliation.
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=row.id,
        journal_line_ids=[bank_line_of(db, banking, matched_entry, "1120").id],
        actor=banking.owner,
    )
    db.flush()

    detail = reports.cashbook_detail(
        db,
        banking.company_id,
        bank_account_id=row.id,
        date_from=SEP_1,
        date_to=SEP_30,
    )
    labels = {row_.entry_id: row_.reconciled for row_ in detail.rows}

    assert labels[locked_entry.id] == "BRC-000001"
    assert labels[matched_entry.id] == reports.MATCHED_NOT_LOCKED
    assert labels[outstanding_entry.id] is None, "blank means outstanding"


def test_the_description_column_reads_the_entry_not_the_reference_twice(
    db: Session, banking: Banking
) -> None:
    """A cashbook entry's bank line carries its *reference* as the line description — the kernel
    puts it there for the statement matcher. The report has a Reference column of its own, so its
    Description reads the entry's words rather than printing the reference twice."""
    entry = cashbook(
        db,
        banking,
        account_code="1120",
        amount=Decimal(-80000),
        on=SEP_10,
        reference="CHQ004417",
        description="Cheque 004417, cleaning contractor",
    )
    db.commit()
    assert bank_line_of(db, banking, entry, "1120").description == "CHQ004417"

    detail = reports.cashbook_detail(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        date_from=SEP_1,
        date_to=SEP_30,
    )
    [row] = [row for row in detail.rows if row.entry_id == entry.id]
    assert row.reference == "CHQ004417"
    assert row.description == "Cheque 004417, cleaning contractor"


# --- The summary --------------------------------------------------------------------------------


def test_the_summary_agrees_with_the_detail_it_drills_to(
    db: Session, banking: Banking
) -> None:
    """One row per account, and every figure the same one the detail shows.

    Built *from* `cashbook_detail` rather than from a second aggregate query, which is the point:
    P4 shipped a report reading one field while a screen read another, and a summary that
    recomputed could disagree with the page a user opens from it.
    """
    cashbook(db, banking, account_code="1120", amount=Decimal(1000000), on=AUG_31)
    cashbook(db, banking, account_code="1110", amount=Decimal(50000), on=AUG_31)
    cashbook(
        db, banking, account_code="1121", amount=Decimal("500.00"), on=SEP_3, currency="USD"
    )
    db.commit()

    summary = {row.code: row for row in reports.cashbook_summary(
        db, banking.company_id, date_from=SEP_1, date_to=SEP_30
    )}

    assert set(summary) == {"BK-RWF", "BK-USD", "CASH"}
    assert summary["BK-RWF"].opening_balance == Decimal(1000000)
    assert summary["BK-RWF"].closing_balance == Decimal(1000000)
    assert summary["CASH"].closing_balance == Decimal(50000)
    assert summary["BK-USD"].closing_balance == Decimal("500.00")
    assert summary["BK-USD"].currency_code == "USD"
    assert summary["BK-USD"].closing_base != summary["BK-USD"].closing_balance

    for code in summary:
        detail = reports.cashbook_detail(
            db,
            banking.company_id,
            bank_account_id=summary[code].bank_account_id,
            date_from=SEP_1,
            date_to=SEP_30,
        )
        assert summary[code].closing_balance == detail.closing_balance
        assert summary[code].receipts == detail.receipts_total
        assert summary[code].payments == detail.payments_total


# --- The reconciliation report ------------------------------------------------------------------


def test_the_report_shows_the_stored_figures_beside_the_live_ones(
    db: Session, banking: Banking
) -> None:
    """Decision 6's *Posted after lock*, and why both readings are on the page.

    A locked reconciliation said something, and this report prints what it said — reproduced from
    the lines that existed at the lock — beside what the same date computes now. They differ by
    exactly the late lines. Showing only the live figures would silently restate a signed
    document; showing only the stored ones could not explain a difference anybody noticed.
    """
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    db.commit()
    row = banking.bank("BK-RWF")
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=row.id,
        journal_line_ids=[bank_line_of(db, banking, entry, "1120").id],
        actor=banking.owner,
    )
    reconciliation = reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=row.id,
        reconciliation_date=SEP_30,
        statement_balance=Decimal(1000),
        actor=banking.owner,
    )
    db.flush()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.commit()

    # Dated inside the locked period, posted after it: decision 5's late line.
    cashbook(db, banking, account_code="1120", amount=Decimal(-250), on=SEP_10)
    db.commit()

    report = reports.reconciliation_report(db, banking.company_id, reconciliation.id)

    assert report.number == "BRC-000001"
    assert report.status == "locked"
    assert report.stored is not None
    assert report.stored.ledger_balance == Decimal(1000), "what it said"
    assert report.stored.outstanding_total == Decimal(0)
    assert report.live.ledger_balance == Decimal(750), "what today computes"

    assert len(report.posted_after_lock) == 1
    late = report.posted_after_lock[0]
    assert late.amount == Decimal(-250)
    assert late.dated_inside == "BRC-000001", "the flag names the reconciliation it fell inside"

    # The adjusted bank balance is the figure the statement foots to.
    assert report.stored.adjusted_bank_balance == Decimal(1000)


def test_an_open_reconciliation_has_no_stored_reading_and_no_late_lines(
    db: Session, banking: Banking
) -> None:
    """There is no "after" until it is signed, so both are empty rather than zero-filled."""
    cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    db.commit()
    reconciliation = reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        reconciliation_date=SEP_30,
        statement_balance=Decimal(1000),
        actor=banking.owner,
    )
    db.flush()

    report = reports.reconciliation_report(db, banking.company_id, reconciliation.id)

    assert report.status == "open"
    assert report.stored is None
    assert report.posted_after_lock == ()
    assert report.live.outstanding_total == Decimal(1000), "nothing ticked yet"
    assert report.live.difference == Decimal(1000) - Decimal(0)


# --- The enquiry (decision 10) ------------------------------------------------------------------


def test_the_enquiry_answers_every_figure_decision_10_names(
    db: Session, banking: Banking
) -> None:
    """Per account: the book balance both ways, the last locked reconciliation, the open one,
    the unmatched statement lines, the outstanding ledger lines and the latest statement.

    Asserted as *figures* rather than as the presence of keys, for the reason P4's blank
    `partner_name` is remembered: a key with nothing behind it satisfies a schema and a screen
    and nothing else.
    """
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    cashbook(db, banking, account_code="1120", amount=Decimal(-250), on=SEP_10)
    db.commit()
    row = banking.bank("BK-RWF")

    matching.tick(
        db,
        banking.company_id,
        bank_account_id=row.id,
        journal_line_ids=[bank_line_of(db, banking, entry, "1120").id],
        actor=banking.owner,
    )
    locked = reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=row.id,
        reconciliation_date=SEP_3,
        statement_balance=Decimal(1000),
        actor=banking.owner,
    )
    db.flush()
    reconciliation_service.lock(db, banking.company_id, locked.id, actor=banking.owner)
    db.flush()
    key_statement(
        db,
        banking,
        lines=[(SEP_10, "A DEPOSIT NOBODY KEYED", Decimal(4000))],
        opening=Decimal(1000),
    )
    # Opened and left open: the enquiry names it as the standing reconciliation.
    reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=row.id,
        reconciliation_date=SEP_30,
        statement_balance=Decimal(5000),
        actor=banking.owner,
    )
    db.flush()

    enquiry = reports.bank_account_enquiry(
        db, banking.company_id, row.id, as_of=SEP_30
    )

    assert enquiry.code == "BK-RWF"
    assert enquiry.kind == "bank"
    assert enquiry.book_balance == Decimal(750)
    assert enquiry.book_balance_base == Decimal(750), "base-currency account, so the same"
    assert enquiry.last_reconciliation_number == "BRC-000001"
    assert enquiry.last_reconciled_at == SEP_3
    assert enquiry.last_reconciled_balance == Decimal(1000)
    assert enquiry.open_reconciliation_number == "BRC-000002"
    assert enquiry.unmatched_statement_count == 1
    assert enquiry.unmatched_statement_total == Decimal(4000)
    assert enquiry.outstanding_count == 1, "the −250 nobody has matched"
    assert enquiry.outstanding_total == Decimal(-250)
    assert enquiry.latest_statement_number == "BST-000001"
    assert enquiry.latest_statement_to == SEP_10


def test_the_enquiry_reads_the_foreign_account_in_both_currencies(
    db: Session, banking: Banking
) -> None:
    """`book_balance` in USD, `book_balance_base` in francs — the two figures decision 10 names,
    and the reason it names both: a USD account's balance means nothing to a balance sheet and
    its base value means nothing to the bank."""
    cashbook(
        db, banking, account_code="1121", amount=Decimal("500.00"), on=SEP_3, currency="USD"
    )
    db.commit()

    enquiry = reports.bank_account_enquiry(
        db, banking.company_id, banking.bank("BK-USD").id, as_of=SEP_30
    )

    assert enquiry.currency_code == "USD"
    assert enquiry.book_balance == Decimal("500.00")
    assert enquiry.book_balance_base != enquiry.book_balance
    assert enquiry.last_reconciliation_number is None
    assert enquiry.open_reconciliation_number is None
    assert enquiry.latest_statement_number is None


def test_a_matched_line_is_not_outstanding_on_the_enquiry(
    db: Session, banking: Banking
) -> None:
    """The anti-vacuity half of the outstanding count: match the line and the count falls.

    Without this the assertion above would pass over a query that counted every line on the
    account, which is the shape of defect P4's step-4 review found six of.
    """
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    db.commit()
    row = banking.bank("BK-RWF")
    before = reports.bank_account_enquiry(
        db, banking.company_id, row.id, as_of=SEP_30
    )
    assert before.outstanding_count == 1

    key_statement(db, banking, lines=[(SEP_3, "A DEPOSIT", Decimal(1000))])
    db.flush()
    line = db.scalars(
        matching.unmatched_statement_lines(db, banking.company_id, row)
    ).one()
    matching.create_match(
        db,
        banking.company_id,
        bank_account_id=row.id,
        statement_line_ids=[line.id],
        journal_line_ids=[bank_line_of(db, banking, entry, "1120").id],
        kind=BankMatchKind.MANUAL,
        rule=BankMatchRule.MANUAL,
        actor=banking.owner,
    )
    db.flush()

    after = reports.bank_account_enquiry(db, banking.company_id, row.id, as_of=SEP_30)
    assert after.outstanding_count == 0
    assert after.outstanding_total == Decimal(0)
    assert after.unmatched_statement_count == 0


# --- The posting group key (the owner's pin at the step-3 gate) ---------------------------------


def test_the_revaluation_group_key_is_scope_currency_and_account(
    db: Session, banking: Banking
) -> None:
    """**Pins the shape of `_entry_lines`' group key**, which P7's own tests do not.

    P7 asserted the *figures* the map produces, and those assertions survived P8's widening
    untouched — which is exactly why the key itself needs a test of its own: a future change
    could go back to `(role, currency)` and every P7 test would still pass while two bank
    accounts in one currency quietly netted into one `1130` line.

    The key is `(scope, currency_id, bank_account_id)`, with the account set only on a bank
    group. Asserted over a run that carries both kinds at once, so the AR/AP half and the bank
    half are pinned in the same breath.
    """
    from app.models.fiscalization import FxRevaluationRole
    from app.subledger import revaluation as revaluation_service
    from tests.banking.test_bank_revaluation import (
        _a_usd_bank_balance,
        _a_usd_supplier_invoice,
        _rates,
    )

    _rates(db, banking)
    _a_usd_bank_balance(db, banking)
    _a_usd_supplier_invoice(db, banking)
    db.commit()

    view = revaluation_service.preview(
        db, banking.company_id, revaluation_date=SEP_30, role=FxRevaluationRole.ALL
    )
    groups = view.by_group()

    assert all(len(key) == 3 for key in groups), "(scope, currency_id, bank_account_id)"
    usd = banking.ledger.cur("USD")
    assert ("ap", usd, None) in groups, "a subledger group names no account"
    assert ("bank", usd, banking.bank("BK-USD").id) in groups, "a bank group names its account"
    assert len(groups) == 2

    # And the sort the entry lines are emitted in is total over the key, so a bank group with a
    # None account id cannot collide with one that has an id.
    assert sorted(groups, key=lambda key: (key[0], key[1], key[2] or 0))[0][0] == "ap"


def test_the_group_key_keeps_two_accounts_in_one_currency_apart(
    db: Session, banking: Banking
) -> None:
    """The regression the key exists to prevent, stated as a property of the key rather than of
    the posted entry: two bank groups in one currency are two keys."""
    from app.banking import accounts as accounts_service
    from app.kernel import accounts as kernel_accounts
    from app.models.fiscalization import FxRevaluationRole
    from app.models.gl import AccountClass, ControlType
    from app.subledger import revaluation as revaluation_service
    from tests.banking.test_bank_revaluation import _a_usd_bank_balance, _rates

    _rates(db, banking)
    _a_usd_bank_balance(db, banking)
    second_gl = kernel_accounts.create_account(
        db,
        banking.company_id,
        kernel_accounts.AccountInput(
            code="1122",
            name="Bank Account USD Two",
            class_=AccountClass.ASSET,
            parent_id=banking.ledger.acct("1100"),
            control_type=ControlType.BANK,
        ),
        actor=banking.owner,
    )
    second = accounts_service.ensure_row(db, second_gl)
    assert second is not None
    accounts_service.update(
        db, second, code="BK-USD-2", currency_id=banking.ledger.cur("USD"), actor=banking.owner
    )
    banking.ledger.accounts["1122"] = second_gl
    db.flush()
    cashbook(
        db, banking, account_code="1122", amount=Decimal("200.00"), on=SEP_3, currency="USD"
    )
    db.commit()

    groups = revaluation_service.preview(
        db, banking.company_id, revaluation_date=SEP_30, role=FxRevaluationRole.BANK
    ).by_group()

    assert len(groups) == 2
    assert {key[2] for key in groups} == {banking.bank("BK-USD").id, second.id}
    assert len({key[1] for key in groups}) == 1, "one currency, and still two groups"


def test_the_cashbook_reads_a_reversed_entry_as_two_lines(
    db: Session, banking: Banking
) -> None:
    """Append-only, seen from the report's side. A correction is a reversing entry, so the
    cashbook shows both and the closing balance nets — never one edited row."""
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    db.commit()
    from app.kernel import posting
    from app.kernel.events import ReversalRequested

    posting.post(
        db,
        ReversalRequested(
            entry_date=SEP_10,
            entry_id=entry.id,
            reason="keyed against the wrong account",
            description="Reversal",
        ),
        company_id=banking.company_id,
        actor=banking.owner,
    )
    db.flush()

    detail = reports.cashbook_detail(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        date_from=SEP_1,
        date_to=SEP_30,
    )
    assert len(detail.rows) == 2
    assert detail.receipts_total == Decimal(1000)
    assert detail.payments_total == Decimal(1000)
    assert detail.closing_balance == Decimal(0)
    assert reports.base_closing_ties(db, banking.company_id, detail)
    assert detail.rows[-1].running_balance == Decimal(0)


def test_a_range_with_no_lines_still_carries_its_opening(
    db: Session, banking: Banking
) -> None:
    """An empty month is not a blank report: the opening and closing are the balance brought
    forward, and the tie still holds. An empty-state that showed zero would be the "Nothing to
    report over a full subledger" defect rule 13 was written for."""
    cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=AUG_31)
    db.commit()

    detail = reports.cashbook_detail(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        date_from=SEP_1,
        date_to=SEP_30,
    )
    assert detail.rows == ()
    assert detail.opening_balance == Decimal(1000)
    assert detail.closing_balance == Decimal(1000)
    assert reports.base_closing_ties(db, banking.company_id, detail)
    assert _tb_figure(db, banking, "1120", as_of=SEP_30) == Decimal(1000)


def test_a_range_before_the_next_period_end_ties_on_base_only(
    db: Session, banking: Banking
) -> None:
    """The two ties' different reach, restated on the account the tape uses: mid-month the base
    tie holds and the currency one declines, and at the period end both hold."""
    cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    db.commit()

    for date_to, currency_tie in ((SEP_3 + timedelta(days=2), None), (SEP_30, True)):
        detail = reports.cashbook_detail(
            db,
            banking.company_id,
            bank_account_id=banking.bank("BK-RWF").id,
            date_from=SEP_1,
            date_to=date_to,
        )
        assert reports.base_closing_ties(db, banking.company_id, detail)
        assert reports.currency_closing_ties(db, banking.company_id, detail) is currency_tie
