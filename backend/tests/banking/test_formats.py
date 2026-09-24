"""The statement parser and its column mappings (P8 decision 3).

Over the three committed samples the acceptance tape imports, plus a hand-built awkward file
that carries every departure from the generic layout at once: `DD/MM/YYYY` dates, thousands
separators, a **signed** amount column in `debit_positive`, a blank reference, and two
identical fees on one day. The awkward one is built here rather than committed because it is
not a statement the phase is written to — it exists to make the mapping do work, and a reader
should see what it holds without opening a second file.
"""

import json
from datetime import date
from decimal import Decimal

import pytest

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
    StatementEmptyAmount,
    StatementEmptyDescription,
    StatementSignConvention,
)
from tests.banking.conftest import REAL_SAMPLES, sample

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


def test_the_generic_preset_refuses_an_empty_description_and_reads_a_zero_as_filled() -> None:
    """The two options exist for real exports; the preset the committed samples are in keeps
    the behaviour every test above was written to."""
    assert GENERIC_PRESET.empty_description == StatementEmptyDescription.REFUSE
    assert GENERIC_PRESET.zero_is_empty is False


def test_empty_description_reference_still_refuses_a_row_with_neither() -> None:
    fmt = GENERIC_PRESET.model_copy(
        update={"empty_description": StatementEmptyDescription.REFERENCE}
    )
    content = (
        b"Date,Description,Reference,Debit,Credit,Balance\n"
        b"2026-09-12,,CHG 1,20,,999980\n"
        b"2026-09-12,,,20,,999960\n"
    )

    parsed = parse(content, fmt, bank_account_id=ACCOUNT)

    assert [(line.row, line.description) for line in parsed.lines] == [(2, "CHG 1")]
    assert [(e.row, e.column, e.message) for e in parsed.errors] == [
        (3, "description_column", "empty description")
    ]


def test_zero_is_empty_reads_a_filler_zero_as_empty_but_not_both() -> None:
    fmt = GENERIC_PRESET.model_copy(update={"zero_is_empty": True})
    content = (
        b"Date,Description,Reference,Debit,Credit,Balance\n"
        b"2026-09-12,A FEE,,2500,0.00,997500\n"
        b"2026-09-15,A DEPOSIT,,0,40000,1037500\n"
        b"2026-09-16,NOTHING,,0.00,0,1037500\n"
    )

    parsed = parse(content, fmt, bank_account_id=ACCOUNT)

    assert [line.amount for line in parsed.lines] == [Decimal("-2500"), Decimal("40000")]
    assert [(e.row, e.message) for e in parsed.errors] == [(4, "no debit and no credit")]


# --- The owner's real exports (`docs/banking/samples/`, precondition (d)) -------------------
#
# Five statements from two Rwandan banks in three layouts, each read through the mapping
# committed beside it. The figures are the README's table; every one is the bank's.

#: file → (mapping, lines, opening, closing). `None` balances: the BK export has no balance
#: column, and its opening and closing are keyed at import.
REAL_EXPORTS = {
    "bpr-2025-05.csv": ("bpr", 32, Decimal("1110776.00"), Decimal("2408456.00")),
    "bpr-2025-06.csv": ("bpr", 45, Decimal("2408456.00"), Decimal("4274862.00")),
    "bpr-2026-07.csv": ("bpr", 2, Decimal("230032.00"), Decimal("30012.00")),
    "bpr-2022-09.csv": ("bpr-2022", 21, Decimal("-181940953.28"), Decimal("238769800.00")),
    "bk-2019-10.csv": ("bk", 249, None, None),
    # 282 rows: 281 lines and the `BALANCE B/FWD` its mapping skips for having no amount.
    "kcb-2023-12.csv": ("kcb", 281, Decimal("0.00"), Decimal("4867.00")),
}


def _real_format(mapping: str, **override: object) -> StatementFormat:
    raw = json.loads((REAL_SAMPLES / f"{mapping}.format.json").read_text())
    return validate_format({**raw, **override})


