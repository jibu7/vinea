"""Matching: the assertion that a statement line and a set of ledger lines are one event
(P8 decision 4).

**Rules suggest; people post.** Nothing here writes a journal line, and nothing here posts on
its own: `auto_match` only ever asserts that two things the ledger and the bank *both already
hold* are the same event, and `post_from_statement_line` asks the kernel (or P4) to post and
then records the match in that same transaction. A `bank_rules` row is a *prefill* — it decides
what the drawer opens with, never what is written.

Three properties carry the whole design, and each is enforced rather than assumed:

* **A line is in at most one match.** Both member tables are unique on their line, in the
  database. That is what makes `outstanding` a partition of the account's lines rather than a
  query that happens to agree with one.
* **A match with statement members balances.** Σ statement `amount` == Σ ledger
  `reconciled_amount`, refused otherwise (`match_unbalanced`, with the difference). The
  difference is never stored: it is *posted*, and the posted line joins the match.
* **A match is effective at a date** when every member — statement and ledger — is dated on or
  before it. A receipt dated 30 September matched to a statement credit dated 2 October is
  outstanding at 30 September and reconciled at 31 October, which is what a deposit in transit
  is. `reconciliation.py` reads `effective_at` for exactly that.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.banking import accounts as accounts_service
from app.banking.formats import normalise
from app.core.errors import ConflictError, NotFoundError
from app.kernel.errors import LedgerStateError
from app.kernel.money import base_currency
from app.models.banking import (
    BankAccount,
    BankMatch,
    BankMatchJournalLine,
    BankMatchKind,
    BankMatchRule,
    BankMatchStatementLine,
    BankRule,
    BankStatementLine,
    PaymentRun,
    PaymentRunLine,
    PaymentRunStatus,
)
from app.models.journal import JournalEntry, JournalLine, JournalStatus
from app.models.subledger import PartnerDocument
from app.models.user import User
from app.services.audit import record_audit

ZERO = Decimal(0)

#: How far a statement line's value date may sit from a candidate's entry date, per rule.
#:
#: The `reference` rule is generous because it has a *token* to go on — a document number the
#: bank printed — and a cheque presented six weeks after it was written is the ordinary case it
#: exists for. `amount_date` has nothing but a figure, so it is tight: at ±3 days an equal
#: amount is a coincidence worth acting on, and at ±30 it is a coin toss.
REFERENCE_WINDOW_DAYS = 30
AMOUNT_DATE_WINDOW_DAYS = 3

#: The shortest token the `reference` rule will look for inside a statement line's text.
#: Normalisation strips punctuation, so a two-character token would match inside almost
#: anything — `C1` is a customer code and also the tail of `MOMO 0788 C1234`.
MIN_REFERENCE_TOKEN = 4


@dataclass(frozen=True)
class MatchCandidate:
    """One ledger line a statement line could be. `rule` is why, and `amount` is its reconciled
    amount — the figure the statement side compares against, never the raw `amount`."""

    journal_line_id: int
    entry_id: int
    entry_number: str
    entry_date: date
    doc_type: str
    description: str | None
    reference: str | None
    amount: Decimal
    rule: BankMatchRule


@dataclass
class AutoMatchResult:
    matched: list[BankMatch] = field(default_factory=list)
    #: statement line id → the candidates that tied, for the lines the workspace must ask about.
    ambiguous: dict[int, list[MatchCandidate]] = field(default_factory=dict)

    @property
    def matched_count(self) -> int:
        return len(self.matched)


# --- Reading the two sides ---------------------------------------------------------------------


def unmatched_journal_lines(
    db: Session, company_id: int, row: BankAccount, *, on_or_before: date | None = None
) -> Select:
    """The account's ledger lines that are in no match, as a statement selecting
    `(JournalLine, JournalEntry)`. A `Select` rather than rows, because every caller narrows it
    differently and materialising a tenant's whole bank history to filter it in Python is the
    shape issue #54 was about one table along."""
    member = (
        select(BankMatchJournalLine.id)
        .where(BankMatchJournalLine.journal_line_id == JournalLine.id)
        .correlate(JournalLine)
    )
    statement = (
        select(JournalLine, JournalEntry)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id == row.gl_account_id,
            ~member.exists(),
        )
    )
    if on_or_before is not None:
        statement = statement.where(JournalEntry.entry_date <= on_or_before)
    return statement


def unmatched_statement_lines(
    db: Session, company_id: int, row: BankAccount, *, on_or_before: date | None = None
) -> Select:
    """The account's live statement lines that are in no match. Void lines are excluded here
    rather than by every caller — a voided statement leaves every listing and every count
    (decision 3), and this is the one place that has to remember it."""
    member = (
        select(BankMatchStatementLine.id)
        .where(BankMatchStatementLine.statement_line_id == BankStatementLine.id)
        .correlate(BankStatementLine)
    )
    statement = select(BankStatementLine).where(
        BankStatementLine.company_id == company_id,
        BankStatementLine.bank_account_id == row.id,
        BankStatementLine.is_void.is_(False),
        ~member.exists(),
    )
    if on_or_before is not None:
        statement = statement.where(BankStatementLine.value_date <= on_or_before)
    return statement


# --- The balance rule ---------------------------------------------------------------------------


def _reconciled_total(
    db: Session, row: BankAccount, base_currency_id: int, journal_line_ids: list[int]
) -> Decimal:
    lines = db.scalars(
        select(JournalLine).where(JournalLine.id.in_(journal_line_ids))
    ).all()
    return sum(
        (accounts_service.reconciled_amount(line, row, base_currency_id) for line in lines),
        ZERO,
    )


def _statement_total(db: Session, statement_line_ids: list[int]) -> Decimal:
    if not statement_line_ids:
        return ZERO
    lines = db.scalars(
        select(BankStatementLine).where(BankStatementLine.id.in_(statement_line_ids))
    ).all()
    return sum((line.amount for line in lines), ZERO)


