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
# (e2e.primary@vinea.example / e2e.secondary@vinea.example). A DROP DATABASE + CREATE
# DATABASE in place would skip that re-init, since GRANT/ALTER DEFAULT PRIVILEGES are
# per-database, not per-cluster — recreating the volume is what actually gets the app role's
# permissions back.
#
# **Export E2E_PASSWORD first.** The seed hashes it and the Playwright suite signs in with it;
# neither has a literal to fall back to, so an unset variable stops the seed with a message
# rather than creating users nothing can log in as:
#   export E2E_PASSWORD="$$(openssl rand -base64 24)"
db-reset:
	docker compose down -v
	docker compose up -d --wait db
	docker compose run --rm backend uv run alembic upgrade head
	docker compose up -d backend frontend
	docker compose exec -e E2E_PASSWORD -T backend uv run python -m app.scripts.seed_e2e

# The migration gate, run against a throwaway database — up from nothing, models-vs-migrations
# check, then all the way back down. Use this rather than hand-rolling it: `alembic/env.py`
# reads **`MIGRATION_DATABASE_URL`** (the superuser role), *not* `DATABASE_URL`, so
# `DATABASE_URL=…/somewhere_else alembic downgrade base` silently targets the dev database
# and wipes it. CI gets the same gate for free because its `vinea` database is untouched by
# pytest, which works in a per-process `vinea_test_<pid>` database (see backend/tests/conftest.py).
#
# Runs **inside the backend container**, like every other backend check here. It used to shell
# out to `cd backend && uv run`, which cannot work on a machine that has ever brought the stack
# up: the container writes `backend/.venv` as root through the bind mount, so the host `uv`
# then fails with `Permission denied` on `.venv/CACHEDIR.TAG` before alembic is even reached.
# A gate nobody can run locally is a gate that only CI enforces, which is the drift this
# project treats as a bug rather than a fact of life.
#
# `db:5432` rather than `localhost:5432` because the URL is resolved from inside the container,
# and `env -u DATABASE_URL` inside the shell rather than `-e DATABASE_URL=` on the exec: the
# latter sets it to an empty string, which SQLAlchemy then fails to parse.
MIGRATION_SCRATCH_DB ?= vinea_migration_check
migrate-check:
	docker compose exec -T db psql -U vinea -d postgres -q \
	  -c 'DROP DATABASE IF EXISTS $(MIGRATION_SCRATCH_DB)' \
	  -c 'CREATE DATABASE $(MIGRATION_SCRATCH_DB)'
	docker compose exec -T \
	  -e MIGRATION_DATABASE_URL=postgresql+psycopg://vinea:vinea@db:5432/$(MIGRATION_SCRATCH_DB) \
	  backend sh -c 'env -u DATABASE_URL sh -c "uv run alembic upgrade head && uv run alembic check && uv run alembic downgrade base"'
	docker compose exec -T db psql -U vinea -d postgres -q \
	  -c 'DROP DATABASE IF EXISTS $(MIGRATION_SCRATCH_DB)'
	@echo "migrate-check: upgrade-from-zero, alembic check and downgrade-to-base all green"
