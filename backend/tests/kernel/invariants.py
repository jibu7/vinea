"""The accounting invariant suite (Master Plan §6). Call `assert_ledger_invariants` after
every scenario that moves money; every failure here is product-fatal by definition."""

import re
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.kernel.balances import verify_period_balances
from app.kernel.enquiries import trial_balance
from app.kernel.money import is_rounded
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
    db: Session, company_id: int, *, previous: LedgerSnapshot | None = None
) -> LedgerSnapshot:
    """§6 invariants 1, 5 (+ the P2 structural rules). Returns a snapshot for the next call."""
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

    # 1b. The trial balance foots at every date on which anything was posted (and today).
    dates = sorted({entry.entry_date for entry in posted})
    for as_of in dates:
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
    sequences = {
        seq.doc_type: seq
        for seq in db.scalars(
            select(DocumentSequence).where(
                DocumentSequence.company_id == company_id, DocumentSequence.branch_id.is_(None)
            )
        )
    }
    by_doc_type: dict[str, list[int]] = {}
    for entry in entries:
        match = _TRAILING_DIGITS.search(entry.number)
        assert match, f"unparseable number {entry.number}"
        by_doc_type.setdefault(entry.doc_type, []).append(int(match.group(1)))
    for doc_type, numbers in by_doc_type.items():
        numbers.sort()
        assert numbers == list(range(1, len(numbers) + 1)), f"gap in {doc_type}: {numbers}"
        assert sequences[doc_type].next_number == len(numbers) + 1, doc_type

    return current