# --- Creating a match ---------------------------------------------------------------------------


def create_match(
    db: Session,
    company_id: int,
    *,
    bank_account_id: int,
    statement_line_ids: list[int],
    journal_line_ids: list[int],
    kind: BankMatchKind,
    rule: BankMatchRule,
    note: str | None = None,
    actor: User,
    request: Request | None = None,
    audit: bool = True,
) -> BankMatch:
    """Assert that these lines are one event.

    Every refusal here is checked **before** the first write, so a rejected match leaves
    nothing behind — the same rule P7's `plan()`-before-write follows, and the reason the
    workspace can show `match_unbalanced` with a difference the user can act on rather than a
    half-written match somebody has to clean up.
    """
    row = accounts_service.get(db, company_id, bank_account_id)
    if not journal_line_ids and not statement_line_ids:
        raise LedgerStateError("A match needs members", code="match_is_empty")

    _refuse_foreign_members(db, company_id, row, statement_line_ids, journal_line_ids)
    _refuse_already_matched(db, statement_line_ids, journal_line_ids)

    if statement_line_ids:
        base = base_currency(db, company_id).id
        statement_total = _statement_total(db, statement_line_ids)
        ledger_total = _reconciled_total(db, row, base, journal_line_ids)
        difference = statement_total - ledger_total
        if difference != ZERO:
            raise LedgerStateError(
                f"The statement side is {statement_total} and the ledger side {ledger_total}; "
                f"the difference of {difference} has to be posted, not matched",
                code="match_unbalanced",
                field_errors={"lines": [f"difference {difference:+f}"]},
            )
    elif not journal_line_ids:  # pragma: no cover - the empty case is refused above
        raise LedgerStateError("A match needs members", code="match_is_empty")

    match = BankMatch(
        company_id=company_id,
        bank_account_id=row.id,
        kind=kind,
        rule=rule,
        matched_by=actor.id,
        matched_at=datetime.now(UTC),
        note=note,
    )
    db.add(match)
    db.flush()
    for statement_line_id in statement_line_ids:
        db.add(
            BankMatchStatementLine(
                company_id=company_id, match_id=match.id, statement_line_id=statement_line_id
            )
        )
    for journal_line_id in journal_line_ids:
        db.add(
            BankMatchJournalLine(
                company_id=company_id, match_id=match.id, journal_line_id=journal_line_id
            )
        )
    db.flush()
    if audit:
        record_audit(
            db,
            company_id=company_id,
            action="bank_match.created",
            entity="bank_matches",
            entity_id=match.id,
            after={
                "bank_account": row.code,
                "kind": kind.value,
                "rule": rule.value,
                "statement_lines": statement_line_ids,
                "journal_lines": journal_line_ids,
            },
            actor_user_id=actor.id,
            actor_email=actor.email,
            request=request,
        )
    return match


def tick(
    db: Session,
    company_id: int,
    *,
    bank_account_id: int,
    journal_line_ids: list[int],
    note: str | None = None,
    actor: User,
    request: Request | None = None,
) -> BankMatch:
    """Paper mode (decision 5): the ledger line is ticked against a statement nobody imported.

    A `tick` has journal members and no statement members, so the balance rule has nothing to
    check — there is no statement side to balance against. The reconciliation's identity is
    unchanged: a ticked line is not outstanding, and the keyed statement balance is what it is
    proved against.
    """
    return create_match(
        db,
        company_id,
        bank_account_id=bank_account_id,
        statement_line_ids=[],
        journal_line_ids=journal_line_ids,
        kind=BankMatchKind.TICK,
        rule=BankMatchRule.TICK,
        note=note,
        actor=actor,
        request=request,
    )


def _refuse_foreign_members(
    db: Session,
    company_id: int,
    row: BankAccount,
    statement_line_ids: list[int],
    journal_line_ids: list[int],
) -> None:
    """Every member belongs to this bank account. Without this a match could assert that a line
    on one account and a line on another are the same event, and both accounts' reconciliations
    would then be reading a figure the other one also claimed."""
    if statement_line_ids:
        foreign = db.scalars(
            select(BankStatementLine.id).where(
                BankStatementLine.id.in_(statement_line_ids),
                or_(
                    BankStatementLine.company_id != company_id,
                    BankStatementLine.bank_account_id != row.id,
                ),
            )
        ).all()
        _refuse_across_accounts(db, statement_line_ids, foreign, "statement")
    if journal_line_ids:
        foreign = db.scalars(
            select(JournalLine.id).where(
                JournalLine.id.in_(journal_line_ids),
                or_(
                    JournalLine.company_id != company_id,
                    JournalLine.gl_account_id != row.gl_account_id,
                ),
            )
        ).all()
        _refuse_across_accounts(db, journal_line_ids, foreign, "ledger")


def _refuse_across_accounts(
    db: Session, requested: list[int], foreign: list[int], side: str
) -> None:
    missing = sorted(set(requested) - _existing_ids(db, requested, side))
    if missing:
        raise NotFoundError(f"{side} line {missing[0]} not found")
    if foreign:
        raise LedgerStateError(
            f"{side} line {sorted(foreign)[0]} belongs to another bank account",
            code="match_across_accounts",
            field_errors={f"{side}_line_ids": ["belongs to another bank account"]},
        )


def _existing_ids(db: Session, ids: list[int], side: str) -> set[int]:
    model = BankStatementLine if side == "statement" else JournalLine
    return set(db.scalars(select(model.id).where(model.id.in_(ids))))


