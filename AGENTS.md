# AGENTS.md — Vinea ERP
Spec: `docs/Vinea_ERP_Master_Plan_v5.md` (v5.1, frozen). Full rules: `.github/copilot-instructions.md` (read it first — the 12 architecture rules are non-negotiable).
Build/test: `cd backend && uv sync && uv run ruff check . && uv run pytest -q` · `cd frontend && npm install && npm run build`.
Local services: `docker compose up -d db redis minio`.
Never: mutable balance columns · direct GL writes outside the PostingEngine · editing applied migrations · `*_new.py` parallel modules · float money · tokens in localStorage.
