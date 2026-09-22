"""The compounding-error property for banking (P8 decision 12).

One Hypothesis machine drives a *random sequence* over three accounts — a base-currency bank
account, a foreign-currency one and a cash account — and asserts the ledger, subledger **and**
bank invariant suites after every single step.

The operations are the phase's: cashbook entries and settlements (some in USD against the RWF
account, which decision 2's one-sided rule allows), statements **generated from the ledger and
then perturbed** — keyed line by line, and sometimes written out as a CSV and imported through
the file path — auto-match, manual n:m matches, ticks, unmatches, opens, locks, reopens, and
supplier payment runs with their reversals. Revaluation joins at step 4.

**The perturbations are the point.** A statement that simply mirrored the ledger would
reconcile on the first try and prove nothing: what this phase has to survive is a bank whose
record differs from ours in all the ordinary ways at once. So the generator drops lines (they
become outstanding), adds lines the ledger has never seen (they have to be posted from the
statement side), shifts value dates across the reconciliation date (deposits in transit), and
re-imports the same file and an overlapping one.

Checking only the end state hides an error one operation introduces and the next one masks,
which on a reconciliation is the ordinary case: a figure that is wrong by one match and right
again after the next lock looks identical to a figure that was never wrong.

It runs at a **0-dp base** (RWF) and a **2-dp base**, because the reconciled-amount rule
crosses a rounding boundary — a USD receipt into a base-currency account is compared on its
`base_amount`, and what that rounds to differs at each scale.

Illegal steps are skipped rather than failed: matching what is already matched, locking at a
difference, reopening what is not the latest. The property under test is the invariant suite,
not the plumbing.

Carries `@pytest.mark.slow`, which is how the nightly deep workflow selects it.
"""

import itertools
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.banking import matching
from app.banking import payment_runs as payment_run_service
from app.banking import reconciliation as reconciliation_service
from app.banking import statements as statements_service
from app.banking.formats import ParsedLine
from app.core.errors import AppError
from app.kernel import posting
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import CashbookEntry, CashbookKind, CashbookLineSpec
from app.models.banking import (
    BankMatch,
    BankMatchKind,
    BankMatchRule,
    BankStatementLine,
    ReconciliationStatus,
)
from app.models.journal import JournalEntry, JournalLine
from app.models.subledger import PartnerDocument
from tests.banking.conftest import Banking, ap_invoice, build_banking
from tests.banking.invariants import assert_bank_invariants
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.invariants import assert_subledger_invariants

_EXAMPLE = itertools.count()
ZERO = Decimal(0)

#: Which refusals the generator actually provoked. **Measured, not assumed** — P6 step 5 watched
#: three consecutive deep passes come back green with a different refusal at zero each time, and
#: every one was a generator defect that would have shipped if the number had not been read by
#: hand.
_REFUSALS: dict[str, int] = {}
#: How far the machine got towards the operations whose interesting case is a *conjunction*
#: rather than a single draw. A census that counted only the refusal cannot tell "the guard
#: held" from "the machine never got near it", which is exactly the confusion that moved five of
#: P6's eight floors to targeted properties at P7.
_REACH: dict[str, int] = {}


