"""Permission constants — ported from the v4 lineage (Master Plan §5 P1, Appendix A.2).

Format is `module:action`. Modules that do not exist yet (inventory, order entry, BOM,
POS, reporting) keep their constants here so roles seeded in P1 stay stable as later
phases land; the endpoints that consume them arrive with their phase.
"""

from collections.abc import Callable, Iterable

from fastapi import Depends

# Administration
USERS_CREATE = "users:create"
USERS_READ = "users:read"
USERS_UPDATE = "users:update"
USERS_DELETE = "users:delete"
USERS_MANAGE_ROLES = "users:manage_roles"

ROLES_CREATE = "roles:create"
ROLES_READ = "roles:read"
ROLES_UPDATE = "roles:update"
ROLES_DELETE = "roles:delete"
ROLES_MANAGE_PERMISSIONS = "roles:manage_permissions"

COMPANY_READ = "company:read"
COMPANY_UPDATE = "company:update"
ACCOUNTING_PERIODS_MANAGE = "accounting_periods:manage"
# Reopening a closed period / fiscal year is audited and deliberately narrower (ADR-08).
ACCOUNTING_PERIODS_REOPEN = "accounting_periods:reopen"

# Common / maintenance
COMMON_SETUP_CURRENCIES = "common:setup_currencies"
COMMON_SETUP_TAXES = "common:setup_taxes"
COMMON_SETUP_BRANCHES = "common:setup_branches"

# General Ledger
GL_SETUP_MANAGE = "gl:setup_manage"
GL_JOURNAL_POST = "gl:journal_post"
GL_REPORTS_VIEW = "gl:reports_view"

# Projects — a costing dimension shared by GL, AR/AP, inventory and order entry (D8)
PROJECTS_READ = "projects:read"
PROJECTS_MANAGE = "projects:manage"

# Accounts Receivable
AR_SETUP_MANAGE = "ar:setup_manage"
AR_TRANSACTIONS_POST = "ar:transactions_post"
AR_REPORTS_VIEW = "ar:reports_view"
AR_WRITEOFF_APPROVE = "ar:writeoff_approve"
# Posting past a customer credit limit is audited, so it is its own permission (P4 D8).
AR_CREDIT_LIMIT_OVERRIDE = "ar:credit_limit_override"

# Accounts Payable
AP_SETUP_MANAGE = "ap:setup_manage"
AP_TRANSACTIONS_POST = "ap:transactions_post"
AP_REPORTS_VIEW = "ap:reports_view"
AP_CREDIT_LIMIT_OVERRIDE = "ap:credit_limit_override"

# Inventory
INV_SETUP_MANAGE = "inv:setup_manage"
INV_TRANSACTIONS_ADJUST = "inv:transactions_adjust"
INV_REPORTS_VIEW = "inv:reports_view"
# Opening a count session and keying the sheet. Its own permission because counting is a
# **stock-taker's** job and adjustment posting is not: while this was folded into
# `inv:transactions_adjust`, the only way to let somebody count was to let them post
# adjustments, which is the authority a count exists to take out of their hands. Entering a
# count moves nothing — the sheet is a working paper until Process.
INV_COUNT_ENTER = "inv:count_enter"
# Processing a count session posts a variance document against every counted line at once, so
# it is separated from ordinary adjustment posting the way the AR write-off approval is — and
# from `inv:count_enter` above, so the person who counts is not the person who posts.
INV_COUNT_PROCESS = "inv:count_process"
# Renaming an item code moves every enquiry and report that reads by code, so — like the
# customer/supplier rename — it is its own permission rather than part of setup.
INV_ITEM_RENAME = "inv:item_rename"

# Order Entry
OE_SETUP_MANAGE = "oe:setup_manage"
OE_SALES_ORDERS_MANAGE = "oe:sales_orders_manage"
OE_PURCHASE_ORDERS_MANAGE = "oe:purchase_orders_manage"
OE_GRV_PROCESS = "oe:grv_process"
OE_REPORTS_VIEW = "oe:reports_view"
# Posting a landed cost moves value into stock that is already on hand and, where it is not,
# straight to COGS — it restates nothing earlier but it does change what everything sold from
# here on costs. Its own permission (P6 decision 11) rather than folding into `oe:grv_process`,
# because receiving goods and revaluing them are different authorities.
OE_LANDED_COST_POST = "oe:landed_cost_post"