def _refuse_already_matched(
    db: Session, statement_line_ids: list[int], journal_line_ids: list[int]
) -> None:
    """The unique constraints would refuse this too, as an `IntegrityError` with an index name
    in it. Checking here is what turns that into a refusal a screen can render — and it names
    the line, which the constraint does not."""
    if statement_line_ids:
        taken = db.scalar(
            select(BankMatchStatementLine.statement_line_id).where(
                BankMatchStatementLine.statement_line_id.in_(statement_line_ids)
            )
        )
        if taken is not None:
            raise ConflictError(
                f"Statement line {taken} is already matched",
                code="statement_line_matched",
                field_errors={"statement_line_ids": [f"line {taken} is already matched"]},
            )
    if journal_line_ids:
        taken = db.scalar(
            select(BankMatchJournalLine.journal_line_id).where(
                BankMatchJournalLine.journal_line_id.in_(journal_line_ids)
            )
        )
        if taken is not None:
            raise ConflictError(
                f"Ledger line {taken} is already matched",
                code="journal_line_matched",
                field_errors={"journal_line_ids": [f"line {taken} is already matched"]},
            )


def get_match(db: Session, company_id: int, match_id: int) -> BankMatch:
    match = db.get(BankMatch, match_id)
    if match is None or match.company_id != company_id:
        raise NotFoundError("Match not found")
    return match


def unmatch(
    db: Session,
    company_id: int,
    match_id: int,
    *,
    actor: User,
    request: Request | None = None,
) -> None:
    """Delete the assertion. Refused once it belongs to a locked reconciliation.

    Deleting rather than flagging, because a match is an assertion and withdrawing one leaves
    nothing to record — what the *reconciliation* said is the permanent record, and that is a
    stored snapshot which this cannot reach. The refusal is what keeps the two consistent: a
    match inside a locked reconciliation is part of a proof somebody signed.
    """
    match = get_match(db, company_id, match_id)
    assert_unmatchable(db, company_id, match_id)
    record_audit(
        db,
        company_id=company_id,
        action="bank_match.removed",
        entity="bank_matches",
        entity_id=match.id,
        before={"kind": match.kind.value, "rule": match.rule.value},
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )
    db.delete(match)
    db.flush()


def assert_unmatchable(db: Session, company_id: int, match_id: int) -> None:
    """The one refusal `unmatch` raises, asked on its own.

    Separated so a caller that is about to release several matches can hear the refusal
    **before** it starts writing — which is what a payment-run reversal needs: the run's bank
    line may sit inside a locked reconciliation, and finding that out after N unallocations
    would leave the run half undone (decision 7).
    """
    match = get_match(db, company_id, match_id)
    if match.reconciliation_id is None:
        return
    locked = _reconciliation_number(db, company_id, match.reconciliation_id)
    raise LedgerStateError(
        f"This match belongs to {locked}; reopen it first",
        code="reconciliation_locked",
        field_errors={"match_id": [f"locked in {locked}"]},
    )


def _reconciliation_number(db: Session, company_id: int, reconciliation_id: int) -> str:
    from app.models.banking import BankReconciliation

    number = db.scalar(
        select(BankReconciliation.number).where(
            BankReconciliation.company_id == company_id,
            BankReconciliation.id == reconciliation_id,
        )
    )
    return number or f"reconciliation {reconciliation_id}"


# --- Effectiveness ------------------------------------------------------------------------------


def effective_at(db: Session, company_id: int, match_id: int, on_date: date) -> bool:
    """Every member dated on or before `on_date`.

    Both sides, deliberately. A ledger line dated inside the month matched to a statement line
    the bank booked in the next one is a **deposit in transit**: the money is ours at the
    reconciliation date and the bank does not show it yet, so the match is not effective and
    the ledger line is outstanding. Reading only the ledger side would reconcile it a month
    early and the difference would close against a statement that never said so.
    """
    latest_statement = db.scalar(
        select(BankStatementLine.value_date)
        .join(
            BankMatchStatementLine,
            BankMatchStatementLine.statement_line_id == BankStatementLine.id,
        )
        .where(BankMatchStatementLine.match_id == match_id)
        .order_by(BankStatementLine.value_date.desc())
        .limit(1)
    )
    if latest_statement is not None and latest_statement > on_date:
        return False
    latest_ledger = db.scalar(
        select(JournalEntry.entry_date)
        .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
        .join(
            BankMatchJournalLine,
            BankMatchJournalLine.journal_line_id == JournalLine.id,
        )
        .where(
            BankMatchJournalLine.match_id == match_id,
            JournalEntry.company_id == company_id,
        )
        .order_by(JournalEntry.entry_date.desc())
        .limit(1)
    )
    return not (latest_ledger is not None and latest_ledger > on_date)


def effective_match_ids(db: Session, company_id: int, row: BankAccount, on_date: date) -> set[int]:
    """Every match on this account effective at the date, in one pair of queries rather than
    one `effective_at` per match — the reconciliation asks this of every match it holds and the
    per-match version would be a query per row."""
    late_statement = (
        select(BankMatchStatementLine.match_id)
        .join(
            BankStatementLine,
            BankStatementLine.id == BankMatchStatementLine.statement_line_id,
        )
        .where(
            BankMatchStatementLine.company_id == company_id,
            BankStatementLine.value_date > on_date,
        )
    )
    late_ledger = (
        select(BankMatchJournalLine.match_id)
        .join(JournalLine, JournalLine.id == BankMatchJournalLine.journal_line_id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            BankMatchJournalLine.company_id == company_id,
            JournalEntry.entry_date > on_date,
        )
    )
    return set(
        db.scalars(
            select(BankMatch.id).where(
                BankMatch.company_id == company_id,
                BankMatch.bank_account_id == row.id,
                BankMatch.id.not_in(late_statement),
                BankMatch.id.not_in(late_ledger),
            )
        )
    )


# --- The three auto rules (decision 4, in this order) --------------------------------------------