def _count(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


#: The refusals a deep pass must actually provoke, and the floor each must clear.
#:
#: **Step 3 moved two in and brought a third with it.** `payment_exceeds_open` needed payment
#: runs and now has them, and `document_not_open` is its neighbour: a run that names an invoice
#: a previous run already closed. Both are a single drawn operation away, which is what this
#: machine is good at, so both are floors here rather than targeted properties.
#:
#: `statement_already_imported` joined them by a different route. Step 2's note said this
#: machine keys its statements line by line, so the file-hash refusal was unreachable and lived
#: in `tests/banking/test_statements.py` by construction. That was true of `import_manual` and
#: is no longer true of the machine: `import_statement_file` writes the generated statement out
#: as a `generic` CSV and imports *those bytes*, which makes the second import of the same file
#: an ordinary draw. The parser-test objection still stands for the dates and the amounts — the
#: CSV is generated from the ledger rather than drawn — but the refusal under test here is the
#: hash, and a hash needs a file.
#:
#: Three rather than one, because one is indistinguishable from a coincidence: a boundary hit
#: once in 300 examples is one the next seed may well miss. Three is not a statistical claim —
#: it is the smallest number that cannot be a single lucky plan.
#: **Three of decision 12's five moved out, over two deep passes**, and the reasoning is the
#: point rather than the list. `match_unbalanced`, `reconciliation_locked` and
#: `statement_lines_unmatched` are *conjunctions* three operations deep:
#:
#: * `match_unbalanced` needs a live statement line and an unmatched ledger line of a
#:   *different* amount both present and both drawn — and this phase's statements are generated
#:   from the ledger, so a blind pair usually balances (46 of 48 did);
#: * `reconciliation_locked` needs a lock to have succeeded, a match to have been assigned to
#:   it, and that match to be the one drawn for unmatching;
#: * `statement_lines_unmatched` needs a statement line still unmatched when a lock is
#:   attempted — and auto-match runs often enough that by lock time there is usually nothing
#:   left. Its *interesting* case is narrower still: a difference of **zero** with an
#:   unexplained line, which is the two-errors-cancelling the refusal ordering exists to stop.
#:
#: The two deep passes reached them 2/0/7 and 1/0/1 against a floor of 3, with every reach
#: counter healthy — the census saying "the generator cannot get there", not "the guard is
#: broken".
#:
#: P7's rule is that the answer to a floor a generator cannot reach is a **targeted property
#: that constructs the precondition**, not a bigger `max_examples`. All three now have one in
#: `tests/banking/test_property_targeted_refusals.py`, and all three are still counted in
#: `_REFUSALS` below — what this machine no longer does is *fail* when a seed misses a chain it
#: was never the right tool for.
#:
#: **`reconciliation_difference` stayed, and it is the counter-example worth keeping in view.**
#: It read zero on the first deep pass too, and moving it out would have been wrong: the cause
#: was that `_lock` only ever keyed the closing figure, so the machine never *tried* to lock at
#: a difference. That is a generator defect, and the fix was one draw in three keying a wrong
#: balance — after which it reached 37. A floor at zero is a question, not an answer; the reach
#: counters are what tell the two cases apart.
#:
#: The two that remain are the ones a single drawn operation can provoke, which is what a
#: machine like this is good at.
REQUIRED_REFUSALS = (
    "reconciliation_difference",
    "bank_account_currency_mismatch",
    "payment_exceeds_open",
    "document_not_open",
    "statement_already_imported",
)
CENSUS_FLOOR = 3

#: What the machine has to *reach* before a zero above means anything. Each of these is a
#: precondition rather than an outcome: `reconciliation_locked` needs a lock to have happened
#: and a match inside it to be chosen for unmatching, and a zero refusal with a zero reach here
#: is a coverage gap rather than a regression.
REQUIRED_REACH = (
    "lock: attempted",
    "lock: attempted at a wrong balance",
    "lock: succeeded",
    "statement: keyed",
    "statement: perturbed with a line the ledger lacks",
    "match: attempted manually",
    "posted from a statement line",
    # Step 3's own preconditions. A payment run needs an open supplier invoice to exist before
    # it can be refused for anything, and a reversal needs a posted run — so a zero against
    # `payment_exceeds_open` means nothing until these two are healthy.
    "payment run: attempted",
    "payment run: posted",
    "statement: imported from a file",
)
REACH_FLOOR = 3

#: Only a run with enough examples can be held to the floors. The per-commit profile draws two
#: and would fail every one, so it stays silent and the nightly deep profile is where the census
#: is enforced — the same split the profiles already make everywhere else.
_FLOORS_FROM_EXAMPLES = 100


def _census() -> str:
    return (
        f"refusals: {dict(sorted(_REFUSALS.items()))}\n"
        f"reach:    {dict(sorted(_REACH.items()))}"
    )


@pytest.fixture(scope="module", autouse=True)
def _report_census():  # noqa: ANN202
    yield
    if _REFUSALS:
        print("\n[property] refusals provoked:", dict(sorted(_REFUSALS.items())))
    if _REACH:
        print("[property] reach:", dict(sorted(_REACH.items())))
    if settings.default.max_examples < _FLOORS_FROM_EXAMPLES:
        return

    short = {
        name: _REFUSALS.get(name, 0)
        for name in REQUIRED_REFUSALS
        if _REFUSALS.get(name, 0) < CENSUS_FLOOR
    }
    unreached = {
        name: _REACH.get(name, 0)
        for name in REQUIRED_REACH
        if _REACH.get(name, 0) < REACH_FLOOR
    }
    assert not unreached, (
        f"the deep pass did not reach {unreached} at least {REACH_FLOOR} times each.\n"
        "These are preconditions, not outcomes: a refusal floor below cannot mean anything "
        "while the machine is not getting to the state that provokes it. Fix the generator "
        "before reading the refusal census.\n" + _census()
    )
    assert not short, (
        f"the deep pass did not provoke {short} at least {CENSUS_FLOOR} times each.\n"
        "A refusal the machine never provokes is a guard this suite does not cover, however "
        "green it looks. Read the reach counters above first — they say whether the guard held "
        "or the generator never got near it, and if it is the latter the fix is a targeted "
        "property that constructs the precondition, not a bigger max_examples (P7's rule).\n"
        + _census()
    )


# --- The plan --------------------------------------------------------------------------------


OPERATIONS = (
    "cashbook_rwf",
    "cashbook_usd_into_rwf",
    "cashbook_on_usd_account",
    #: Decision 2's refusal: an RWF line aimed at the USD account. Drawn deliberately rather
    #: than hoped for, because it is the one refusal in this file that no *legal* sequence
    #: produces — every other one is a state the machine can wander into.
    "cashbook_wrong_currency",
    "cashbook_cash",
    "settlement",
    "supplier_invoice",
    "payment_run",
    "reverse_run",
    "import_statement",
    #: The same generated statement, written out as a `generic` CSV and imported through the
    #: file path — which is the only way `statement_already_imported` is reachable at all.
    "import_statement_file",
    "auto_match",
    "manual_match",
    "tick",
    "unmatch",
    "open_reconciliation",
    "lock",
    "reopen",
)

#: How a generated statement differs from the ledger it was generated from. Weighted towards
#: `faithful` so that reconciliations reach zero often enough for `lock: succeeded` to clear
#: its floor — the interesting perturbations still arrive several times per example.
PERTURBATIONS = ("faithful", "drop_a_line", "add_a_line", "shift_a_date", "faithful")

BASE_DAY = date(2026, 9, 1)


@st.composite
def _plans(draw):  # noqa: ANN001, ANN202
    return draw(
        st.lists(
            st.tuples(
                st.sampled_from(OPERATIONS),
                st.integers(min_value=1, max_value=9),  # amount, in thousands
                st.integers(min_value=0, max_value=40),  # day offset from 1 September
                st.sampled_from(PERTURBATIONS),
                st.integers(min_value=0, max_value=6),  # which row a per-row choice lands on
                # **Its own draw, not a slice of `pick`.** It used to be `pick % 3 == 0`, and
                # step 3's deep pass read `lock: attempted at a wrong balance` at 2 against a
                # floor of 3 over 116 lock attempts — a third of which it should have been.
                # Deriving one decision from another draw's arithmetic makes its frequency a
                # property of Hypothesis's shrinking rather than of the generator, and the
                # census cannot tell that from a guard that stopped firing.
                st.booleans(),  # key the lock balance deliberately wrong
            ),
            min_size=6,
            max_size=22,
        )
    )


def _skip(error: AppError) -> None:
    """An illegal step is skipped, and **counted**. The property under test is the invariant
    suite rather than the plumbing — but a refusal that never happens is a guard nobody is
    exercising, which is what the census is for."""
    _count(_REFUSALS, getattr(error, "code", type(error).__name__))


# --- The operations ----------------------------------------------------------------------------


def _bank_lines(db: Session, banking: Banking, account_code: str) -> list[JournalLine]:
    return list(
        db.scalars(
            select(JournalLine).where(
                JournalLine.company_id == banking.company_id,
                JournalLine.gl_account_id == banking.ledger.acct(account_code),
            )
        )
    )


def _post_cashbook(
    db: Session,
    banking: Banking,
    *,
    account_code: str,
    amount: Decimal,
    on: date,
    currency: str,
) -> None:
    kind = CashbookKind.RECEIPT if amount > ZERO else CashbookKind.PAYMENT
    posting.post(
        db,
        CashbookEntry(
            entry_date=on,
            description="property",
            cash_account_id=banking.ledger.acct(account_code),
            kind=kind,
            currency_id=banking.ledger.cur(currency),
            lines=(
                CashbookLineSpec(
                    gl_account_id=banking.ledger.acct("3400"), amount=abs(amount)
                ),
            ),
        ),
        company_id=banking.company_id,
        actor=banking.owner,
    )


def _statement_rows(
    db: Session, banking: Banking, *, perturbation: str, on: date, pick: int
) -> list[tuple[date, str, Decimal]]:
    """Build a statement **from the ledger** and then perturb it.

    Generated rather than drawn, because a statement of random figures would never match
    anything and the machine would spend every example in the same state. What makes it a test
    is the perturbation: the bank's record differs from ours, and the reconciliation has to say
    how.
    """
    row = banking.bank("BK-RWF")
    unmatched = list(
        db.scalars(
            matching.unmatched_journal_lines(db, banking.company_id, row).order_by(
                JournalLine.id
            )
        )
    )
    if not unmatched:
        return []
    entries = {
        line.id: db.get(JournalEntry, line.entry_id).entry_date for line in unmatched
    }
    base = banking.ledger.cur("RWF")
    rows: list[tuple[date, str, Decimal]] = [
        (
            entries[line.id],
            f"TRF {line.id}",
            line.amount if row.currency_id != base else line.base_amount,
        )
        for line in unmatched
    ]

    if perturbation == "drop_a_line" and len(rows) > 1:
        rows.pop(pick % len(rows))
        _count(_REACH, "statement: perturbed by dropping a line")
    elif perturbation == "add_a_line":
        # A fee the ledger has never seen. The only way to close the reconciliation afterwards
        # is to *post* it from the statement side, which is decision 4's whole mechanism.
        rows.append((on, "MONTHLY ACCOUNT FEE", Decimal(-500)))
        _count(_REACH, "statement: perturbed with a line the ledger lacks")
    elif perturbation == "shift_a_date" and rows:
        index = pick % len(rows)
        value_date, description, amount = rows[index]
        rows[index] = (value_date + timedelta(days=5), description, amount)
        _count(_REACH, "statement: perturbed by shifting a value date")

    return [row_ for row_ in rows if row_[2] != ZERO]


def _key_statement(db: Session, banking: Banking, rows: list[tuple[date, str, Decimal]]) -> None:
    """The keyed path: line by line, as a clerk with a paper statement does it."""
    if not rows:
        return
    parsed = [
        ParsedLine(
            row=index,
            value_date=value_date,
            booking_date=None,
            description=description,
            reference=None,
            amount=amount,
            balance_after=None,
            external_id=None,
            occurrence=sum(
                1
                for earlier in rows[: index - 1]
                if earlier == (value_date, description, amount)
            ),
        )
        for index, (value_date, description, amount) in enumerate(rows, start=1)
    ]
    statements_service.import_manual(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        lines=parsed,
        opening_balance=ZERO,
        closing_balance=sum((line.amount for line in parsed), ZERO),
        actor=banking.owner,
    )
    _count(_REACH, "statement: keyed")


def _import_statement_file(
    db: Session,
    banking: Banking,
    rows: list[tuple[date, str, Decimal]],
    *,
    state: dict,
    pick: int,
) -> None:
    """The **file** path: the same generated statement written out as a `generic` CSV.

    This is what makes `statement_already_imported` reachable at all — the refusal is over the
    file's SHA-256, and there is no file on the keyed path. One draw in three re-offers the
    previous run's bytes rather than building new ones, which is a user finding last month's
    export in their downloads folder and is the whole of what the refusal is for.
    """
    previous = state.get("last_csv")
    if previous is not None and pick % 3 == 0:
        content, file_name = previous
    elif rows:
        content = _generic_csv(rows)
        file_name = f"generated-{len(rows)}-{rows[-1][0]:%Y%m%d}.csv"
    else:
        return
    statements_service.import_statement(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        content=content,
        file_name=file_name,
        actor=banking.owner,
    )
    state["last_csv"] = (content, file_name)
    _count(_REACH, "statement: imported from a file")


def _generic_csv(rows: list[tuple[date, str, Decimal]]) -> bytes:
    """The committed samples' layout — `Date,Description,Reference,Debit,Credit,Balance`, the
    one `formats.GENERIC_PRESET` reads with no mapping on the account."""
    balance = ZERO
    body = ["Date,Description,Reference,Debit,Credit,Balance"]
    for value_date, description, amount in rows:
        balance += amount
        debit = "" if amount > ZERO else str(-amount)
        credit = str(amount) if amount > ZERO else ""
        body.append(
            f"{value_date:%Y-%m-%d},{description},,{debit},{credit},{balance}"
        )
    return ("\r\n".join(body) + "\r\n").encode()


def _run(db: Session, banking: Banking, plan) -> None:  # noqa: ANN001, C901, PLR0912, PLR0915
    #: What the plan has built so far and may refer back to. Payment runs need it: an invoice a
    #: previous run closed is the only way `document_not_open` happens, and a *closed* invoice
    #: is exactly the thing a "list the open ones" helper would hide.
    state: dict = {"invoices": [], "runs": [], "paid": []}
    for operation, magnitude, offset, perturbation, pick, key_it_wrong in plan:
        on = BASE_DAY + timedelta(days=offset)
        amount = Decimal(magnitude * 1000)
        row = banking.bank("BK-RWF")
        try:
            if operation == "cashbook_rwf":
                _post_cashbook(
                    db,
                    banking,
                    account_code="1120",
                    amount=amount if magnitude % 2 else -amount,
                    on=on,
                    currency="RWF",
                )
            elif operation == "cashbook_usd_into_rwf":
                # Decision 2's one-sided rule: legal, and reconciled on its `base_amount`.
                _post_cashbook(
                    db,
                    banking,
                    account_code="1120",
                    amount=Decimal(magnitude),
                    on=on,
                    currency="USD",
                )
                _count(_REACH, "usd line on the base-currency account")
            elif operation == "cashbook_on_usd_account":
                _post_cashbook(
                    db,
                    banking,
                    account_code="1121",
                    amount=Decimal(magnitude),
                    on=on,
                    currency="USD",
                )
            elif operation == "cashbook_wrong_currency":
                _count(_REACH, "currency rule: attempted")
                _post_cashbook(
                    db,
                    banking,
                    account_code="1121",
                    amount=amount,
                    on=on,
                    currency="RWF",
                )
            elif operation == "cashbook_cash":
                _post_cashbook(
                    db,
                    banking,
                    account_code="1110",
                    amount=amount,
                    on=on,
                    currency="RWF",
                )
            elif operation == "settlement":
                _settlement(db, banking, amount=amount, on=on)
            elif operation == "supplier_invoice":
                _supplier_invoice(db, banking, state, amount=amount, on=on)
            elif operation == "payment_run":
                _payment_run(db, banking, state, on=on, pick=pick, magnitude=magnitude)
            elif operation == "reverse_run":
                _reverse_run(db, banking, state, pick=pick)
            elif operation == "import_statement":
                _key_statement(
                    db,
                    banking,
                    _statement_rows(
                        db, banking, perturbation=perturbation, on=on, pick=pick
                    ),
                )
            elif operation == "import_statement_file":
                _import_statement_file(
                    db,
                    banking,
                    _statement_rows(
                        db, banking, perturbation=perturbation, on=on, pick=pick
                    ),
                    state=state,
                    pick=pick,
                )
            elif operation == "auto_match":
                result = matching.auto_match(
                    db, banking.company_id, row.id, actor=banking.owner
                )
                _count(_REACH, "auto-match: run")
                if result.matched:
                    _count(_REACH, "auto-match: matched something")
                if result.ambiguous:
                    _count(_REACH, "auto-match: found a tie and declined")
            elif operation == "manual_match":
                _manual_match(db, banking, pick=pick)
            elif operation == "tick":
                _tick(db, banking, pick=pick)
            elif operation == "unmatch":
                _unmatch(db, banking, pick=pick)
            elif operation == "open_reconciliation":
                reconciliation_service.open_reconciliation(
                    db,
                    banking.company_id,
                    bank_account_id=row.id,
                    reconciliation_date=on,
                    statement_balance=amount,
                    actor=banking.owner,
                )
                _count(_REACH, "reconciliation: opened")
            elif operation == "lock":
                _lock(
                    db,
                    banking,
                    # Half the draws are deliberately out, so both halves of the lock — the one
                    # that closes and the one that is refused — are reached.
                    wrong_by=amount if key_it_wrong else ZERO,
                )
            elif operation == "reopen":
                _reopen(db, banking)
            db.flush()
        except (LedgerStateError, PostingError) as error:
            db.rollback()
            _skip(error)
            continue
        except AppError as error:
            db.rollback()
            _skip(error)
            continue

        assert_ledger_invariants(db, banking.company_id)
        assert_subledger_invariants(db, banking.company_id)
        assert_bank_invariants(db, banking.company_id)


def _settlement(db: Session, banking: Banking, *, amount: Decimal, on: date) -> None:
    from app.models.partner import PartnerRole
    from app.models.subledger import DocumentKind, InstrumentType
    from app.subledger import documents as documents_service

    documents_service.post_document(
        db,
        banking.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.SETTLEMENT,
            partner_id=banking.customer.id,
            document_date=on,
            description="property receipt",
            amount=amount,
            cash_account_id=banking.ledger.acct("1120"),
            instrument_type=InstrumentType.BANK,
        ),
        actor=banking.owner,
    )
    _count(_REACH, "settlement: posted")


