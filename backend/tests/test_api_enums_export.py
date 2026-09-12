"""The generated frontend enum module must match the Python enums it came from.

`app/scripts/export_api_enums.py` writes `frontend/src/lib/api-enums.ts`. This is the gate
that keeps the committed file honest — the same shape as `alembic check`: regenerate, compare,
fail with the command that fixes it.

Without this the generator is a convenience someone ran once. With it, a value cannot change on
the server and go on being wrong in the browser, which is the defect P5 step 6 shipped and the
e2e caught: two screens comparing `control_type` against `"INV"` when the wire value is
`"inventory"`.
"""

import enum

import pytest

from app.scripts.export_api_enums import EXPORTED, TARGET, render


def test_the_committed_typescript_matches_the_python_enums() -> None:
    assert TARGET.exists(), (
        f"{TARGET} is missing; run `uv run python -m app.scripts.export_api_enums`"
    )
    assert TARGET.read_text(encoding="utf-8") == render(), (
        "frontend/src/lib/api-enums.ts is out of date with the Python enums. Regenerate it:\n"
        "  cd backend && uv run python -m app.scripts.export_api_enums"
    )


def test_every_exported_enum_is_a_str_enum() -> None:
    """A non-string enum would render its *name* where the API sends something else — the
    generator would happily emit a lie, so the shape is checked rather than assumed."""
    for enum_type in EXPORTED:
        assert issubclass(enum_type, enum.StrEnum), f"{enum_type.__name__} is not a StrEnum"
        for member in enum_type:
            assert isinstance(member.value, str)


def test_the_export_list_has_no_duplicates() -> None:
    names = [enum_type.__name__ for enum_type in EXPORTED]
    assert len(names) == len(set(names)), "an enum is exported twice; the last one would win"


@pytest.mark.parametrize(
    ("enum_name", "member", "value"),
    [
        # The values screens actually compare against, pinned here as well as generated. If a
        # rename ever changes one of these on purpose, it should cost a deliberate edit in two
        # places rather than sliding through as a regenerated file nobody reads.
        ("ControlType", "INVENTORY", "inventory"),
        ("ControlType", "AR", "ar"),
        ("ControlType", "AP", "ap"),
        ("ItemType", "STOCK", "stock"),
        ("NegativeStockPolicy", "BLOCK", "block"),
        ("NegativeStockPolicy", "ALLOW", "allow"),
        ("InventoryTransactionKind", "ADJUSTMENT_IN", "adjustment_in"),
        ("InventoryTransactionKind", "COUNT_VARIANCE", "count_variance"),
        ("InventoryTransactionKind", "OPENING_BALANCE", "opening_balance"),
    ],
)
def test_the_values_the_screens_depend_on(enum_name: str, member: str, value: str) -> None:
    by_name = {enum_type.__name__: enum_type for enum_type in EXPORTED}
    assert by_name[enum_name][member].value == value
