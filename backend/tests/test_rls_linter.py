"""ADR-01 policy linter: no `company_id` table may silently opt out of RLS, and no
`tenant_isolation` policy may be narrower than `FOR ALL` with both `USING` and `WITH CHECK`
— a policy without `WITH CHECK` reads correctly but lets a tenant *write* another's rows."""

from sqlalchemy import text

LINTER_QUERY = text(
    """
    SELECT c.relname,
           c.relrowsecurity,
           c.relforcerowsecurity,
           p.policyname IS NOT NULL AS has_policy,
           p.cmd,
           p.qual IS NOT NULL AS has_using,
           p.with_check IS NOT NULL AS has_with_check
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
    LEFT JOIN pg_policies p
           ON p.schemaname = 'public'
          AND p.tablename = c.relname
          AND p.policyname = 'tenant_isolation'
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


def test_every_tenant_policy_is_for_all_with_a_write_check(admin_engine) -> None:
    with admin_engine.connect() as conn:
        rows = conn.execute(LINTER_QUERY).all()

    offenders = [
        (row.relname, row.cmd, row.has_using, row.has_with_check)
        for row in rows
        if row.cmd != "ALL" or not row.has_using or not row.has_with_check
    ]
    assert offenders == [], (
        "tenant_isolation policies must be FOR ALL with both USING and WITH CHECK: "
        f"{offenders}"
    )


def test_the_jobs_table_is_covered(admin_engine) -> None:
    """Job artifacts are customer statement PDFs; name the table explicitly so a future
    change that drops it out of the tenant tables list fails here, not in production."""
    with admin_engine.connect() as conn:
        rows = {row.relname: row for row in conn.execute(LINTER_QUERY).all()}

    job = rows.get("jobs")
    assert job is not None, "jobs must carry company_id and be linted"
    assert job.relrowsecurity and job.relforcerowsecurity
    assert job.has_policy and job.cmd == "ALL" and job.has_using and job.has_with_check