def _supplier_invoice(
    db: Session, banking: Banking, state: dict, *, amount: Decimal, on: date
) -> None:
    """Something for a payment run to pay. Posted through P4, like everything else here."""
    invoice = ap_invoice(db, banking, banking.supplier, amount=amount, on=on)
    state["invoices"].append(invoice.id)
    _count(_REACH, "supplier invoice: posted")


def _payment_run(
    db: Session,
    banking: Banking,
    state: dict,
    *,
    on: date,
    pick: int,
    magnitude: int,
) -> None:
    """Pay one or two invoices the plan has posted — **drawn from every invoice, not from the
    open ones**.

    That is deliberate, and it is the same choice `_manual_match` makes about balancing. A
    generator that filtered to what was payable could never provoke `document_not_open`, and
    the census would read zero with every reach counter healthy — the shape P6 step 5 spent a
    phase learning to distrust. One draw in three also asks for more than the invoice has open,
    which is `payment_exceeds_open` arriving from the ordinary case rather than a special one.
    """
    invoices = state["invoices"]
    if not invoices:
        return
    chosen = [invoices[pick % len(invoices)]]
    if len(invoices) > 1 and magnitude % 2:
        chosen.append(invoices[(pick + 1) % len(invoices)])
    # **One draw in three names an invoice a previous run already closed**, which is the whole
    # of `document_not_open`: a selection made while the invoice was open, posted after
    # somebody else paid it. Drawing it blind from `invoices` does not get there — a plan long
    # enough to hold two runs *and* to have them collide on the same invoice is a conjunction,
    # and the first deep pass read this refusal at zero with 302 runs posted. Constructing it
    # in the generator is the same fix `_lock`'s wrong balance had at step 2, for the same
    # reason: the machine should try the thing, not wait to stumble into it.
    if state["paid"] and pick % 3 == 0:
        chosen.insert(0, state["paid"][pick % len(state["paid"])])
    over = Decimal(magnitude * 1000) if pick % 3 == 0 else None
    _count(_REACH, "payment run: attempted")
    run = payment_run_service.post_run(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        payment_date=on,
        lines=[
            payment_run_service.RunLineInput(
                document_id=document_id,
                amount=None if over is None else _open_of(db, document_id) + over,
            )
            for document_id in dict.fromkeys(chosen)
        ],
        actor=banking.owner,
    )
    state["runs"].append(run.id)
    state["paid"].extend(
        line.document_id
        for line in payment_run_service.lines_of(db, banking.company_id, run.id)
    )
    _count(_REACH, "payment run: posted")