# Fiscalization (P7) — the EBM device, its queue, and what they produce.
#
# Three rather than one, because they are three different jobs. Setting a device up and
# mapping the masters onto the authority's code tables is an administrator's act, done once.
# Working the queue — retrying, verifying against the device, attaching a receipt read off the
# portal, accepting a purchase the authority is holding — is a daily operational authority
# over what the business has told a revenue authority, and it is the one that most needs to be
# grantable on its own. Reading the queue, the receipts and the daily reports is neither.
FISCAL_SETUP_MANAGE = "fiscal:setup_manage"
FISCAL_QUEUE_MANAGE = "fiscal:queue_manage"
FISCAL_REPORTS_VIEW = "fiscal:reports_view"
#: Closing the fiscal day (decision 11's Z). Its own permission, added in P7 step 4: decision 15
#: gives `fiscal:reports_view` the X/Z **view** and names nothing for the act, and the act is
#: not a reading. A Z is irreversible, takes a number from the device's `FZR` run, and moves the
#: boundary every later day is measured from — closer in kind to filing a return than to
#: retrying a queue row, and `fiscal:queue_manage` would have handed it to whoever may press
#: Retry. A till supervisor closes the day; they do not administer the device.
FISCAL_CLOSE_DAY = "fiscal:close_day"

# Tax returns (P7). Viewing a return is an ordinary accounting enquiry; **filing** one posts a
# settlement entry and freezes the figures against a high-water mark, and is irreversible
# except through the return itself — so it is separated the way the AR write-off approval is.
TAX_VAT_RETURN_VIEW = "tax:vat_return_view"
TAX_VAT_RETURN_FILE = "tax:vat_return_file"

# Unrealized FX revaluation (P7 decision 13). Its own permission under `gl` rather than
# `gl:journal_post`: a revaluation restates the base value of every open foreign-currency item
# on the balance sheet at a date, which is a month-end authority and not a keying one.
GL_FX_REVALUE = "gl:fx_revalue"

# Reporting & analytics
REPORTING_FINANCIAL_STATEMENTS_VIEW = "reporting:financial_statements_view"
REPORTING_FINANCIAL_STATEMENTS_GENERATE = "reporting:financial_statements_generate"
REPORTING_TEMPLATES_MANAGE = "reporting:templates_manage"
REPORTING_SCHEDULES_MANAGE = "reporting:schedules_manage"
REPORTING_BANK_RECONCILIATION_MANAGE = "reporting:bank_reconciliation_manage"
REPORTING_AR_AGING_VIEW = "reporting:ar_aging_view"
REPORTING_AP_AGING_VIEW = "reporting:ap_aging_view"
REPORTING_GL_ADVANCED_VIEW = "reporting:gl_advanced_view"
REPORTING_COMPARATIVE_ANALYSIS = "reporting:comparative_analysis"
REPORTING_CASH_FLOW_VIEW = "reporting:cash_flow_view"
REPORTING_TRIAL_BALANCE_VIEW = "reporting:trial_balance_view"
REPORTING_INVENTORY_VALUATION_VIEW = "reporting:inventory_valuation_view"
REPORTING_DASHBOARD_VIEW = "reporting:dashboard_view"
REPORTING_EXPORT = "reporting:export"

# Bill of Materials
BOM_SETUP_MANAGE = "bom:setup_manage"
BOM_MANUFACTURING_CREATE = "bom:manufacturing_create"
BOM_MANUFACTURING_PROCESS = "bom:manufacturing_process"
BOM_REPORTS_VIEW = "bom:reports_view"
BOM_MRP_RUN = "bom:mrp_run"

# Point of Sale
POS_SETUP_MANAGE = "pos:setup_manage"
POS_TILL_OPERATE = "pos:till_operate"
POS_TILL_MANAGE = "pos:till_manage"
POS_SALES_CREATE = "pos:sales_create"
POS_RETURNS_PROCESS = "pos:returns_process"
POS_REPORTS_VIEW = "pos:reports_view"
POS_RECONCILE = "pos:reconcile"

