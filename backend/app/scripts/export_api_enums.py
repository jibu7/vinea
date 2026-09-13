"""Export every enum that crosses the API as TypeScript constants.

The frontend used to compare against string literals typed out by hand, and P5 step 6 found
out what that costs: two screens filtered on `control_type === "INV"` while the enum
serialises `"inventory"`, so the pickers were empty and a correctly-mapped account displayed
as "Not set". A screen that renders perfectly and says something untrue — caught by an e2e,
but only because one existed.

Fixing the instance would have left the class of defect intact. This is the class: **one
generated module, derived from the Python enums, checked for drift by a test.** A literal in a
screen file cannot be wrong about a value it no longer contains.

Run `uv run python -m app.scripts.export_api_enums` to regenerate;
`tests/test_api_enums_export.py` fails if the committed file and these enums disagree, the
same way `alembic check` fails on an un-generated migration.
"""

import enum
import os
from pathlib import Path

from app.models.company import CompanyStatus
from app.models.fiscal import PeriodStatus
from app.models.gl import AccountClass, ControlType
from app.models.inventory import (
    InventoryDocumentStatus,
    InventoryTransactionKind,
    ItemType,
    NegativeStockPolicy,
    StockCountStatus,
    StockTransferStatus,
)
from app.models.job import JobStatus
from app.models.journal import JournalStatus
from app.models.membership import MembershipStatus
from app.models.partner import AgeingBasis, DueBasis, PartnerRole, TaxMode
from app.models.subledger import DocumentKind, DocumentStatus, InstrumentType
from app.models.tax import TaxNature

#: Every enum whose *string values* reach the browser — whether a schema types the field as
#: the enum or as a bare `str`. Ordered as a reader would look for them: shared, then per
#: module. Adding one here and regenerating is the only way a value gets to the frontend.
EXPORTED: tuple[type[enum.StrEnum], ...] = (
    # Shared
    CompanyStatus,
    MembershipStatus,
    PeriodStatus,
    JobStatus,
    TaxNature,
    # General ledger
    AccountClass,
    ControlType,
    JournalStatus,
    # AR/AP
    PartnerRole,
    TaxMode,
    DueBasis,
    AgeingBasis,
    DocumentKind,
    DocumentStatus,
    InstrumentType,
    # Inventory
    ItemType,
    NegativeStockPolicy,
    InventoryTransactionKind,
    InventoryDocumentStatus,
    StockTransferStatus,
    StockCountStatus,
)

# `REPO_ROOT` first, then the path relative to this file — the same convention
# `tests/test_schema_invariants.py` uses, and for the same reason. `parents[3]` is the repo
# root on a host checkout and `/` inside the backend container, where the tree is `/app` and
# the repo is bind-mounted read-only at `/repo`. Without this the drift gate cannot find the
# file it guards under `docker compose exec`, which is how this project runs its backend
# checks, and fails there for a reason that has nothing to do with the enums.
#
# Reading is all the gate needs. Regenerating still has to happen on the host, because that
# mount is read-only — which is what the failure messages tell you to do.
TARGET = (
    Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[3])
    / "frontend"
    / "src"
    / "lib"
    / "api-enums.ts"
)

HEADER = '''/**
 * GENERATED FILE — do not edit by hand.
 *
 * Every enum value that crosses the API, derived from the Python enums in `backend/app/models`
 * by `backend/app/scripts/export_api_enums.py`. Regenerate with:
 *
 *     cd backend && uv run python -m app.scripts.export_api_enums
 *
 * `backend/tests/test_api_enums_export.py` fails if this file and those enums disagree, so a
 * value here cannot drift from the one the server actually sends.
 *
 * Import these instead of writing the string. P5 step 6 shipped two screens comparing
 * `control_type` against `"INV"` when the wire value is `"inventory"`: the pickers were empty
 * and a mapped account read "Not set" — a screen that rendered perfectly and was wrong.
 * `src/lib/api-enums.test.ts` keeps those literals out of screen files.
 *
 * Each export is both a value and a type:
 *
 *     import { ControlType } from "@/lib/api-enums";
 *     account.control_type === ControlType.INVENTORY      // value
 *     function f(t: ControlType) {}                        // type — the union of the values
 */
'''


def render() -> str:
    blocks = [HEADER]
    for enum_type in EXPORTED:
        name = enum_type.__name__
        lines = [f"export const {name} = {{"]
        for member in enum_type:
            lines.append(f'  {member.name}: "{member.value}",')
        lines.append("} as const;")
        lines.append(f"export type {name} = (typeof {name})[keyof typeof {name}];")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(render(), encoding="utf-8")
    print(f"wrote {TARGET} ({len(EXPORTED)} enums)")


if __name__ == "__main__":
    main()
