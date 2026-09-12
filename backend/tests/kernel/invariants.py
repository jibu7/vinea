"""The accounting invariant suite (Master Plan §6). Call `assert_ledger_invariants` after
every scenario that moves money; every failure here is product-fatal by definition."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.kernel.balances import verify_period_balances
from app.kernel.enquiries import trial_balance
from app.kernel.money import is_rounded
from app.kernel.sequences import SequenceClaimant, claimants_for
from app.models.currency import Currency
from app.models.fiscal import AccountingPeriod
from app.models.journal import DocumentSequence, JournalEntry, JournalLine, JournalStatus

ZERO = Decimal(0)
_TRAILING_DIGITS = re.compile(r"(\d+)$")


@dataclass(frozen=True)
class LedgerSnapshot:
    """Checksums of every posted line, keyed by entry id — compare two snapshots to prove
    that nothing already posted was touched in between."""

    checksums: dict[int, str]


def snapshot_ledger(db: Session, company_id: int) -> LedgerSnapshot:
    rows = db.execute(
        text(
            """
            SELECT e.id,
                   md5(string_agg(
                       concat_ws('|', l.id, l.line_no, l.gl_account_id, l.branch_id,
                                 l.project_id, l.currency_id, l.exchange_rate, l.amount,
                                 l.base_amount, l.tax_code_id, l.tax_amount, l.partner_type,
                                 l.partner_id, l.item_id, e.number, e.entry_date, e.period_id,
                                 e.status),
                       ',' ORDER BY l.line_no))
              FROM journal_entries e
              JOIN journal_lines l ON l.entry_id = e.id
             WHERE e.company_id = :company_id AND e.status = 'posted'
             GROUP BY e.id
            """
        ),
        {"company_id": company_id},
    ).all()
    return LedgerSnapshot(checksums={row[0]: row[1] for row in rows})


def assert_ledger_invariants(
    db: Session,
    company_id: int,
    *,
    previous: LedgerSnapshot | None = None,
    trial_balance_dates: set[date] | None = None,
) -> LedgerSnapshot:
    """§6 invariants 1, 5 (+ the P2 structural rules). Returns a snapshot for the next call.

    `trial_balance_dates` is for callers that run this suite **after every step** of a long
    scenario — the property tests. Pass a set the caller owns and each posting date has its
    trial balance built once per run instead of once per step; without it, every call rebuilds
    a report for every date in the company's history, which is quadratic in the length of the
    scenario and was measured at 91% of the P5 property test's runtime.

    Sound because of two invariants this same function asserts: posted rows never change (5a),
    and every posted entry sums to zero (1). A trial balance as of any date is therefore a sum
    over whole balanced entries, so a date that footed cannot stop footing — only *new* data
    can be wrong, and the newest date is re-checked on every call because data keeps arriving
    at it. The caller owning the set is what keeps it from leaking between tests, which a
    module-level cache could not: `RESTART IDENTITY` hands the next test the same company id.
    """
    entries = db.scalars(select(JournalEntry).where(JournalEntry.company_id == company_id)).all()
    posted = [entry for entry in entries if entry.status == JournalStatus.POSTED]
    assert all(entry.status == JournalStatus.POSTED for entry in entries), (
        "draft entries must never survive a posting transaction"
    )

    # 1. Σ base_amount = 0 for every posted entry, and every entry has ≥ 2 lines.
    sums = dict(
        db.execute(
            select(JournalLine.entry_id, func.sum(JournalLine.base_amount))
            .where(JournalLine.company_id == company_id)
            .group_by(JournalLine.entry_id)
        ).all()
    )
    counts = dict(
        db.execute(
            select(JournalLine.entry_id, func.count())
            .where(JournalLine.company_id == company_id)
            .group_by(JournalLine.entry_id)
        ).all()
    )
    for entry in posted:
        assert sums.get(entry.id, ZERO) == ZERO, f"{entry.number} does not balance"
        assert counts.get(entry.id, 0) >= 2, f"{entry.number} has fewer than two lines"

    # Structural: period covers the entry date; amounts respect currency precision.
    periods = {p.id: p for p in db.scalars(select(AccountingPeriod))}
    currencies = {c.id: c for c in db.scalars(select(Currency))}
    base = next(c for c in currencies.values() if c.is_base)
    for entry in posted:
        period = periods[entry.period_id]
        assert period.start_date <= entry.entry_date <= period.end_date, entry.number
    for line in db.scalars(select(JournalLine).where(JournalLine.company_id == company_id)):
        assert is_rounded(line.amount, currencies[line.currency_id].decimal_places), line.id
        assert is_rounded(line.base_amount, base.decimal_places), line.id
        assert line.amount != ZERO, f"zero-amount line {line.id}"
        assert (line.amount > 0) == (line.base_amount > 0), f"sign flip on line {line.id}"

    # 1b. The trial balance foots at every date on which anything was posted.
    dates = sorted({entry.entry_date for entry in posted})
    if trial_balance_dates is None:
        to_check: list[date] = dates
    else:
        to_check = [as_of for as_of in dates if as_of not in trial_balance_dates]
        to_check.extend(dates[-1:])
        trial_balance_dates.update(dates)
    for as_of in dict.fromkeys(to_check):
        report = trial_balance(db, company_id, as_of=as_of)
        assert report.foots, f"trial balance does not foot as of {as_of}"

    # ADR-04: period_balances is exactly the recomputation from raw lines.
    drift = verify_period_balances(db, company_id)
    assert drift == [], f"period_balances drift: {drift[:3]}"

    # 5a. No posted row mutated since the previous snapshot.
    current = snapshot_ledger(db, company_id)
    if previous is not None:
        for entry_id, checksum in previous.checksums.items():
            assert current.checksums.get(entry_id) == checksum, f"entry {entry_id} was mutated"

    # 5b. Sequences are gapless per (company, doc_type): numbers 1..N with N = next - 1.
    #
    # "Numbers", not "entry numbers". Until P5 those were the same thing, because a journal
    # entry was the only thing that could claim one — except `ALC-`, which allocations have
    # always held alone and which this check therefore never looked at. Since P5 step 3 a
    # posting in which nothing carried value produces moves and **no entry** (decision 1), and
    # such a document claims a number of its own so the run keeps no holes. A company with one
    # zero-cost adjustment and one ordinary one has `ADJ-000001` on a document and
    # `ADJ-000002` on an entry, which is right and which this check called a gap.
    #
    # Who may hold a number is **not** decided here. `SEQUENCE_CLAIMANTS` lives beside
    # `document_sequences`, and a doc type that registers no claimant fails this assertion
    # rather than being skipped — so a later phase that starts numbering something registers
    # it where the numbers are defined instead of editing this file.
    for sequence in db.scalars(
        select(DocumentSequence).where(DocumentSequence.company_id == company_id)
    ):
        if sequence.branch_id is not None:
            # Branch-scoped runs are their own number space and nothing claims one yet; when
            # something does, this check needs the claimant query scoped to the branch too.
            # Skipping loudly beats checking the wrong space quietly.
            continue
        try:
            claimants = claimants_for(sequence.doc_type)
        except KeyError as unregistered:  # noqa: PERF203 - one sequence, one message
            raise AssertionError(str(unregistered)) from unregistered
        numbers = sorted(
            _claimed_numbers(db, company_id, sequence.doc_type, claimants)
        )
        assert numbers == list(range(1, len(numbers) + 1)), (
            f"gap in {sequence.doc_type}: {numbers}"
        )
        assert sequence.next_number == len(numbers) + 1, (
            f"{sequence.doc_type} has issued {len(numbers)} numbers but its sequence is at "
            f"{sequence.next_number}: a number was claimed and nothing kept it"
        )

    return current


def _claimed_numbers(
    db: Session,
    company_id: int,
    doc_type: str,
    claimants: Sequence[SequenceClaimant],
) -> list[int]:
    """Every number held in this run, from every table the registry says may hold one.

    A number belonging to two claimants shows up twice and fails the 1..N check, which is the
    point: "gapless" means every number belongs to exactly one thing.
    """
    numbers: list[int] = []
    for claimant in claimants:
        conditions = ["company_id = :company_id"]
        if claimant.doc_type_column is not None:
            conditions.append(f"{claimant.doc_type_column} = :doc_type")
        if claimant.where is not None:
            conditions.append(f"({claimant.where})")
        rows = db.execute(
            text(
                f"SELECT {claimant.number_column} FROM {claimant.table} "  # noqa: S608 - names
                f"WHERE {' AND '.join(conditions)}"  # come from the registry, never a request
            ),
            {"company_id": company_id, "doc_type": str(doc_type)},
        ).all()
        for (number,) in rows:
            match = _TRAILING_DIGITS.search(number)
            assert match, f"unparseable number {number}"
            numbers.append(int(match.group(1)))
    return numbers
