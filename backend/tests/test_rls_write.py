"""Write-side tenancy: RLS must reject INSERTs aimed at another tenant, and a session
with no context at all must see nothing (fail-closed, not fail-open)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.db import set_tenant
from app.models.audit import AuditLog
from app.models.company import Branch
from app.models.membership import CompanyMembership, MembershipStatus

COMPANY_SCOPED_TABLES = text(
    """
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
    WHERE n.nspname = 'public'
      AND c.relkind = 'r'
      AND a.attname = 'company_id'
      AND a.attnum > 0
      AND NOT a.attisdropped
    ORDER BY c.relname
    """
)


def _cross_tenant_rows(foreign_company_id: int) -> list[object]:
    return [
        Branch(
            company_id=foreign_company_id, code="SMUGGLED", name="Smuggled branch", is_active=True
        ),
        CompanyMembership(
            company_id=foreign_company_id,
            email="intruder@kigali.example",
            is_owner=True,
            status=MembershipStatus.ACTIVE,
        ),
        AuditLog(
            company_id=foreign_company_id,
            action="forged.entry",
            entity="companies",
            entity_id=str(foreign_company_id),
        ),
    ]


@pytest.mark.parametrize("index", range(3))
def test_insert_into_another_tenant_is_rejected(db: Session, two_tenants, index: int) -> None:
    first, second = two_tenants
    set_tenant(db, first.company.id)

    db.add(_cross_tenant_rows(second.company.id)[index])
    with pytest.raises(ProgrammingError) as excinfo:
        db.flush()

    assert "row-level security" in str(excinfo.value).lower()
    db.rollback()


def test_update_cannot_move_a_row_into_another_tenant(db: Session, two_tenants) -> None:
    first, second = two_tenants
    set_tenant(db, first.company.id)

    branch = db.query(Branch).one()
    branch.company_id = second.company.id
    with pytest.raises(ProgrammingError):
        db.flush()
    db.rollback()


def test_every_tenant_isolation_policy_covers_writes(admin_engine) -> None:
    with admin_engine.connect() as conn:
        policies = conn.execute(
            text(
                """
                SELECT tablename, cmd, qual, with_check
                FROM pg_policies
                WHERE schemaname = 'public' AND policyname = 'tenant_isolation'
                ORDER BY tablename
                """
            )
        ).all()

    assert policies, "expected tenant_isolation policies"
    offenders = [
        policy.tablename
        for policy in policies
        if policy.cmd != "ALL" or not policy.with_check or not policy.qual
    ]
    assert offenders == [], (
        "tenant_isolation policies must be FOR ALL with an explicit WITH CHECK, "
        f"otherwise writes are unguarded: {offenders}"
    )


def test_policy_count_matches_company_scoped_table_count(admin_engine) -> None:
    with admin_engine.connect() as conn:
        tables = {row.relname for row in conn.execute(COMPANY_SCOPED_TABLES)}
        guarded = {
            row.tablename
            for row in conn.execute(
                text(
                    "SELECT tablename FROM pg_policies "
                    "WHERE schemaname = 'public' AND policyname = 'tenant_isolation'"
                )
            )
        }

    assert tables == guarded


def test_platform_mode_defaults_to_false_when_unset_or_empty(db: Session) -> None:
    db.execute(text("SELECT set_config('app.platform_mode', '', true)"))
    assert db.execute(text("SELECT app_platform_mode()")).scalar_one() is False

    db.execute(text("SELECT set_config('app.platform_mode', 'off', true)"))
    assert db.execute(text("SELECT app_platform_mode()")).scalar_one() is False

    # A brand-new connection has never seen the GUC at all.
    db.rollback()
    with db.get_bind().connect() as conn:
        assert conn.execute(text("SELECT app_platform_mode()")).scalar_one() is False
        assert conn.execute(text("SELECT app_current_company_id()")).scalar_one() is None


def test_a_session_without_any_settings_sees_no_tenant_rows(db: Session, two_tenants) -> None:
    with db.get_bind().connect() as conn:
        tables = [row.relname for row in conn.execute(COMPANY_SCOPED_TABLES)]
        assert tables, "expected company_id tables to probe"
        for table in tables:
            count = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608
            assert count == 0, f"{table} leaked {count} rows without a tenant context"


def test_seeded_rows_are_visible_once_a_tenant_is_bound(db: Session, two_tenants) -> None:
    """Counterpart to the test above: the zero counts must come from RLS, not empty tables."""
    first, _ = two_tenants
    set_tenant(db, first.company.id)
    assert db.execute(text("SELECT count(*) FROM branches")).scalar_one() == 1
    assert db.execute(text("SELECT count(*) FROM tax_codes")).scalar_one() == 4
