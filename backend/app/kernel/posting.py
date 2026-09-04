"""The Posting Engine — the single GL authority (ADR-05).

`post()` turns a typed event into one balanced, immutable journal entry **inside the
caller's transaction** (the caller commits together with its source document). Nothing else
in the codebase writes `journal_entries` / `journal_lines`.

Validation order (ADR-04): period open → accounts active & postable (and not module-owned
control accounts for manual events) → dimensions required by the account → balances to
zero in base currency after per-line rounding. The DB triggers from migration 0002 re-check
the structural rules, so a bug here cannot produce an unbalanced or back-dated entry.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.kernel import balances
from app.kernel.errors import LedgerStateError, PostingError, translate_db_error
from app.kernel.events import (
    CashbookEntry,
    CashbookKind,
    LineSpec,
    ManualJournal,
    PeriodClosed,
    PostingEvent,
    ReversalRequested,
)
from app.kernel.money import (
    ZERO,
    Converted,
    base_currency,
    is_rounded,
    resolve_tax_code,
    split_tax,
    to_base,
)
from app.kernel.periods import get_fiscal_year, lock_period_for_posting, periods_of
from app.kernel.sequences import claim_number
from app.models.company import Branch
from app.models.currency import Currency
from app.models.fiscal import AccountingPeriod
from app.models.gl import (
    CASHBOOK_CONTROL_TYPES,
    PROFIT_AND_LOSS_CLASSES,
    ControlType,
    GLAccount,
    GLSettings,
    Project,
)
from app.models.journal import JournalEntry, JournalLine, JournalStatus
from app.models.user import User

IDEMPOTENCY_INDEX = "uq_journal_entries_company_idempotency_key"
REVERSAL_INDEX = "uq_journal_entries_reverses_entry_id"

# Manual journals may not touch module-owned accounts; the cashbook owns bank/cash and may
# post bank↔bank transfers, but never the AR/AP/inventory subledger controls.
SUBLEDGER_CONTROL_TYPES = frozenset({ControlType.AR, ControlType.AP, ControlType.INVENTORY})
PARTNER_TYPE_FOR_CONTROL = {ControlType.AR: "customer", ControlType.AP: "supplier"}


# --- Account determination chain (ADR-05) ---------------------------------------------

AccountResolver = Callable[[Session, int, LineSpec, PostingEvent], int | None]


def _line_override(db: Session, company_id: int, spec: LineSpec, event: PostingEvent) -> int | None:
    return spec.gl_account_id


def _partner_default(
    db: Session, company_id: int, spec: LineSpec, event: PostingEvent
) -> int | None:
    """P4: customers/suppliers carry default revenue/expense and control accounts."""
    return None


def _item_default(db: Session, company_id: int, spec: LineSpec, event: PostingEvent) -> int | None:
    """P5: items carry sales / COGS / inventory accounts."""
    return None


def _transaction_type_default(
    db: Session, company_id: int, spec: LineSpec, event: PostingEvent
) -> int | None:
    """P4: per-module transaction types with default contra accounts."""
    return None


def _module_default(
    db: Session, company_id: int, spec: LineSpec, event: PostingEvent
) -> int | None:
    """Last resort: `gl_settings`. Only the year-end close relies on it in P2."""
    if isinstance(event, PeriodClosed) and spec.transaction_type == "retained_earnings":
        settings = gl_settings_for(db, company_id)
        return settings.retained_earnings_account_id
    return None


DETERMINATION_CHAIN: tuple[AccountResolver, ...] = (
    _line_override,
    _partner_default,
    _item_default,
    _transaction_type_default,
    _module_default,
)


def resolve_account(db: Session, company_id: int, spec: LineSpec, event: PostingEvent) -> int:
    for resolver in DETERMINATION_CHAIN:
        account_id = resolver(db, company_id, spec, event)
        if account_id is not None:
            return account_id
    raise PostingError(
        "Could not determine a GL account for a line",
        code="account_undetermined",
        field_errors={"gl_account_id": ["required"]},
    )


def gl_settings_for(db: Session, company_id: int) -> GLSettings:
    settings = db.scalar(select(GLSettings).where(GLSettings.company_id == company_id))
    if settings is None:
        raise PostingError("GL settings are not configured", code="gl_settings_missing")
    return settings


# --- Resolution -------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedLine:
    gl_account_id: int
    branch_id: int
    currency_id: int
    converted: Converted
    project_id: int | None = None
    partner_type: str | None = None
    partner_id: int | None = None
    item_id: int | None = None
    tax_code_id: int | None = None
    tax_amount: Decimal = ZERO
    description: str | None = None
    source_doc_type: str | None = None
    source_doc_id: int | None = None
    source_line_id: int | None = None


class _Context:
    """Per-posting lookups (one query per referenced table, then dict access)."""

    def __init__(self, db: Session, company_id: int) -> None:
        self.db = db
        self.company_id = company_id
        self.base = base_currency(db, company_id)
        self._currencies: dict[int, Currency] = {self.base.id: self.base}
        self._accounts: dict[int, GLAccount] = {}
        self._branches: dict[int, Branch] = {}
        self._projects: dict[int, Project] = {}
        self._main_branch: Branch | None = None

    def main_branch(self) -> Branch:
        if self._main_branch is None:
            branch = self.db.scalar(
                select(Branch).where(
                    Branch.company_id == self.company_id, Branch.is_main, Branch.is_active
                )
            )
            if branch is None:
                raise PostingError("Company has no active main branch", code="no_main_branch")
            self._main_branch = branch
        return self._main_branch

    def preload_accounts(self, ids: set[int]) -> None:
        missing = ids - self._accounts.keys()
        if missing:
            for account in self.db.scalars(
                select(GLAccount).where(
                    GLAccount.company_id == self.company_id, GLAccount.id.in_(missing)
                )
            ):
                self._accounts[account.id] = account

    def account(self, account_id: int) -> GLAccount:
        if account_id not in self._accounts:
            self.preload_accounts({account_id})
        account = self._accounts.get(account_id)
        if account is None:
            raise PostingError(
                f"GL account {account_id} not found",
                code="account_not_found",
                field_errors={"gl_account_id": ["unknown account"]},
            )
        return account

    def currency(self, currency_id: int | None) -> Currency:
        if currency_id is None:
            return self.base
        if currency_id not in self._currencies:
            currency = self.db.get(Currency, currency_id)
            if currency is None or currency.company_id != self.company_id or not currency.is_active:
                raise PostingError(
                    f"Currency {currency_id} is not available",
                    code="currency_not_found",
                    field_errors={"currency_id": ["unknown or inactive currency"]},
                )
            self._currencies[currency_id] = currency
        return self._currencies[currency_id]

    def branch(self, branch_id: int | None, fallback: int | None) -> Branch:
        target = branch_id if branch_id is not None else fallback
        if target is None:
            return self.main_branch()
        if target not in self._branches:
            branch = self.db.get(Branch, target)
            if branch is None or branch.company_id != self.company_id or not branch.is_active:
                raise PostingError(
                    f"Branch {target} is not available",
                    code="branch_not_found",
                    field_errors={"branch_id": ["unknown or inactive branch"]},
                )
            self._branches[target] = branch
        return self._branches[target]

    def project(self, project_id: int) -> Project:
        if project_id not in self._projects:
            project = self.db.get(Project, project_id)
            if project is None or project.company_id != self.company_id or not project.is_active:
                raise PostingError(
                    f"Project {project_id} is not available",
                    code="project_not_found",
                    field_errors={"project_id": ["unknown or inactive project"]},
                )
            self._projects[project_id] = project
        return self._projects[project_id]


def _check_account(account: GLAccount, event: PostingEvent, *, is_cash_side: bool = False) -> None:
    if not account.is_active:
        raise PostingError(
            f"Account {account.code} is inactive",
            code="account_inactive",
            field_errors={"gl_account_id": [f"{account.code} is inactive"]},
        )
    if not account.is_postable:
        raise PostingError(
            f"Account {account.code} is a header account and cannot be posted to",
            code="account_not_postable",
            field_errors={"gl_account_id": [f"{account.code} is not postable"]},
        )
    if isinstance(event, ReversalRequested):
        return  # mirrors an entry that was legitimately posted
    if isinstance(event, ManualJournal) and account.is_control:
        raise PostingError(
            f"Account {account.code} is a {account.control_type} control account; post through "
            "its module (cashbook / subledger), not a manual journal",
            code="control_account_manual_posting",
            field_errors={"gl_account_id": [f"{account.code} is a control account"]},
        )
    if isinstance(event, CashbookEntry):
        if is_cash_side and account.control_type not in CASHBOOK_CONTROL_TYPES:
            raise PostingError(
                f"Account {account.code} is not a bank or cash account",
                code="not_a_cash_account",
                field_errors={"cash_account_id": ["must be a bank/cash control account"]},
            )
        if not is_cash_side and account.control_type in SUBLEDGER_CONTROL_TYPES:
            raise PostingError(
                f"Account {account.code} is a {account.control_type} control account; use the "
                "subledger",
                code="control_account_manual_posting",
                field_errors={"gl_account_id": [f"{account.code} is a control account"]},
            )
    if isinstance(event, PeriodClosed) and account.is_control:
        raise PostingError(
            "Retained earnings must not be a control account", code="invalid_retained_earnings"
        )


def _check_required_dimensions(account: GLAccount, spec: LineSpec) -> None:
    """Dimensions demanded by the account: subledger controls need their partner/item so the
    control-account invariant (Σ open documents == control balance) is checkable."""
    expected_partner = PARTNER_TYPE_FOR_CONTROL.get(account.control_type)  # type: ignore[arg-type]
    if expected_partner is not None:
        if spec.partner_id is None or spec.partner_type != expected_partner:
            raise PostingError(
                f"Account {account.code} requires a {expected_partner} on the line",
                code="dimension_required",
                field_errors={"partner_id": [f"{expected_partner} required"]},
            )
    if account.control_type == ControlType.INVENTORY and spec.item_id is None:
        raise PostingError(
            f"Account {account.code} requires an item on the line",
            code="dimension_required",
            field_errors={"item_id": ["item required"]},
        )
    if (spec.partner_type is None) != (spec.partner_id is None):
        raise PostingError(
            "partner_type and partner_id must be given together",
            code="dimension_invalid",
            field_errors={"partner_id": ["partner_type and partner_id go together"]},
        )
    if spec.tax_amount != ZERO and spec.tax_code_id is None:
        raise PostingError(
            "tax_amount requires a tax_code_id",
            code="dimension_invalid",
            field_errors={"tax_code_id": ["required when tax_amount is set"]},
        )


def _resolve_lines(
    ctx: _Context, event: PostingEvent, specs: Sequence[LineSpec], *, cash_side_index: int | None
) -> list[ResolvedLine]:
    if len(specs) < 2:
        raise PostingError(
            "A journal entry needs at least two lines",
            code="too_few_lines",
            field_errors={"lines": ["at least two lines required"]},
        )
    account_ids = [resolve_account(ctx.db, ctx.company_id, spec, event) for spec in specs]
    ctx.preload_accounts(set(account_ids))

    resolved: list[ResolvedLine] = []
    for index, (spec, account_id) in enumerate(zip(specs, account_ids, strict=True)):
        account = ctx.account(account_id)
        _check_account(account, event, is_cash_side=index == cash_side_index)
        _check_required_dimensions(account, spec)

        branch = ctx.branch(spec.branch_id, event.branch_id)
        if spec.project_id is not None:
            ctx.project(spec.project_id)
        currency = ctx.currency(spec.currency_id)
        if spec.tax_code_id is not None:
            resolve_tax_code(ctx.db, ctx.company_id, spec.tax_code_id, event.entry_date)

        if spec.amount == ZERO:
            raise PostingError(
                f"Line {index + 1} has a zero amount",
                code="zero_amount_line",
                field_errors={f"lines.{index}.amount": ["must not be zero"]},
            )
        if not is_rounded(spec.amount, currency.decimal_places):
            raise PostingError(
                f"Line {index + 1}: {currency.code} allows {currency.decimal_places} decimals",
                code="amount_precision",
                field_errors={f"lines.{index}.amount": ["too many decimal places"]},
            )
        if not is_rounded(spec.tax_amount, currency.decimal_places):
            raise PostingError(
                f"Line {index + 1}: tax amount exceeds {currency.code} precision",
                code="amount_precision",
                field_errors={f"lines.{index}.tax_amount": ["too many decimal places"]},
            )
        converted = to_base(
            ctx.db,
            spec.amount,
            currency,
            event.entry_date,
            base=ctx.base,
            rate=spec.exchange_rate,
        )
        resolved.append(
            ResolvedLine(
                gl_account_id=account.id,
                branch_id=branch.id,
                currency_id=currency.id,
                converted=converted,
                project_id=spec.project_id,
                partner_type=spec.partner_type,
                partner_id=spec.partner_id,
                item_id=spec.item_id,
                tax_code_id=spec.tax_code_id,
                tax_amount=spec.tax_amount,
                description=spec.description,
                source_doc_type=spec.source_doc_type or event.source_doc_type,
                source_doc_id=spec.source_doc_id or event.source_doc_id,
                source_line_id=spec.source_line_id,
            )
        )

    difference = sum((line.converted.base_amount for line in resolved), ZERO)
    if difference != ZERO:
        raise PostingError(
            f"Entry does not balance in {ctx.base.code}: difference {difference:+f}",
            code="unbalanced_entry",
            field_errors={"lines": [f"base-currency difference {difference:+f}"]},
        )
    return resolved


# --- Event → line specs -----------------------------------------------------------------


def _cashbook_specs(ctx: _Context, event: CashbookEntry) -> tuple[list[LineSpec], int]:
    """Counterparts (+ tax lines) and the derived bank/cash line. Returns the specs and the
    index of the cash-side line."""
    if not event.lines:
        raise PostingError(
            "A cashbook entry needs at least one line",
            code="too_few_lines",
            field_errors={"lines": ["at least one line required"]},
        )
    currency = ctx.currency(event.currency_id)
    sign = Decimal(1) if event.kind == CashbookKind.PAYMENT else Decimal(-1)
    specs: list[LineSpec] = []
    gross_total = ZERO
    for index, line in enumerate(event.lines):
        if line.amount <= ZERO:
            raise PostingError(
                f"Line {index + 1}: cashbook amounts must be positive",
                code="invalid_amount",
                field_errors={f"lines.{index}.amount": ["must be positive"]},
            )
        if not is_rounded(line.amount, currency.decimal_places):
            raise PostingError(
                f"Line {index + 1}: {currency.code} allows {currency.decimal_places} decimals",
                code="amount_precision",
                field_errors={f"lines.{index}.amount": ["too many decimal places"]},
            )
        net, tax = line.amount, ZERO
        tax_account_id: int | None = None
        if line.tax_code_id is not None:
            tax_code = resolve_tax_code(ctx.db, ctx.company_id, line.tax_code_id, event.entry_date)
            split = split_tax(
                line.amount,
                tax_code.rate_pct,
                inclusive=line.tax_inclusive,
                decimal_places=currency.decimal_places,
            )
            net, tax = split.net, split.tax
            if tax != ZERO:
                if tax_code.gl_account_id is None:
                    raise PostingError(
                        f"Tax code {tax_code.code} has no GL account",
                        code="tax_code_without_account",
                        field_errors={f"lines.{index}.tax_code_id": ["no GL account"]},
                    )
                tax_account_id = tax_code.gl_account_id
        common = {
            "currency_id": currency.id,
            "exchange_rate": event.exchange_rate,
            "branch_id": line.branch_id,
            "project_id": line.project_id,
            "partner_type": line.partner_type,
            "partner_id": line.partner_id,
            "source_line_id": None,
        }
        specs.append(
            LineSpec(
                amount=sign * net,
                gl_account_id=line.gl_account_id,
                tax_code_id=line.tax_code_id,
                tax_amount=sign * tax,
                description=line.description,
                **common,
            )
        )
        if tax_account_id is not None:
            specs.append(
                LineSpec(
                    amount=sign * tax,
                    gl_account_id=tax_account_id,
                    tax_code_id=line.tax_code_id,
                    tax_amount=ZERO,
                    description=line.description,
                    **common,
                )
            )
        gross_total += net + tax
    specs.append(
        LineSpec(
            amount=-sign * gross_total,
            gl_account_id=event.cash_account_id,
            currency_id=currency.id,
            exchange_rate=event.exchange_rate,
            branch_id=event.branch_id,
            description=event.reference or event.description,
        )
    )
    return specs, len(specs) - 1


def _load_reversible(
    db: Session, company_id: int, entry_id: int, *, allow_closing_entry: bool
) -> JournalEntry:
    original = db.get(JournalEntry, entry_id)
    if original is None or original.company_id != company_id:
        raise PostingError(f"Journal entry {entry_id} not found", code="entry_not_found")
    if original.status != JournalStatus.POSTED:
        raise LedgerStateError("Only posted entries can be reversed", code="entry_not_posted")
    if original.event_type == PeriodClosed.event_type and not allow_closing_entry:
        raise LedgerStateError(
            "Year-end closing entries are reversed by reopening the fiscal year",
            code="use_fiscal_year_reopen",
        )
    already = db.scalar(
        select(JournalEntry.id).where(JournalEntry.reverses_entry_id == original.id)
    )
    if already is not None:
        raise LedgerStateError(
            f"Entry {original.number} was already reversed", code="entry_already_reversed"
        )
    return original


def _reversal_specs(original: JournalEntry) -> list[LineSpec]:
    """Exact mirror: base amounts negated as frozen, not re-converted at today's rate, so
    the trial balance returns to precisely where it was."""
    return [
        LineSpec(
            amount=-line.amount,
            gl_account_id=line.gl_account_id,
            currency_id=line.currency_id,
            exchange_rate=line.exchange_rate,
            branch_id=line.branch_id,
            project_id=line.project_id,
            partner_type=line.partner_type,
            partner_id=line.partner_id,
            item_id=line.item_id,
            tax_code_id=line.tax_code_id,
            tax_amount=-line.tax_amount,
            description=line.description,
            source_doc_type=line.source_doc_type,
            source_doc_id=line.source_doc_id,
            source_line_id=line.id,
        )
        for line in original.lines
    ]


def _period_close_specs(ctx: _Context, event: PeriodClosed) -> list[LineSpec]:
    """P&L balance per (account, branch) over the year's periods, in base currency, moved
    into retained earnings per branch."""
    year = get_fiscal_year(ctx.db, ctx.company_id, event.fiscal_year_id)
    period_ids = [period.id for period in periods_of(ctx.db, year)]
    rows = ctx.db.execute(
        select(
            JournalLine.gl_account_id,
            JournalLine.branch_id,
            func.sum(JournalLine.base_amount),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .join(GLAccount, GLAccount.id == JournalLine.gl_account_id)
        .where(
            JournalLine.company_id == ctx.company_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.period_id.in_(period_ids),
            GLAccount.class_.in_(PROFIT_AND_LOSS_CLASSES),
        )
        .group_by(JournalLine.gl_account_id, JournalLine.branch_id)
        .order_by(JournalLine.branch_id, JournalLine.gl_account_id)
    ).all()
    specs: list[LineSpec] = []
    per_branch: dict[int, Decimal] = {}
    for account_id, branch_id, balance in rows:
        if balance == ZERO:
            continue
        specs.append(
            LineSpec(
                amount=-balance,
                gl_account_id=account_id,
                currency_id=ctx.base.id,
                branch_id=branch_id,
                description=f"Year-end close {year.name}",
            )
        )
        per_branch[branch_id] = per_branch.get(branch_id, ZERO) + balance
    for branch_id, total in sorted(per_branch.items()):
        if total == ZERO:
            continue
        specs.append(
            LineSpec(
                amount=total,
                currency_id=ctx.base.id,
                branch_id=branch_id,
                transaction_type="retained_earnings",
                description=f"Retained earnings {year.name}",
            )
        )
    return specs


# --- post() -----------------------------------------------------------------------------


def find_by_idempotency_key(db: Session, company_id: int, key: str) -> JournalEntry | None:
    return db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id, JournalEntry.idempotency_key == key
        )
    )


def post(
    db: Session, event: PostingEvent, *, company_id: int, actor: User | None
) -> JournalEntry | None:
    """Post one event. Returns the entry (an existing one when the idempotency key was seen
    before), or None when the event legitimately produces nothing (a year-end with no P&L
    movement). Never commits."""
    if event.idempotency_key:
        existing = find_by_idempotency_key(db, company_id, event.idempotency_key)
        if existing is not None:
            return existing

    ctx = _Context(db, company_id)
    period: AccountingPeriod = lock_period_for_posting(db, company_id, event.entry_date)

    cash_side_index: int | None = None
    doc_type = str(event.doc_type)
    description = event.description
    reverses_entry_id: int | None = None
    reversal_reason: str | None = None
    if isinstance(event, ManualJournal):
        specs: Sequence[LineSpec] = event.lines
    elif isinstance(event, CashbookEntry):
        specs, cash_side_index = _cashbook_specs(ctx, event)
    elif isinstance(event, ReversalRequested):
        original = _load_reversible(
            db, company_id, event.entry_id, allow_closing_entry=event.allow_closing_entry
        )
        if event.entry_date < original.entry_date:
            raise PostingError(
                "A reversal cannot be dated before the entry it reverses",
                code="reversal_before_original",
                field_errors={"entry_date": ["must be on/after the original entry date"]},
            )
        specs = _reversal_specs(original)
        doc_type = original.doc_type
        description = event.description or f"Reversal of {original.number}: {event.reason}"
        reverses_entry_id = original.id
        reversal_reason = event.reason
    elif isinstance(event, PeriodClosed):
        specs = _period_close_specs(ctx, event)
        if not specs:
            return None
        description = event.description or f"Year-end close {event.fiscal_year_id}"
    else:
        raise PostingError(
            f"Event {event.event_type} is not postable yet (its module arrives in a later phase)",
            code="unsupported_event",
        )

    resolved = _resolve_lines(ctx, event, specs, cash_side_index=cash_side_index)
    return _write(
        db,
        ctx,
        event,
        period=period,
        doc_type=doc_type,
        description=description,
        lines=resolved,
        actor=actor,
        reverses_entry_id=reverses_entry_id,
        reversal_reason=reversal_reason,
    )


def _write(
    db: Session,
    ctx: _Context,
    event: PostingEvent,
    *,
    period: AccountingPeriod,
    doc_type: str,
    description: str,
    lines: list[ResolvedLine],
    actor: User | None,
    reverses_entry_id: int | None,
    reversal_reason: str | None,
) -> JournalEntry:
    claimed = claim_number(db, ctx.company_id, doc_type, event.branch_id)
    entry = JournalEntry(
        company_id=ctx.company_id,
        number=claimed.number,
        doc_type=claimed.doc_type,
        event_type=event.event_type,
        entry_date=event.entry_date,
        period_id=period.id,
        description=description,
        source_doc_type=event.source_doc_type,
        source_doc_id=event.source_doc_id,
        status=JournalStatus.DRAFT,
        reverses_entry_id=reverses_entry_id,
        reversal_reason=reversal_reason,
        idempotency_key=event.idempotency_key,
    )
    db.add(entry)
    try:
        db.flush()
        journal_lines = [
            JournalLine(
                company_id=ctx.company_id,
                entry_id=entry.id,
                line_no=line_no,
                gl_account_id=line.gl_account_id,
                branch_id=line.branch_id,
                project_id=line.project_id,
                partner_type=line.partner_type,
                partner_id=line.partner_id,
                item_id=line.item_id,
                currency_id=line.currency_id,
                exchange_rate=line.converted.rate,
                amount=line.converted.amount,
                base_amount=line.converted.base_amount,
                tax_code_id=line.tax_code_id,
                tax_amount=line.tax_amount,
                description=line.description,
                source_doc_type=line.source_doc_type,
                source_doc_id=line.source_doc_id,
                source_line_id=line.source_line_id,
            )
            for line_no, line in enumerate(lines, 1)
        ]
        db.add_all(journal_lines)
        db.flush()
        # The only header UPDATE the immutability trigger ever allows: draft → posted.
        entry.status = JournalStatus.POSTED
        entry.posted_by = actor.id if actor else None
        entry.posted_at = datetime.now(UTC)
        db.flush()
        # Fire the deferred DB checks now so a violation surfaces here, not at commit.
        db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        db.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    except IntegrityError as exc:
        if IDEMPOTENCY_INDEX in str(exc.orig):
            raise LedgerStateError(
                "A posting with this idempotency key is already in progress",
                code="idempotency_conflict",
            ) from exc
        if REVERSAL_INDEX in str(exc.orig):
            raise LedgerStateError(
                "This entry was reversed concurrently", code="entry_already_reversed"
            ) from exc
        translated = translate_db_error(exc)
        if translated is not None:
            raise translated from exc
        raise
    except DBAPIError as exc:
        translated = translate_db_error(exc)
        if translated is not None:
            raise translated from exc
        raise

    balances.apply_lines(db, entry, journal_lines)
    return entry


def reverse(
    db: Session,
    entry_id: int,
    *,
    company_id: int,
    on_date: date,
    reason: str,
    actor: User | None,
    idempotency_key: str | None = None,
) -> JournalEntry:
    """Produce the linked reversing entry (`reverses_entry_id`). Corrections are never
    edits (ADR-04)."""
    entry = post(
        db,
        ReversalRequested(
            entry_id=entry_id,
            reason=reason,
            entry_date=on_date,
            idempotency_key=idempotency_key,
        ),
        company_id=company_id,
        actor=actor,
    )
    assert entry is not None
    return entry
