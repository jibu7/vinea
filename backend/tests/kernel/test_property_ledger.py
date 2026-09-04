"""P2 DoD: ≥100 property-generated balanced entries across 3 currencies × 2 branches ×
2 projects → every invariant holds; reversing a batch restores the trial balance exactly."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.kernel import posting
from app.kernel.enquiries import trial_balance
from app.kernel.events import LineSpec, ManualJournal
from tests.kernel.conftest import YEAR, Ledger
from tests.kernel.invariants import assert_ledger_invariants

CURRENCIES = ("RWF", "USD", "EUR")
BRANCHES = ("MAIN", "MUS")
PROJECTS = (None, "P-ALPHA", "P-BETA")
# Postable, non-control accounts from rw_sme_v1 (manual journals may not touch controls).
ACCOUNTS = (
    "1400", "1500", "1610", "1620", "2200", "2300", "2400", "2500", "3100", "3300",
    "4100", "4200", "4300", "4400", "5100", "5200", "5300", "6100", "6200", "6300",
    "6400", "6500", "6600", "6700", "6800", "6900", "6950", "6990",
)  # fmt: skip
YEAR_START = date(YEAR, 1, 1)


@dataclass(frozen=True)
class Pair:
    """A debit and its mirror credit in one currency — exact in base by construction, which
    is what lets random generation produce balanced entries without a rounding plug."""

    minor_units: int
    currency: str
    debit: str
    credit: str
    branch: str
    project: str | None


@dataclass(frozen=True)
class RandomEntry:
    day_offset: int
    pairs: tuple[Pair, ...]

    @property
    def entry_date(self) -> date:
        return YEAR_START + timedelta(days=self.day_offset)


pairs = st.builds(
    Pair,
    minor_units=st.integers(min_value=1, max_value=10**9),
    currency=st.sampled_from(CURRENCIES),
    debit=st.sampled_from(ACCOUNTS),
    credit=st.sampled_from(ACCOUNTS),
    branch=st.sampled_from(BRANCHES),
    project=st.sampled_from(PROJECTS),
)
entries = st.builds(
    RandomEntry,
    day_offset=st.integers(min_value=0, max_value=364),
    pairs=st.lists(pairs, min_size=1, max_size=4).map(tuple),
)


def _amount(pair: Pair, ledger: Ledger) -> Decimal:
    places = ledger.currencies[pair.currency].decimal_places
    return Decimal(pair.minor_units).scaleb(-places)


def _post(db: Session, ledger: Ledger, spec: RandomEntry, tag: str):  # noqa: ANN202
    lines: list[LineSpec] = []
    for pair in spec.pairs:
        amount = _amount(pair, ledger)
        common = {
            "currency_id": ledger.cur(pair.currency),
            "branch_id": ledger.branches[pair.branch].id,
            "project_id": ledger.projects[pair.project].id if pair.project else None,
        }
        lines.append(LineSpec(amount=amount, gl_account_id=ledger.acct(pair.debit), **common))
        lines.append(LineSpec(amount=-amount, gl_account_id=ledger.acct(pair.credit), **common))
    entry = posting.post(
        db,
        ManualJournal(entry_date=spec.entry_date, description=tag, lines=tuple(lines)),
        company_id=ledger.company_id,
        actor=ledger.owner,
    )
    db.commit()
    assert entry is not None
    return entry


def _net_by_account(db: Session, ledger: Ledger, as_of: date) -> dict[int, Decimal]:
    report = trial_balance(db, ledger.company_id, as_of=as_of)
    assert report.foots
    return {row.gl_account_id: row.net for row in report.rows if row.net != 0}


@given(
    batch=st.lists(entries, min_size=100, max_size=120),
    to_reverse=st.lists(entries, min_size=5, max_size=15),
)
@settings(
    max_examples=2,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
def test_random_balanced_entries_keep_every_invariant(
    db: Session, ledger: Ledger, batch: list[RandomEntry], to_reverse: list[RandomEntry]
) -> None:
    snapshot = assert_ledger_invariants(db, ledger.company_id)

    for index, spec in enumerate(batch):
        _post(db, ledger, spec, f"random {index}")
    snapshot = assert_ledger_invariants(db, ledger.company_id, previous=snapshot)

    year_end = date(YEAR, 12, 31)
    before = _net_by_account(db, ledger, year_end)
    posted = [
        _post(db, ledger, spec, f"to reverse {index}") for index, spec in enumerate(to_reverse)
    ]
    for entry in posted:
        posting.reverse(
            db,
            entry.id,
            company_id=ledger.company_id,
            on_date=year_end,
            reason="property test",
            actor=ledger.owner,
        )
        db.commit()

    assert _net_by_account(db, ledger, year_end) == before
    assert_ledger_invariants(db, ledger.company_id, previous=snapshot)
    # Dimensioned views foot too: every pair is balanced within its own branch.
    for branch in ledger.branches.values():
        assert trial_balance(db, ledger.company_id, as_of=year_end, branch_id=branch.id).foots
