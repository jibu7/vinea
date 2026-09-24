"""Statement import: preview, import, dedup and void (P8 decision 3).

A statement is **the bank's record**, imported as it came. Nothing here posts, nothing here
edits a line, and nothing here moves money — the whole module writes to two tables neither the
ledger nor the subledger reads.

Three behaviours are worth stating up front because each is the answer to a real habit:

* **Preview before you write.** `preview()` parses the file with the account's mapping and
  returns what it found — the first rows, the opening and closing it derived, how many lines
  the account already holds, and every parse error with its row number. It stores nothing. An
  import with any parse error imports *nothing*: half a statement is not a statement.
* **The same export again is normal.** Rwandan bank portals export "this month" and the month
  overlaps. Lines already held are skipped and counted, and the result says "N new, M
  skipped". It is an ordinary outcome, not an error.
* **The same *file* twice is a mistake.** Byte-identical, and refused
  (`statement_already_imported`). A mistaken import is **voided** and the file imported again
  — which works because a voided statement's hash and its lines' fingerprints block nothing.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.banking import accounts as accounts_service
from app.banking.formats import (
    ParsedLine,
    ParseError,
    file_sha256,
    line_fingerprint,
    load_format,
    parse,
    validate_format,
)
from app.core.errors import ConflictError, NotFoundError
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.sequences import DocType, claim_number
from app.models.banking import (
    BankAccount,
    BankAccountKind,
    BankMatchStatementLine,
    BankStatement,
    BankStatementLine,
    StatementSource,
    StatementStatus,
)
from app.models.user import User
from app.services.audit import record_audit

#: How many parsed rows the preview hands back. Twenty is enough to recognise that the mapping
#: is aimed at the right columns and few enough that a thousand-line export does not become a
#: thousand-row response — and the counts and the errors below are complete either way.
PREVIEW_ROWS = 20


class StatementParseError(PostingError):
    """Row-numbered parse failures. A `PostingError` rather than a bare 422 so it lands in the
    same envelope as every other refusal a screen shows inline."""

    code = "statement_parse_error"


@dataclass(frozen=True)
class PreviewLine:
    row: int
    value_date: date
    booking_date: date | None
    description: str
    reference: str | None
    amount: Decimal
    balance_after: Decimal | None
    external_id: str | None
    #: Already held by this account, so an import would count it under `lines_skipped`.
    already_held: bool


@dataclass(frozen=True)
class StatementPreview:
    bank_account_id: int
    file_name: str | None
    file_sha256: str
    #: The mapping the file was read with, as it would be snapshotted.
    format_snapshot: dict[str, Any]
    lines: list[PreviewLine]
    line_count: int
    new_count: int
    skipped_count: int
    #: Rows the mapping's `empty_amount: skip` passed over — a `BALANCE B/FWD`, not a line
    #: already held. Reported apart from `skipped_count` so the two are never read as one.
    lines_skipped_no_amount: int
    from_date: date | None
    to_date: date | None
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    errors: list[ParseError]
    #: The whole file already imported and not voided — an import would be refused.
    duplicate_file: bool


@dataclass(frozen=True)
class ImportResult:
    statement: BankStatement
    new_count: int
    skipped_count: int
    lines_skipped_no_amount: int = 0
    #: True when the key replayed an earlier import: nothing was written this time.
    replayed: bool = False


def _bank_account(db: Session, company_id: int, bank_account_id: int) -> BankAccount:
    row = accounts_service.get(db, company_id, bank_account_id)
    if row.kind != BankAccountKind.BANK:
        raise LedgerStateError(
            f"{row.code} is a cash account; there is no statement to import",
            code="statement_needs_bank",
            field_errors={"bank_account_id": ["a cash account has no statement"]},
        )
    return row


def _held_fingerprints(db: Session, bank_account_id: int) -> set[str]:
    """What this account already holds, voided lines excluded — decision 3's rule that a
    voided statement blocks nothing, applied at the one place it decides an outcome."""
    return set(
        db.scalars(
            select(BankStatementLine.fingerprint).where(
                BankStatementLine.bank_account_id == bank_account_id,
                BankStatementLine.is_void.is_(False),
            )
        )
    )


def _held_external_ids(db: Session, bank_account_id: int) -> set[str]:
    return set(
        db.scalars(
            select(BankStatementLine.external_id).where(
                BankStatementLine.bank_account_id == bank_account_id,
                BankStatementLine.is_void.is_(False),
                BankStatementLine.external_id.is_not(None),
            )
        )
    )


def _fingerprint_of(line: ParsedLine, bank_account_id: int) -> str:
    return line_fingerprint(
        bank_account_id=bank_account_id,
        value_date=line.value_date,
        amount=line.amount,
        description=line.description,
        reference=line.reference,
        occurrence=line.occurrence,
    )


def _is_held(
    line: ParsedLine, fingerprint: str, held: set[str], external: set[str]
) -> bool:
    """The bank's own transaction id wins where the format has one — it is the bank saying
    "this is the row you already have", which is a stronger statement than any hash Vinea
    computes over the text it printed."""
    if line.external_id and line.external_id in external:
        return True
    return fingerprint in held


def _duplicate_file(db: Session, bank_account_id: int, digest: str) -> BankStatement | None:
    return db.scalar(
        select(BankStatement).where(
            BankStatement.bank_account_id == bank_account_id,
            BankStatement.file_sha256 == digest,
            BankStatement.status != StatementStatus.VOID,
        )
    )


def preview(
    db: Session,
    company_id: int,
    *,
    bank_account_id: int,
    content: bytes,
    file_name: str | None = None,
    override_format: dict[str, Any] | None = None,
) -> StatementPreview:
    """Parse and report. Writes nothing.

    `override_format` is what *Test with a file* on the format editor passes: the mapping being
    edited, not the one stored, so a user can try a change before saving it onto an account
    whose next import would otherwise fail on it.
    """
    row = _bank_account(db, company_id, bank_account_id)
    fmt = (
        # `validate_format`, not a bare `model_validate`: *Test with a file* sends a mapping a
        # user is still editing, and a half-written one has to come back as
        # `statement_format_invalid` on the screen rather than as a 500.
        validate_format(override_format)
        if override_format is not None
        else load_format(row.statement_format)
    )
    parsed = parse(content, fmt, bank_account_id=bank_account_id)
    held = _held_fingerprints(db, bank_account_id)
    external = _held_external_ids(db, bank_account_id)

    lines: list[PreviewLine] = []
    skipped = 0
    for line in parsed.lines:
        already = _is_held(line, _fingerprint_of(line, bank_account_id), held, external)
        skipped += 1 if already else 0
        if len(lines) < PREVIEW_ROWS:
            lines.append(
                PreviewLine(
                    row=line.row,
                    value_date=line.value_date,
                    booking_date=line.booking_date,
                    description=line.description,
                    reference=line.reference,
                    amount=line.amount,
                    balance_after=line.balance_after,
                    external_id=line.external_id,
                    already_held=already,
                )
            )
    opening, closing = parsed.derived_balances()
    digest = file_sha256(content)
    return StatementPreview(
        bank_account_id=bank_account_id,
        file_name=file_name,
        file_sha256=digest,
        format_snapshot=fmt.model_dump(mode="json"),
        lines=lines,
        line_count=len(parsed.lines),
        new_count=len(parsed.lines) - skipped,
        skipped_count=skipped,
        lines_skipped_no_amount=parsed.skipped_no_amount,
        from_date=parsed.from_date,
        to_date=parsed.to_date,
        opening_balance=opening,
        closing_balance=closing,
        errors=parsed.errors,
        duplicate_file=_duplicate_file(db, bank_account_id, digest) is not None,
    )


def import_statement(
    db: Session,
    company_id: int,
    *,
    bank_account_id: int,
    content: bytes,
    file_name: str | None = None,
    opening_balance: Decimal | None = None,
    closing_balance: Decimal | None = None,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> ImportResult:
    """Write the statement and its new lines. Refuses on any parse error, and on the same file
    twice.

    The keyed `opening_balance` / `closing_balance` override what the balance column derived —
    the preview shows both and the user confirms. Where the format has no balance column they
    are the only source, and an import that has neither is refused rather than storing a zero
    that a reconciliation would later default its statement balance from.
    """
    replayed = _replay(db, company_id, idempotency_key, idempotency_hash)
    if replayed is not None:
        return ImportResult(
            statement=replayed,
            new_count=replayed.line_count,
            skipped_count=replayed.lines_skipped,
            lines_skipped_no_amount=replayed.lines_skipped_no_amount,
            replayed=True,
        )

    row = _bank_account(db, company_id, bank_account_id)
    digest = file_sha256(content)
    duplicate = _duplicate_file(db, bank_account_id, digest)
    if duplicate is not None:
        raise ConflictError(
            f"This file was already imported as {duplicate.number}",
            code="statement_already_imported",
            field_errors={"file": [f"already imported as {duplicate.number}"]},
        )

    fmt = load_format(row.statement_format)
    parsed = parse(content, fmt, bank_account_id=bank_account_id)
    if parsed.errors:
        first = parsed.errors[0]
        raise StatementParseError(
            f"Row {first.row}: {first.message}",
            field_errors={
                f"rows.{error.row}.{error.column or 'row'}": [error.message]
                for error in parsed.errors
            },
        )
    if not parsed.lines:
        raise LedgerStateError(
            "The file holds no statement lines", code="statement_empty"
        )

    derived_opening, derived_closing = parsed.derived_balances()
    opening = opening_balance if opening_balance is not None else derived_opening
    closing = closing_balance if closing_balance is not None else derived_closing
    if opening is None or closing is None:
        raise LedgerStateError(
            "This format carries no balance column, so the opening and closing balances "
            "must be keyed",
            code="statement_balances_required",
            field_errors={"closing_balance": ["required for this format"]},
        )

    return _write(
        db,
        row,
        parsed_lines=parsed.lines,
        source=StatementSource.CSV,
        file_name=file_name,
        digest=digest,
        format_snapshot=fmt.model_dump(mode="json"),
        opening=opening,
        closing=closing,
        skipped_no_amount=parsed.skipped_no_amount,
        actor=actor,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
        request=request,
    )


def import_manual(
    db: Session,
    company_id: int,
    *,
    bank_account_id: int,
    lines: list[ParsedLine],
    opening_balance: Decimal,
    closing_balance: Decimal,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> ImportResult:
    """A paper statement, keyed line by line. Same table, same fingerprints, same dedup — so
    the same statement keyed twice is skipped exactly as a re-exported file is, and everything
    downstream (the matcher, the reconciliation, the report) cannot tell the two apart."""
    row = _bank_account(db, company_id, bank_account_id)
    replayed = _replay(db, company_id, idempotency_key, idempotency_hash)
    if replayed is not None:
        return ImportResult(
            statement=replayed,
            new_count=replayed.line_count,
            skipped_count=replayed.lines_skipped,
            lines_skipped_no_amount=replayed.lines_skipped_no_amount,
            replayed=True,
        )
    if not lines:
        raise LedgerStateError("A statement needs at least one line", code="statement_empty")
    return _write(
        db,
        row,
        parsed_lines=lines,
        source=StatementSource.MANUAL,
        file_name=None,
        digest=None,
        format_snapshot=None,
        opening=opening_balance,
        closing=closing_balance,
        actor=actor,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
        request=request,
    )


def _write(
    db: Session,
    row: BankAccount,
    *,
    parsed_lines: list[ParsedLine],
    source: StatementSource,
    file_name: str | None,
    digest: str | None,
    format_snapshot: dict[str, Any] | None,
    opening: Decimal,
    closing: Decimal,
    actor: User,
    idempotency_key: str | None,
    idempotency_hash: str | None,
    request: Request | None,
    skipped_no_amount: int = 0,
) -> ImportResult:
    held = _held_fingerprints(db, row.id)
    external = _held_external_ids(db, row.id)

    kept: list[tuple[ParsedLine, str]] = []
    skipped = 0
    for line in parsed_lines:
        fingerprint = _fingerprint_of(line, row.id)
        if _is_held(line, fingerprint, held, external):
            skipped += 1
            continue
        # Within one file too: a fingerprint carries its occurrence index, so this only fires
        # on a file that literally repeats a row the parser already numbered.
        held.add(fingerprint)
        if line.external_id:
            external.add(line.external_id)
        kept.append((line, fingerprint))

    number = claim_number(db, row.company_id, DocType.BANK_STATEMENT)
    statement = BankStatement(
        company_id=row.company_id,
        bank_account_id=row.id,
        number=number.number,
        source=source,
        file_name=file_name,
        file_sha256=digest,
        format_snapshot=format_snapshot,
        # Over the **whole file**, not only the lines kept: the statement covers the period the
        # bank exported, and a re-export whose every line was already held still says which
        # weeks it was a statement of.
        from_date=min(line.value_date for line in parsed_lines),
        to_date=max(line.value_date for line in parsed_lines),
        opening_balance=opening,
        closing_balance=closing,
        line_count=len(kept),
        lines_skipped=skipped,
        lines_skipped_no_amount=skipped_no_amount,
        status=StatementStatus.OPEN,
        imported_by=actor.id,
        imported_at=datetime.now(UTC),
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(statement)
    db.flush()

    for line_no, (line, fingerprint) in enumerate(kept, start=1):
        db.add(
            BankStatementLine(
                company_id=row.company_id,
                statement_id=statement.id,
                bank_account_id=row.id,
                line_no=line_no,
                value_date=line.value_date,
                booking_date=line.booking_date,
                description=line.description,
                reference=line.reference,
                amount=line.amount,
                balance_after=line.balance_after,
                external_id=line.external_id,
                fingerprint=fingerprint,
                is_void=False,
            )
        )
    db.flush()
    record_audit(
        db,
        company_id=row.company_id,
        action="bank_statement.imported",
        entity="bank_statements",
        entity_id=statement.id,
        after={
            "number": statement.number,
            "bank_account": row.code,
            "new": len(kept),
            "skipped": skipped,
            "skipped_no_amount": skipped_no_amount,
        },
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )
    return ImportResult(
        statement=statement,
        new_count=len(kept),
        skipped_count=skipped,
        lines_skipped_no_amount=skipped_no_amount,
    )


def get(db: Session, company_id: int, statement_id: int) -> BankStatement:
    row = db.get(BankStatement, statement_id)
    if row is None or row.company_id != company_id:
        raise NotFoundError("Bank statement not found")
    return row


def list_statements(
    db: Session,
    company_id: int,
    *,
    bank_account_id: int | None = None,
    include_void: bool = False,
    limit: int = 50,
) -> list[BankStatement]:
    statement = select(BankStatement).where(BankStatement.company_id == company_id)
    if bank_account_id is not None:
        statement = statement.where(BankStatement.bank_account_id == bank_account_id)
    if not include_void:
        statement = statement.where(BankStatement.status != StatementStatus.VOID)
    return list(
        db.scalars(
            statement.order_by(BankStatement.to_date.desc(), BankStatement.id.desc()).limit(limit)
        )
    )


def lines_of(db: Session, company_id: int, statement_id: int) -> list[BankStatementLine]:
    return list(
        db.scalars(
            select(BankStatementLine)
            .where(
                BankStatementLine.company_id == company_id,
                BankStatementLine.statement_id == statement_id,
            )
            .order_by(BankStatementLine.line_no)
        )
    )


def void(
    db: Session,
    company_id: int,
    statement_id: int,
    *,
    reason: str | None = None,
    actor: User,
    request: Request | None = None,
) -> BankStatement:
    """Take the whole statement back out — the only correction a statement has.

    Refused while any of its lines is in a match (`statement_has_matches`): unmatch them
    first. That order is deliberate rather than cascading, because a match may belong to a
    *locked* reconciliation, and silently deleting it would move a figure that is supposed to
    be a permanent record.

    The lines stay. They were genuinely read off a file, they are immutable, and what changes
    is the one column `VN013` exempts — `is_void`, which is what takes them out of the
    fingerprint uniqueness scope, every listing and every count.
    """
    statement = get(db, company_id, statement_id)
    if statement.status == StatementStatus.VOID:
        raise LedgerStateError(
            f"{statement.number} is already void", code="statement_already_void"
        )
    matched = db.scalar(
        select(func.count())
        .select_from(BankMatchStatementLine)
        .join(
            BankStatementLine,
            BankStatementLine.id == BankMatchStatementLine.statement_line_id,
        )
        .where(
            BankMatchStatementLine.company_id == company_id,
            BankStatementLine.statement_id == statement_id,
        )
    )
    if matched:
        raise LedgerStateError(
            f"{statement.number} has {matched} matched line(s); unmatch them first",
            code="statement_has_matches",
            field_errors={"statement_id": [f"{matched} line(s) are matched"]},
        )
    statement.status = StatementStatus.VOID
    statement.voided_by = actor.id
    statement.voided_at = datetime.now(UTC)
    db.execute(
        update(BankStatementLine)
        .where(
            BankStatementLine.company_id == company_id,
            BankStatementLine.statement_id == statement_id,
        )
        .values(is_void=True)
    )
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="bank_statement.voided",
        entity="bank_statements",
        entity_id=statement.id,
        before={"status": StatementStatus.OPEN.value},
        after={"status": StatementStatus.VOID.value, "reason": reason},
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )
    return statement


def _replay(
    db: Session, company_id: int, key: str | None, request_hash: str | None
) -> BankStatement | None:
    if not key:
        return None
    statement = db.scalar(
        select(BankStatement).where(
            BankStatement.company_id == company_id, BankStatement.idempotency_key == key
        )
    )
    if statement is None:
        return None
    if request_hash is not None and statement.idempotency_hash != request_hash:
        raise LedgerStateError(
            f"Idempotency-Key {key} was already used for a different request "
            f"({statement.number}); use a new key",
            code="idempotency_key_reused",
        )
    return statement