ALL_PERMISSIONS: tuple[str, ...] = (
    USERS_CREATE,
    USERS_READ,
    USERS_UPDATE,
    USERS_DELETE,
    USERS_MANAGE_ROLES,
    ROLES_CREATE,
    ROLES_READ,
    ROLES_UPDATE,
    ROLES_DELETE,
    ROLES_MANAGE_PERMISSIONS,
    COMPANY_READ,
    COMPANY_UPDATE,
    ACCOUNTING_PERIODS_MANAGE,
    ACCOUNTING_PERIODS_REOPEN,
    COMMON_SETUP_CURRENCIES,
    COMMON_SETUP_TAXES,
    COMMON_SETUP_BRANCHES,
    GL_SETUP_MANAGE,
    GL_JOURNAL_POST,
    GL_REPORTS_VIEW,
    PROJECTS_READ,
    PROJECTS_MANAGE,
    AR_SETUP_MANAGE,
    AR_TRANSACTIONS_POST,
    AR_REPORTS_VIEW,
    AR_WRITEOFF_APPROVE,
    AR_CREDIT_LIMIT_OVERRIDE,
    AP_SETUP_MANAGE,
    AP_TRANSACTIONS_POST,
    AP_REPORTS_VIEW,
    AP_CREDIT_LIMIT_OVERRIDE,
    INV_SETUP_MANAGE,
    INV_TRANSACTIONS_ADJUST,
    INV_REPORTS_VIEW,
    INV_COUNT_ENTER,
    INV_COUNT_PROCESS,
    INV_ITEM_RENAME,
    OE_SETUP_MANAGE,
    OE_SALES_ORDERS_MANAGE,
    OE_PURCHASE_ORDERS_MANAGE,
    OE_GRV_PROCESS,
    OE_REPORTS_VIEW,
    OE_LANDED_COST_POST,
    FISCAL_SETUP_MANAGE,
    FISCAL_QUEUE_MANAGE,
    FISCAL_REPORTS_VIEW,
    FISCAL_CLOSE_DAY,
    TAX_VAT_RETURN_VIEW,
    TAX_VAT_RETURN_FILE,
    GL_FX_REVALUE,
    REPORTING_FINANCIAL_STATEMENTS_VIEW,
    REPORTING_FINANCIAL_STATEMENTS_GENERATE,
    REPORTING_TEMPLATES_MANAGE,
    REPORTING_SCHEDULES_MANAGE,
    REPORTING_BANK_RECONCILIATION_MANAGE,
    REPORTING_AR_AGING_VIEW,
    REPORTING_AP_AGING_VIEW,
    REPORTING_GL_ADVANCED_VIEW,
    REPORTING_COMPARATIVE_ANALYSIS,
    REPORTING_CASH_FLOW_VIEW,
    REPORTING_TRIAL_BALANCE_VIEW,
    REPORTING_INVENTORY_VALUATION_VIEW,
    REPORTING_DASHBOARD_VIEW,
    REPORTING_EXPORT,
    BOM_SETUP_MANAGE,
    BOM_MANUFACTURING_CREATE,
    BOM_MANUFACTURING_PROCESS,
    BOM_REPORTS_VIEW,
    BOM_MRP_RUN,
    POS_SETUP_MANAGE,
    POS_TILL_OPERATE,
    POS_TILL_MANAGE,
    POS_SALES_CREATE,
    POS_RETURNS_PROCESS,
    POS_REPORTS_VIEW,
    POS_RECONCILE,
)

# Roles seeded into every new tenant (ported from the v4 defaults).
SYSTEM_ROLES: tuple[dict[str, object], ...] = (
    {
        "name": "Administrator",
        "description": "Full access to every module and setting",
        "permissions": list(ALL_PERMISSIONS),
    },
    {
        "name": "Accountant",
        "description": "Manages financial transactions and reports",
        "permissions": [
            COMPANY_READ,
            ACCOUNTING_PERIODS_MANAGE,
            GL_SETUP_MANAGE,
            GL_JOURNAL_POST,
            GL_REPORTS_VIEW,
            PROJECTS_READ,
            PROJECTS_MANAGE,
            AR_TRANSACTIONS_POST,
            AR_REPORTS_VIEW,
            AP_TRANSACTIONS_POST,
            AP_REPORTS_VIEW,
            # P7: the VAT return and the month-end revaluation are an accountant's work, and
            # so is reading what the EBM queue has done. Managing devices and working the
            # queue by hand are not — those stay with the administrator.
            FISCAL_REPORTS_VIEW,
            FISCAL_CLOSE_DAY,
            TAX_VAT_RETURN_VIEW,
            TAX_VAT_RETURN_FILE,
            GL_FX_REVALUE,
            REPORTING_FINANCIAL_STATEMENTS_VIEW,
            REPORTING_TRIAL_BALANCE_VIEW,
        ],
    },
    {
        "name": "Sales Manager",
        "description": "Manages sales and customer relationships",
        "permissions": [
            COMPANY_READ,
            AR_SETUP_MANAGE,
            AR_TRANSACTIONS_POST,
            AR_REPORTS_VIEW,
            PROJECTS_READ,
            OE_SALES_ORDERS_MANAGE,
            OE_REPORTS_VIEW,
        ],
    },
    {
        "name": "Clerk",
        "description": "Basic data entry and read-only reporting",
        "permissions": [
            COMPANY_READ,
            USERS_READ,
            GL_REPORTS_VIEW,
            AR_REPORTS_VIEW,
            AP_REPORTS_VIEW,
            PROJECTS_READ,
        ],
    },
)

OWNER_ROLE_NAME = "Administrator"


def require(*permissions: str) -> Callable[..., object]:
    """FastAPI dependency asserting the caller holds *all* the given permissions.

    Company owners and impersonating platform admins pass implicitly.
    """
    from app.api.deps import require_permissions

    return Depends(require_permissions(permissions))


def has_permissions(granted: Iterable[str], required: Iterable[str]) -> bool:
    granted_set = set(granted)
    return all(permission in granted_set for permission in required)
