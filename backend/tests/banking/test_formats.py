"""The statement parser and its column mappings (P8 decision 3).

Over the three committed samples the acceptance tape imports, plus a hand-built awkward file
that carries every departure from the generic layout at once: `DD/MM/YYYY` dates, thousands
separators, a **signed** amount column in `debit_positive`, a blank reference, and two
identical fees on one day. The awkward one is built here rather than committed because it is
not a statement the phase is written to — it exists to make the mapping do work, and a reader
should see what it holds without opening a second file.
"""

from datetime import date
from decimal import Decimal

from app.banking.formats import (
    GENERIC_PRESET,
    StatementFormat,
    StatementFormatError,
    file_sha256,
    line_fingerprint,
    normalise,
    parse,
    validate_format,
)
from app.models.banking import (
    StatementAmountMode,
    StatementSignConvention,
)
from tests.banking.conftest import sample

ACCOUNT = 7  # any id; it only has to be the same one on both sides of a fingerprint

#: Every departure from the generic layout at once. The two `ACCOUNT FEE` rows are the case the
#: occurrence index exists for: same day, same amount, same text, and genuinely two fees.
AWKWARD = (
    b"Value Date;Narrative;Ref;Movement;Running Balance\n"
    b"01/09/2026;OPENING TRANSFER;TRF 900;1 200 000,00;1 200 000,00\n"
    b"12/09/2026;ACCOUNT FEE;;2 500,00;1 197 500,00\n"
    b"12/09/2026;ACCOUNT FEE;;2 500,00;1 195 000,00\n"
    b"15/09/2026;INWARD TRANSFER;;-350 000,00;1 545 000,00\n"
)

AWKWARD_FORMAT = StatementFormat(
    preset="custom",
    delimiter=";",
    header_rows=1,
    date_column="Value Date",
    date_format="%d/%m/%Y",
    description_column="Narrative",
    reference_column="Ref",
    amount_mode=StatementAmountMode.SIGNED,
    amount_column="Movement",
    # The bank writes a withdrawal as a positive movement; a deposit is the negative one.
    sign_convention=StatementSignConvention.DEBIT_POSITIVE,
    balance_column="Running Balance",
    decimal_separator=",",
    thousands_separator=" ",
)


def test_the_generic_preset_reads_the_september_rwf_sample() -> None:
    parsed = parse(sample("generic-bk-rwf-sep.csv"), GENERIC_PRESET, bank_account_id=ACCOUNT)

    assert parsed.errors == []
    assert [(line.value_date, line.amount) for line in parsed.lines] == [
        (date(2026, 9, 3), Decimal("118000")),
        (date(2026, 9, 5), Decimal("59000")),
        # A debit column is money out, so it arrives negative — the ledger's own sign.
        (date(2026, 9, 10), Decimal("-384000")),
        (date(2026, 9, 12), Decimal("-2500")),
        (date(2026, 9, 15), Decimal("260000")),
        (date(2026, 9, 20), Decimal("40000")),
    ]
    assert parsed.lines[0].reference == "DEP 1001"
    assert parsed.lines[1].reference is None
    assert (parsed.from_date, parsed.to_date) == (date(2026, 9, 3), date(2026, 9, 20))


def test_the_opening_balance_is_the_first_line_less_its_own_amount() -> None:
    """1 118 000 is the balance *after* a credit of 118 000, so the statement opened at
    1 000 000 — the figure the reconciliation before it locked at."""
    parsed = parse(sample("generic-bk-rwf-sep.csv"), GENERIC_PRESET, bank_account_id=ACCOUNT)

    assert parsed.derived_balances() == (Decimal("1000000"), Decimal("1090500"))


def test_a_two_decimal_currency_keeps_its_places() -> None:
    parsed = parse(sample("generic-bk-usd-sep.csv"), GENERIC_PRESET, bank_account_id=ACCOUNT)

    assert [line.amount for line in parsed.lines] == [Decimal("500.00"), Decimal("-5.00")]
    assert parsed.derived_balances() == (Decimal("0.00"), Decimal("495.00"))


def test_the_overlap_sample_repeats_one_line_of_september() -> None:
    september = parse(
        sample("generic-bk-rwf-sep.csv"), GENERIC_PRESET, bank_account_id=ACCOUNT
    )
    october = parse(
        sample("generic-bk-rwf-overlap-oct.csv"), GENERIC_PRESET, bank_account_id=ACCOUNT
    )

    held = {_fingerprint(line) for line in september.lines}
    repeated = [line for line in october.lines if _fingerprint(line) in held]

    assert [line.description for line in repeated] == ["CASH DEPOSIT DEP 4471"]
    assert len(october.lines) == 3