def _open_of(db: Session, document_id: int) -> Decimal:
    document = db.get(PartnerDocument, document_id)
    return ZERO if document is None else document.open_amount


def _reverse_run(db: Session, banking: Banking, state: dict, *, pick: int) -> None:
    """Reverse a run drawn blind — including one already reversed, and one whose bank line is
    inside a locked reconciliation. Both are refusals the census counts."""
    runs = state["runs"]
    if not runs:
        return
    _count(_REACH, "run reversal: attempted")
    payment_run_service.reverse_run(
        db,
        banking.company_id,
        runs[pick % len(runs)],
        reason="property",
        actor=banking.owner,
    )
    _count(_REACH, "run reversal: succeeded")


def _manual_match(db: Session, banking: Banking, *, pick: int) -> None:
    """Take one unmatched statement line and one unmatched ledger line and assert they are the
    same event — **without** checking first that they balance.

    Deliberately not clamped. P6's machine used to clamp every match to the remaining quantity,
    which meant the boundary refusal could not be drawn at all and the suite looked as though it
    covered something it never reached. Here the pair is drawn blind, so `match_unbalanced` is
    provoked by the ordinary case rather than by a special one.
    """
    row = banking.bank("BK-RWF")
    statement_lines = list(
        db.scalars(
            matching.unmatched_statement_lines(db, banking.company_id, row).order_by(
                BankStatementLine.id
            )
        )
    )
    journal_lines = list(
        db.scalars(
            matching.unmatched_journal_lines(db, banking.company_id, row).order_by(
                JournalLine.id
            )
        )
    )
    if not statement_lines or not journal_lines:
        return
    _count(_REACH, "match: attempted manually")
    matching.create_match(
        db,
        banking.company_id,
        bank_account_id=row.id,
        statement_line_ids=[statement_lines[pick % len(statement_lines)].id],
        journal_line_ids=[journal_lines[pick % len(journal_lines)].id],
        kind=BankMatchKind.MANUAL,
        rule=BankMatchRule.MANUAL,
        actor=banking.owner,
    )
    _count(_REACH, "match: made manually")


