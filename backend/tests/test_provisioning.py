from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import ALL_PERMISSIONS, OWNER_ROLE_NAME, SYSTEM_ROLES
from app.db import set_tenant
from app.models.company import Branch, CompanyStatus
from app.models.currency import Currency
from app.models.fiscal import AccountingPeriod, FiscalYear
from app.models.gl import GLSettings, GLTransactionType
from app.models.inventory import (
    INVENTORY_MODULE,
    InventoryTransactionKind,
    NegativeStockPolicy,
    Uom,
    UomCategory,
    Warehouse,
)
from app.models.membership import CompanyMembership, MembershipStatus, Role
from app.models.tax import TaxCode, TaxNature
from app.services.seed_rwanda import COA_TEMPLATE


def test_signup_provisions_company_owner_and_seed_pack(db: Session, two_tenants) -> None:
    tenant, _ = two_tenants
    set_tenant(db, tenant.company.id)

    assert tenant.company.status == CompanyStatus.ACTIVE
    assert tenant.company.fiscal_country == "RW"
    assert tenant.company.coa_template == COA_TEMPLATE

    membership = db.scalars(select(CompanyMembership)).one()
    assert membership.user_id == tenant.user.id
    assert membership.is_owner is True
    assert membership.status == MembershipStatus.ACTIVE

    roles = {role.name: role for role in db.scalars(select(Role))}
    assert set(roles) == {str(spec["name"]) for spec in SYSTEM_ROLES}
    assert set(roles[OWNER_ROLE_NAME].permissions) == set(ALL_PERMISSIONS)
    assert all(role.is_system for role in roles.values())


def test_seed_pack_currencies_branch_and_fiscal_year(db: Session, two_tenants) -> None:
    tenant, _ = two_tenants
    set_tenant(db, tenant.company.id)

    currencies = {currency.code: currency for currency in db.scalars(select(Currency))}
    assert set(currencies) == {"RWF", "USD"}
    assert currencies["RWF"].is_base is True
    assert currencies["RWF"].decimal_places == 0
    assert currencies["USD"].decimal_places == 2

    branch = db.scalars(select(Branch)).one()
    assert branch.code == "MAIN"
    assert branch.is_main is True

    fiscal_year = db.scalars(select(FiscalYear)).one()
    year = date.today().year
    assert fiscal_year.name == str(year)
    assert fiscal_year.start_date == date(year, 1, 1)
    assert fiscal_year.end_date == date(year, 12, 31)
    periods = db.scalars(select(AccountingPeriod).order_by(AccountingPeriod.period_no)).all()
    assert [period.period_no for period in periods] == list(range(1, 13))


def test_seed_pack_uses_the_corrected_vat_labels(db: Session, two_tenants) -> None:
    """Appendix C.1: output VAT is charged on sales, input VAT is paid on purchases."""
    tenant, _ = two_tenants
    set_tenant(db, tenant.company.id)

    codes = {code.code: code for code in db.scalars(select(TaxCode))}
    assert set(codes) == {"VAT-OUT-18", "VAT-IN-18", "VAT-EXEMPT", "VAT-ZERO"}

    assert codes["VAT-OUT-18"].nature == TaxNature.OUTPUT
    assert codes["VAT-OUT-18"].name == "Output VAT 18% (Sales)"
    assert codes["VAT-OUT-18"].rate_pct == 18

    assert codes["VAT-IN-18"].nature == TaxNature.INPUT
    assert codes["VAT-IN-18"].name == "Input VAT 18% (Purchases)"
    assert codes["VAT-IN-18"].rate_pct == 18

    assert codes["VAT-EXEMPT"].nature == TaxNature.EXEMPT
    assert codes["VAT-EXEMPT"].rate_pct == 0
    assert codes["VAT-ZERO"].nature == TaxNature.ZERO_RATED
    assert codes["VAT-ZERO"].rate_pct == 0


def test_each_tenant_gets_its_own_seed_pack(db: Session, two_tenants) -> None:
    first, second = two_tenants
    for tenant in (first, second):
        set_tenant(db, tenant.company.id)
        assert db.scalars(select(TaxCode)).all().__len__() == 4
        assert db.scalars(select(Currency)).all().__len__() == 2


def test_seed_pack_inventory_masters(db: Session, two_tenants) -> None:
    """P5 step 1: a tenant is able to post stock the moment it signs up — four UoM categories
    each with a base unit, a default warehouse, the single in-transit warehouse, and the six
    inventory transaction types, each carrying the kind its posting map keys off."""
    for tenant in two_tenants:
        set_tenant(db, tenant.company.id)

        categories = {row.code: row for row in db.scalars(select(UomCategory))}
        assert set(categories) == {"COUNT", "WEIGHT", "VOLUME", "LENGTH"}
        bases = db.scalars(select(Uom).where(Uom.is_base)).all()
        assert len(bases) == 4
        assert {row.category_id for row in bases} == {row.id for row in categories.values()}

        warehouses = {row.code: row for row in db.scalars(select(Warehouse))}
        assert set(warehouses) == {"MAIN", "TRANSIT"}
        assert warehouses["MAIN"].is_default is True
        assert warehouses["TRANSIT"].is_in_transit is True
        assert warehouses["MAIN"].branch_id == db.scalars(select(Branch.id)).one()

        types = {
            row.code: row
            for row in db.scalars(
                select(GLTransactionType).where(GLTransactionType.module == INVENTORY_MODULE)
            )
        }
        assert set(types) == {"ADJIN", "ADJOUT", "REVAL", "TRF", "CNTV", "OPEN"}
        assert all(row.kind is not None for row in types.values())
        assert types["OPEN"].kind == InventoryTransactionKind.OPENING_BALANCE

        settings = db.scalars(select(GLSettings)).one()
        assert settings.default_warehouse_id == warehouses["MAIN"].id
        assert settings.negative_stock_policy == NegativeStockPolicy.BLOCK
        for field in (
            "inventory_account_id",
            "inventory_in_transit_account_id",
            "inventory_adjustment_account_id",
            "stock_count_variance_account_id",
            "cogs_account_id",
        ):
            assert getattr(settings, field) is not None, field