def candidates_for(
    db: Session, company_id: int, row: BankAccount, line: BankStatementLine
) -> list[MatchCandidate]:
    """What this statement line could be, by each rule in turn.

    The order is the decision's: `reference`, then `payment_run`, then `amount_date`. It is an
    order of *evidence*, not of convenience — a document number the bank printed is the bank
    telling you what the line is; a run number is the same thing for a batch; an equal amount
    within three days is a guess that happens to be a good one. A rule is only applied where it
    yields exactly one candidate, so the order decides which evidence wins when two rules both
    have something to say.
    """
    for finder in (_by_reference, _by_payment_run, _by_amount_and_date):
        found = finder(db, company_id, row, line)
        if found:
            return found
    return []


def _open_candidates(
    db: Session, company_id: int, row: BankAccount, *, window: int, line: BankStatementLine
) -> list[tuple[JournalLine, JournalEntry]]:
    statement = unmatched_journal_lines(db, company_id, row).where(
        JournalEntry.entry_date >= line.value_date - timedelta(days=window),
        JournalEntry.entry_date <= line.value_date + timedelta(days=window),
    )
    return list(db.execute(statement).all())


def _candidate(
    line: JournalLine,
    entry: JournalEntry,
    row: BankAccount,
    base_currency_id: int,
    rule: BankMatchRule,
) -> MatchCandidate:
    return MatchCandidate(
        journal_line_id=line.id,
        entry_id=entry.id,
        entry_number=entry.number,
        entry_date=entry.entry_date,
        doc_type=entry.doc_type,
        description=line.description or entry.description,
        reference=entry.reference,
        amount=accounts_service.reconciled_amount(line, row, base_currency_id),
        rule=rule,
    )


def _tokens_of(line: BankStatementLine) -> str:
    """The statement line's text, normalised — upper case, alphanumerics only. A bank that
    writes `INV-1 / C1` one month and `INV-1  C1` the next is writing the same line, and a rule
    that turned on the punctuation would be a coin toss."""
    return normalise(line.description) + normalise(line.reference)


def _by_reference(
    db: Session, company_id: int, row: BankAccount, line: BankStatementLine
) -> list[MatchCandidate]:
    """(i) The statement's own text contains a candidate's document number or reference token.

    The *statement* contains the *ledger's* token, not the other way round: the bank prints its
    narrative around what the payer typed, so `INWARD TRF C1 INV-3` contains `INV-3`. Looking
    for the statement's text inside the ledger's would find nothing.
    """
    text = _tokens_of(line)
    if not text:
        return []
    base = base_currency(db, company_id).id
    found: list[MatchCandidate] = []
    for journal_line, entry in _open_candidates(
        db, company_id, row, window=REFERENCE_WINDOW_DAYS, line=line
    ):
        if accounts_service.reconciled_amount(journal_line, row, base) != line.amount:
            continue
        if any(
            len(token) >= MIN_REFERENCE_TOKEN and token in text
            for token in _reference_tokens(db, company_id, entry)
        ):
            found.append(_candidate(journal_line, entry, row, base, BankMatchRule.REFERENCE))
    return found


def _reference_tokens(db: Session, company_id: int, entry: JournalEntry) -> list[str]:
    """What this entry might be called on a statement: its own number, its reference, and the
    reference of the partner document it came from — a receipt's `reference` is where the
    customer's own quote of an invoice number lands (P4)."""
    tokens = [normalise(entry.number), normalise(entry.reference)]
    document_reference = db.scalar(
        select(PartnerDocument.reference).where(
            PartnerDocument.company_id == company_id,
            PartnerDocument.journal_entry_id == entry.id,
        )
    )
    tokens.append(normalise(document_reference))
    return [token for token in tokens if token]


def _by_payment_run(
    db: Session, company_id: int, row: BankAccount, line: BankStatementLine
) -> list[MatchCandidate]:
    """(ii) The bank's one debit for a batch, matched to **all** of that run's settlement lines.

    The one-to-many decision 7 produces: a bulk transfer shows on the statement as a single
    debit carrying the run's number, while the ledger holds one `PMT-` per supplier. This
    returns every one of the run's lines, so the caller builds a single match with N journal
    members — which then balances by construction, because the run's total is Σ of its
    settlements.

    Finds nothing until P8 step 3 puts rows in `payment_runs`; the rule is here because it is
    the second of decision 4's three and the order is the rule.
    """
    text = _tokens_of(line)
    if not text:
        return []
    base = base_currency(db, company_id).id
    runs = db.scalars(
        select(PaymentRun).where(
            PaymentRun.company_id == company_id,
            PaymentRun.bank_account_id == row.id,
            PaymentRun.status == PaymentRunStatus.POSTED,
        )
    ).all()
    for run in runs:
        if normalise(run.number) not in text:
            continue
        # The bank shows the money leaving, so its line is negative where the run's total is
        # the positive sum of what was paid.
        if -line.amount != run.total:
            continue
        lines = _run_journal_lines(db, company_id, row, run)
        if lines:
            return [
                _candidate(journal_line, entry, row, base, BankMatchRule.PAYMENT_RUN)
                for journal_line, entry in lines
            ]
    return []


def _run_journal_lines(
    db: Session, company_id: int, row: BankAccount, run: PaymentRun
) -> list[tuple[JournalLine, JournalEntry]]:
    settlements = select(PaymentRunLine.settlement_document_id).where(
        PaymentRunLine.company_id == company_id, PaymentRunLine.run_id == run.id
    )
    entries = select(PartnerDocument.journal_entry_id).where(
        PartnerDocument.company_id == company_id, PartnerDocument.id.in_(settlements)
    )
    statement = unmatched_journal_lines(db, company_id, row).where(
        JournalEntry.id.in_(entries)
    )
    return list(db.execute(statement).all())