def _tick(db: Session, banking: Banking, *, pick: int) -> None:
    row = banking.bank("BK-RWF")
    journal_lines = list(
        db.scalars(
            matching.unmatched_journal_lines(db, banking.company_id, row).order_by(
                JournalLine.id
            )
        )
    )
    if not journal_lines:
        return
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=row.id,
        journal_line_ids=[journal_lines[pick % len(journal_lines)].id],
        actor=banking.owner,
    )
    _count(_REACH, "tick: made")


def _unmatch(db: Session, banking: Banking, *, pick: int) -> None:
    """Unmatch a match drawn blind — including, when there is one, a match inside a locked
    reconciliation. That is what provokes `reconciliation_locked`, and it is a conjunction
    (a lock, then a match inside it, then that match drawn) which is why `lock: succeeded` is a
    reach floor of its own."""
    matches = list(
        db.scalars(
            select(BankMatch)
            .where(
                BankMatch.company_id == banking.company_id,
                BankMatch.bank_account_id == banking.bank("BK-RWF").id,
            )
            .order_by(BankMatch.id)
        )
    )
    if not matches:
        return
    chosen = matches[pick % len(matches)]
    if chosen.reconciliation_id is not None:
        _count(_REACH, "unmatch: attempted inside a locked reconciliation")
    matching.unmatch(db, banking.company_id, chosen.id, actor=banking.owner)
    _count(_REACH, "unmatch: succeeded")


