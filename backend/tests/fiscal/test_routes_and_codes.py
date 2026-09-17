"""The two route profiles, the §4 code tables, and the item-code format.

These are the parts of the Rwanda adapter that are *tables* rather than behaviour, and a table
is exactly the thing that rots quietly: a path typed from the wrong document, a rate that drifts
from what the receipt has to print, a padding rule somebody guessed at. Each one gets an
assertion that says what it is for.
"""

import pytest

from app.fiscal.rwanda import codes
from app.fiscal.rwanda.routes import (
    ROUTES,
    Operation,
    UnsupportedOperation,
    routes_for,
)
from app.models.fiscalization import FiscalProfile


def test_every_operation_has_a_vsdc_path() -> None:
    """The `vsdc` profile is complete: it is the contract the build is written to, and a
    missing path there is an operation nothing can perform."""
    missing = [operation for operation in Operation if ROUTES[operation][0] is None]

    assert missing == []


def test_every_operation_is_in_the_table() -> None:
    """The forcing function: an `Operation` added without a path fails here rather than
    raising `KeyError` inside an adapter call."""
    assert sorted(ROUTES) == sorted(Operation)


def test_the_two_profiles_use_different_paths_for_the_same_operation() -> None:
    """Which is the whole reason the table exists. If these ever coincide the profile column
    is decoration."""
    vsdc = routes_for(FiscalProfile.VSDC)
    osdc = routes_for(FiscalProfile.OSDC)

    assert vsdc.path(Operation.SAVE_SALES) == "/trnsSales/saveSales"
    assert osdc.path(Operation.SAVE_SALES) == "/saveTrnsSalesOsdc"
    assert vsdc.path(Operation.INITIALIZE) != osdc.path(Operation.INITIALIZE)


def test_only_the_osdc_profile_carries_the_cmc_key() -> None:
    """`osdc` talks to the EBM API server directly and authenticates each request with the
    device's CMC key; `vsdc` talks to a local VSDC that already holds it."""
    assert routes_for(FiscalProfile.OSDC).carries_cmc_key is True
    assert routes_for(FiscalProfile.VSDC).carries_cmc_key is False


def test_a_profile_without_a_path_refuses_and_says_where_to_fix_it() -> None:
    """`/branches/saveBrancheCustomers` has no OSDC equivalent in the pinned documents. The
    refusal names the table, because the fix is a row in it and not a workaround."""
    with pytest.raises(UnsupportedOperation) as error:
        routes_for(FiscalProfile.OSDC).path(Operation.SAVE_BRANCH_CUSTOMER)

    assert "ROUTES" in str(error.value)


# --- Code tables --------------------------------------------------------------------------


def test_the_programmed_rates_cover_every_tax_class() -> None:
    """CIS §7.22–7.23 requires every programmed rate above zero to print on every receipt —
    including a receipt with no line at that rate. A class with no rate here is a receipt that
    cannot be printed correctly."""
    assert set(codes.PROGRAMMED_RATES) == {member.value for member in codes.TaxType}
    assert codes.PROGRAMMED_RATES[codes.TaxType.STANDARD] == "18.00"


def test_only_the_temporary_code_is_retried() -> None:
    """The retry policy, stated as a test.

    A `9xx` refusal RRA will make again costs nothing to retry except a log line and a delay
    before somebody looks at it; the reason this matters is the opposite case — a duplicate
    (`994`) returns no receipt data, so a row retried into one is a registered sale with
    nothing to print.
    """
    assert codes.is_retryable(codes.RESULT_TEMPORARY) is True
    for refusal in (
        codes.RESULT_PURCHASE_CODE_REQUIRED,
        codes.RESULT_PURCHASE_CODE_INVALID,
        codes.RESULT_PURCHASE_CODE_USED,
        codes.RESULT_UNKNOWN_TIN,
        codes.RESULT_DUPLICATE,
    ):
        assert codes.is_retryable(refusal) is False, refusal


def test_ok_is_not_retryable_either() -> None:
    """Belt and braces: `000` reaching the retry decision at all would be a bug, and a policy
    that said "retry a success" would hide it."""
    assert codes.is_retryable(codes.RESULT_OK) is False