def test_the_awkward_file_parses_through_its_mapping() -> None:
    parsed = parse(AWKWARD, AWKWARD_FORMAT, bank_account_id=ACCOUNT)

    assert parsed.errors == []
    assert [(line.value_date, line.amount, line.reference) for line in parsed.lines] == [
        # `debit_positive`: the file's `1 200 000,00` is money *out*.
        (date(2026, 9, 1), Decimal("-1200000.00"), "TRF 900"),
        (date(2026, 9, 12), Decimal("-2500.00"), None),
        (date(2026, 9, 12), Decimal("-2500.00"), None),
        (date(2026, 9, 15), Decimal("350000.00"), None),
    ]


def test_two_identical_fees_on_one_day_are_two_lines() -> None:
    """The occurrence index, which is the whole reason it is in the fingerprint material.

    Without it the second fee hashes to the first, an import stores one line where the bank
    showed two, and the reconciliation is 2 500 short with nothing on the screen to explain it.
    """
    parsed = parse(AWKWARD, AWKWARD_FORMAT, bank_account_id=ACCOUNT)
    fees = [line for line in parsed.lines if line.description == "ACCOUNT FEE"]

    assert [line.occurrence for line in fees] == [0, 1]
    assert _fingerprint(fees[0]) != _fingerprint(fees[1])


def test_the_same_two_fees_re_exported_are_skipped_as_two() -> None:
    """The other half of the same rule: the index is stable across files, so an overlapping
    export recognises both fees rather than importing a third."""
    first = parse(AWKWARD, AWKWARD_FORMAT, bank_account_id=ACCOUNT)
    again = parse(AWKWARD, AWKWARD_FORMAT, bank_account_id=ACCOUNT)

    assert {_fingerprint(line) for line in first.lines} == {
        _fingerprint(line) for line in again.lines
    }


def test_a_wrong_date_format_is_a_row_numbered_error_and_not_an_exception() -> None:
    wrong = GENERIC_PRESET.model_copy(update={"date_format": "%d/%m/%Y"})

    parsed = parse(sample("generic-bk-rwf-sep.csv"), wrong, bank_account_id=ACCOUNT)

    assert parsed.lines == []
    assert len(parsed.errors) == 6
    first = parsed.errors[0]
    assert (first.row, first.column) == (2, "date_column")
    assert "2026-09-03" in first.message


def test_a_mapping_aimed_at_a_missing_column_says_so_once() -> None:
    """Once, against the header — not once per row. A thousand-line export mapped at the wrong
    file should produce one legible error, not a thousand identical ones."""
    wrong = GENERIC_PRESET.model_copy(update={"description_column": "Narrative"})

    parsed = parse(sample("generic-bk-rwf-sep.csv"), wrong, bank_account_id=ACCOUNT)

    assert [(error.row, error.column) for error in parsed.errors] == [(1, "description_column")]


def test_a_row_with_both_a_debit_and_a_credit_is_refused() -> None:
    """A bank writes one or the other. A row with both is a mapping pointed at the wrong pair
    of columns, and netting it would import a plausible wrong number."""
    content = (
        b"Date,Description,Reference,Debit,Credit,Balance\n"
        b"2026-09-03,BOTH,,100,200,900\n"
    )

    parsed = parse(content, GENERIC_PRESET, bank_account_id=ACCOUNT)

    assert parsed.lines == []
    assert parsed.errors[0].row == 2
    assert "debit" in parsed.errors[0].message


def test_a_blank_row_is_skipped_rather_than_reported() -> None:
    content = (
        b"Date,Description,Reference,Debit,Credit,Balance\n"
        b"2026-09-03,A DEPOSIT,,,100,900\n"
        b",,,,,\n"
        b"2026-09-04,ANOTHER,,,100,1000\n"
    )

    parsed = parse(content, GENERIC_PRESET, bank_account_id=ACCOUNT)

    assert parsed.errors == []
    assert len(parsed.lines) == 2


def test_a_zero_amount_row_is_refused_with_its_row_number() -> None:
    content = (
        b"Date,Description,Reference,Debit,Credit,Balance\n"
        b"2026-09-03,NIL MOVEMENT,,0,,900\n"
    )

    parsed = parse(content, GENERIC_PRESET, bank_account_id=ACCOUNT)

    assert parsed.lines == []
    assert (parsed.errors[0].row, parsed.errors[0].message) == (2, "zero amount")