def _real(name: str, **override: object):  # noqa: ANN202 - a ParsedStatement
    mapping = REAL_EXPORTS[name][0]
    return parse(
        (REAL_SAMPLES / name).read_bytes(),
        _real_format(mapping, **override),
        bank_account_id=ACCOUNT,
    )


@pytest.mark.parametrize("mapping", ["bpr", "bpr-2022", "bk", "kcb"])
def test_each_committed_mapping_loads_through_validate_format(mapping: str) -> None:
    """Through the save path, and every key the file sets survives it unchanged — a field the
    model did not know would be dropped silently, and the mapping would read differently."""
    raw = json.loads((REAL_SAMPLES / f"{mapping}.format.json").read_text())

    dumped = validate_format(raw).model_dump(mode="json")

    assert {key: dumped.get(key) for key in raw} == raw


@pytest.mark.parametrize("name", list(REAL_EXPORTS))
def test_each_real_export_parses_through_its_mapping_with_no_errors(name: str) -> None:
    _, lines, opening, closing = REAL_EXPORTS[name]

    parsed = _real(name)

    assert parsed.errors == []
    assert len(parsed.lines) == lines
    assert parsed.derived_balances() == (opening, closing)


@pytest.mark.parametrize("name", [name for name, row in REAL_EXPORTS.items() if row[2] is not None])
def test_each_real_export_with_a_balance_column_ties_opening_to_closing(name: str) -> None:
    """Row by row, not just end to end: every line's balance is the one before it plus its own
    amount, so a sign read the wrong way on any line — a negative debit, a filler zero — shows
    here on the row that did it."""
    _, _, opening, closing = REAL_EXPORTS[name]

    parsed = _real(name)

    running = opening
    for line in parsed.lines:
        running += line.amount
        assert line.balance_after == running, f"row {line.row}"
    assert running == closing


def test_the_bk_export_sums_to_its_keyed_movement() -> None:
    """No balance column, so the tie is the README's: 234 debits, 15 credits, net +1,059 —
    the keyed closing 2,659 less the keyed opening 1,600."""
    parsed = _real("bk-2019-10.csv")

    assert sum(1 for line in parsed.lines if line.amount < 0) == 234
    assert sum(1 for line in parsed.lines if line.amount > 0) == 15
    assert sum(line.amount for line in parsed.lines) == Decimal("1059")


def test_a_reused_bpr_reference_is_two_lines_with_two_fingerprints() -> None:
    """Rows 28–29 of June 2025: one `FT…` reference, one day, one description, RWF 200 and
    20,000. The reason `external_id_column` is not mapped for BPR — the amount is what tells
    them apart, and the fingerprint carries it."""
    parsed = _real("bpr-2025-06.csv")
    pair = [line for line in parsed.lines if line.row in (28, 29)]

    assert [line.reference for line in pair] == [pair[0].reference] * 2
    assert pair[0].reference.startswith("FT")
    assert [line.amount for line in pair] == [Decimal("-200.00"), Decimal("-20000.00")]
    assert _fingerprint(pair[0]) != _fingerprint(pair[1])


def test_four_identical_sme_fees_carry_occurrence_indexes_0_to_3() -> None:
    parsed = _real("bpr-2022-09.csv")
    fees = [line for line in parsed.lines if line.description == "SME MANAGEMENT FEE"]

    assert [line.occurrence for line in fees] == [0, 1, 2, 3]
    assert len({_fingerprint(line) for line in fees}) == 4


@pytest.mark.parametrize(
    ("name", "errors"),
    [("bpr-2025-05.csv", 16), ("bpr-2025-06.csv", 22), ("bpr-2026-07.csv", 1)],
)
def test_bpr_2025_without_empty_description_reference_refuses_every_fee_line(
    name: str, errors: int
) -> None:
    """The sensitivity half: the parser as it stood read these files as 16, 22 and 1 errors —
    one per `CHG…` fee line — and so imported nothing."""
    parsed = _real(name, empty_description="refuse")

    assert len(parsed.errors) == errors
    assert {(e.column, e.message) for e in parsed.errors} == {
        ("description_column", "empty description")
    }