# --- §4.17, the item code -----------------------------------------------------------------


def test_the_item_code_is_built_to_the_documents_sample() -> None:
    """`RW1NTXU0000006` — origin `RW`, product type `1`, packaging `NT`, quantity unit `U`
    padded to two characters with `X`, sequence `0000006`."""
    built = codes.build_item_code(
        origin_country="RW",
        product_type=codes.ProductType.RAW_MATERIAL,
        packaging_unit="NT",
        quantity_unit="U",
        sequence_no=6,
    )

    assert built == "RW1NTXU0000006"


def test_a_two_character_quantity_unit_is_not_padded() -> None:
    assert codes.build_item_code(
        origin_country="rw",
        product_type=codes.ProductType.FINISHED_PRODUCT,
        packaging_unit="bx",
        quantity_unit="kg",
        sequence_no=42,
    ) == "RW2BXKG0000042"


#: Every item code anyone has published, and the reason there is a table rather than an
#: assertion: the rule is not written down in prose anywhere, so the examples *are* the
#: specification and a rule is only as good as the examples it reproduces.
#:
#: The first two were all this test had, and a rule that fit them both — "left-pad the quantity
#: unit to two characters with `X`" — got the other three wrong. §4.6 publishes three-character
#: codes, and all three three-character codes below carry an `X` the padding reading would not
#: have produced.
PUBLISHED_ITEM_CODES = (
    ("RW", "2", "NT", "BA", 12, "RW2NTBA0000012", "§4.17's worked example; BA needs no marker"),
    ("RW", "1", "NT", "U", 6, "RW1NTXU0000006", "same document; qtyUnitCd 'U' in the body"),
    ("KR", "2", "AM", "BLL", 1, "KR2AMXBLL0000001", "same document; BLL keeps its X"),
    ("RW", "2", "NT", "U", 2, "RW2NTXU0000002", "live receipt, invoice 1 (docs/rra/)"),
    ("RW", "2", "NT", "NOX", 14, "RW2NTXNOX0000014", "live receipt, invoice 22 (docs/rra/)"),
)


@pytest.mark.parametrize(
    ("origin", "product_type", "packaging", "quantity_unit", "sequence_no", "expected", "source"),
    PUBLISHED_ITEM_CODES,
    ids=[case[5] for case in PUBLISHED_ITEM_CODES],
)
def test_every_published_item_code_is_reproduced(
    origin: str,
    product_type: str,
    packaging: str,
    quantity_unit: str,
    sequence_no: int,
    expected: str,
    source: str,
) -> None:
    built = codes.build_item_code(
        origin_country=origin,
        product_type=product_type,
        packaging_unit=packaging,
        quantity_unit=quantity_unit,
        sequence_no=sequence_no,
    )

    assert built == expected, f"{source}: built {built}"


def test_the_sequence_is_always_the_last_seven_digits() -> None:
    """The length is **not** fixed — a three-character quantity unit makes a sixteen-character
    code, which is why the old "always fourteen" assertion here was hiding a bug rather than
    catching one. What is fixed is the tail: a revenue authority keys on the sequence, and a
    code whose sequence width moved with the number could not be keyed on.
    """
    for sequence_no in (1, 999, 1_000_000, 9_999_999):
        for quantity_unit, width in (("U", 14), ("KG", 14), ("BLL", 16)):
            built = codes.build_item_code(
                origin_country="RW",
                product_type=codes.ProductType.SERVICE,
                packaging_unit="NT",
                quantity_unit=quantity_unit,
                sequence_no=sequence_no,
            )
            assert built.endswith(f"{sequence_no:07d}")
            assert len(built) == width, built
            assert len(built) <= 20, "itemCd is CHAR(20)"


def test_the_refund_reasons_are_the_published_range() -> None:
    """01–13. A reason is required on every credit note and deliberately not defaulted — "why
    was this refunded" is a question only the person issuing it can answer."""
    assert [member.value for member in codes.RefundReason] == [
        f"{number:02d}" for number in range(1, 14)
    ]