def _by_amount_and_date(
    db: Session, company_id: int, row: BankAccount, line: BankStatementLine
) -> list[MatchCandidate]:
    """(iii) One unmatched ledger line of equal reconciled amount within ±3 days, and no second.

    The weakest of the three and the one that most needs the uniqueness rule: two receipts of
    the same amount in one week is an ordinary Tuesday for a business with a price list, and
    matching either would be a coin toss the reconciliation would then carry as fact. Where it
    ties, `auto_match` leaves both for a person.
    """
    base = base_currency(db, company_id).id
    return [
        _candidate(journal_line, entry, row, base, BankMatchRule.AMOUNT_DATE)
        for journal_line, entry in _open_candidates(
            db, company_id, row, window=AMOUNT_DATE_WINDOW_DAYS, line=line
        )
        if accounts_service.reconciled_amount(journal_line, row, base) == line.amount
    ]


def auto_match(
    db: Session,
    company_id: int,
    bank_account_id: int,
    *,
    actor: User,
    request: Request | None = None,
) -> AutoMatchResult:
    """Apply the three rules to every unmatched statement line, in order, **only where the
    candidate is unique**, and never touching an existing match.

    Ambiguity is not a match. Where a rule finds two candidates the line is left alone and both
    are reported, because the workspace's job at that point is to show a person the choice —
    and a rule that guessed would put a fact in the reconciliation that nobody checked.
    """
    row = accounts_service.get(db, company_id, bank_account_id)
    result = AutoMatchResult()
    for line in db.scalars(unmatched_statement_lines(db, company_id, row)).all():
        found = candidates_for(db, company_id, row, line)
        if not found:
            continue
        rule = found[0].rule
        # A `payment_run` hit is one match over all of the run's lines — the one-to-many the
        # bank produces. The other two rules are one-to-one, so more than one candidate is a
        # tie rather than a set.
        if rule != BankMatchRule.PAYMENT_RUN and len(found) > 1:
            result.ambiguous[line.id] = found
            continue
        result.matched.append(
            create_match(
                db,
                company_id,
                bank_account_id=row.id,
                statement_line_ids=[line.id],
                journal_line_ids=[candidate.journal_line_id for candidate in found],
                kind=BankMatchKind.AUTO,
                rule=rule,
                actor=actor,
                request=request,
                audit=False,
            )
        )
    if result.matched:
        record_audit(
            db,
            company_id=company_id,
            action="bank_match.auto_matched",
            entity="bank_accounts",
            entity_id=row.id,
            after={
                "matched": result.matched_count,
                "ambiguous": len(result.ambiguous),
                "rules": sorted({match.rule.value for match in result.matched}),
            },
            actor_user_id=actor.id,
            actor_email=actor.email,
            request=request,
        )
    return result


# --- Rules as prefill (decision 4's last paragraph) ----------------------------------------------


@dataclass(frozen=True)
class Prefill:
    """What the post-from-a-statement-line drawer opens with. A suggestion and nothing else:
    the engine's own `not_a_cash_account` and `control_account_manual_posting` refusals are
    what stop a rule from aiming at a control account, and a person still presses Post."""

    rule_id: int | None
    gl_account_id: int | None
    tax_code_id: int | None
    partner_type: str | None
    partner_id: int | None
    description: str
    #: `receipt` on a credit, `payment` on a debit — the line's own sign, not the rule's.
    kind: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "gl_account_id": self.gl_account_id,
            "tax_code_id": self.tax_code_id,
            "partner_type": self.partner_type,
            "partner_id": self.partner_id,
            "description": self.description,
            "kind": self.kind,
        }


def prefill_for(db: Session, company_id: int, line: BankStatementLine) -> Prefill:
    """The highest-priority active rule whose pattern the line's description contains,
    case-insensitively — or the account defaults where none matches.

    Matching on the raw description rather than the normalised form, because a rule is written
    by a person looking at their own statement: they type `ACCOUNT FEE` because that is what
    the bank prints, and normalising both sides would make `ACCOUNTFEE` match too, which is a
    pattern nobody meant.
    """
    kind = "receipt" if line.amount > ZERO else "payment"
    rules = db.scalars(
        select(BankRule)
        .where(
            BankRule.company_id == company_id,
            BankRule.bank_account_id == line.bank_account_id,
            BankRule.is_active,
        )
        .order_by(BankRule.priority, BankRule.id)
    ).all()
    text = (line.description or "").casefold()
    for rule in rules:
        if rule.pattern.casefold() in text:
            return Prefill(
                rule_id=rule.id,
                gl_account_id=rule.gl_account_id,
                tax_code_id=rule.tax_code_id,
                partner_type=rule.partner_type,
                partner_id=rule.partner_id,
                description=rule.description or line.description,
                kind=rule.kind or kind,
            )
    return Prefill(
        rule_id=None,
        gl_account_id=_default_account(db, company_id, kind),
        tax_code_id=None,
        partner_type=None,
        partner_id=None,
        description=line.description,
        kind=kind,
    )


def _default_account(db: Session, company_id: int, kind: str) -> int | None:
    """`bank_charges_account_id` on a payment, `bank_interest_account_id` on a receipt — the
    two `gl_settings` keys P8 step 1 seeded, and the reason they are *defaults for a drawer*
    rather than a posting map: a debit the ledger lacks is usually a fee and a credit is
    usually interest, and the person posting may say otherwise."""
    from app.models.gl import GLSettings

    settings = db.scalar(select(GLSettings).where(GLSettings.company_id == company_id))
    if settings is None:
        return None
    return (
        settings.bank_interest_account_id
        if kind == "receipt"
        else settings.bank_charges_account_id
    )


def matches_of(db: Session, company_id: int, bank_account_id: int) -> list[BankMatch]:
    return list(
        db.scalars(
            select(BankMatch)
            .where(
                BankMatch.company_id == company_id,
                BankMatch.bank_account_id == bank_account_id,
            )
            .order_by(BankMatch.id)
        )
    )