def _lock(db: Session, banking: Banking, *, wrong_by: Decimal) -> None:
    """Lock the standing reconciliation, keying the balance it would need to close — or, one
    draw in three, a balance that is deliberately out by a drawn amount.

    **The split is the generator fix the first deep pass asked for.** Keying only the closing
    figure made `lock: succeeded` reach 98 and `reconciliation_difference` reach *zero*: the
    machine could never lock at a difference because it never tried to. Both are needed — a
    lock that succeeds is the precondition for `reconciliation_locked` below, and a lock that
    fails is the only way to provoke the refusal — so the draw decides which, rather than the
    code choosing one and the census going quiet about the other.
    """
    standing = reconciliation_service.open_for(
        db, banking.company_id, banking.bank("BK-RWF").id
    )
    if standing is None:
        return
    _count(_REACH, "lock: attempted")
    live = reconciliation_service.live_figures(db, banking.company_id, standing)
    closing = live.ledger_balance - live.outstanding_total
    if wrong_by != ZERO:
        _count(_REACH, "lock: attempted at a wrong balance")
    reconciliation_service.lock(
        db,
        banking.company_id,
        standing.id,
        statement_balance=closing + wrong_by,
        actor=banking.owner,
    )
    _count(_REACH, "lock: succeeded")


def _reopen(db: Session, banking: Banking) -> None:
    latest = reconciliation_service.latest_locked(
        db, banking.company_id, banking.bank("BK-RWF").id
    )
    if latest is None:
        return
    reconciliation_service.reopen(
        db, banking.company_id, latest.id, reason="property", actor=banking.owner
    )
    _count(_REACH, "reopen: succeeded")