def test_bpr_2022_without_zero_is_empty_refuses_every_row() -> None:
    parsed = _real("bpr-2022-09.csv", zero_is_empty=False)

    assert parsed.lines == []
    assert len(parsed.errors) == 21
    assert {e.message for e in parsed.errors} == {
        "both the debit and the credit column carry an amount"
    }


def test_the_generic_preset_refuses_a_row_with_no_amount() -> None:
    assert GENERIC_PRESET.empty_amount == StatementEmptyAmount.REFUSE


def test_empty_amount_skip_counts_the_row_and_refuses_what_it_cannot_read() -> None:
    """Skipped means *empty*. A cell holding something unreadable is still refused with its
    row number — skipping it would drop a movement the bank recorded."""
    fmt = GENERIC_PRESET.model_copy(update={"empty_amount": StatementEmptyAmount.SKIP})
    content = (
        b"Date,Description,Reference,Debit,Credit,Balance\n"
        b"2026-09-01,BALANCE B/FWD,,,,1000000\n"
        b"2026-09-03,A DEPOSIT,,,118000,1118000\n"
        b"2026-09-04,SMUDGED,,1.2.3,,1118000\n"
    )

    parsed = parse(content, fmt, bank_account_id=ACCOUNT)

    assert [line.amount for line in parsed.lines] == [Decimal("118000")]
    assert parsed.skipped_no_amount == 1
    assert {e.row for e in parsed.errors} == {4}
    assert (parsed.errors[0].column, parsed.errors[0].message) == (
        "debit_column",
        "'1.2.3' is not a number",
    )


def test_empty_amount_skip_refuses_a_cell_that_strips_to_nothing() -> None:
    """`abc` strips to nothing under `_AMOUNT_NOISE`, and before this was read as an empty
    cell — so `skip` dropped the row with only a count to show."""
    fmt = GENERIC_PRESET.model_copy(update={"empty_amount": StatementEmptyAmount.SKIP})
    content = (
        b"Date,Description,Reference,Debit,Credit,Balance\n"
        b"2026-09-03,A DEPOSIT,,,118000,1118000\n"
        b"2026-09-04,SMUDGED,,abc,,1118000\n"
    )

    parsed = parse(content, fmt, bank_account_id=ACCOUNT)

    assert [line.amount for line in parsed.lines] == [Decimal("118000")]
    assert parsed.skipped_no_amount == 0
    assert {e.row for e in parsed.errors} == {3}
    assert (parsed.errors[0].column, parsed.errors[0].message) == (
        "debit_column",
        "'abc' is not a number",
    )


@pytest.mark.parametrize("marker", ["-", "\u2013", "\u2014"])
def test_empty_amount_skip_reads_a_dash_in_both_columns_as_empty(marker: str) -> None:
    """A dash alone — hyphen, en dash or em dash — is a bank's blank, not a number it failed
    to write."""
    fmt = GENERIC_PRESET.model_copy(update={"empty_amount": StatementEmptyAmount.SKIP})
    content = (
        "Date,Description,Reference,Debit,Credit,Balance\n"
        f"2026-09-01,BALANCE B/FWD,,{marker},{marker},1000000\n"
        f"2026-09-03,A DEPOSIT,,{marker},118000,1118000\n"
    ).encode()

    parsed = parse(content, fmt, bank_account_id=ACCOUNT)

    assert (len(parsed.lines), parsed.skipped_no_amount, parsed.errors) == (1, 1, [])
    assert parsed.lines[0].amount == Decimal("118000")


@pytest.mark.parametrize("cell", ["abc", "RWF", "+", "RWF -"])
def test_an_unreadable_debit_beside_a_credit_is_refused_not_read_on_the_credit(
    cell: str,
) -> None:
    """The default `refuse`, and the gap from before #76: the debit read as empty and the row
    went in as a credit — a plausible number from a row the bank wrote two things on."""
    content = (
        "Date,Description,Reference,Debit,Credit,Balance\n"
        f"2026-09-03,SMUDGED,,{cell},118000,1118000\n"
    ).encode()

    parsed = parse(content, GENERIC_PRESET, bank_account_id=ACCOUNT)

    assert parsed.lines == []
    assert [(e.row, e.column, e.message) for e in parsed.errors] == [
        (2, "debit_column", f"{cell!r} is not a number")
    ]


