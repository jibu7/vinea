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

# Common / maintenance
COMMON_SETUP_CURRENCIES = "common:setup_currencies"
COMMON_SETUP_TAXES = "common:setup_taxes"
COMMON_SETUP_BRANCHES = "common:setup_branches"

# General Ledger
GL_SETUP_MANAGE = "gl:setup_manage"
GL_JOURNAL_POST = "gl:journal_post"
GL_REPORTS_VIEW = "gl:reports_view"

# Accounts Receivable
AR_SETUP_MANAGE = "ar:setup_manage"
AR_TRANSACTIONS_POST = "ar:transactions_post"
AR_REPORTS_VIEW = "ar:reports_view"
AR_WRITEOFF_APPROVE = "ar:writeoff_approve"

# Accounts Payable
AP_SETUP_MANAGE = "ap:setup_manage"
AP_TRANSACTIONS_POST = "ap:transactions_post"
AP_REPORTS_VIEW = "ap:reports_view"

# Inventory
INV_SETUP_MANAGE = "inv:setup_manage"
INV_TRANSACTIONS_ADJUST = "inv:transactions_adjust"
INV_REPORTS_VIEW = "inv:reports_view"

# Order Entry
OE_SETUP_MANAGE = "oe:setup_manage"
OE_SALES_ORDERS_MANAGE = "oe:sales_orders_manage"
OE_PURCHASE_ORDERS_MANAGE = "oe:purchase_orders_manage"
OE_GRV_PROCESS = "oe:grv_process"
OE_REPORTS_VIEW = "oe:reports_view"

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
    COMMON_SETUP_CURRENCIES,
    COMMON_SETUP_TAXES,
    COMMON_SETUP_BRANCHES,
    GL_SETUP_MANAGE,
    GL_JOURNAL_POST,
    GL_REPORTS_VIEW,
    AR_SETUP_MANAGE,
    AR_TRANSACTIONS_POST,
    AR_REPORTS_VIEW,
    AR_WRITEOFF_APPROVE,
    AP_SETUP_MANAGE,
    AP_TRANSACTIONS_POST,
    AP_REPORTS_VIEW,
    INV_SETUP_MANAGE,
    INV_TRANSACTIONS_ADJUST,
    INV_REPORTS_VIEW,
    OE_SETUP_MANAGE,
    OE_SALES_ORDERS_MANAGE,
    OE_PURCHASE_ORDERS_MANAGE,
    OE_GRV_PROCESS,
    OE_REPORTS_VIEW,
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
            AR_TRANSACTIONS_POST,
            AR_REPORTS_VIEW,
            AP_TRANSACTIONS_POST,
            AP_REPORTS_VIEW,
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
