"""The two route profiles (decision 1), in one table.

`vsdc` is the v1.0.5 contract a locally hosted VSDC exposes; `osdc` is the v1.0.1 one spoken
directly to the EBM 2.1 API server by a system that cannot host the VSDC. They are **the same
vocabulary at different paths**, plus one difference in the request: the `osdc` profile carries
a `cmcKey` on every call.

One table rather than two adapters because the difference is exactly this much. Two classes
would duplicate every payload, every retry decision and every mapping in order to express a
path map and one header-shaped field — and would let them drift. Which profile RRA certifies
for a cloud vendor is the owner's conversation with the authority; the code does not decide it,
it is a column on the device.

A profile missing an operation is an explicit `None`, not a missing key:
`/branches/saveBrancheCustomers` has no OSDC equivalent in the pinned documents, and an
adapter asking for it gets a refusal that says so rather than a `KeyError` three frames down.
"""

import enum
from dataclasses import dataclass

from app.models.fiscalization import FiscalProfile


class Operation(enum.StrEnum):
    """Every call this phase makes. Named for what it does, not for either profile's path."""

    INITIALIZE = "initialize"
    SELECT_CODES = "select_codes"
    SELECT_ITEM_CLASSES = "select_item_classes"
    SELECT_CUSTOMER = "select_customer"
    SELECT_BRANCHES = "select_branches"
    SELECT_NOTICES = "select_notices"
    SAVE_BRANCH_CUSTOMER = "save_branch_customer"
    SAVE_ITEM = "save_item"
    SELECT_ITEMS = "select_items"
    SELECT_IMPORT_ITEMS = "select_import_items"
    UPDATE_IMPORT_ITEMS = "update_import_items"
    SAVE_SALES = "save_sales"
    SELECT_PURCHASES = "select_purchases"
    SAVE_PURCHASES = "save_purchases"
    SAVE_STOCK_ITEMS = "save_stock_items"
    SELECT_STOCK_ITEMS = "select_stock_items"
    SAVE_STOCK_MASTER = "save_stock_master"


#: operation → (vsdc path, osdc path). `None` means the profile has no such call.
ROUTES: dict[Operation, tuple[str, str | None]] = {
    Operation.INITIALIZE: ("/initializer/selectInitInfo", "/selectInitOsdcInfo"),
    Operation.SELECT_CODES: ("/code/selectCodes", "/selectCodeList"),
    Operation.SELECT_ITEM_CLASSES: ("/itemClass/selectItemsClass", "/selectItemClsList"),
    Operation.SELECT_CUSTOMER: ("/customers/selectCustomer", "/selectCustomer"),
    Operation.SELECT_BRANCHES: ("/branches/selectBranches", "/selectBhfList"),
    Operation.SELECT_NOTICES: ("/notices/selectNotices", "/selectNoticeList"),
    # No OSDC path in the pinned documents. Not used by this phase either — branch customers
    # are a pharmacy/insurance feature — and kept in the table so its absence is recorded
    # rather than looking like an oversight.
    Operation.SAVE_BRANCH_CUSTOMER: ("/branches/saveBrancheCustomers", None),
    Operation.SAVE_ITEM: ("/items/saveItems", "/saveItem"),
    Operation.SELECT_ITEMS: ("/items/selectItems", "/selectItemList"),
    Operation.SELECT_IMPORT_ITEMS: ("/imports/selectImportItems", "/selectImportItemList"),
    Operation.UPDATE_IMPORT_ITEMS: ("/imports/updateImportItems", "/updateImportItem"),
    Operation.SAVE_SALES: ("/trnsSales/saveSales", "/saveTrnsSalesOsdc"),
    Operation.SELECT_PURCHASES: (
        "/trnsPurchase/selectTrnsPurchaseSales",
        "/selectTrnsPurchaseSalesList",
    ),
    Operation.SAVE_PURCHASES: ("/trnsPurchase/savePurchases", "/insertTrnsPurchase"),
    Operation.SAVE_STOCK_ITEMS: ("/stock/saveStockItems", "/insertStockIO"),
    Operation.SELECT_STOCK_ITEMS: ("/stock/selectStockItems", "/selectStockMoveList"),
    Operation.SAVE_STOCK_MASTER: ("/stockMaster/saveStockMaster", "/saveStockMaster"),
}

#: The published servers. A device stores its own `base_url`, so these are what the register
#: screen offers rather than what the adapter uses — a test device pointed at the in-repo
#: sandbox is the ordinary case in development and in e2e.
TEST_SERVER = "https://sdcsandbox.rra.gov.rw"
PRODUCTION_SERVER = "https://api-ebm.rra.gov.rw"


class UnsupportedOperation(Exception):
    """This profile has no path for that call."""


@dataclass(frozen=True)
class RouteTable:
    """The paths one device speaks, and whether its requests carry the CMC key."""

    profile: FiscalProfile

    @property
    def carries_cmc_key(self) -> bool:
        """`osdc` talks to the EBM API server directly and authenticates each request with the
        device's CMC key; `vsdc` talks to a local VSDC that already holds it."""
        return self.profile == FiscalProfile.OSDC

    def path(self, operation: Operation) -> str:
        vsdc_path, osdc_path = ROUTES[operation]
        path = osdc_path if self.profile == FiscalProfile.OSDC else vsdc_path
        if path is None:
            raise UnsupportedOperation(
                f"the {self.profile} profile has no path for {operation}. If RRA has published "
                "one since v1.0.1, add it to ROUTES in app/fiscal/rwanda/routes.py — the "
                "table is the only place a path is written."
            )
        return path


def routes_for(profile: FiscalProfile) -> RouteTable:
    return RouteTable(profile=profile)
