"""Banking: the bank-account master, imported statements, matches, reconciliations and
payment runs (Master Plan §5 P8).

**No balance lives here.** The cashbook balance of an account is Σ `journal_lines` on its GL
account and has been since P2 (ADR-04). A *statement* is the bank's record of the same money,
imported as it came and never edited; a *match* is the assertion that a statement line and a
set of ledger lines are the same event; a *reconciliation* is the dated proof that the two
agree. The only stored figures are on `bank_reconciliations`, and they are a **snapshot of a
proof** — reproducible from the lines that existed when it locked, which is what
`assert_bank_invariants` clause 4 asserts.

`bank_accounts.last_reconciled_at` / `last_reconciled_balance` are the one cache, and they
cache a *stored row* (the latest locked reconciliation) rather than a derived balance —
clause 7 recomputes them from that row every time the suite runs.

Two rules are enforced from the database rather than by convention, the P2 way:

* **A statement line is immutable** (`VN013`). It is the bank's record; a mistaken import is
  voided as a whole, never edited line by line (decision 3).
* **A foreign-currency bank account holds only its own currency** (`VN012`). The engine
  refuses it first, for the field error; the trigger is what makes it a guarantee (decision 2).
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import AuditedMixin, CompanyScopedMixin, pg_enum

MONEY = Numeric(20, 6)

#: The module string P8 would have used if anything here posted. Nothing does: every posting
#: this phase causes goes through `CashbookEntry`, `post_document()`, `allocate()` or the P7
#: revaluation, so no new value ever reaches `journal_entries.module` (decision 1).
BANKING_MODULE = "bank"


class BankAccountKind(enum.StrEnum):
    """Copied from the GL account's control type and asserted equal to it.

    Duplicated rather than joined for one reason: the difference decides behaviour on screens
    and in refusals — a `cash` account has no statement format and no reconciliation
    (`reconciliation_needs_bank`) — and a listing that had to join `gl_accounts` to know which
    it was would read the control type as the authority anyway. Clause 6 of
    `assert_bank_invariants` is what keeps the copy honest.
    """

    BANK = "bank"
    CASH = "cash"


class StatementSource(enum.StrEnum):
    """How the statement got here. `manual` is a paper statement keyed line by line; it takes
    the same path from the moment its lines exist (decision 3)."""

    CSV = "csv"
    MANUAL = "manual"


class StatementStatus(enum.StrEnum):
    OPEN = "open"
    VOID = "void"


class BankMatchKind(enum.StrEnum):
    """Who asserted the match. `posted` is the one that also *caused* a posting: the ledger
    line it points at was written from the statement side in the same transaction."""

    AUTO = "auto"
    MANUAL = "manual"
    TICK = "tick"
    POSTED = "posted"


class BankMatchRule(enum.StrEnum):
    """Why the match was made. The first three are the auto rules, applied in this order and
    only where the candidate is unique (decision 4)."""

    REFERENCE = "reference"
    PAYMENT_RUN = "payment_run"
    AMOUNT_DATE = "amount_date"
    POSTED_FROM_STATEMENT = "posted_from_statement"
    MANUAL = "manual"
    TICK = "tick"


class ReconciliationStatus(enum.StrEnum):
    OPEN = "open"
    LOCKED = "locked"


class PaymentRunStatus(enum.StrEnum):
    POSTED = "posted"
    REVERSED = "reversed"


class StatementAmountMode(enum.StrEnum):
    """How a format's file spells the amount. `debit_credit` is two columns, at most one of
    them filled; `signed` is one column plus which sign means money in."""

    DEBIT_CREDIT = "debit_credit"
    SIGNED = "signed"


class StatementSignConvention(enum.StrEnum):
    CREDIT_POSITIVE = "credit_positive"
    DEBIT_POSITIVE = "debit_positive"


class StatementFormatPreset(enum.StrEnum):
    GENERIC = "generic"
    CUSTOM = "custom"


class StatementEmptyDescription(enum.StrEnum):
    """What a format does with a row whose description column is empty. `refuse` is the
    generic preset's answer; `reference` is BPR's, whose transfer-fee lines carry a `CHG…`
    reference and nothing else. A mapping value, not a column: no PG type."""

    REFUSE = "refuse"
    REFERENCE = "reference"


bank_account_kind_type = pg_enum(BankAccountKind, "bank_account_kind")
statement_source_type = pg_enum(StatementSource, "bank_statement_source")
statement_status_type = pg_enum(StatementStatus, "bank_statement_status")
bank_match_kind_type = pg_enum(BankMatchKind, "bank_match_kind")
bank_match_rule_type = pg_enum(BankMatchRule, "bank_match_rule")
reconciliation_status_type = pg_enum(ReconciliationStatus, "bank_reconciliation_status")
payment_run_status_type = pg_enum(PaymentRunStatus, "payment_run_status")


class BankAccount(AuditedMixin, CompanyScopedMixin, Base):
    """A master over one flagged GL account — never a second balance for it.

    Exactly one row per `bank` / `cash` control account, created in the same transaction as the
    account itself by `app.banking.accounts.ensure_row` (decision 2). The kernel does not call
    it — `app/kernel` may not import the banking package — so the callers are the API layer and
    the seed pack, and invariant clause 6 is what catches a path that forgets.
    """

    __tablename__ = "bank_accounts"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_bank_accounts_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_bank_accounts_company_code"),
        # One master per flagged account. The uniqueness is the invariant's other half:
        # clause 6 asserts every such account *has* a row, this asserts it has only one.
        UniqueConstraint(
            "company_id", "gl_account_id", name="uq_bank_accounts_company_gl_account"
        ),
        ForeignKeyConstraint(
            ["company_id", "gl_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_bank_accounts_gl_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "currency_id"],
            ["currencies.company_id", "currencies.id"],
            name="fk_bank_accounts_currency",
            ondelete="RESTRICT",
        ),
        # A cash account reconciles against nothing (P11 owns cash-ups), so it carries no
        # format and no reconciliation.
        CheckConstraint(
            "kind <> 'cash' OR statement_format IS NULL", name="cash_account_has_no_format"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    gl_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[BankAccountKind] = mapped_column(bank_account_kind_type, nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The account's own currency — a fact the GL cannot hold, because `gl_accounts` carry no
    #: currency at all. It decides the one-sided rule of decision 2 and therefore what a
    #: *reconciled amount* means for every line on the account.
    currency_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(200))
    account_number: Mapped[str | None] = mapped_column(String(50))
    account_holder: Mapped[str | None] = mapped_column(String(200))
    bank_branch: Mapped[str | None] = mapped_column(String(200))
    swift_bic: Mapped[str | None] = mapped_column(String(20))
    #: The column mapping this account's exports are read with (decision 3). Copied onto every
    #: statement at import, so editing it never changes what a stored statement meant.
    statement_format: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: The latest locked reconciliation's date and **statement** balance. A cache of a stored
    #: row, verified by clause 7 — not a balance of the account, which stays Σ journal lines.
    last_reconciled_at: Mapped[date | None] = mapped_column(Date)
    last_reconciled_balance: Mapped[Decimal | None] = mapped_column(MONEY)


class BankRule(AuditedMixin, CompanyScopedMixin, Base):
    """A prefill for a recurring statement line — the monthly fee to `6700`, interest to
    `4300`. **Rules never post** (decision 4): they open the drawer filled in, a person
    presses Post, and the engine's own `not_a_cash_account` / `control_account_manual_posting`
    refusals are what stop a rule from aiming at a control account."""

    __tablename__ = "bank_rules"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_bank_rules_company_id_id"),
        ForeignKeyConstraint(
            ["company_id", "bank_account_id"],
            ["bank_accounts.company_id", "bank_accounts.id"],
            name="fk_bank_rules_bank_account",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "gl_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_bank_rules_gl_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "tax_code_id"],
            ["tax_codes.company_id", "tax_codes.id"],
            name="fk_bank_rules_tax_code",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "partner_id"],
            ["partners.company_id", "partners.id"],
            name="fk_bank_rules_partner",
            ondelete="RESTRICT",
        ),
        Index("ix_bank_rules_account_priority", "company_id", "bank_account_id", "priority"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Case-insensitive substring on the statement line's description.
    pattern: Mapped[str] = mapped_column(String(200), nullable=False)
    #: What the drawer opens as. NULL means "take it from the line's sign", which is the
    #: normal case — a fee is a payment and interest a receipt because of how they land.
    kind: Mapped[str | None] = mapped_column(String(10))
    gl_account_id: Mapped[int | None] = mapped_column(BigInteger)
    tax_code_id: Mapped[int | None] = mapped_column(BigInteger)
    partner_type: Mapped[str | None] = mapped_column(String(10))
    partner_id: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str | None] = mapped_column(String(200))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BankStatement(AuditedMixin, CompanyScopedMixin, Base):
    """One import — a file, or a paper statement keyed by hand.

    `file_sha256` is unique per bank account **among non-void statements**, which is a partial
    index rather than a constraint on purpose: a mistaken import is voided and the same file
    imported again, and a voided statement must block nothing (decision 3).
    """

    __tablename__ = "bank_statements"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_bank_statements_company_id_id"),
        UniqueConstraint("company_id", "number", name="uq_bank_statements_company_number"),
        ForeignKeyConstraint(
            ["company_id", "bank_account_id"],
            ["bank_accounts.company_id", "bank_accounts.id"],
            name="fk_bank_statements_bank_account",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_bank_statements_account_sha256",
            "bank_account_id",
            "file_sha256",
            unique=True,
            postgresql_where=text("status <> 'void' AND file_sha256 IS NOT NULL"),
        ),
        Index(
            "ix_bank_statements_account_dates",
            "company_id",
            "bank_account_id",
            "from_date",
            "to_date",
        ),
        CheckConstraint("to_date >= from_date", name="range_is_forward"),
        CheckConstraint("line_count >= 0 AND lines_skipped >= 0", name="counts_not_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    source: Mapped[StatementSource] = mapped_column(statement_source_type, nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    #: The mapping **as it was when this file was read**. A later edit to the account's format
    #: must not change what a stored statement meant.
    format_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date] = mapped_column(Date, nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    closing_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lines_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[StatementStatus] = mapped_column(
        statement_status_type, nullable=False, default=StatementStatus.OPEN
    )
    imported_by: Mapped[int | None] = mapped_column(BigInteger)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    voided_by: Mapped[int | None] = mapped_column(BigInteger)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    idempotency_hash: Mapped[str | None] = mapped_column(String(64))


class BankStatementLine(AuditedMixin, CompanyScopedMixin, Base):
    """One line of the bank's record. **Immutable once written** (`VN013`).

    `amount` is **credit positive**, the same sign the ledger carries: money into the account
    is a debit on its GL account and a positive `amount` on the journal line, and the bank's
    credit column is the same event. One convention on both sides is what lets a match assert
    equality rather than equality-after-a-sign-flip.

    `fingerprint` is what makes an overlapping export ordinary rather than an error. It hashes
    the account, the value date, the amount, the normalised description, the reference **and
    the occurrence index among identical tuples within the file** — so two identical fees on
    one day are two lines, and the same two in next month's overlapping export are skipped as
    two rather than collapsing into one.
    """

    __tablename__ = "bank_statement_lines"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_bank_statement_lines_company_id_id"),
        # Partial, and this is the whole reason `is_void` exists as a column. Decision 3 says
        # a voided statement's fingerprints block nothing — a mistaken import is voided and
        # the same file imported again — so the uniqueness scope has to be "the lines that
        # still count". A partial index cannot reach `bank_statements.status`, and the lines
        # cannot be deleted (they are immutable), so the status is denormalised onto the line
        # and the trigger exempts exactly that column, the way P7's receipt trigger exempts
        # `copy_count`. The invariant asserts the copy equals its statement's status.
        Index(
            "uq_bank_statement_lines_account_fingerprint",
            "bank_account_id",
            "fingerprint",
            unique=True,
            postgresql_where=text("NOT is_void"),
        ),
        ForeignKeyConstraint(
            ["company_id", "statement_id"],
            ["bank_statements.company_id", "bank_statements.id"],
            name="fk_bank_statement_lines_statement",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "bank_account_id"],
            ["bank_accounts.company_id", "bank_accounts.id"],
            name="fk_bank_statement_lines_bank_account",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_bank_statement_lines_account_external_id",
            "bank_account_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL AND NOT is_void"),
        ),
        Index(
            "ix_bank_statement_lines_account_date",
            "company_id",
            "bank_account_id",
            "value_date",
        ),
        CheckConstraint("amount <> 0", name="amount_not_zero"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    statement_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Denormalised so the matcher never joins for it — every candidate query is per account.
    bank_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    value_date: Mapped[date] = mapped_column(Date, nullable=False)
    booking_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    balance_after: Mapped[Decimal | None] = mapped_column(MONEY)
    #: The bank's own transaction id, when the format has one. Unique per account where
    #: present, so a re-export is skipped on it before the fingerprint is even needed.
    external_id: Mapped[str | None] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Its statement's `status`, denormalised. The **only** column `VN013` lets change, and
    #: only `void_statement` changes it: a voided line has to leave the uniqueness scope, the
    #: listings and the counts, and it cannot be deleted because it is the bank's record of
    #: something that was genuinely read off a file.
    is_void: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class BankMatch(AuditedMixin, CompanyScopedMixin, Base):
    """The assertion that a set of statement lines and a set of ledger lines are one event.

    Members live in the two tables below, each unique on its line — a statement line is in at
    most one match, a journal line is in at most one match, and those are DB constraints
    rather than a service rule. A match **with statement members balances**: Σ statement
    `amount` == Σ ledger reconciled amount. A `tick` match has journal members only, which is
    paper mode (decision 5).
    """

    __tablename__ = "bank_matches"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_bank_matches_company_id_id"),
        ForeignKeyConstraint(
            ["company_id", "bank_account_id"],
            ["bank_accounts.company_id", "bank_accounts.id"],
            name="fk_bank_matches_bank_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reconciliation_id"],
            ["bank_reconciliations.company_id", "bank_reconciliations.id"],
            name="fk_bank_matches_reconciliation",
            ondelete="RESTRICT",
        ),
        Index("ix_bank_matches_account", "company_id", "bank_account_id"),
        Index(
            "ix_bank_matches_reconciliation",
            "company_id",
            "reconciliation_id",
            postgresql_where=text("reconciliation_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[BankMatchKind] = mapped_column(bank_match_kind_type, nullable=False)
    rule: Mapped[BankMatchRule] = mapped_column(bank_match_rule_type, nullable=False)
    #: Assigned at lock, to every match effective at the reconciliation's date that has none.
    #: That assignment — not a date comparison — is what membership means afterwards, so a
    #: match made later cannot move a locked figure (clause 4).
    reconciliation_id: Mapped[int | None] = mapped_column(BigInteger)
    matched_by: Mapped[int | None] = mapped_column(BigInteger)
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


class BankMatchStatementLine(AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "bank_match_statement_lines"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "id", name="uq_bank_match_statement_lines_company_id_id"
        ),
        # A statement line is in at most one match — the constraint, not the convention.
        UniqueConstraint(
            "statement_line_id", name="uq_bank_match_statement_lines_statement_line"
        ),
        ForeignKeyConstraint(
            ["company_id", "match_id"],
            ["bank_matches.company_id", "bank_matches.id"],
            name="fk_bank_match_statement_lines_match",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "statement_line_id"],
            ["bank_statement_lines.company_id", "bank_statement_lines.id"],
            name="fk_bank_match_statement_lines_line",
            ondelete="RESTRICT",
        ),
        Index("ix_bank_match_statement_lines_match", "company_id", "match_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    match_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    statement_line_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class BankMatchJournalLine(AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "bank_match_journal_lines"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_bank_match_journal_lines_company_id_id"),
        # A journal line is in at most one match.
        UniqueConstraint("journal_line_id", name="uq_bank_match_journal_lines_journal_line"),
        ForeignKeyConstraint(
            ["company_id", "match_id"],
            ["bank_matches.company_id", "bank_matches.id"],
            name="fk_bank_match_journal_lines_match",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "journal_line_id"],
            ["journal_lines.company_id", "journal_lines.id"],
            name="fk_bank_match_journal_lines_line",
            ondelete="RESTRICT",
        ),
        Index("ix_bank_match_journal_lines_match", "company_id", "match_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    match_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    journal_line_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class BankReconciliation(AuditedMixin, CompanyScopedMixin, Base):
    """The dated proof that the ledger and the bank agree — and once locked, a snapshot that
    never changes.

    `high_water_line_id` is the greatest `journal_lines.id` on the account at the moment of
    the lock. A line dated on or before the reconciliation date but posted *after* it (id
    above the mark) is a **late line**: outstanding in the next reconciliation, flagged on the
    workspace, and never folded back into this one. Membership by id, exactly as P7's VAT
    return and Z report decided it — a timestamp range is what lets a row fall between two
    closes.
    """

    __tablename__ = "bank_reconciliations"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_bank_reconciliations_company_id_id"),
        UniqueConstraint("company_id", "number", name="uq_bank_reconciliations_company_number"),
        ForeignKeyConstraint(
            ["company_id", "bank_account_id"],
            ["bank_accounts.company_id", "bank_accounts.id"],
            name="fk_bank_reconciliations_bank_account",
            ondelete="RESTRICT",
        ),
        # One open reconciliation per bank account, from the database. The service refuses it
        # first with `reconciliation_open_exists`; this is why the refusal cannot be raced.
        Index(
            "uq_bank_reconciliations_open_per_account",
            "bank_account_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index(
            "ix_bank_reconciliations_account_date",
            "company_id",
            "bank_account_id",
            "reconciliation_date",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    reconciliation_date: Mapped[date] = mapped_column(Date, nullable=False)
    statement_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    ledger_balance: Mapped[Decimal | None] = mapped_column(MONEY)
    outstanding_total: Mapped[Decimal | None] = mapped_column(MONEY)
    difference: Mapped[Decimal | None] = mapped_column(MONEY)
    status: Mapped[ReconciliationStatus] = mapped_column(
        reconciliation_status_type, nullable=False, default=ReconciliationStatus.OPEN
    )
    high_water_line_id: Mapped[int | None] = mapped_column(BigInteger)
    #: The outstanding items as they stood at the lock — line, date, number, amount. Stored
    #: because the report prints what the reconciliation *said*, beside a live recomputation.
    outstanding_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    locked_by: Mapped[int | None] = mapped_column(BigInteger)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_by: Mapped[int | None] = mapped_column(BigInteger)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_reason: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    idempotency_hash: Mapped[str | None] = mapped_column(String(64))


class PaymentRun(AuditedMixin, CompanyScopedMixin, Base):
    """A batch supplier payment: N ordinary P4 settlements, one bank line on the statement.

    There are **no draft rows** — the selection is previewed and posted in one call, P7's
    preview → post shape — which is why `number` is claimed at posting and every row holds
    one, reversed rows included (the `PYR-` run is gapless).
    """

    __tablename__ = "payment_runs"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_payment_runs_company_id_id"),
        UniqueConstraint("company_id", "number", name="uq_payment_runs_company_number"),
        ForeignKeyConstraint(
            ["company_id", "bank_account_id"],
            ["bank_accounts.company_id", "bank_accounts.id"],
            name="fk_payment_runs_bank_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "currency_id"],
            ["currencies.company_id", "currencies.id"],
            name="fk_payment_runs_currency",
            ondelete="RESTRICT",
        ),
        Index("ix_payment_runs_account_date", "company_id", "bank_account_id", "payment_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    #: The run's own number, stamped on every settlement it posts. That is what the bank's one
    #: line quotes, and what decision 4's `payment_run` rule matches on.
    reference: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[PaymentRunStatus] = mapped_column(
        payment_run_status_type, nullable=False, default=PaymentRunStatus.POSTED
    )
    posted_by: Mapped[int | None] = mapped_column(BigInteger)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reversal_reason: Mapped[str | None] = mapped_column(Text)
    reversed_by: Mapped[int | None] = mapped_column(BigInteger)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    idempotency_hash: Mapped[str | None] = mapped_column(String(64))


class PaymentRunLine(AuditedMixin, CompanyScopedMixin, Base):
    """One invoice paid by the run. `settlement_document_id` and `allocation_id` are the P4
    rows it produced — the run owns no posting of its own."""

    __tablename__ = "payment_run_lines"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_payment_run_lines_company_id_id"),
        ForeignKeyConstraint(
            ["company_id", "run_id"],
            ["payment_runs.company_id", "payment_runs.id"],
            name="fk_payment_run_lines_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "partner_id"],
            ["partners.company_id", "partners.id"],
            name="fk_payment_run_lines_partner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "document_id"],
            ["partner_documents.company_id", "partner_documents.id"],
            name="fk_payment_run_lines_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "settlement_document_id"],
            ["partner_documents.company_id", "partner_documents.id"],
            name="fk_payment_run_lines_settlement",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "allocation_id"],
            ["allocations.company_id", "allocations.id"],
            name="fk_payment_run_lines_allocation",
            ondelete="RESTRICT",
        ),
        Index("ix_payment_run_lines_run", "company_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    partner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    document_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    settlement_document_id: Mapped[int | None] = mapped_column(BigInteger)
    allocation_id: Mapped[int | None] = mapped_column(BigInteger)
