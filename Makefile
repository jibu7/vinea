up: ; docker compose up --build -d
down: ; docker compose down
logs: ; docker compose logs -f backend
be-test: ; cd backend && uv run pytest -q
be-lint: ; cd backend && uv run ruff check .
fe-dev: ; cd frontend && npm run dev

# `docker compose down -v` wipes every named volume in docker-compose.yml, not just
# Postgres' pgdata — MinIO's miniodata goes too, so this is a full dev-stack data wipe.
# Brings db back up from scratch — re-running docker/init-app-role.sql so the vinea_app
# role and its grants exist again — then migrates and seeds the e2e fixture tenants
# (e2e.primary@vinea.example / e2e.secondary@vinea.example, password from
# backend/app/scripts/seed_e2e.py). A DROP DATABASE + CREATE DATABASE in place would skip
# that re-init, since GRANT/ALTER DEFAULT PRIVILEGES are per-database, not per-cluster —
# recreating the volume is what actually gets the app role's permissions back.
db-reset:
	docker compose down -v
	docker compose up -d --wait db
	docker compose run --rm backend uv run alembic upgrade head
	docker compose up -d backend frontend
	docker compose exec -T backend uv run python -m app.scripts.seed_e2e
