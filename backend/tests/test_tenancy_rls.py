"""Cross-tenant probes with the application-level filter deliberately removed (P1 DoD)."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.db import set_tenant
from app.models.company import Branch
from app.models.membership import Role
from app.models.tax import TaxCode


def test_unfiltered_queries_only_see_the_bound_tenant(db: Session, two_tenants) -> None:
    first, second = two_tenants

    set_tenant(db, first.company.id)
    # No `.where(Branch.company_id == ...)` on purpose — RLS is the backstop.
    assert {branch.company_id for branch in db.scalars(select(Branch))} == {first.company.id}
    assert {code.company_id for code in db.scalars(select(TaxCode))} == {first.company.id}
    assert {role.company_id for role in db.scalars(select(Role))} == {first.company.id}

    set_tenant(db, second.company.id)
    assert {branch.company_id for branch in db.scalars(select(Branch))} == {second.company.id}


def test_explicit_cross_tenant_filter_returns_nothing(db: Session, two_tenants) -> None:
    first, second = two_tenants

    set_tenant(db, first.company.id)
    leaked = db.scalars(select(Branch).where(Branch.company_id == second.company.id)).all()
    assert leaked == []
    assert db.scalar(select(Branch.id).where(Branch.company_id == second.company.id)) is None


def test_raw_sql_without_a_tenant_context_returns_nothing(db: Session, two_tenants) -> None:
    set_tenant(db, None)
    assert db.execute(text("SELECT count(*) FROM branches")).scalar_one() == 0
    assert db.execute(text("SELECT count(*) FROM company_memberships")).scalar_one() == 0


def test_writes_into_another_tenant_are_rejected(db: Session, two_tenants) -> None:
    first, second = two_tenants

    set_tenant(db, first.company.id)
    db.add(Branch(company_id=second.company.id, code="X", name="Smuggled", is_active=True))
    with pytest.raises(ProgrammingError):
        db.flush()
    db.rollback()


def test_the_app_role_cannot_bypass_rls(db: Session) -> None:
    role = db.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).one()
    assert role.rolsuper is False
    assert role.rolbypassrls is False
