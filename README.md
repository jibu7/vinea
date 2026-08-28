# Vinea ERP

Multi-tenant cloud ERP for East African SMEs (Rwanda first). Sage-Evolution-class functionality on a modern stack: immutable ledger kernel, central posting engine, RRA EBM fiscalization, Postgres RLS tenancy.

**Plan:** `docs/Vinea_ERP_Master_Plan_v5.md` (v5.1 — frozen for build). **Status:** Phase 0 complete.

## Quick start
```bash
cp .env.example .env
docker compose up --build     # db + redis + minio + backend :8000 + frontend :3000
```
Backend only: `cd backend && uv sync && uv run uvicorn app.main:app --reload`
Frontend only: `cd frontend && npm install && npm run dev`
Tests/lint: `make be-test` · `make be-lint`

## Layout
`backend/` FastAPI + SQLAlchemy 2 + Alembic · `frontend/` Next.js 15 · `docs/` master plan · `.github/workflows/` CI
