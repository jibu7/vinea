"""Fixtures for the tax suite.

The VAT return is not a fiscal feature — a company that never fiscalizes still files one, and
`app/tax/` imports nothing from `app/fiscal/`. But the tenant the fiscal suite builds is the one
with a month of realistic postings behind it, four tax classes on its items and a supplier with
a TIN, so the return is exercised against the data it will actually meet rather than against a
tenant invented for it. The fixtures are re-exported the way `tests/inventory` re-exports
`ledger`, which is this build's idiom for sharing one.
"""

from tests.fiscal.conftest import fiscal_posting as fiscal_posting  # noqa: PLC0414
from tests.fiscal.conftest import sandbox_client as sandbox_client  # noqa: PLC0414
from tests.fiscal.conftest import sandbox_state as sandbox_state  # noqa: PLC0414
