---
applyTo: "backend/**"
---
# Backend rules
- SQLAlchemy 2.0 declarative style only: `class X(Base): id: Mapped[int] = mapped_column(primary_key=True)`. All models import `Base` from `app.db` (it carries the constraint naming convention — do not create another Base).
- Register every model module in `app/models/__init__.py` so Alembic autogenerate sees it. After `alembic revision --autogenerate`, **read the generated file** and fix it (autogenerate misses RLS policies, triggers, and enum changes — write those by hand with `op.execute`).
- RLS: for every new table with `company_id`, the migration must `ENABLE ROW LEVEL SECURITY` and create the `tenant_isolation` policy using `current_setting('app.company_id')`.
- Money/quantities: `Numeric(20, 6)`; rates `Numeric(20, 10)`; use `decimal.Decimal` in Python, never float.
- API: routers under `app/api/v1/`, Pydantic schemas under `app/schemas/`, business logic in `app/services/` (never in routers). Cursor pagination on list endpoints; one error envelope `{code, message, field_errors}`.
- Tests: pytest under `backend/tests/`, one file per feature, use the shared fixtures in `tests/conftest.py`. Any test that posts money must assert the trial balance still foots (`assert_ledger_invariants(db, company_id)`).
- Lint: `ruff` (line length 100). Type hints everywhere.