def members_of(db: Session, company_id: int, match_id: int) -> tuple[list[int], list[int]]:
    """`(statement line ids, journal line ids)`."""
    statement_lines = list(
        db.scalars(
            select(BankMatchStatementLine.statement_line_id).where(
                BankMatchStatementLine.company_id == company_id,
                BankMatchStatementLine.match_id == match_id,
            )
        )
    )
    journal_lines = list(
        db.scalars(
            select(BankMatchJournalLine.journal_line_id).where(
                BankMatchJournalLine.company_id == company_id,
                BankMatchJournalLine.match_id == match_id,
            )
        )
    )
    return statement_lines, journal_lines


def match_by_journal_line(db: Session, company_id: int, journal_line_id: int) -> BankMatch | None:
    """The match a ledger line sits in, for the GL entry page's "matched / `BRC-n` /
    outstanding" column (decision 10)."""
    return db.scalar(
        select(BankMatch)
        .join(BankMatchJournalLine, BankMatchJournalLine.match_id == BankMatch.id)
        .where(
            BankMatch.company_id == company_id,
            BankMatchJournalLine.journal_line_id == journal_line_id,
        )
    )


# --- Posting from a statement line (decision 4's last mechanism) ---------------------------------
#
# **This is how the ledger catches up with the bank, and the only way.** A statement line the
# ledger lacks — a fee, interest, a deposit nobody keyed — is not adjusted into the
# reconciliation and is not turned into a journal line by this module. It is *posted*, through
# the kernel or through P4, and the resulting line then joins a `posted` match.
#
# The posting and the match are written in **one transaction**, which is the rule that makes the
# whole thing safe in both directions: a posted line is never left unmatched (the user would
# post it twice), and a failed posting leaves no match (the reconciliation would claim a line
# that does not exist). Neither half is recoverable by a person looking at the screen
# afterwards, which is why it is a transaction rather than two calls and a retry.


@dataclass(frozen=True)
class PostedFromStatement:
    """What the drawer produced: the entry, the line on the bank account, and the match."""

    entry_id: int
    entry_number: str
    journal_line_id: int
    match: BankMatch
    #: The P4 document, where the drawer posted a receipt or a payment rather than a cashbook
    #: entry. `None` for a cashbook entry, which has no document of its own.
    document_id: int | None = None
    document_number: str | None = None


def _bank_line_of(
    db: Session, company_id: int, entry_id: int, row: BankAccount
) -> JournalLine:
    """The one line of this entry that sits on the bank account.

    Exactly one, and asserted rather than assumed: a `CashbookEntry` derives a single bank line
    for the gross (P2), and a P4 settlement posts one line to its `cash_account_id`. If a
    future event ever posted two, matching one of them would silently leave the other
    outstanding forever — so this refuses rather than picking.
    """
    lines = db.scalars(
        select(JournalLine).where(
            JournalLine.company_id == company_id,
            JournalLine.entry_id == entry_id,
            JournalLine.gl_account_id == row.gl_account_id,
        )
    ).all()
    if len(lines) != 1:
        raise LedgerStateError(
            f"The posting produced {len(lines)} lines on {row.code}; a statement line matches "
            "exactly one",
            code="posting_is_not_one_bank_line",
        )
    return lines[0]


def _refuse_if_matched(db: Session, line: BankStatementLine) -> None:
    taken = db.scalar(
        select(BankMatchStatementLine.match_id).where(
            BankMatchStatementLine.statement_line_id == line.id
        )
    )
    if taken is not None:
        raise ConflictError(
            f"Statement line {line.id} is already matched",
            code="statement_line_matched",
            field_errors={"statement_line_id": ["already matched"]},
        )


def get_statement_line(db: Session, company_id: int, line_id: int) -> BankStatementLine:
    line = db.get(BankStatementLine, line_id)
    if line is None or line.company_id != company_id:
        raise NotFoundError("Statement line not found")
    if line.is_void:
        raise LedgerStateError(
            "That statement line belongs to a voided statement",
            code="statement_line_void",
        )
    return line


