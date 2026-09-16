up: ; docker compose up --build -d
down: ; docker compose down
logs: ; docker compose logs -f backend
# In the container, not on the host: the container writes `backend/.venv` as root through the
# bind mount, so a host `uv run` fails on `.venv/CACHEDIR.TAG` once the stack has been up.
#
# **This is not why the suite took ~19 minutes.** Measured at P5 step 9 on the same 161-test
# `tests/kernel` subset, back to back: in the container 257.19s, on the host 269.60s. The host
# is ~5% *slower* — it reaches Postgres through the published port where the container has it
# on the compose network. What cost the time was the per-process test database and its
# migration run, not where pytest is invoked.
#
# Which is what running under xdist is for. `conftest.py` names its database `vinea_test_<pid>`
# and every worker is its own process, so each gets its own database, its own migration run and
# its own session fixture — the setup that dominated the wall clock now happens on several cores
# at once. Nothing is deselected: the item count is the same as serial, and a run that reports
# fewer is a bug in this line, not a faster suite.
#: **Capped, not `auto`.** Each xdist worker migrates and holds open its own
#: `vinea_test_<pid>` database, and the migration run takes locks on every table it touches
#: inside one transaction. Postgres' `max_locks_per_transaction` defaults to 64 and the lock
#: table is sized `max_locks_per_transaction x (max_connections + max_prepared_transactions)`
#: **for the whole cluster**, so the limit is shared: on a 16-core machine `-n auto` started
#: sixteen concurrent migration runs and they exhausted it together —
#: `out of shared memory / You might need to increase max_locks_per_transaction` — on a
#: machine where nothing was wrong except the core count.
#:
#: Capping the workers is the fix chosen over raising the limit in the compose Postgres
#: command, for two reasons. The limit would have to be raised on every developer's database
#: and in CI, which is a second thing to keep in step; and the cap costs nothing measurable —
#: past four workers the suite is bounded by Postgres, not by cores, so the eight extra
#: workers on a 16-core machine were buying contention rather than speed.
#:
#: CI is unaffected either way: its runner has 4 cores, so `auto` and this cap are the same
#: number there. Override it on a machine that wants a different one:
#:   PYTEST_WORKERS=8 make be-test
PYTEST_WORKERS ?= 4
be-test: ; docker compose exec -T backend uv run pytest -n $(PYTEST_WORKERS) -q
be-lint: ; docker compose exec -T backend uv run ruff check .

# For a machine whose `backend/.venv` is its own. Same commands on the host, opt-in, so nobody
# whose venv works is forced through Docker — and so the comparison above stays reproducible.
# `UV_PROJECT_ENVIRONMENT` points elsewhere if the bind-mounted venv is root-owned:
#   UV_PROJECT_ENVIRONMENT=/tmp/vinea-hostvenv make be-test-host
be-test-host: ; cd backend && env -u DATABASE_URL uv run pytest -n $(PYTEST_WORKERS) -q
be-lint-host: ; cd backend && env -u DATABASE_URL uv run ruff check .
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
# **Set E2E_PASSWORD first**, in the environment or in the repo-root `.env`. The seed hashes
# it and the Playwright suite signs in with it; neither has a literal to fall back to, so an
# unset variable stops the seed with a message rather than creating users nothing can log in
# as. Both sides read the environment first and `.env` second (P6 step 9 — until then only the
# exported value reached the seed, and a password left in `.env` was read by `docker compose`
# and by nothing else), so either of these works and the two cannot disagree:
#   export E2E_PASSWORD="$$(openssl rand -base64 24)"
# or, in .env:
#   E2E_PASSWORD=...
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
