---
mode: agent
description: Build Phase 1 — SaaS shell backend (tenancy, memberships, auth, RLS, provisioning)
---
Implement **Phase 1** exactly as specified in `docs/Vinea_ERP_Master_Plan_v5.md` §5 (P1) and ADR-01, ADR-02, ADR-03, ADR-09.

Work in this order, committing after each step with green tests:
1. Models + migration: `companies`, `users`, `company_memberships`, `roles`, `membership_roles`, `audit_log`, plus the RLS policy on every `company_id` table. Add the **policy-linter test** (`tests/test_rls_linter.py`) that fails if any `company_id` table lacks RLS.
2. Request tenancy: a dependency that sets `SET LOCAL app.company_id` from the authenticated membership. Add a test that removes the app-level filter on purpose and proves RLS still returns nothing cross-tenant.
3. Auth: login (access + rotating refresh in httpOnly cookies), logout, `/me`, password reset tokens, email verification stub, invitation flow (invite → pending membership → accept).
4. Permissions: port the permission constant set from the plan's v4 lineage (see §5 P1) into `app/core/permissions.py` and a `require(*perms)` dependency.
5. Tenant provisioning service: signup → company → **Rwanda seed pack** (COA template placeholder, tax codes Output 18% / Input 18% / Exempt / Zero-rated with correct labels per Appendix C.1, RWF base + USD, default branch, fiscal year) → owner membership.
6. Operator console API: list/suspend tenants, impersonate-with-audit.

Definition of Done (must all be true): two tenants seeded; cross-tenant probes empty under RLS; invitation round-trip test passes; `uv run ruff check . && uv run pytest -q` green; `alembic upgrade head` from an empty database succeeds.
