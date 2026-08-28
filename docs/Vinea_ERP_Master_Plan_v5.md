# 🚀 Vinea ERP — Master Plan v5 (SaaS Rebuild)

**Document Version:** 5.1 — **FROZEN FOR BUILD**
**Date:** August 2026
**Supersedes:** PRD v4.0 (Phase 8 status doc), Biwi_prd.md v1.0, phase specs from prior sessions
**Status:** Rebuild from zero — greenfield repo. v5.1 freeze (28 Aug 2026): Fixed Assets, landed cost and project costing pulled into launch scope; hybrid UI direction confirmed (owner decisions).

---

## 0. Decisions Locked In This Version

| # | Decision | Choice | Rationale (short) |
|---|----------|--------|-------------------|
| D1 | Product model | Commercial multi-tenant cloud ERP, "Sage-like", sold to East African SMEs and to accountants managing multiple client companies | Owner's stated direction |
| D2 | First market | **Rwanda**, then East Africa (Kenya, Uganda, Tanzania, Burundi, DRC) | Owner's stated direction |
| D3 | Tenancy | **Shared database, shared schema, `company_id` on every tenant table, enforced by PostgreSQL Row-Level Security** (application filter + DB policy, defense in depth) | Cheapest to operate at SME scale, one migration path, RLS closes the "forgot a WHERE clause" leak class. Escape hatch documented in ADR-01 |
| D4 | Stack | FastAPI (Python 3.12+, SQLAlchemy 2.0, Pydantic v2, Alembic) · Next.js 15 (App Router, TS, Tailwind) · PostgreSQL 16 · Redis (jobs/cache) · Docker | Team already knows it; nothing about the failures of v1 was the stack's fault |
| D5 | Architecture centerpiece | **Ledger Kernel first**: immutable double-entry journal + central Posting Engine, with tax, currency and branch as first-class journal-line dimensions from day 1 | Kills the v4 plan's fatal flaw (Phases 12–13 retrofit) |
| D6 | Compliance centerpiece | **RRA EBM integration via VSDC** built as a pluggable *fiscalization adapter*, so Kenya eTIMS / Uganda EFRIS / Tanzania VFD slot in later without touching invoicing logic | An invoicing product in Rwanda that can't produce EBM receipts is not sellable to VAT-registered businesses |

| D7 | Launch scope additions (v5.1) | **Fixed Assets** (P9), **landed cost / Importation Split** (P6), **project costing** (kernel P2, master P3, P&L P10); UI = **hybrid** (modern shell + dense work screens) | Owner decisions after Sage screenshot review |

Everything below assumes these seven decisions.

---

## 1. Product Definition

### 1.1 What we are building

A cloud ERP in the mold of Sage Evolution / Sage Business Cloud, for East African SMEs: General Ledger, AR, AP, Inventory, Order Entry, Banking, POS and light Manufacturing, wrapped in a proper SaaS shell (tenant self-service, subscriptions, accountant multi-client access), with statutory compliance (VAT, e-invoicing) treated as a core feature rather than an afterthought. The three-tier intent-based navigation from v4 (**Maintenance / Transactions / Reports**) is retained — it tested well and mirrors what Sage users already know.

### 1.2 Personas

1. **Business owner / manager** — wants dashboards, invoices that are RRA-legal, and to know who owes them money.
2. **Accountant / bookkeeping firm** — the key channel. One login, memberships in many client companies, switches context from a company picker. This persona is why identity is modeled as *User + CompanyMembership*, not the v4 `user.company_id`.
3. **Clerk / storekeeper** — data entry under tight permissions.
4. **Cashier** — POS screen only; every sale must produce a fiscal receipt.
5. **Platform operator (you)** — separate operator console: tenant list, plans, usage, support impersonation with audit trail. Not the in-app "superuser with X-Company-ID header" hack from v4.

### 1.3 What "Sage-like" implies for scope priorities

Sage's SME value is: dependable ledgers, statutory compliance, ageing/statements that accountants trust, and bank reconciliation. It is **not** exotic analytics. Therefore the rebuild prioritizes (in order): ledger correctness → fiscalization → AR/AP/allocations/ageing → inventory costing → banking & reconciliation → reporting polish → POS → BOM. BOM and POS remain in scope but late, exactly as in v4.

---

## 2. Compliance Architecture (Rwanda first)

### 2.1 The facts that shape the design

