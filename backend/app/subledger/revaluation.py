"""Unrealized FX on open foreign-currency partner documents (decision 13).

Why it lives here. The kernel imports no subledger models, and a revaluation is a reading of
partner open items — so it cannot sit in `app/kernel/`. It sits beside `allocations.py`, which
owns *realized* FX, because the two are the same arithmetic at different moments: realized FX is
the difference the day an open item is settled, unrealized FX is the difference on a day it
still stands. Its posting module stays `gl` as decision 13 locks, because that is a property of
the event and not of the package it is emitted from.

**Never the control accounts.** `1200` and `2100` are subledger-only: their balance is the sum
of open items at their booking rates, which is P4's invariant and is asserted after every step
of the acceptance tape. A revaluation posting into them would break it on the first run. The
other side goes to `1290 AR Revaluation` / `2190 AP Revaluation` instead, and a balance sheet
reads the control and its revaluation account together.

**Why the mirror.** The entry is dated the revaluation date and its reversal the following day,
both in one transaction. The balance sheet at the date carries the revaluation; the next period
opens without it, so the *next* month end revalues from booking rates again rather than from
last month's adjusted figure. Two entries rather than a period-end-only balance is what keeps
the ledger append-only about it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import NotFoundError
from app.kernel import posting
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import FxRevalued, LineSpec, ReversalRequested
from app.kernel.money import ZERO, base_currency, rate_on, round_amount
from app.kernel.periods import assert_period_open, find_period
from app.kernel.sequences import DocType, claim_number
from app.models.currency import Currency
from app.models.fiscalization import (
    FxRevaluation,
    FxRevaluationLine,
    FxRevaluationRole,
    FxRevaluationStatus,
)
from app.models.job import Job
from app.models.journal import JournalLine
from app.models.partner import Partner, PartnerRole
from app.models.subledger import DocumentStatus, PartnerDocument
from app.models.user import User
from app.services.audit import record_audit
from app.services.jobs import handler

#: `journal_entries.source_doc_type`, so the GL entry drills back to the run.
FX_REVALUATION_SOURCE = "fx_revaluation"

#: Which **partner** roles a run covers. `both` is one run and one entry over AR and AP
#: together, which is the ordinary month end.
#:
#: P8 decision 8 adds `bank` and `all` to `FxRevaluationRole` — the enum and the Postgres type
#: are rebuilt at P8 step 1 so the column can hold them — and the bank *scope* is built at P8
#: step 4. Until then the two new roles are refused by name rather than falling off the end of
#: this map as a `KeyError`: an enum value the API accepts and the service cannot compute is a
#: 500 waiting for whoever tries it first, and "not built yet" is a thing a refusal can say.
_ROLES: dict[FxRevaluationRole, tuple[PartnerRole, ...]] = {
    FxRevaluationRole.AR: (PartnerRole.AR,),
    FxRevaluationRole.AP: (PartnerRole.AP,),
    FxRevaluationRole.BOTH: (PartnerRole.AR, PartnerRole.AP),
}


def _partner_roles(role: FxRevaluationRole) -> tuple[PartnerRole, ...]:
    covered = _ROLES.get(role)
    if covered is None:
        raise LedgerStateError(
            f"Revaluing {role.value} balances is not built yet; use ar, ap or both",
            code="fx_revaluation_role_unsupported",
            field_errors={"role": ["not available yet"]},
        )
    return covered


@dataclass(frozen=True)
class RevaluationLine:
    """One open document at the revaluation date.

    Every figure is kept rather than recomputed later: the rate used is the rate that stood on
    the day, and a correction to `exchange_rates` afterwards must not silently restate a posted
    revaluation.
    """

    document_id: int
    document_number: str
    role: PartnerRole
    partner_id: int
    partner_name: str
    currency_id: int
    currency_code: str
    #: Signed by the control account's side, so an AR invoice is positive and an AP invoice
    #: negative — the same sense the ledger holds them in.
    open_amount: Decimal
    booking_rate: Decimal
    carrying_base: Decimal
    rate_at_date: Decimal
    revalued_base: Decimal
    difference: Decimal


@dataclass(frozen=True)
class RevaluationPreview:
    """What a run would post, without posting it."""

    revaluation_date: date
    role: FxRevaluationRole
    lines: tuple[RevaluationLine, ...]

    @property
    def total_difference(self) -> Decimal:
        return sum((line.difference for line in self.lines), ZERO)

    def by_group(self) -> dict[tuple[PartnerRole, int], Decimal]:
        """The gain or loss per (role, currency) — one posting group each (decision 13)."""
        groups: dict[tuple[PartnerRole, int], Decimal] = {}
        for line in self.lines:
            key = (line.role, line.currency_id)
            groups[key] = groups.get(key, ZERO) + line.difference
        return groups


def preview(
    db: Session, company_id: int, *, revaluation_date: date, role: FxRevaluationRole
) -> RevaluationPreview:
    """The lines as they stand, with no refusals but the arithmetic ones.

    Deliberately permissive: a preview of a date that could not be posted is still worth
    reading, and the refusals belong at the moment of posting where they can be acted on.
    """
    base = base_currency(db, company_id)
    rows = db.execute(
        select(PartnerDocument, Partner.name, Currency)
        .join(
            Partner,
            (Partner.id == PartnerDocument.partner_id)
            & (Partner.company_id == PartnerDocument.company_id),
        )
        .join(
            Currency,
            (Currency.id == PartnerDocument.currency_id)
            & (Currency.company_id == PartnerDocument.company_id),
        )
        .where(
            PartnerDocument.company_id == company_id,
            PartnerDocument.role.in_(_partner_roles(role)),
            PartnerDocument.status == DocumentStatus.POSTED,
            PartnerDocument.open_amount != ZERO,
            PartnerDocument.currency_id != base.id,
            PartnerDocument.document_date <= revaluation_date,
        )
        .order_by(PartnerDocument.id)
    )

    rate_cache: dict[int, Decimal] = {}
    lines: list[RevaluationLine] = []
    for document, partner_name, currency in rows:
        if currency.id not in rate_cache:
            rate_cache[currency.id] = rate_on(db, currency, revaluation_date)
        rate_at_date = rate_cache[currency.id]
        # The open amount is a magnitude; `direction` is the side of the control account it
        # sits on, so this is the exposure in the sense the ledger already holds it.
        signed = document.direction * document.open_amount
        carrying = round_amount(signed * document.exchange_rate, base.decimal_places)
        revalued = round_amount(signed * rate_at_date, base.decimal_places)
        lines.append(
            RevaluationLine(
                document_id=document.id,
                document_number=document.number,
                role=document.role,
                partner_id=document.partner_id,
                partner_name=partner_name,
                currency_id=currency.id,
                currency_code=currency.code,
                open_amount=signed,
                booking_rate=document.exchange_rate,
                carrying_base=carrying,
                rate_at_date=rate_at_date,
                revalued_base=revalued,
                difference=revalued - carrying,
            )
        )
    return RevaluationPreview(
        revaluation_date=revaluation_date, role=role, lines=tuple(lines)
    )


def post_revaluation(
    db: Session,
    company_id: int,
    *,
    revaluation_date: date,
    role: FxRevaluationRole,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> FxRevaluation:
    """Post the run and its mirror in one transaction. Never commits.

    The three refusals of decision 13, in the order a user meets them: the date must be a period
    end, its period must be open, and no run for that (role, date) may already stand.
    """
    replayed = _replay(db, company_id, idempotency_key, idempotency_hash)
    if replayed is not None:
        return replayed

    period = find_period(db, company_id, revaluation_date)
    if period.end_date != revaluation_date:
        raise PostingError(
            "A revaluation is run at a period end, and "
            f"{revaluation_date.isoformat()} is not one — "
            f"{period.name} ends on {period.end_date.isoformat()}",
            code="fx_revaluation_not_period_end",
            field_errors={"revaluation_date": ["must be the last day of a period"]},
        )
    assert_period_open(period)
    _refuse_a_second_run(db, company_id, revaluation_date=revaluation_date, role=role)

    view = preview(db, company_id, revaluation_date=revaluation_date, role=role)
    lines = _entry_lines(db, company_id, view)

    run_id = _reserve_run_id(db)
    entry = None
    mirror = None
    if lines:
        entry = posting.post(
            db,
            FxRevalued(
                entry_date=revaluation_date,
                description=f"FX revaluation at {revaluation_date.isoformat()}",
                lines=tuple(lines),
                source_doc_type=FX_REVALUATION_SOURCE,
                source_doc_id=run_id,
                idempotency_key=f"{idempotency_key}:run" if idempotency_key else None,
            ),
            company_id=company_id,
            actor=actor,
        )
        # The mirror, in this same transaction. Dated the following day so the balance sheet at
        # the revaluation date carries the adjustment and the next period opens without it.
        mirror = posting.post(
            db,
            ReversalRequested(
                entry_date=revaluation_date + timedelta(days=1),
                entry_id=entry.id,
                reason="Unrealized FX reverses the day after the revaluation",
                description=f"Mirror of {entry.number}",
            ),
            company_id=company_id,
            actor=actor,
        )

    # One number per run, the rule the `VAT` run learned the hard way: a run that posts takes
    # its entry's number, and only a run whose every difference was zero claims one of its own
    # — which is what `_VALUELESS_REVALUATION` says by being narrowed to `journal_entry_id IS
    # NULL`. The mirror is an `FXR` entry too and holds the number after it.
    number = (
        entry.number
        if entry is not None
        else claim_number(db, company_id, DocType.FX_REVALUATION).number
    )

    run = FxRevaluation(
        id=run_id,
        company_id=company_id,
        number=number,
        revaluation_date=revaluation_date,
        role=role,
        journal_entry_id=entry.id if entry is not None else None,
        mirror_entry_id=mirror.id if mirror is not None else None,
        status=FxRevaluationStatus.POSTED,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(run)
    db.flush()

    for line in view.lines:
        db.add(
            FxRevaluationLine(
                company_id=company_id,
                revaluation_id=run.id,
                document_id=line.document_id,
                currency_id=line.currency_id,
                open_amount=line.open_amount,
                booking_rate=line.booking_rate,
                carrying_base=line.carrying_base,
                rate_at_date=line.rate_at_date,
                revalued_base=line.revalued_base,
                difference=line.difference,
            )
        )
    db.flush()

    record_audit(
        db,
        company_id=company_id,
        action="fx_revaluation.post",
        entity="fx_revaluation",
        entity_id=run.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={
            "number": run.number,
            "revaluation_date": revaluation_date.isoformat(),
            "role": str(role),
            "lines": len(view.lines),
            "total_difference": str(view.total_difference),
            "journal_entry_id": run.journal_entry_id,
            "mirror_entry_id": run.mirror_entry_id,
        },
        request=request,
    )
    return run


def reverse_revaluation(
    db: Session,
    company_id: int,
    revaluation_id: int,
    *,
    reason: str,
    actor: User,
    request: Request | None = None,
) -> FxRevaluation:
    """Take a run back out, so the date can be revalued again.

    **A run is undone by a counter-pair, not by a reversal.** Its entry has already been
    reversed — by its own mirror, the next day — and the kernel refuses to reverse an entry
    twice (`entry_already_reversed`), which is right: the pair nets to zero across the two days
    and reversing either half alone would leave the other stranded. What has to be undone is the
    *balance at the revaluation date*, where the entry stands unmatched.

    So this posts the run again with every sign flipped, and mirrors that the following day. The
    four entries then cancel at both dates: `A + (−A)` on the revaluation date, `(−A) + A` on
    the day after. The counter-entry is what `reversal_entry_id` names; its own mirror is
    reachable through `reverses_entry_id`, the way every mirror is.
    """
    run = db.scalar(
        select(FxRevaluation).where(
            FxRevaluation.company_id == company_id, FxRevaluation.id == revaluation_id
        )
    )
    if run is None:
        raise NotFoundError("FX revaluation not found")
    if run.status is FxRevaluationStatus.REVERSED:
        raise LedgerStateError(
            f"{run.number} has already been reversed",
            code="fx_revaluation_reversed",
            field_errors={"revaluation_id": ["already reversed"]},
        )

    counter = None
    if run.journal_entry_id is not None:
        counter = posting.post(
            db,
            FxRevalued(
                entry_date=run.revaluation_date,
                description=f"Reversal of FX revaluation {run.number}: {reason}",
                lines=tuple(_negated_lines(db, company_id, run.journal_entry_id)),
                source_doc_type=FX_REVALUATION_SOURCE,
                source_doc_id=run.id,
            ),
            company_id=company_id,
            actor=actor,
        )
        posting.post(
            db,
            ReversalRequested(
                entry_date=run.revaluation_date + timedelta(days=1),
                entry_id=counter.id,
                reason="The counter-entry mirrors like the run it undoes",
                description=f"Mirror of {counter.number}",
            ),
            company_id=company_id,
            actor=actor,
        )
    run.status = FxRevaluationStatus.REVERSED
    run.reversal_entry_id = counter.id if counter is not None else None
    db.flush()

    record_audit(
        db,
        company_id=company_id,
        action="fx_revaluation.reverse",
        entity="fx_revaluation",
        entity_id=run.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        before={"status": str(FxRevaluationStatus.POSTED)},
        after={
            "status": str(FxRevaluationStatus.REVERSED),
            "reason": reason,
            "reversal_entry_id": run.reversal_entry_id,
        },
        request=request,
    )
    return run


def lines_of(db: Session, company_id: int, revaluation_id: int) -> Sequence[FxRevaluationLine]:
    return list(
        db.scalars(
            select(FxRevaluationLine)
            .where(
                FxRevaluationLine.company_id == company_id,
                FxRevaluationLine.revaluation_id == revaluation_id,
            )
            .order_by(FxRevaluationLine.id)
        )
    )


def _entry_lines(
    db: Session, company_id: int, view: RevaluationPreview
) -> list[LineSpec]:
    """Two lines per (role, currency): the revaluation account, and the gain or loss.

    The difference is in ledger sense already, so the revaluation account takes it as it stands
    and the P&L takes its negative. That one turn is what makes the map role-agnostic: an AR
    exposure that grew is a debit to `1290` and a credit to gain; an AP exposure that grew is a
    credit to `2190` and a debit to loss, and neither needs a rule of its own.
    """
    settings = posting.gl_settings_for(db, company_id)
    lines: list[LineSpec] = []
    for (role, currency_id), difference in sorted(
        view.by_group().items(), key=lambda item: (str(item[0][0]), item[0][1])
    ):
        if difference == ZERO:
            continue
        revaluation_account = _required(
            settings,
            "ar_revaluation_account_id" if role is PartnerRole.AR else "ap_revaluation_account_id",
            "AR revaluation account" if role is PartnerRole.AR else "AP revaluation account",
        )
        pnl_amount = -difference
        pnl_account = _required(
            settings,
            "unrealized_fx_gain_account_id"
            if pnl_amount < ZERO
            else "unrealized_fx_loss_account_id",
            "unrealized FX gain account" if pnl_amount < ZERO else "unrealized FX loss account",
        )
        group = [
            line
            for line in view.lines
            if line.role is role and line.currency_id == currency_id
        ]
        label = f"{group[0].currency_code} {role.value.upper()} revaluation"
        lines.append(
            LineSpec(
                amount=difference,
                gl_account_id=revaluation_account,
                currency_id=None,
                description=label,
            )
        )
        lines.append(
            LineSpec(
                amount=pnl_amount,
                gl_account_id=pnl_account,
                currency_id=None,
                description=label,
            )
        )
    return lines


def _negated_lines(db: Session, company_id: int, entry_id: int) -> list[LineSpec]:
    """The posted entry, sign for sign, the other way round.

    Read back off `journal_lines` rather than recomputed from the open items, because what has
    to be undone is what was *posted* — the rate that stood on the day is frozen on the entry,
    and re-deriving it would let a later correction to `exchange_rates` change the size of a
    reversal.

    The amounts go out as amounts, not as frozen bases: `base_amount` may only be supplied by a
    reversal, and this is a fresh entry rather than one. It needs no freezing anyway — every
    revaluation line is posted in the base currency, so its amount *is* its base amount and
    there is no rate for a re-conversion to get wrong.
    """
    rows = db.execute(
        select(
            JournalLine.gl_account_id,
            JournalLine.base_amount,
            JournalLine.description,
        )
        .where(JournalLine.company_id == company_id, JournalLine.entry_id == entry_id)
        .order_by(JournalLine.id)
    )
    return [
        LineSpec(
            amount=-base_amount,
            gl_account_id=gl_account_id,
            description=description,
        )
        for gl_account_id, base_amount, description in rows
    ]


def _required(settings: object, attribute: str, label: str) -> int:
    value = getattr(settings, attribute, None)
    if value is None:
        raise PostingError(
            f"Set the {label} in GL defaults before running a revaluation",
            code="gl_setting_missing",
            field_errors={attribute: ["required"]},
        )
    return int(value)


def _refuse_a_second_run(
    db: Session, company_id: int, *, revaluation_date: date, role: FxRevaluationRole
) -> None:
    """One standing run per (role, date).

    A `both` run covers AR and AP, so it clashes with a role-specific run on the same date and
    a role-specific one clashes with it — otherwise the same exposure would be revalued twice
    and the second adjustment would sit on top of the first.
    """
    covered = set(_partner_roles(role))
    standing = db.scalars(
        select(FxRevaluation).where(
            FxRevaluation.company_id == company_id,
            FxRevaluation.revaluation_date == revaluation_date,
            FxRevaluation.status == FxRevaluationStatus.POSTED,
        )
    )
    for run in standing:
        if covered & set(_partner_roles(run.role)):
            raise LedgerStateError(
                f"{run.number} already revalued {run.role.value} at "
                f"{revaluation_date.isoformat()}. Reverse it first.",
                code="fx_revaluation_exists",
                field_errors={"revaluation_date": [f"already revalued by {run.number}"]},
            )


def _reserve_run_id(db: Session) -> int:
    """The `partner_documents` cycle-breaker again: the entry names the run, and a posted entry
    can never be updated to add the id afterwards (rule 3)."""
    return int(
        db.execute(
            text("SELECT nextval(pg_get_serial_sequence('fx_revaluations', 'id'))")
        ).scalar_one()
    )


def _replay(
    db: Session, company_id: int, key: str | None, request_hash: str | None
) -> FxRevaluation | None:
    if not key:
        return None
    run = db.scalar(
        select(FxRevaluation).where(
            FxRevaluation.company_id == company_id, FxRevaluation.idempotency_key == key
        )
    )
    if run is None:
        return None
    if request_hash is not None and run.idempotency_hash != request_hash:
        raise LedgerStateError(
            f"Idempotency-Key {key} was already used for a different request ({run.number}); "
            "use a new key",
            code="idempotency_key_reused",
        )
    return run


#: The job kind decision 13 names. A month-end run over every open foreign-currency document
#: can be long, so it is offered out of band on `run_job` as well as synchronously — the
#: preview endpoint is the synchronous half, and this is the one that posts.
FX_REVALUATION_JOB = "fx_revaluation"


@handler(FX_REVALUATION_JOB)
def run_fx_revaluation_job(db: Session, job: Job) -> None:
    """Post a run from a queued job.

    `run_job` has already set the tenant and the actor on this session; the *service* still
    needs a `User` for the audit row, so the requester is loaded rather than assumed. A job
    with no requester cannot post, because an audited service call takes a real actor and never
    `None` (rule 5).
    """
    params = job.params or {}
    if job.requested_by is None:
        raise LedgerStateError(
            "An FX revaluation job must name the user who asked for it",
            code="job_actor_missing",
        )
    actor = db.get(User, job.requested_by)
    if actor is None:
        raise NotFoundError("The user who requested this revaluation no longer exists")

    run = post_revaluation(
        db,
        job.company_id,
        revaluation_date=date.fromisoformat(params["revaluation_date"]),
        role=FxRevaluationRole(params.get("role", FxRevaluationRole.BOTH)),
        actor=actor,
        idempotency_key=params.get("idempotency_key"),
    )
    job.result = {
        "revaluation_id": run.id,
        "number": run.number,
        "journal_entry_id": run.journal_entry_id,
        "mirror_entry_id": run.mirror_entry_id,
        "lines": len(lines_of(db, job.company_id, run.id)),
    }
