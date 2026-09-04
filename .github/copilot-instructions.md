# Vinea ERP — Copilot Agent Instructions

You are building **Vinea ERP**: a multi-tenant cloud ERP for East African SMEs (Rwanda first), Sage-Evolution-class scope. The frozen specification is `docs/Vinea_ERP_Master_Plan_v5.md` (v5.1). Read the relevant phase section and its **Definition of Done** before starting any task. Never invent scope beyond the plan; if the plan is ambiguous, ask.

## Non-negotiable architecture rules (from the ADRs — violations are bugs)
1. **No mutable balance columns.** Never add `current_balance`-style fields to accounts, customers, suppliers or items. Balances are derived from immutable `journal_lines` / stock moves (materialized `period_balances` is allowed only as a verifiable cache).
2. **Modules never write journal entries directly.** They emit typed events to the central `PostingEngine` (`app/kernel/posting.py`). One posting authority, period.
3. **Posted financial rows are append-only.** No UPDATE/DELETE of posted journal entries or lines — corrections are reversing entries (`reverses_entry_id`). Enforce with DB triggers, not convention.
4. **Journal lines carry dimensions:** `branch_id`, `project_id`, `currency_id`, `exchange_rate`, `amount`, `base_amount`, `tax_code_id`, `tax_amount`, `partner_*`, `item_id`, `source_doc_*`. Σ `base_amount` = 0 per entry.
5. **Tenancy:** every tenant table has `company_id NOT NULL` **and** a Postgres RLS policy. The policy-linter test must pass. Identity is `users` + `company_memberships`, never `user.company_id`.
6. **Money:** `NUMERIC(20,6)` amounts, `NUMERIC(20,10)` rates. Round per line, half-up, to `currencies.decimal_places` (RWF = 0). Never use float for money.
7. **Document numbers** come from `document_sequences` claimed with `SELECT … FOR UPDATE`. Gapless per (company, doc_type).
8. **Accounting periods are enforced** in the Posting Engine for every entry, including subledger-originated ones.
9. **One implementation per domain.** Never create `*_new.py` / `*_v2.py` parallels. Refactor in place.
10. **Migrations apply from zero.** Never edit an applied migration; never add "emergency" fix scripts. `alembic upgrade head` on an empty DB must always succeed (CI gate).
11. **Auth:** httpOnly cookies + refresh rotation. No tokens in localStorage.
12. **Compliance:** fiscalization goes through the `FiscalizationAdapter` interface + durable outbox. Country logic never leaks into AR/POS code.

## Quality bar (every task)
- `cd backend && uv run ruff check . && uv run pytest -q` must be green before you report done. Add tests with the code, including accounting-invariant assertions where money moves.
- Small, focused commits with conventional messages (`feat(kernel): …`, `test(ar): …`).
- Update `docs/` only when the plan itself changes (rare); otherwise document in code/docstrings.

## Stack conventions
- Backend: Python 3.12, FastAPI, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Pydantic v2, Alembic, `uv`. See `.github/instructions/backend.instructions.md`.
- Frontend: Next.js 15 App Router, TypeScript strict, Tailwind 4. See `.github/instructions/frontend.instructions.md`.
- Infra: `docker compose up -d db redis minio` for local services.
