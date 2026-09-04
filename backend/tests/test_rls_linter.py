"""ADR-01 policy linter: no `company_id` table may silently opt out of RLS."""

from sqlalchemy import text

LINTER_QUERY = text(
    """
    SELECT c.relname,
           c.relrowsecurity,
           c.relforcerowsecurity,
           EXISTS (
               SELECT 1 FROM pg_policies p
               WHERE p.schemaname = 'public'
                 AND p.tablename = c.relname
                 AND p.policyname = 'tenant_isolation'
           ) AS has_policy
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


def test_every_company_scoped_table_has_forced_rls(admin_engine) -> None:
    with admin_engine.connect() as conn:
        rows = conn.execute(LINTER_QUERY).all()

    assert rows, "expected at least one company_id table"
    offenders = [
        row.relname
        for row in rows
        if not (row.relrowsecurity and row.relforcerowsecurity and row.has_policy)
    ]
    assert offenders == [], (
        "tables with company_id but without ENABLE + FORCE ROW LEVEL SECURITY and a "
        f"tenant_isolation policy: {offenders}"
    )