def test_a_signed_column_refuses_an_unreadable_cell_and_calls_a_dash_empty() -> None:
    """In `signed` mode the same two cells used to vanish: `_read_decimal` returned None with
    no error, `_read_amount` trusted it had recorded one, and the row was dropped unreported
    even under `refuse`."""
    content = (
        b"Value Date;Narrative;Ref;Movement;Running Balance\n"
        b"12/09/2026;SMUDGED;;abc;997 500,00\n"
        b"13/09/2026;DASHED;;-;997 500,00\n"
    )

    parsed = parse(content, AWKWARD_FORMAT, bank_account_id=ACCOUNT)

    assert parsed.lines == []
    assert [(e.row, e.message) for e in parsed.errors] == [
        (2, "'abc' is not a number"),
        (3, "empty amount"),
    ]


def test_empty_amount_skip_reads_filler_zeros_in_both_columns_as_empty() -> None:
    """With `zero_is_empty`, zeros in both columns are the same empty row; without it they are
    a filled row, refused as carrying both — `empty_amount` does not reinterpret a zero."""
    content = (
        b"Date,Description,Reference,Debit,Credit,Balance\n"
        b"2026-09-01,BALANCE B/FWD,,0.00,0.00,1000000\n"
        b"2026-09-03,A DEPOSIT,,0.00,118000,1118000\n"
    )
    skip = GENERIC_PRESET.model_copy(
        update={"empty_amount": StatementEmptyAmount.SKIP, "zero_is_empty": True}
    )

    parsed = parse(content, skip, bank_account_id=ACCOUNT)

    assert (len(parsed.lines), parsed.skipped_no_amount, parsed.errors) == (1, 1, [])
    literal = parse(
        content,
        skip.model_copy(update={"zero_is_empty": False}),
        bank_account_id=ACCOUNT,
    )
    assert literal.skipped_no_amount == 0
    assert {e.row for e in literal.errors} == {2, 3}


def test_empty_amount_skip_applies_to_an_empty_signed_column() -> None:
    fmt = AWKWARD_FORMAT.model_copy(update={"empty_amount": StatementEmptyAmount.SKIP})
    content = (
        b"Value Date;Narrative;Ref;Movement;Running Balance\n"
        b"01/09/2026;BALANCE B/FWD;;;1 000 000,00\n"
        b"12/09/2026;ACCOUNT FEE;;2 500,00;997 500,00\n"
    )

    parsed = parse(content, fmt, bank_account_id=ACCOUNT)

    assert (len(parsed.lines), parsed.skipped_no_amount, parsed.errors) == (1, 1, [])
    assert parsed.derived_balances() == (Decimal("1000000.00"), Decimal("997500.00"))


def test_the_kcb_export_skips_its_brought_forward_row_and_derives_the_same_opening() -> None:
    """KCB 2023: 282 rows — the `BALANCE B/FWD` (0.00, no amount) and 281 lines, 241 out and
    40 in. With the row skipped the opening is the first line's balance less its own amount,
    1,563,147.00 − 1,563,147.00 = 0.00: the figure the B/FWD row printed."""
    parsed = _real("kcb-2023-12.csv")

    assert (len(parsed.lines), len(parsed.errors), parsed.skipped_no_amount) == (281, 0, 1)
    assert parsed.lines[0].row == 3  # row 2 of the file is the B/FWD
    assert parsed.derived_balances()[0] == Decimal("0.00")
    assert sum(line.amount for line in parsed.lines) == Decimal("4867.00")
    assert sum(1 for line in parsed.lines if line.amount < 0) == 241
    assert sum(1 for line in parsed.lines if line.amount > 0) == 40


def test_kcb_without_empty_amount_skip_refuses_the_brought_forward_row() -> None:
    """The sensitivity half: one error, on row 2, and one error refuses the whole file."""
    parsed = _real("kcb-2023-12.csv", empty_amount="refuse")

    assert parsed.skipped_no_amount == 0
    assert [(e.row, e.message) for e in parsed.errors] == [(2, "no debit and no credit")]