- Rwanda's Electronic Invoicing System (EIS) has been mandatory for registered taxpayers; VAT-registered businesses must issue EBM invoices for every sale (B2B and B2C).
- Software vendors with their own invoicing system integrate through the **VSDC (Virtual Sales Data Controller)** API and must obtain **RRA certification** (application to `cis_sdc_certification@rra.gov.rw`, technical review against the CIS4VSDC specification and RRA's checkpoint sheet).
- VAT registration threshold: turnover above **FRW 20,000,000** in any 12 months (or FRW 5,000,000 in the preceding quarter). Standard VAT rate **18%** — which is exactly the client tax list in `software_interface.txt` (Input 18%, Output 18%, Exempt 0%, Zero-rated 0%), confirming that list was Rwanda-shaped all along.
- **RWF is a zero-decimal currency.** Money handling must respect per-currency decimal places (RWF=0, USD/EUR=2), which the old plan never addressed.

> ⚠️ **Action item for you (non-code):** download the current CIS4VSDC technical specification + checkpoint Excel from rra.gov.rw and begin the certification conversation early — RRA review is on the critical path for go-live, and the spec dictates exact receipt fields (SDC ID, receipt signature, internal data, QR content, item classification codes, receipt/copy/training/proforma types). Phase 7 below is written to the published spec pattern but must be validated against the current document version before implementation.

### 2.2 Fiscalization adapter pattern

All statutory e-invoicing goes through one internal interface so country logic never leaks into AR/POS code:

```python
class FiscalizationAdapter(Protocol):
    def register_item(self, item: Item) -> FiscalItemRef: ...
    def fiscalize_invoice(self, doc: FiscalDocument) -> FiscalReceipt: ...
    def fiscalize_refund(self, doc: FiscalDocument, original: FiscalReceipt) -> FiscalReceipt: ...
    def report_purchase(self, doc: FiscalDocument) -> None: ...   # Rwanda: purchase acceptance
    def report_stock(self, movement: StockMovement) -> None: ...  # Rwanda VSDC stock endpoints
```

- Implementations: `RwandaVSDCAdapter` (Phase 7), later `KenyaETimsAdapter`, `UgandaEfrisAdapter`, `TanzaniaVfdAdapter`. A `NullAdapter` serves non-mandated tenants and development.
- Fiscalization is **asynchronous with a durable outbox**: posting an invoice enqueues a fiscalization job; the invoice is not printable/emailable as "final" until the fiscal receipt data (SDC ID, signature, QR payload) is attached. Retries with backoff handle RRA downtime; a per-tenant fiscal queue dashboard shows stuck documents. This is why Redis + a job runner is in the stack from Phase 0, not "future".
- The invoice/receipt PDF and POS print templates reserve the fiscal block (signature, SDC info, QR) from the very first template version.

### 2.3 Tax engine requirements (baked into the kernel, Phase 2)

- Tax codes carry: rate, nature (Output/Input/Exempt/Zero-rated), GL account, effective-date ranges (rates change; history must not).
- Every document line stores `tax_code_id`, `tax_amount`, and whether pricing is tax-inclusive or exclusive (retail in Rwanda is typically VAT-inclusive; B2B quotes often exclusive — both must work).
- Rounding policy: round **per line** to the currency's decimal places, half-up; document totals are sums of rounded lines. One documented rule, applied everywhere, property-tested.
- VAT return report = derived entirely from journal-line tax dimensions, so it reconciles to the GL by construction.

---

## 3. Architecture Decision Records

### ADR-01 — Tenancy: shared schema + `company_id` + PostgreSQL RLS

Every tenant-scoped table has `company_id BIGINT NOT NULL REFERENCES companies(id)` and an RLS policy:

```sql
ALTER TABLE journal_lines ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON journal_lines
  USING (company_id = current_setting('app.company_id')::bigint);
```

The FastAPI dependency that opens each request's DB session executes `SET LOCAL app.company_id = :cid` from the authenticated membership context. The application still filters by `company_id` (for query-plan quality), but a missed filter now returns nothing instead of leaking another tenant's ledger. The migration test suite includes a check that **every** table containing `company_id` has RLS enabled (a linter query over `pg_policies`), so new tables can't silently opt out.

Escape hatch: if a whale customer someday demands physical isolation, the same schema deploys as database-per-tenant with the RLS layer inert. Do not build schema-per-tenant now — migration fan-out across hundreds of schemas is the operational trap that kills small teams.

### ADR-02 — Identity: User + CompanyMembership

```
users(id, email UNIQUE, hashed_password, full_name, is_platform_admin, ...)
company_memberships(id, user_id, company_id, is_owner, is_active, UNIQUE(user_id, company_id))
membership_roles(membership_id, role_id)
roles(id, company_id, name, permissions JSONB)   -- roles remain per-company
```

Login authenticates the *user*; the client then selects (or auto-selects) a membership, and the session carries `{user_id, company_id, permission_set}`. The accountant persona gets a company switcher listing their memberships. Invitation flow: owner invites by email → pending membership → accept creates/links user. This replaces v4's `user.company_id` and its superadmin header workaround.

### ADR-03 — Auth transport

Access token (15 min) + rotating refresh token (14 d), both in `httpOnly; Secure; SameSite=Lax` cookies. No tokens in localStorage (v4's plan was XSS-exposed). Add: password reset via emailed token, email verification on signup, session revocation list in Redis. TOTP 2FA is a fast follow, not v1.

### ADR-04 — The Ledger Kernel (immutable journal, derived balances)

- `journal_entries` and `journal_lines` are **append-only**. No UPDATE/DELETE of posted financial rows — enforced by DB trigger, not convention. Corrections are reversing entries linked via `reverses_entry_id`.
- **No mutable balance columns anywhere.** `GLAccount.current_balance`, `Customer.current_balance`, `Supplier.current_balance` from v4 are deleted from the design. Balances are computed from journal lines; a `period_balances` materialized summary (account × period × branch × currency) is maintained transactionally for speed and is *verifiable* against the raw lines (invariant test).
- Journal line carries the dimensions: `branch_id`, `currency_id`, `exchange_rate`, `amount` (transaction currency) and `base_amount` (company base currency), `tax_code_id`, `tax_amount`, `partner_id` (customer/supplier), `item_id`, `source_doc_type`, `source_doc_id`. Reporting by branch, VAT return, FX revaluation and subledger reconciliation all fall out of this one table.
- Posting validates: entry balances to zero **in base currency**, period is Open, all accounts active and postable (no posting to header accounts), dimensions required by the account class are present.

### ADR-05 — Posting Engine (single GL authority)

Modules never write journal entries directly. They emit typed events:

```
InvoicePosted, CreditNotePosted, ReceiptPosted, PaymentPosted,
GoodsReceived, SupplierInvoiceMatched, StockAdjusted, StockTransferred,
StockSold(COGS), PeriodClosed, FxRevalued, ManufactureCompleted, PosSaleCompleted
```

A rules-driven `PostingEngine` maps event → balanced entry using the account-determination chain (document line override → partner default → item default → transaction-type default → module defaults), writes it atomically **in the same DB transaction as the source document**, and links `source_doc_type/id` both ways. One module to unit-test exhaustively; adding a country, a tax, or a new document type is configuration plus one event handler — not five copies of GL code.

### ADR-06 — Money & precision

`NUMERIC(20,6)` storage for amounts and `NUMERIC(20,10)` for rates/factors; presentation and *rounding* respect `currencies.decimal_places` (RWF=0). Exchange rates are dated rows (`exchange_rates(currency_id, valid_from, rate)`), never a single mutable column. Realized FX gain/loss posts at allocation time; unrealized revaluation is a period-end job. Base-currency amounts are frozen on each line at posting time.

### ADR-07 — Document numbering

`document_sequences(company_id, branch_id NULL, doc_type, prefix, next_number)` claimed with `SELECT ... FOR UPDATE` inside the posting transaction. Numbers are gapless per (company, doc_type) as required for fiscal documents. v4's `next_so_number` on a defaults row (race-prone) is gone.

### ADR-08 — Accounting periods & year-end

Period status (`Future/Open/Closed/Locked`) is enforced by the Posting Engine on every entry, including subledger-originated ones — the v4 gap. Year-end close is a first-class routine: closes P&L into `retained_earnings_account_id`, locks the year, and is itself a (reversible-by-reopening) journal event. Fiscal calendars are per-company (Rwanda tax year is calendar year, but companies may want different management years).

### ADR-09 — Audit & data lifecycle

All tables: `created_at, created_by, updated_at, updated_by`. Financial documents: no hard delete, `Void` status with reason + reversing entry. A generic `audit_log(company_id, actor, action, entity, entity_id, before, after, at)` records master-data changes and sensitive actions (role edits, period reopening, operator impersonation). "Rename Accounts / Rename Item Code" from the client spec = changing the *code* of an account/item with full history intact — trivial now because history references surrogate IDs, and the rename itself lands in `audit_log`.

### ADR-10 — Background jobs & files

Job runner (arq or Celery on Redis) from Phase 0: fiscalization outbox, statement PDF batches, report exports, FX revaluation, email. Object storage (S3-compatible; MinIO in dev) for attachments, logos, generated PDFs. Every AR/AP document supports attachments — accountants live on this.

### ADR-11 — API & frontend conventions

Cursor pagination + `?filter` conventions on every list endpoint; one error envelope (`{code, message, field_errors}`); idempotency keys on POSTs that create financial documents (double-click protection). Frontend keeps Zustand + TanStack Query + RHF/Zod, adds: shadcn/ui as the component base, a shared `DocumentForm` engine (header + dynamic lines + totals + tax) reused by JE/Invoice/CN/PO/SO/GRN — v4 rebuilt that form six times.

---

## 4. Core Schema Sketch (kernel tables only)

```sql
companies(id, name, tin, vat_registered, base_currency_id, fiscal_country, address JSONB, plan_id, status, ...)
branches(id, company_id, name, is_main, address JSONB, ...)
users / company_memberships / roles / membership_roles          -- ADR-02
fiscal_years(id, company_id, start_date, end_date, status)
accounting_periods(id, company_id, fiscal_year_id, name, start_date, end_date, status)
gl_accounts(id, company_id, code, name, class, parent_id, is_postable, is_control, control_type NULL, is_active)
currencies(id, company_id, code, name, symbol, decimal_places, is_base)
exchange_rates(id, company_id, currency_id, valid_from, rate)
tax_codes(id, company_id, code, name, nature, rate_pct, gl_account_id, valid_from, valid_to NULL)
journal_entries(id, company_id, number, entry_date, period_id, description, source_doc_type, source_doc_id,
                status, posted_by, posted_at, reverses_entry_id NULL)
journal_lines(id, entry_id, company_id, gl_account_id, branch_id, partner_type NULL, partner_id NULL,
              item_id NULL, currency_id, exchange_rate, amount, base_amount,        -- signed; SUM(base)=0 per entry
              tax_code_id NULL, tax_amount, description, source_line_id NULL)
period_balances(company_id, period_id, gl_account_id, branch_id, currency_id, debit_base, credit_base)  -- derived
document_sequences(company_id, branch_id NULL, doc_type, prefix, next_number)
audit_log(...)
fiscal_outbox(id, company_id, doc_type, doc_id, adapter, status, attempts, last_error, receipt JSONB)
```

Subledgers (AR/AP documents, allocations, stock moves, orders) reference the kernel; they never bypass it. Customer/supplier "balance" and "ageing" are queries over open documents; the control-account invariant (`SUM(open AR docs) == AR control account balance`) is asserted by the test suite after every scenario.

---

## 5. Rebuilt Phase Plan

The plan runs **P0–P12**. v4's Phases 12–13 (tax/currency/branch retrofit) still don't exist — those dimensions live in the kernel from P2; the count grew back to thirteen because Fixed Assets, landed cost and project costing joined launch scope (D7). Each phase ends with its Definition of Done plus the standing gate: **all accounting invariants green, migrations apply cleanly from zero, CI green.**

### P0 — Foundation & CI *(≈1 wk)*
Monorepo (`backend/`, `frontend/`, `infra/`, `docs/`); Docker Compose (postgres 16, redis, minio, backend, worker, frontend); GitHub Actions running ruff/mypy/pytest and eslint/tsc/vitest on every PR; pre-commit hooks; `.env` handling; seed script entrypoint; error tracking (Sentry) and structured JSON logging wired from the start.
**DoD:** `docker compose up` → hello endpoints; CI fails a deliberately broken PR.

### P1 — SaaS shell backend *(≈2 wk)*
Companies, users, memberships, roles/permissions (permission constants carried over from v4 — they were good), invitations, auth per ADR-03, RLS infrastructure + policy linter, audit columns/middleware, tenant provisioning service: signup → company created → **Rwanda seed pack** (COA template, 18%/exempt/zero tax codes, RWF base + USD, default branch, fiscal year) → owner membership. Operator console API (tenant list, suspend, impersonate-with-audit).
**DoD:** two tenants seeded; cross-tenant access attempts return empty under RLS even with the app filter deliberately removed in a test; invitation round-trip works.

### P2 — Ledger Kernel *(≈2–3 wk, the heart)*
Everything in ADR-04…ADR-08: COA (hierarchical, control accounts), periods + enforcement, immutable journal + reversal, Posting Engine + account determination, dimensions, document sequences, tax code table, exchange-rate table, manual Journal Entry & Cashbook Entry APIs, Trial Balance & Account Transactions queries, **invariant test suite** (zero-sum, closed-period rejection, immutability trigger, sequence gaplessness under concurrent posting — an actual parallelism test).
**DoD:** 100 property-based random balanced entries posted across 3 currencies and 2 branches; trial balance foots; reversal restores it; a backdated entry into a closed period is rejected at DB and API level.

### P3 — Frontend foundation & admin UIs *(≈2 wk)*
Opens with the **design sprint** (B.3): brand kit, tokens, hi-fi prototypes of dashboard / document workspace / POS, owner-approved before wiring. Then: auth screens, company switcher, four-intent nav (Maintenance/Transactions/Enquiries/Reports) (permission-filtered, as v4), admin CRUD (users/roles/company/periods/currencies/tax codes/branches/**projects**), the shared `DocumentForm` engine, JE + Cashbook entry UIs, TB & Account-Transactions report screens, onboarding wizard (company details → COA template pick → tax confirm → invite team).
**DoD:** a new tenant can sign up and post a balanced multi-line JE entirely through the UI in under 5 minutes.

### P4 — AR & AP on the kernel *(≈3 wk)*
Built together as one symmetric "partner documents" pattern: partners (customers/suppliers) with terms & credit limits (enforced with override-permission), sales reps, document types, invoices / credit & debit notes / receipts & payments (all tax-aware, multi-currency, line-based), **allocations** with realized-FX posting, open-item engine, project tagging on lines, ageing (config bucket sets), statements (PDF via job), transaction listings. "Return to supplier" = AP debit note, as in v4.
**DoD:** invoice→part-payment→allocation→statement cycle in RWF and USD; AR/AP control accounts reconcile to open items in the invariant suite; credit-limit block fires.

### P5 — Inventory *(≈3 wk)*
Items (stock/service/non-stock), UoM + conversions, barcodes, warehouses, item-location quantities as **derived** from stock moves (same philosophy as GL), weighted-average costing with an explicit **negative-stock policy** (default: block; per-company override posts at last cost and flags for review), adjustments, transfers (in-transit account), counts (session → variance → adjustments), valuation & movement reports. Invariant: stock valuation == inventory GL balance, always.
**DoD:** the costing test tape (receipts/issues/backdated receipt/count variance) reproduces hand-calculated average costs to the rounding rule.

### P6 — Order Entry & three-way match *(≈3 wk)*
SO (commit stock, → AR invoice with COGS event), PO (on-order qty), GRN (→ stock receipt against **GRN accrual/clearing account**), supplier-invoice **matching** against GRN with qty/price variance posting, backorder handling, document flows & statuses, OE listings, and **landed cost (Importation Split)**: allocate freight/insurance/duty cost documents across GRV lines (by value, qty or weight) into item cost via a landed-cost clearing account. "Breakup" (kit explosion at order time — the item left dangling in v4) lands here as SO-line kit expansion.
**DoD:** procure-to-pay leaves the accrual account at zero when fully matched; an import GRV with allocated landed costs values stock at true landed cost and clears the allocation account; order-to-cash moves stock, COGS and AR correctly; partial receipt/partial invoice paths tested.

### P7 — Fiscalization (Rwanda VSDC) & VAT returns *(≈2–3 wk + RRA certification calendar)*
`RwandaVSDCAdapter` per the current CIS4VSDC spec: item registration/classification, invoice & refund fiscalization, purchase acceptance, stock reporting endpoints as required; durable outbox + retry + queue dashboard; fiscal blocks on invoice/receipt templates (SDC ID, signature, QR); **VAT return report** (output vs input, exempt/zero-rated split) derived from journal tax dimensions; unrealized-FX revaluation job (it's a compliance-adjacent period-end routine, so it lives here).
**DoD:** end-to-end fiscalization against RRA's test environment passes their checkpoint sheet; a queued invoice survives simulated RRA downtime and completes; certification application submitted.

### P8 — Banking *(≈2 wk)*
Cashbook module proper (bank/cash accounts as flagged GL accounts, receipts/payments feed them — largely exists from P2/P4), **bank statement import** (CSV first; camt/MT940 later), reconciliation workspace (auto-match by amount/ref/date + manual match), reconciliation report, payment runs (batch supplier payments → single bank line). This covers the *Bank reconciliation* and *Cashbooks* reports the client spec listed but v4 never scheduled.
**DoD:** import a real bank CSV, reach a zero unreconciled difference, lock the reconciliation.

### P9 — Fixed Assets *(≈2 wk)*
Asset categories (default GL accounts, method, useful life), asset register (capitalize from an AP invoice line or direct entry, with cost/branch/project dimensions), depreciation methods (straight-line, reducing balance), period **depreciation run** as a job emitting `DepreciationPosted` kernel events, disposals & write-offs (proceeds vs NBV → gain/loss posting), revaluation basic, asset enquiry + register/depreciation/disposal reports. Small module by construction — the Posting Engine and job runner already exist.
**DoD:** an asset capitalized from a supplier invoice depreciates over three closed periods, is disposed at a gain, and every figure ties to the GL; depreciation into a closed period is impossible.

### P10 — Reporting & analytics *(≈2–3 wk)*
Balance Sheet, Income Statement (with branch/period comparatives), Cash Flow (indirect), GL detail with drill-down to source documents, sales analysis, **project profitability** (P&L by `project_id`, D8) & **slow movers** (the v4 placeholders — definable now because every sale line carries item + partner + branch dimensions), dashboard KPIs, export engine (PDF + XLSX via jobs), saved report parameters.
**DoD:** BS balances to zero by construction; IS ties to TB; every report exports.

### P11 — POS *(≈3 wk)*
Till setup, cashier sessions (float, cash-up, end-of-day recon → journal event), touch UI, barcode scanning, receipt printing **with fiscal block** (POS sales fiscalize through the same adapter/outbox — with an offline queue, since VSDC-class integrations tolerate deferred transmission but the sale can't wait), returns against original receipt, cashier & inventory-sales reports.
**DoD:** a full till day: open → 20 mixed sales incl. a return → cash-up matches → journal + fiscal queue clean.

### P12 — BOM light, commercialization & launch hardening *(≈3–4 wk)*
Multi-level BOM, manufacture order (consume components → produce finished at rolled-up cost, via `ManufactureCompleted` event), material requirements listing. Then the SaaS commercial layer: plans & limits (companies/users/documents-per-month), subscription billing — **Rwanda reality: MTN MoMo + card via a regional PSP (DPO Pay or Flutterwave); Stripe does not onboard Rwandan merchants** — trial→paywall flow, dunning. Finally: Playwright E2E suite over the golden paths, load test on posting throughput, backup/restore drill, production infra (start simple: one VM or small managed platform + managed Postgres + object storage; Kubernetes only when tenant count justifies it), runbooks, user docs.
**DoD:** a stranger can sign up, subscribe, invoice legally in Rwanda, reconcile their bank, and you can restore last night's backup.

---

## 6. Testing Strategy (non-negotiable this time)

v4's own PRD listed testing as tech debt; in an accounting product that is product-fatal. The rebuild treats the **invariant suite** as the definition of correctness, run in CI after every scenario test:

1. Σ base_amount = 0 for every posted entry; trial balance foots at any date.
2. AR/AP control account == Σ open partner documents.
3. Inventory GL account == Σ (qty_on_hand × avg_cost) across locations.
4. Tax report totals == Σ journal tax dimensions for the period.
5. No posted row ever mutated (checksum audit); no sequence gaps per doc type.
6. RLS: every `company_id` table has a policy; cross-tenant probes return empty.

Plus: property-based tests on rounding/costing, one migration-from-zero job in CI, Playwright E2E from P3 onward (not deferred to a final phase), and a permanent "demo company" fixture with two months of realistic Rwandan transactions used by every layer of tests and by sales demos.

---

## 7. Delta Log — v4 plan → v5 plan

| v4 item | v5 disposition |
|---|---|
| Mutable `current_balance` columns (GL/customer/supplier) | **Removed** — derived balances + invariants (ADR-04) |
| Per-module GL posting code (AR, AP, Inv, OE each) | **Replaced** by Posting Engine events (ADR-05) |
| Phase 12 Tax & Multi-currency retrofit | **Dissolved** — dimensions in kernel (P2); VAT return in P7 |
| Phase 13 Branch integration | **Dissolved** — `branch_id` on journal line from P2 |
| `user.company_id` + superadmin `X-Company-ID` header | **Replaced** by memberships + operator console (ADR-02) |
| `next_so_number` on defaults row | **Replaced** by locked document sequences (ADR-07) |
| JWT in localStorage | **Replaced** by httpOnly cookie + refresh rotation (ADR-03) |
| Bank reconciliation & Cashbooks reports (in client spec, never phased) | **Scheduled** — P8 |
| OE "Breakup", Sales analysis, Slow movers, POS "Transaction" (dangling) | **Scheduled** — P6, P10, P10, P11 |
| Accounting periods unenforced; no year-end close | **Enforced** + close routine (ADR-08) |
| App-level tenancy only | **+ Postgres RLS** with policy linter (ADR-01) |
| No jobs/files/email infra | **In P0/P1** (ADR-10) — prerequisite for fiscalization & statements |
| E2E testing deferred to final phase | **Continuous** from P3 |

## 8. Open Items (need your input, none block P0–P2)

1. **RRA spec version** — send me the current CIS4VSDC PDF + checkpoint sheet when you obtain them; P7 details get finalized against it.
2. **COA template** — do you have a preferred Rwandan SME chart of accounts (or a Sage Evolution export) to seed, or should I draft one?
3. **Languages** — English-only at launch, or English + Kinyarwanda (+ French) from day 1? i18n plumbing is cheap in P3, expensive later.
4. **POS offline depth** — occasional network blips (queue-and-retry, planned) vs. full offline-first PWA (significantly more work)? Depends on your target retailers' connectivity.
5. ~~Old repo~~ — **Resolved**: audited in Appendix A; salvage list in A.2.
6. ~~Production data~~ — **Resolved**: owner confirms test data only; no migration script needed.

---
**Document Status:** FROZEN v5.1 — P0 scaffold delivered with this document

---

## Appendix A — Repo Audit: `github.com/jibu7/biwi` (28 Aug 2026)

The repo turned out to be well **beyond** the v4 PRD's "Phase 8 complete": it contains POS (tills/sessions/reconciliation), BOM, a platform/SaaS layer (BillingPlan, CompanySubscription, UsageMetric, FeatureFlag, PlatformInvoice), forex gain/loss, tax calculator + tax reports, a reporting module, per-company i18n formatting, ~350 frontend TSX files, 50 test files, and a live deployment target (Render + Neon).

### A.1 Findings — every predicted flaw confirmed in code, plus new ones

| Finding | Evidence | v5 answer |
|---|---|---|
| Mutable balances | `current_balance Numeric(15,2)` on GLAccount, Customer, Supplier | ADR-04 derived balances |
| GL posting scattered | posting calls in **8** crud files (gl, ar, ar_new, ap, ap_new, pos, bom, forex_service) | ADR-05 Posting Engine |
| **Duplicated parallel modules** | `ar.py` (34 KB) **and** `ar_new.py` (41 KB); `ap.py` and `ap_new.py` both live | one implementation per domain, ever |
| Tenancy hack escalated | `User.company_id` made *nullable* + CHECK `user_type='platform_admin' OR company_id IS NOT NULL` | ADR-02 memberships |
| Migration incoherence | 48 migrations; commits titled "EMERGENCY", plus `nuclear_migration_reset.py`, `fix_production_migrations.py` — this is the "repo not coherent to updated system" the owner reported | CI migration-from-zero gate (P0) |
| No CI | `.github/` has docs, **no `workflows/`** | P0 |
| No fiscalization | zero hits for EBM / VSDC / RRA / Rwanda | P7 is new work, no legacy conflict |
| No payment provider | zero hits for Stripe / Flutterwave / MoMo / DPO — billing tables exist but nothing charges | P11 |
| Precision | `Numeric(15,2)` amounts everywhere → breaks on RWF (0 dp) and rate math | ADR-06 |

### A.2 Salvage list (port, don't re-derive)

1. **Seed data** — `init_db.py`: default COA, the 18%/exempt/zero tax-type set, AP/GL transaction types and defaults → basis of the P1 Rwanda seed pack (COA to be reshaped, §8 Q2).
2. **Frontend UI kit** — `components/ui/`: CurrencyInput, CurrencyDisplay, CustomerSelect/SupplierSelect, data-table, date/date-range pickers, dialog/form/toast set, Logo/BrandKit → port into P3 nearly as-is.
3. **Navigation tree + permission constants** — proven three-tier structure → P3.
4. **Domain references** — POS till/session/reconciliation model shape (P11), BOM crud logic (P12), billing-plan/subscription table shapes (P12), i18n company-format fields (P3), integration tests in `backend/tests/integration/` as behavioral specs for the new invariant suite.

### A.3 Do **not** carry forward

The ledger/posting layer, the `_new` duplicates, the migration chain (start migrations at zero), the nullable-company_id tenancy, `Numeric(15,2)` money, and any code path that updates a `current_balance` column.

### A.4 New open item

`database_dumps/` + `backups/` + Neon/Render config imply a **production deployment may hold real data**. Q6: are there live users/companies on it? If yes, P1 gains a one-off data-migration script (old schema → new kernel via opening-balance journals per company); if no, we skip it. **Answered 28 Aug: test data only — no migration script needed.**

---

## Appendix B — Sage Evolution Feature Map (owner's screenshots, Aug 2026)

Source: live Sage Evolution at a Rwandan SME (RWF, 18% VAT, tax-inclusive line pricing) — the exact deployment class Vinea replaces. Every menu item was mapped against this plan.

### B.1 Already covered by v5 (validation)

GL / AR / AP / Inventory / Order Entry / BOM / POS / Tax / Common (P2–P12) · Cashbook & Journal Batches (P2, P8) · GRV two-step flow — *Receive Stock* then *Process Invoice* — with Unprocessed→Confirmed→Processed states (exactly the P6 GRN→match design) · Tax-inclusive line pricing with line tax + Exclusive/Tax/Inclusive footer totals (§2.3, ADR-06) · guided setup "Process Flow" home (P3 onboarding wizard — keep the visual metaphor) · Agent Administration / System Config (operator console P1 + company settings P3) · Sage Intelligence / Visual Reports / Charts (P10 analytics).

### B.2 New items surfaced — dispositions

| Sage component | What it is | v5 disposition |
|---|---|---|
| **Enquiries** (4th top-level intent) | Interactive drill-down screens (account/customer/item enquiry) distinct from printed reports | **Adopt**: navigation becomes *Maintenance / Transactions / Enquiries / Reports* (P3); per-module enquiry screens ship with each module instead of waiting for P10 |
| **Project** selector on document lines | Job/project costing dimension | **Adopt in kernel now**: `project_id` added to `journal_lines` + stock moves in P2; Projects master in P3; P&L-by-project in P10 — **confirmed launch scope** |
| **Importation Split / Split Allocation** | Landed-cost allocation (freight, insurance, duty) across GRV lines into item cost | Strong differentiator for Rwandan importers → **confirmed P6 launch scope** |
| **Fixed Assets** (tiles + every menu) | Asset register, depreciation runs | **Scheduled as P9 — confirmed launch scope**: register + straight-line/reducing-balance depreciation runs posting as kernel events |
| **Post-dated cheques** (AR/AP, "due" widgets) | Future-dated payment instruments tracked until maturity | Backlog P4+: regionally common; model as documents with `maturity_date` excluded from cash until matured |
| **Settlement Terms** on documents | Early-payment discount terms | Backlog P4: terms master + discount posting at allocation |
| **My Desktop** actionable widgets | Post-dated due, IBT due, scheduled counts due, notifications, incidents | P10 dashboard widget framework; in-app notifications ride the P1 job/audit infra |
| **IBT requisition→issue→receipt** workflow | Inter-branch transfer with request/approve steps | P5 ships direct transfers; requisition workflow = backlog enhancement |
| **Contact Management / Incidents** | CRM-lite | Thin version only (contacts & notes on partners, P4); full CRM out of v1 |
| Alert/Information Alerts · Delivery Mgmt · Voucher Mgmt · Inventory Issue/Optimisation · Global Tax | Sage add-on modules | **Out of v1** unless owner flags a specific need |

### B.3 Design language ("make it beautiful")

Committed direction — **hybrid**: a modern, airy SaaS shell (distinctive type pairing, real design tokens, light + dark, generous dashboard) wrapped around **dense, keyboard-first document workspaces** where accountants live — the GRV screen's ergonomics reborn on the web: spreadsheet-grade line grid, sticky Exclusive/Tax/Inclusive footer, status chips, `Ctrl+K` command palette replacing the explorer tree, autosave drafts. P3 now opens with a **design sprint**: brand kit + tokens, then high-fidelity prototypes of the three flagship screens (dashboard, document workspace, POS) approved *before* wiring begins — beauty is a phase gate, not a coat of paint. **(Owner confirmed: hybrid.)**

---

## Appendix C — Client Menu Spec → Build Coverage Matrix

The owner's original menu ordering (software_interface docx) is **adopted as the navigation contract** — the tree users see, in the owner's order — and doubles as the launch acceptance checklist. Build order remains the v5 phase sequence. Every line item maps:

| Menu section (owner's order) | Items | Built in |
|---|---|---|
| Administration → User | users, roles | P1 backend · P3 UI |
| Maintenance → Common | Foreign Currency; Company details (Company, Accounting period, General) | P1–P2 backend · P3 UI |
| Maintenance → Tax | Tax types (18%/18%/Exempt/Zero) | P1 seed · P2 kernel · P3 UI |
| Maintenance → General Ledger | COA, Branches, Transaction types, Defaults, Rename Accounts | P2 · P3 |
| Maintenance → AR / AP | Customers, Sales reps, Suppliers, Transaction types, Defaults, Rename | P4 |
| Maintenance → Inventory | Items, Warehouses, Trans types, Variable barcodes, UoM categories, Defaults, Rename Item Code | P5 |
| Maintenance → Order entry / BOM / POS | Order defaults / BOM items+defaults / Tills+types+defaults | P6 / P12 / P11 |
| Transactions → GL | Cashbook batches, Journal batches | P2 (banking depth P8) |
| Transactions → AR | Credit note, Invoice, AR batches; Sales order | P4; SO in P6 |
| Transactions → AP *(mislabeled "Account Receivable" in spec — see C.1)* | GRV, Purchase order, Return to supplier, AP batches | P4 · P6 |
| Transactions → Inventory | Journal batches, Transfers, Adjustments, Counts (+ CN/GRV/Invoice/RTS stock impacts) | P5 (impacts via P4/P6 events) |
| Transactions → OE | Purchase order, **Breakup** | P6 (breakup = kit explosion) |
| Transactions → BOM / POS | Manufacture process, Breakup / Sales, Returns, Transaction | P12 / P11 |
| Reports → GL | Account transaction (P2), Trial Balance (P2), Chart of account (P3), **Bank reconciliation (P8)**, **Cashbooks (P8)**, Balance sheet (P10), Income statement (P10) | as noted |
| Reports → AR / AP | Age analyses, Allocation, Listings, Statements | P4 |
| Reports → Inventory | Movement, Count, Transaction, Valuation (P5); Sales analyses, Slow movers (P10) | as noted |
| Reports → BOM / POS | Manufacture process, MRP / Cashier sales, Inventory sales | P12 / P11 |

**Coverage: 100%** — the six items v4 had orphaned (bank rec, cashbooks, breakup ×2, sales analyses, slow movers, POS transaction) all have phase homes.

### C.1 Spec corrections (fix in the product, confirm with owner)

1. **Input/Output labels are swapped** in the tax list: the spec reads "Input (sales)" and "Output (purchases)", but VAT convention (incl. RRA) is **Output VAT = charged on sales, Input VAT = paid on purchases**. The P1 seed uses the correct mapping; only the labels change, rates stay 18/18/0/0.
2. **Duplicated "Account Receivable" heading**: the second block (GRV, PO, Return to supplier, AP batches) is the **Accounts Payable** transactions section — a copy/paste slip carried since the original spec.
3. **Allocation appears only under Reports** — but allocating receipts/payments to invoices is a *transaction*. The nav adds "Allocate" entries under Transactions → AR and → AP (P4); the Allocation *report* stays where the spec put it.

### C.2 Additions layered onto the owner's tree (post-spec decisions)

The spec predates the v5 decisions, so the final tree = owner's ordering **plus**: the **Enquiries** tier (App. B), fiscalization status/queue screens (P7), bank statement import & reconciliation workspace as transactions not just reports (P8), Allocate screens (C.1.3), SaaS administration (subscription, team, operator console), plus the now-confirmed Fixed Assets branch (P9, D7), landed cost on GRVs (P6, D8), and Projects (P3/P10, D8).