def post_cashbook_from_line(
    db: Session,
    company_id: int,
    statement_line_id: int,
    *,
    gl_account_id: int,
    tax_code_id: int | None = None,
    description: str | None = None,
    reference: str | None = None,
    entry_date: date | None = None,
    branch_id: int | None = None,
    project_id: int | None = None,
    partner_type: str | None = None,
    partner_id: int | None = None,
    actor: User,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> PostedFromStatement:
    """Post the fee, the interest, the bank's own movement — as a `CashbookEntry`, and match it.

    The kernel decides everything about the posting: the sign follows the statement line (a
    credit is a receipt, a debit a payment), the amount is the line's own, and the bank side is
    the *derived* line `CashbookEntry` produces. Nothing here writes a journal line; the engine
    does, and its `not_a_cash_account` and `control_account_manual_posting` refusals are what
    stop a `bank_rules` row from aiming the drawer at a control account.
    """
    from app.kernel import posting as posting_service
    from app.kernel.events import CashbookEntry, CashbookKind, CashbookLineSpec

    line = get_statement_line(db, company_id, statement_line_id)
    _refuse_if_matched(db, line)
    row = accounts_service.get(db, company_id, line.bank_account_id)

    kind = CashbookKind.RECEIPT if line.amount > ZERO else CashbookKind.PAYMENT
    event = CashbookEntry(
        entry_date=entry_date or line.value_date,
        description=description or line.description,
        reference=reference if reference is not None else line.reference,
        branch_id=branch_id,
        idempotency_key=idempotency_key,
        cash_account_id=row.gl_account_id,
        kind=kind,
        # The account's own currency, so a foreign-currency account's line is posted in the
        # currency decision 2's rule requires of it — and a base-currency account's in base,
        # which is what its statement is denominated in.
        currency_id=row.currency_id,
        lines=(
            CashbookLineSpec(
                gl_account_id=gl_account_id,
                amount=abs(line.amount),
                tax_code_id=tax_code_id,
                branch_id=branch_id,
                project_id=project_id,
                partner_type=partner_type,
                partner_id=partner_id,
                description=description or line.description,
            ),
        ),
    )
    entry = posting_service.post(db, event, company_id=company_id, actor=actor)
    assert entry is not None
    bank_line = _bank_line_of(db, company_id, entry.id, row)
    match = create_match(
        db,
        company_id,
        bank_account_id=row.id,
        statement_line_ids=[line.id],
        journal_line_ids=[bank_line.id],
        kind=BankMatchKind.POSTED,
        rule=BankMatchRule.POSTED_FROM_STATEMENT,
        note=f"posted from statement line {line.id}",
        actor=actor,
        request=request,
        audit=False,
    )
    record_audit(
        db,
        company_id=company_id,
        action="bank_match.posted_from_statement",
        entity="bank_matches",
        entity_id=match.id,
        after={
            "statement_line_id": line.id,
            "entry": entry.number,
            "kind": "cashbook",
            "amount": str(line.amount),
        },
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )
    return PostedFromStatement(
        entry_id=entry.id,
        entry_number=entry.number,
        journal_line_id=bank_line.id,
        match=match,
    )


def post_settlement_from_line(
    db: Session,
    company_id: int,
    statement_line_id: int,
    *,
    partner_id: int,
    description: str | None = None,
    reference: str | None = None,
    document_date: date | None = None,
    instrument_type: Any = None,
    branch_id: int | None = None,
    project_id: int | None = None,
    actor: User,
    permissions: set[str] | None = None,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> PostedFromStatement:
    """Post the customer receipt or the supplier payment the bank is showing, and match it.

    **Unallocated, deliberately.** Deciding which invoices a receipt pays is the AR/AP
    allocation screen's job and P4 already has one, with the realized FX and the settlement
    discount that go with it. A reconciliation that allocated as a side effect would be making
    a subledger decision from a bank statement, which is exactly the direction this phase
    refuses to work in: the bank tells you money moved, not what it was for.

    The role follows the sign, because that is what the bank's own line says: money in is a
    customer receipt, money out is a supplier payment.
    """
    from app.models.partner import PartnerRole
    from app.models.subledger import DocumentKind, InstrumentType
    from app.subledger import documents as documents_service

    line = get_statement_line(db, company_id, statement_line_id)
    _refuse_if_matched(db, line)
    row = accounts_service.get(db, company_id, line.bank_account_id)
    role = PartnerRole.AR if line.amount > ZERO else PartnerRole.AP

    document, _ = documents_service.post_document(
        db,
        company_id,
        role,
        documents_service.DocumentInput(
            kind=DocumentKind.SETTLEMENT,
            partner_id=partner_id,
            document_date=document_date or line.value_date,
            description=description or line.description,
            reference=reference if reference is not None else line.reference,
            currency_id=row.currency_id,
            branch_id=branch_id,
            project_id=project_id,
            amount=abs(line.amount),
            cash_account_id=row.gl_account_id,
            instrument_type=instrument_type or InstrumentType.BANK,
        ),
        actor=actor,
        permissions=permissions,
        idempotency_key=idempotency_key,
        request=request,
    )
    bank_line = _bank_line_of(db, company_id, document.journal_entry_id, row)
    match = create_match(
        db,
        company_id,
        bank_account_id=row.id,
        statement_line_ids=[line.id],
        journal_line_ids=[bank_line.id],
        kind=BankMatchKind.POSTED,
        rule=BankMatchRule.POSTED_FROM_STATEMENT,
        note=f"posted from statement line {line.id}",
        actor=actor,
        request=request,
        audit=False,
    )
    record_audit(
        db,
        company_id=company_id,
        action="bank_match.posted_from_statement",
        entity="bank_matches",
        entity_id=match.id,
        after={
            "statement_line_id": line.id,
            "document": document.number,
            "kind": role.value,
            "amount": str(line.amount),
        },
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )
    return PostedFromStatement(
        entry_id=document.journal_entry_id,
        entry_number=document.number,
        journal_line_id=bank_line.id,
        match=match,
        document_id=document.id,
        document_number=document.number,
    )


# --- The listings the workspace's two panes read (step 5) ---------------------------------------
#
# `unmatched_statement_lines` and `unmatched_journal_lines` above are *filters* the matcher uses.
# What a screen needs is every line with its state, because "matched to what" is the column the
# reconciler reads down. Both panes therefore have a listing of their own here rather than the
# screen calling a filter twice and inferring the difference.


@dataclass(frozen=True)
class LedgerLineRow:
    """One ledger line on a bank account, with what the reconciliation makes of it.

    `reconciled_amount` rather than `amount` or `base_amount`, because the pane sits beside the
    statement and the two must be comparable — one definition, `accounts.reconciled_amount`.
    """

    journal_line_id: int
    entry_id: int
    entry_number: str
    entry_date: date
    doc_type: str
    description: str | None
    reference: str | None
    amount: Decimal
    #: None where the line is outstanding.
    match_id: int | None
    match_kind: BankMatchKind | None
    match_rule: BankMatchRule | None
    #: The `BRC-` this line's match was locked into, if it was.
    reconciliation_number: str | None
    #: Set where the line was posted *after* a locked reconciliation whose date it falls inside
    #: — decision 5's late line. The pane flags it "dated inside BRC-n", which calls for a
    #: different action from an ordinary outstanding item.
    dated_inside: str | None

    @property
    def is_outstanding(self) -> bool:
        return self.match_id is None


def list_ledger_lines(
    db: Session,
    company_id: int,
    bank_account_id: int,
    *,
    as_of: date | None = None,
    outstanding_first: bool = True,
) -> list[LedgerLineRow]:
    """The workspace's right pane: every ledger line on the account, with its match state.

    **Outstanding first** by default, which is decision 7's ordering for the screen and not
    merely a nicety: the pane exists to be worked down, and a reconciler scrolling past forty
    matched lines to reach the two that are not is the reason a reconciliation gets abandoned
    half done. Matched lines follow in date order so the pane still reads as a statement.
    """
    row = accounts_service.get(db, company_id, bank_account_id)
    base = base_currency(db, company_id).id
    amount_column = accounts_service.reconciled_amount_column(row, base)
    # Imported here rather than at module scope: `reconciliation.py` imports this module, so a
    # top-level import would be a cycle. One call site, one lazy import.
    from app.banking.reconciliation import _late_line_labels

    late = _late_line_labels(db, company_id, row)

    statement = (
        select(JournalLine, JournalEntry, amount_column)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id == row.gl_account_id,
            JournalEntry.status == JournalStatus.POSTED,
        )
        .order_by(JournalEntry.entry_date, JournalLine.id)
    )
    if as_of is not None:
        statement = statement.where(JournalEntry.entry_date <= as_of)

    rows: list[LedgerLineRow] = []
    for line, entry, amount in db.execute(statement).all():
        match = match_by_journal_line(db, company_id, line.id)
        number = (
            _reconciliation_number(db, company_id, match.reconciliation_id)
            if match is not None and match.reconciliation_id is not None
            else None
        )
        rows.append(
            LedgerLineRow(
                journal_line_id=line.id,
                entry_id=entry.id,
                entry_number=entry.number,
                entry_date=entry.entry_date,
                doc_type=entry.doc_type,
                description=line.description or entry.description,
                reference=entry.reference,
                amount=amount,
                match_id=match.id if match is not None else None,
                match_kind=match.kind if match is not None else None,
                match_rule=match.rule if match is not None else None,
                reconciliation_number=number,
                dated_inside=late.get(line.id),
            )
        )
    if outstanding_first:
        # Stable: `sorted` keeps the date order above within each group.
        rows.sort(key=lambda item: not item.is_outstanding)
    return rows


@dataclass(frozen=True)
class StatementLineState:
    """What a statement line's match is, for the statement detail and the left pane."""

    statement_line_id: int
    match_id: int | None
    match_kind: BankMatchKind | None
    match_rule: BankMatchRule | None
    reconciliation_number: str | None
    #: How many ledger lines the match holds. Three on the bank's single line for a payment run,
    #: which is the one-to-many decision 7 produces and the number a reader needs to see.
    journal_line_count: int


def statement_line_states(
    db: Session, company_id: int, statement_id: int
) -> dict[int, StatementLineState]:
    """Match state for every line of one statement, keyed by line id.

    One query pass for the whole statement rather than a lookup per line, because the detail
    screen renders every line and the per-line version was a hundred round trips for a
    hundred-line export.
    """
    return _line_states(db, company_id, BankStatementLine.statement_id == statement_id)


def account_statement_lines(
    db: Session, company_id: int, row: BankAccount, *, on_or_before: date | None = None
) -> list[tuple[BankStatementLine, StatementLineState]]:
    """The workspace's **left pane**: the account's live statement lines with their match
    state, **unmatched first**, then matched, each group in value-date order.

    The same ordering argument as `list_ledger_lines`: the pane is worked down, so what still
    needs a person comes first. Void lines are not here — a voided statement leaves every
    listing (decision 3).
    """
    condition = (
        (BankStatementLine.bank_account_id == row.id)
        & BankStatementLine.is_void.is_(False)
    )
    if on_or_before is not None:
        condition = condition & (BankStatementLine.value_date <= on_or_before)
    states = _line_states(db, company_id, condition)
    lines = db.scalars(
        select(BankStatementLine)
        .where(BankStatementLine.company_id == company_id, condition)
        .order_by(BankStatementLine.value_date, BankStatementLine.id)
    ).all()
    paired = [(line, states[line.id]) for line in lines]
    # Stable: `sorted` keeps the date order within each group.
    return sorted(paired, key=lambda pair: pair[1].match_id is not None)


def _line_states(db: Session, company_id: int, condition: Any) -> dict[int, StatementLineState]:
    rows = db.execute(
        select(
            BankStatementLine.id,
            BankMatch.id,
            BankMatch.kind,
            BankMatch.rule,
            BankMatch.reconciliation_id,
        )
        .outerjoin(
            BankMatchStatementLine,
            (BankMatchStatementLine.statement_line_id == BankStatementLine.id)
            & (BankMatchStatementLine.company_id == BankStatementLine.company_id),
        )
        .outerjoin(
            BankMatch,
            (BankMatch.id == BankMatchStatementLine.match_id)
            & (BankMatch.company_id == BankMatchStatementLine.company_id),
        )
        .where(BankStatementLine.company_id == company_id, condition)
    ).all()
    states: dict[int, StatementLineState] = {}
    for line_id, match_id, kind, rule, reconciliation_id in rows:
        states[line_id] = StatementLineState(
            statement_line_id=line_id,
            match_id=match_id,
            match_kind=kind,
            match_rule=rule,
            reconciliation_number=(
                _reconciliation_number(db, company_id, reconciliation_id)
                if reconciliation_id is not None
                else None
            ),
            journal_line_count=(
                db.scalar(
                    select(func.count())
                    .select_from(BankMatchJournalLine)
                    .where(
                        BankMatchJournalLine.company_id == company_id,
                        BankMatchJournalLine.match_id == match_id,
                    )
                )
                or 0
            )
            if match_id is not None
            else 0,
        )
    return states