def _post_from_a_statement_line(db: Session, banking: Banking) -> None:
    """Close the loop the `add_a_line` perturbation opens: a statement line the ledger lacks is
    posted through the kernel, and the posted line joins the match in the same transaction."""
    row = banking.bank("BK-RWF")
    line = db.scalars(
        matching.unmatched_statement_lines(db, banking.company_id, row)
        .where(BankStatementLine.description == "MONTHLY ACCOUNT FEE")
        .order_by(BankStatementLine.id)
    ).first()
    if line is None:
        return
    matching.post_cashbook_from_line(
        db,
        banking.company_id,
        line.id,
        gl_account_id=banking.ledger.acct("6700"),
        actor=banking.owner,
    )
    _count(_REACH, "posted from a statement line")


# --- The machines ------------------------------------------------------------------------------


@pytest.mark.slow
@given(plan=_plans())
@settings(deadline=None)
def test_the_invariants_hold_over_any_sequence_at_a_zero_decimal_base(
    db: Session, plan
) -> None:  # noqa: ANN001
    """RWF: no minor unit, so a USD receipt into the base-currency account rounds to whole
    francs and the reconciled amount the statement side compares is that rounded figure."""
    banking = build_banking(
        db,
        company_name=f"Property Bank {next(_EXAMPLE)} Ltd",
        email=f"property.bank.{next(_EXAMPLE)}@example.test",
    )
    _run(db, banking, plan)
    # After the plan, close the loop the `add_a_line` perturbation opens — otherwise the fee it
    # invents is only ever an unmatched line, and `posted from a statement line` never reaches
    # its floor.
    try:
        _post_from_a_statement_line(db, banking)
        db.flush()
    except AppError as error:
        db.rollback()
        _skip(error)
    assert_ledger_invariants(db, banking.company_id)
    assert_subledger_invariants(db, banking.company_id)
    assert_bank_invariants(db, banking.company_id)


