"""Statement column mappings and the CSV parser (P8 decision 3).

**One parser behind one interface**, so camt.053 and MT940 — out of scope this phase, and the
plan says CSV first — arrive as another parser rather than a redesign: `parse()` takes bytes
and a `StatementFormat` and returns `ParsedStatement`, and nothing above it knows what a
delimiter is.

The mapping is a *column mapping*, not a dialect: Rwandan bank exports differ in which column
holds what, whether the amount is one signed column or a debit/credit pair, how a thousand is
separated and how a date is written — and in nothing else that matters. `generic` is the
preset the committed samples are in (`Date, Description, Reference, Debit, Credit, Balance`,
ISO dates), and a real export becomes a `custom` mapping beside it under
`docs/banking/samples/`.

**Column references.** A column is named by its header when the file has one (matched
case-insensitively, trimmed) and by a 0-based index when it does not. Decided this way rather
than "always by index" because a header name survives a bank adding a column in the middle,
which is the change these files actually undergo; and rather than "always by name" because a
headerless export exists and would otherwise need a synthetic header row invented for it.
`header_rows` is how many rows precede the data, and the **last** of them is the header.

**Errors are row-numbered and total.** `parse()` never raises on a bad row: it collects
`ParseError(row, column, message)` for every one of them, and the caller decides. `preview`
shows them all; `import` refuses the whole file (`statement_parse_error`) if there is one,
because half a statement is not a statement.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.core.errors import AppError
from app.kernel.money import fingerprint_material
from app.models.banking import (
    StatementAmountMode,
    StatementEmptyDescription,
    StatementFormatPreset,
    StatementSignConvention,
)

#: Everything but the digits, the sign and the decimal point, once separators are resolved.
_AMOUNT_NOISE = re.compile(r"[^0-9.\-+]")
#: The normalisation both the fingerprint and the `reference` auto-rule read: upper case,
#: alphanumerics only. A bank that writes `INV-1 / C1` one month and `INV-1  C1` the next is
#: writing the same line, and a match that turned on the punctuation would be a coin toss.
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
#: `1 200,00 CR` — a bank signing a single column with a marker instead of a minus.
_SIGN_MARKER = re.compile(r"\s*(CR|DR)$", re.IGNORECASE)


class StatementFormatError(AppError):
    """The mapping itself is unusable — a mode without the columns it needs, an unknown
    preset. Raised when the format is *saved*, not when a file is read, so a bad mapping
    cannot sit on an account waiting to fail at import."""

    status_code = 422
    code = "statement_format_invalid"


class StatementFormat(BaseModel):
    """The column mapping. Stored as JSONB on `bank_accounts.statement_format`, and copied
    onto every statement as `format_snapshot` at import — so editing the account's mapping
    never changes what a stored statement meant."""

    preset: StatementFormatPreset = StatementFormatPreset.GENERIC
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    encoding: str = "utf-8-sig"
    header_rows: int = Field(default=1, ge=0, le=20)
    date_column: str = "Date"
    #: `strptime`. Not inferred: a file of `03/04/2026` is either 3 April or 4 March and
    #: guessing wrong silently moves six months of reconciliation.
    date_format: str = "%Y-%m-%d"
    booking_date_column: str | None = None
    description_column: str = "Description"
    reference_column: str | None = "Reference"
    amount_mode: StatementAmountMode = StatementAmountMode.DEBIT_CREDIT
    debit_column: str | None = "Debit"
    credit_column: str | None = "Credit"
    amount_column: str | None = None
    sign_convention: StatementSignConvention = StatementSignConvention.CREDIT_POSITIVE
    balance_column: str | None = "Balance"
    external_id_column: str | None = None
    decimal_separator: str = Field(default=".", min_length=1, max_length=1)
    thousands_separator: str | None = Field(default=",", max_length=1)
    #: BPR's 2025 e-statement writes its RWF 20 transfer-fee lines with a `CHG…` reference and
    #: an empty Description. `reference` lets the reference stand in for it; a row with
    #: neither is still refused, because a line nobody can name cannot be matched by a person.
    empty_description: StatementEmptyDescription = StatementEmptyDescription.REFUSE
    #: BPR's 2022 e-statement fills the unused column of every row with `0.00`. With this on,
    #: an explicit zero in the debit or credit column reads as empty, so such a row is
    #: one-sided; off, a zero is *filled* and the row is refused as carrying both.
    zero_is_empty: bool = False

    @model_validator(mode="after")
    def _the_mode_has_its_columns(self) -> "StatementFormat":
        if self.amount_mode == StatementAmountMode.DEBIT_CREDIT:
            if not self.debit_column or not self.credit_column:
                raise ValueError("debit_credit needs both debit_column and credit_column")
        elif not self.amount_column:
            raise ValueError("signed needs amount_column")
        if self.thousands_separator and self.thousands_separator == self.decimal_separator:
            raise ValueError("the thousands and decimal separators must differ")
        return self


#: The layout the three committed samples are in, and the one a bank export is mapped *onto*
#: when its columns happen to line up. Held as a `StatementFormat` rather than a dict so the
#: preset cannot drift out of the model's own validation.
GENERIC_PRESET = StatementFormat(preset=StatementFormatPreset.GENERIC)


def load_format(raw: dict[str, Any] | None) -> StatementFormat:
    """A stored mapping, or the preset. `None` means an account whose format was never
    written — the generic layout is the useful default and the preview is where a user finds
    out it does not fit their file."""
    if not raw:
        return GENERIC_PRESET
    return StatementFormat.model_validate(raw)


def validate_format(raw: dict[str, Any]) -> StatementFormat:
    """The save path. Pydantic's message is re-raised in this build's envelope so the screen
    gets `statement_format_invalid` rather than a 422 shaped like a body validation error."""
    try:
        return StatementFormat.model_validate(raw)
    except ValueError as err:
        raise StatementFormatError(str(err), code="statement_format_invalid") from err


@dataclass(frozen=True)
class ParseError:
    #: 1-based, counted over the **file** including its header rows — the number the user sees
    #: in their spreadsheet, not the index of the row among the data.
    row: int
    column: str | None
    message: str


@dataclass(frozen=True)
class ParsedLine:
    row: int
    value_date: date
    booking_date: date | None
    description: str
    reference: str | None
    #: Credit positive, in the bank account's currency — the ledger's own sign convention.
    amount: Decimal
    balance_after: Decimal | None
    external_id: str | None
    #: How many identical (date, amount, description, reference) tuples came before this one
    #: **in this file**. Two identical fees on one day are two lines; the index is what keeps
    #: them two through the fingerprint and therefore through an overlapping re-export.
    occurrence: int


@dataclass
class ParsedStatement:
    lines: list[ParsedLine] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)

    @property
    def from_date(self) -> date | None:
        return min((line.value_date for line in self.lines), default=None)

    @property
    def to_date(self) -> date | None:
        return max((line.value_date for line in self.lines), default=None)

    def derived_balances(self) -> tuple[Decimal | None, Decimal | None]:
        """`(opening, closing)` read off the balance column: closing is the last line's
        balance, opening is the first line's balance less its own amount.

        Both `None` when the format carries no balance column or the file left it empty — the
        preview then shows nothing and the user keys the two figures, which is what a paper
        statement needs anyway. Ordered by the file's own sequence rather than by date,
        because a running balance is a running balance: re-sorting it would compute an
        opening from a row the bank did not intend as the first.
        """
        if not self.lines:
            return None, None
        first, last = self.lines[0], self.lines[-1]
        if first.balance_after is None or last.balance_after is None:
            return None, None
        return first.balance_after - first.amount, last.balance_after


def normalise(text: str | None) -> str:
    """Upper case, alphanumerics only — the form both the fingerprint and the `reference`
    auto-rule compare on."""
    if not text:
        return ""
    return _NON_ALNUM.sub("", text.upper())


def line_fingerprint(
    *,
    bank_account_id: int,
    value_date: date,
    amount: Decimal,
    description: str,
    reference: str | None,
    occurrence: int,
) -> str:
    """Identifies the **event the bank recorded**, so the same line in an overlapping export
    is recognised and skipped.

    Over values, never over text this function assembled — `fingerprint_material` is the one
    place in the build that turns values into bytes, and `tests/test_fingerprints.py` fails on
    a hashing site that reaches a digest any other way. It matters here for the same reason it
    mattered to P7's item hash: `amount` arrives as a `NUMERIC(20,6)` `Decimal` on one path and
    as a parsed `Decimal("2500")` on another, the two are equal as numbers, and a hash over
    their text would call one line two and import a duplicate.

    `occurrence` is in the material deliberately. Without it two identical bank fees on one day
    collapse to one fingerprint, the second is "already held", and the statement imports one
    line where the bank showed two — a difference the reconciliation would then never close.
    """
    return sha256(
        fingerprint_material(
            {
                "bank_account_id": bank_account_id,
                "value_date": value_date,
                "amount": amount,
                "description": normalise(description),
                "reference": normalise(reference),
                "occurrence": occurrence,
            }
        ).encode()
    ).hexdigest()


def file_sha256(content: bytes) -> str:
    """The whole file, byte for byte. Catches the same export imported twice before a single
    line has been parsed (`statement_already_imported`)."""
    return sha256(content).hexdigest()


class _Resolver:
    """Column reference → index, by header name or by position."""

    def __init__(self, header: list[str] | None) -> None:
        self._by_name = (
            {name.strip().casefold(): index for index, name in enumerate(header)}
            if header
            else {}
        )
        self._width = len(header) if header else 0

    def index(self, reference: str) -> int | None:
        found = self._by_name.get(reference.strip().casefold())
        if found is not None:
            return found
        try:
            position = int(reference)
        except ValueError:
            return None
        return position if position >= 0 else None

    def value(self, row: list[str], reference: str) -> str | None:
        index = self.index(reference)
        if index is None or index >= len(row):
            return None
        return row[index].strip()


def parse(content: bytes, fmt: StatementFormat, *, bank_account_id: int) -> ParsedStatement:
    """Read the file with this mapping. Never raises on the file's contents: an unreadable row
    is an error with a row number, and the caller decides whether to show them or refuse."""
    result = ParsedStatement()
    try:
        text = content.decode(fmt.encoding)
    except (UnicodeDecodeError, LookupError) as err:
        result.errors.append(ParseError(row=1, column=None, message=str(err)))
        return result

    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=fmt.delimiter))
    header = rows[fmt.header_rows - 1] if fmt.header_rows >= 1 and rows else None
    resolver = _Resolver(header)
    _check_columns(resolver, fmt, result)
    if result.errors:
        return result

    seen: dict[tuple[str, str, str, str], int] = {}
    for offset, row in enumerate(rows[fmt.header_rows :]):
        row_no = fmt.header_rows + offset + 1
        if not any(cell.strip() for cell in row):
            continue  # a blank separator row, which every export seems to have somewhere
        line = _parse_row(row, row_no, resolver, fmt, result, seen)
        if line is not None:
            result.lines.append(line)
    return result


def _check_columns(resolver: _Resolver, fmt: StatementFormat, result: ParsedStatement) -> None:
    """Every column the mapping names must exist. Checked once against the header rather than
    per row, so a mapping aimed at the wrong file says so on row 1 instead of producing one
    error per line of a thousand-line export."""
    required = [("date_column", fmt.date_column), ("description_column", fmt.description_column)]
    if fmt.amount_mode == StatementAmountMode.DEBIT_CREDIT:
        required += [("debit_column", fmt.debit_column), ("credit_column", fmt.credit_column)]
    else:
        required.append(("amount_column", fmt.amount_column))
    optional = [
        ("reference_column", fmt.reference_column),
        ("balance_column", fmt.balance_column),
        ("external_id_column", fmt.external_id_column),
        ("booking_date_column", fmt.booking_date_column),
    ]
    for field_name, reference in required + optional:
        if reference is None:
            continue
        if resolver.index(reference) is None:
            result.errors.append(
                ParseError(
                    row=1,
                    column=field_name,
                    message=f"no column {reference!r} in the file",
                )
            )


def _parse_row(
    row: list[str],
    row_no: int,
    resolver: _Resolver,
    fmt: StatementFormat,
    result: ParsedStatement,
    seen: dict[tuple[str, str, str, str], int],
) -> ParsedLine | None:
    before = len(result.errors)

    value_date = _read_date(
        resolver.value(row, fmt.date_column), fmt, row_no, "date_column", result
    )
    booking_date = (
        _read_date(
            resolver.value(row, fmt.booking_date_column),
            fmt,
            row_no,
            "booking_date_column",
            result,
            required=False,
        )
        if fmt.booking_date_column
        else None
    )
    reference = (
        resolver.value(row, fmt.reference_column) or None if fmt.reference_column else None
    )
    description = resolver.value(row, fmt.description_column) or ""
    if not description and fmt.empty_description == StatementEmptyDescription.REFERENCE:
        description = reference or ""
    if not description:
        result.errors.append(
            ParseError(row=row_no, column="description_column", message="empty description")
        )
    amount = _read_amount(row, resolver, fmt, row_no, result)
    balance_after = (
        _read_decimal(
            resolver.value(row, fmt.balance_column), fmt, row_no, "balance_column", result
        )
        if fmt.balance_column
        else None
    )
    external_id = (
        resolver.value(row, fmt.external_id_column) or None if fmt.external_id_column else None
    )

    if len(result.errors) > before or value_date is None or amount is None:
        return None

    key = (
        value_date.isoformat(),
        str(amount),
        normalise(description),
        normalise(reference),
    )
    occurrence = seen.get(key, 0)
    seen[key] = occurrence + 1
    return ParsedLine(
        row=row_no,
        value_date=value_date,
        booking_date=booking_date,
        description=description,
        reference=reference,
        amount=amount,
        balance_after=balance_after,
        external_id=external_id,
        occurrence=occurrence,
    )


def _read_date(
    raw: str | None,
    fmt: StatementFormat,
    row_no: int,
    column: str,
    result: ParsedStatement,
    *,
    required: bool = True,
) -> date | None:
    if not raw:
        if required:
            result.errors.append(ParseError(row=row_no, column=column, message="empty date"))
        return None
    try:
        return datetime.strptime(raw, fmt.date_format).date()
    except ValueError:
        result.errors.append(
            ParseError(
                row=row_no,
                column=column,
                message=f"{raw!r} is not a date in {fmt.date_format}",
            )
        )
        return None


def _read_decimal(
    raw: str | None,
    fmt: StatementFormat,
    row_no: int,
    column: str,
    result: ParsedStatement,
) -> Decimal | None:
    if raw is None or not raw.strip():
        return None
    cleaned = raw.strip()
    # A trailing `CR`/`DR` marker is a bank's way of signing **one** column, and it is dropped
    # here because in `debit_credit` mode the column the value sits in already carries the
    # sign — a `CR` in the credit column says what the column says. `_read_amount` refuses it
    # in `signed` mode, where dropping it really would change the number.
    cleaned = _SIGN_MARKER.sub("", cleaned)
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    if fmt.thousands_separator:
        cleaned = cleaned.replace(fmt.thousands_separator, "")
    if fmt.decimal_separator != ".":
        cleaned = cleaned.replace(fmt.decimal_separator, ".")
    cleaned = _AMOUNT_NOISE.sub("", cleaned)
    if not cleaned or cleaned in {"-", "+"}:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        result.errors.append(
            ParseError(row=row_no, column=column, message=f"{raw!r} is not a number")
        )
        return None


def _read_amount(
    row: list[str],
    resolver: _Resolver,
    fmt: StatementFormat,
    row_no: int,
    result: ParsedStatement,
) -> Decimal | None:
    """Credit positive, whichever way the file spells it.

    A debit/credit pair with **both** columns filled is refused rather than netted: a bank
    does not write both, so a row that has both is a mapping pointed at the wrong columns, and
    netting it would import a plausible wrong number.
    """
    if fmt.amount_mode == StatementAmountMode.SIGNED:
        raw = resolver.value(row, fmt.amount_column or "")
        if raw is None or not raw.strip():
            result.errors.append(
                ParseError(row=row_no, column="amount_column", message="empty amount")
            )
            return None
        if _SIGN_MARKER.search(raw):
            # Refused, not stripped. In a single signed column the marker **is** the sign, and
            # dropping it would turn a withdrawal into a deposit of the same size — a
            # plausible wrong number, which is the one outcome worse than a refusal. The
            # mapping cannot express this layout today; a format that needs it gets a
            # `sign_marker` mode rather than a silent guess here.
            result.errors.append(
                ParseError(
                    row=row_no,
                    column="amount_column",
                    message=f"{raw!r} signs the amount with a CR/DR marker, which this "
                    "mapping cannot read",
                )
            )
            return None
        amount = _read_decimal(raw, fmt, row_no, "amount_column", result)
        if amount is None:
            return None  # `_read_decimal` already recorded why, with this row's number
        if fmt.sign_convention == StatementSignConvention.DEBIT_POSITIVE:
            amount = -amount
        return _reject_zero(amount, row_no, "amount_column", result)

    # `is None` throughout rather than truthiness: an explicit `0` in a column is *filled*,
    # and a file that writes one is saying the bank recorded a zero movement. That is refused
    # as a zero amount below, naming the row — not reported as "no debit and no credit", which
    # would send the reader looking at the mapping rather than at the row. Unless the format
    # says `zero_is_empty`, which is the mapping declaring that this bank's zero is filler.
    debit = _read_decimal(
        resolver.value(row, fmt.debit_column or ""), fmt, row_no, "debit_column", result
    )
    credit = _read_decimal(
        resolver.value(row, fmt.credit_column or ""), fmt, row_no, "credit_column", result
    )
    if fmt.zero_is_empty:
        # The layout's filler, not a movement: `0.00` in the column this row does not use.
        # Zero in **both** falls through to "no debit and no credit" — still a refusal.
        debit = None if debit == 0 else debit
        credit = None if credit == 0 else credit
    if debit is not None and credit is not None:
        result.errors.append(
            ParseError(
                row=row_no,
                column="debit_column",
                message="both the debit and the credit column carry an amount",
            )
        )
        return None
    if debit is None and credit is None:
        result.errors.append(
            ParseError(row=row_no, column="credit_column", message="no debit and no credit")
        )
        return None
    if credit is not None:
        return _reject_zero(credit, row_no, "credit_column", result)
    return _reject_zero(-abs(debit), row_no, "debit_column", result)


def _reject_zero(
    amount: Decimal, row_no: int, column: str, result: ParsedStatement
) -> Decimal | None:
    """A zero-amount statement line is not an event. It is refused here rather than stored,
    because `bank_statement_lines.amount <> 0` is a CHECK and a row that reached the insert
    would fail as an integrity error with no row number in it."""
    if amount == 0:
        result.errors.append(ParseError(row=row_no, column=column, message="zero amount"))
        return None
    return amount
