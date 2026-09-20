"""Which adapter a device speaks through.

This is the reason nothing outside `app/fiscal/` ever imports `rwanda/`: a caller asks for the
adapter of a device and gets one, and the mapping from a company's fiscal country to an
implementation lives in one dictionary that a reviewer can read in a breath.

`companies.fiscal_country` is the key, not the device: a device belongs to a branch of a
company, and which revenue authority that company answers to is a property of the company. A
second country is a row here and a package beside `rwanda/`.
"""

from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.fiscal.null import NullAdapter
from app.fiscal.protocol import FiscalizationAdapter
from app.models.company import Company

#: ISO-3166-1 alpha-2 → the adapter factory. Imported lazily inside the factory rather than at
#: module scope, so that importing `app.fiscal` does not drag in every country's payload
#: models — and so the boundary test can assert on the import graph without this module being
#: the exception that proves the rule.
_COUNTRY_ADAPTERS: dict[str, str] = {
    "RW": "rwanda",
}


def adapter_for(
    fiscal_country: str | None, *, client: httpx.Client | None = None
) -> FiscalizationAdapter:
    """The adapter for a company's fiscal country.

    An unmapped country gets `NullAdapter` — it records and sends nothing. That is the right
    answer rather than an exception: a Ugandan tenant on this build is not fiscalized, and
    refusing to construct an adapter would turn "this country is not implemented yet" into a
    crash on a screen that has nothing to do with tax.
    """
    package = _COUNTRY_ADAPTERS.get((fiscal_country or "").upper())
    if package is None:
        return NullAdapter()
    if package == "rwanda":
        from app.fiscal.rwanda.adapter import RwandaEbmAdapter

        return RwandaEbmAdapter(client=client)
    raise LookupError(f"no adapter implementation for {package}")


def adapter_for_company(db: Session, company_id: int) -> FiscalizationAdapter:
    """A company's own adapter, with **no transport**.

    The form every reader of a stored payload needs: `daily.py` computes an X or a Z out of
    `fiscal_receipts.request`, and `enquiries.py` sums the same declarations for the receipts
    listing. Both are pure translation of rows already in the database, and an adapter holding
    an `httpx.Client` would let a report that nobody asked to talk to Kigali fail when the line
    is down.

    Here rather than copied into each, because two spellings of "which adapter is this
    company's" is how one of them comes to answer differently after a country is added.
    """
    company = db.get(Company, company_id)
    return adapter_for(company.fiscal_country if company is not None else None)


def implemented_countries() -> tuple[str, ...]:
    """What this build can fiscalize. Read by the device screen so it can say what it offers
    rather than offering a country nothing implements."""
    return tuple(sorted(_COUNTRY_ADAPTERS))


def describe(adapter: Any) -> str:
    return type(adapter).__name__