@pytest.mark.slow
@given(plan=_plans())
@settings(deadline=None)
def test_the_invariants_hold_over_any_sequence_at_a_two_decimal_base(
    db: Session, plan
) -> None:  # noqa: ANN001
    """The same machine at a two-decimal base.

    The reconciled-amount rule crosses a rounding boundary here that it cannot at 0 dp: a USD
    receipt into the base-currency account is compared on its `base_amount`, and what that
    rounds to — and therefore whether a match balances to the cent — differs at each scale.
    """
    banking = build_banking(
        db,
        company_name=f"Property Cent {next(_EXAMPLE)} Ltd",
        email=f"property.cent.{next(_EXAMPLE)}@example.test",
    )
    base = banking.ledger.base
    base.decimal_places = 2
    db.flush()
    _run(db, banking, plan)
    assert_ledger_invariants(db, banking.company_id)
    assert_subledger_invariants(db, banking.company_id)
    assert_bank_invariants(db, banking.company_id)


@pytest.mark.slow
def test_a_locked_reconciliation_survives_everything_posted_after_it(
    db: Session,
) -> None:
    """A targeted property rather than a drawn one, for the reason P7's report gives: the
    interesting case is a **conjunction** — a lock, then postings dated inside it, then a
    recomputation — and a machine that had to stumble into all three would reach it by luck.

    The claim is decision 5's in one line: whatever is posted afterwards, a locked
    reconciliation's stored figures reproduce exactly.
    """
    banking = build_banking(
        db, company_name="Late Lines Ltd", email="late.lines@example.test"
    )
    row = banking.bank("BK-RWF")
    _post_cashbook(
        db, banking, account_code="1120", amount=Decimal(1000), on=BASE_DAY, currency="RWF"
    )
    db.flush()
    line = _bank_lines(db, banking, "1120")[0]
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=row.id,
        journal_line_ids=[line.id],
        actor=banking.owner,
    )
    reconciliation = reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=row.id,
        reconciliation_date=BASE_DAY + timedelta(days=29),
        statement_balance=Decimal(1000),
        actor=banking.owner,
    )
    db.flush()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.flush()
    stored = (reconciliation.ledger_balance, reconciliation.outstanding_total)

    # Everything a month-end clerk might do afterwards, all of it dated inside the locked
    # period, and none of it allowed to move a signed figure.
    for offset, amount in ((3, Decimal(-200)), (10, Decimal(450)), (20, Decimal(-75))):
        _post_cashbook(
            db,
            banking,
            account_code="1120",
            amount=amount,
            on=BASE_DAY + timedelta(days=offset),
            currency="RWF",
        )
        db.flush()
        assert (reconciliation.ledger_balance, reconciliation.outstanding_total) == stored
        assert_bank_invariants(db, banking.company_id)

    late = reconciliation_service.late_lines(db, banking.company_id, reconciliation)
    assert len(late) == 3
    assert all(line_id > reconciliation.high_water_line_id for line_id in late)
    assert reconciliation.status == ReconciliationStatus.LOCKED