def test_a_headerless_file_is_read_by_column_position() -> None:
    positional = GENERIC_PRESET.model_copy(
        update={
            "header_rows": 0,
            "date_column": "0",
            "description_column": "1",
            "reference_column": None,
            "debit_column": "2",
            "credit_column": "3",
            "balance_column": None,
        }
    )
    content = b"2026-09-03,A DEPOSIT,,118000\n"

    parsed = parse(content, positional, bank_account_id=ACCOUNT)

    assert parsed.errors == []
    assert (parsed.lines[0].description, parsed.lines[0].amount) == ("A DEPOSIT", Decimal("118000"))


# --- The mapping model itself -----------------------------------------------------------------


def test_a_mode_without_its_columns_is_refused_when_the_format_is_saved() -> None:
    """At save time, not at import time: a mapping that cannot work should fail on the screen
    that wrote it, not on the next import somebody runs a month later."""
    try:
        validate_format({"amount_mode": "signed"})
    except StatementFormatError as err:
        assert err.code == "statement_format_invalid"
        assert "amount_column" in err.message
    else:  # pragma: no cover - the assertion above is the test
        raise AssertionError("a signed mapping with no amount column must be refused")


def test_separators_that_agree_are_refused() -> None:
    try:
        validate_format({"decimal_separator": ",", "thousands_separator": ","})
    except StatementFormatError as err:
        assert "separators" in err.message
    else:  # pragma: no cover
        raise AssertionError("one character cannot be both separators")


# --- Normalisation and hashing ----------------------------------------------------------------


def test_normalisation_ignores_punctuation_and_case() -> None:
    assert normalise("inv-1 / c1") == normalise("INV-1  C1") == "INV1C1"
    assert normalise(None) == ""


def test_the_fingerprint_is_over_values_not_their_representations() -> None:
    """`2500` off a parsed CSV and `Decimal("2500.000000")` off `NUMERIC(20,6)` are one amount.
    A hash over their text would call one statement line two and import a duplicate — which is
    precisely what P7 step 3 paid for on the item-registration hash."""
    common = {
        "bank_account_id": ACCOUNT,
        "value_date": date(2026, 9, 12),
        "description": "MONTHLY ACCOUNT FEE",
        "reference": None,
        "occurrence": 0,
    }

    assert line_fingerprint(amount=Decimal("-2500"), **common) == line_fingerprint(
        amount=Decimal("-2500.000000"), **common
    )


def test_the_same_line_on_two_accounts_is_two_fingerprints() -> None:
    common = {
        "value_date": date(2026, 9, 12),
        "amount": Decimal("-2500"),
        "description": "MONTHLY ACCOUNT FEE",
        "reference": None,
        "occurrence": 0,
    }

    assert line_fingerprint(bank_account_id=1, **common) != line_fingerprint(
        bank_account_id=2, **common
    )


def test_the_file_hash_is_over_the_bytes_the_bank_produced() -> None:
    content = sample("generic-bk-rwf-sep.csv")

    assert file_sha256(content) == file_sha256(bytes(content))
    assert file_sha256(content) != file_sha256(content + b"\n")


def _fingerprint(line) -> str:  # noqa: ANN001 - a ParsedLine; typing it adds an import only
    return line_fingerprint(
        bank_account_id=ACCOUNT,
        value_date=line.value_date,
        amount=line.amount,
        description=line.description,
        reference=line.reference,
        occurrence=line.occurrence,
    )


def test_a_cr_dr_marker_in_a_signed_column_is_refused_rather_than_dropped() -> None:
    """In a single signed column the marker **is** the sign. Dropping it would turn a
    withdrawal into a deposit of the same size — a plausible wrong number, which is the one
    outcome worse than a refusal.

    In `debit_credit` mode the same marker is dropped, because there the column the value sits
    in already says which way the money went.
    """
    signed = AWKWARD_FORMAT.model_copy(update={"thousands_separator": None})
    content = (
        b"Value Date;Narrative;Ref;Movement;Running Balance\n"
        b"01/09/2026;OPENING TRANSFER;;1200000,00 CR;1200000,00\n"
    )

    parsed = parse(content, signed, bank_account_id=ACCOUNT)

    assert parsed.lines == []
    assert (parsed.errors[0].row, parsed.errors[0].column) == (2, "amount_column")
    assert "CR/DR" in parsed.errors[0].message


def test_a_cr_marker_in_a_debit_credit_pair_is_dropped() -> None:
    content = (
        b"Date,Description,Reference,Debit,Credit,Balance\n"
        b"2026-09-03,A DEPOSIT,,,118000 CR,1118000\n"
    )

    parsed = parse(content, GENERIC_PRESET, bank_account_id=ACCOUNT)

    assert parsed.errors == []
    assert parsed.lines[0].amount == Decimal("118000")
