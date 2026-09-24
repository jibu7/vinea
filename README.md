# Vinea ERP

Multi-tenant cloud ERP for East African SMEs (Rwanda first). Sage-Evolution-class functionality on a modern stack: immutable ledger kernel, central posting engine, RRA EBM fiscalization, Postgres RLS tenancy.

**Plan:** `docs/Vinea_ERP_Master_Plan_v5.md` (v5.1 — frozen for build). **Status:** Phase 8 complete (GL kernel · AR/AP subledger · inventory · order entry with the three-way match, landed cost and kits · Rwanda fiscalization through the VSDC/OSDC contract, VAT returns and the unrealized-FX revaluation · banking: bank accounts, statement import with per-bank mappings, the reconciliation workspace that locks only at a zero difference, payment runs, bank revaluation, and the Cashbooks and Bank reconciliation reports). The banking build is proven on the owner's real exports from BPR, BK and KCB. Phase 7's fiscalization is still **code-complete, certification pending**: RRA certification needs test-environment access nobody holds yet, plus training and proforma receipts and the PLU report, and `docs/rra/certification.md` is the runbook. Phase reports: `docs/p4-final-report.md`, `docs/p5-final-report.md`, `docs/p6-final-report.md`, `docs/p7-final-report.md`, `docs/p8-final-report.md`.

## Quick start
```bash
cp .env.example .env
docker compose up --build     # db + redis + minio + backend :8000 + frontend :3000
```
Backend only: `cd backend && uv sync && uv run uvicorn app.main:app --reload`
Frontend only: `cd frontend && npm install && npm run dev`
Tests/lint: `make be-test` · `make be-lint`
Reset dev data (full stack wipe incl. MinIO — drop, migrate, seed): `make db-reset`

## Layout
`backend/` FastAPI + SQLAlchemy 2 + Alembic · `frontend/` Next.js 15 · `docs/` master plan · `.github/workflows/` CI
