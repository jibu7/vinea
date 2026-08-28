up: ; docker compose up --build -d
down: ; docker compose down
logs: ; docker compose logs -f backend
be-test: ; cd backend && uv run pytest -q
be-lint: ; cd backend && uv run ruff check .
fe-dev: ; cd frontend && npm run dev
