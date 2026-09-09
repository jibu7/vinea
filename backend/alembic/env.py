"""Alembic environment.

Migrations run against **`MIGRATION_DATABASE_URL`** (the superuser role), never
`DATABASE_URL` (the RLS-enforcing `vinea_app` role). Getting that wrong is not a slow
failure — `alembic downgrade base` against the dev database drops every table in it — so
this module names the database it resolved on every run and refuses two specific footguns:
a `downgrade base` against a database that still holds tenants, and an environment whose
two URLs disagree about which database is the target.
"""

import os
import sys
from logging.config import fileConfig

import sqlalchemy as sa
from sqlalchemy import engine_from_config, make_url, pool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from alembic import context
from app.config import settings
from app.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

ALLOW_DESTRUCTIVE_ENV = "ALEMBIC_ALLOW_DESTRUCTIVE"


class MigrationTargetError(RuntimeError):
    """The resolved migration target is wrong or the operation is too destructive to run."""


def _describe(url_string: str) -> str:
    """`user@host:port/dbname` — never the password."""
    url = make_url(url_string)
    host = url.host or "localhost"
    port = f":{url.port}" if url.port else ""
    return f"{url.username or '?'}@{host}{port}/{url.database}"


def _assert_urls_agree(migration_url: str) -> None:
    """A `DATABASE_URL` naming a *different* database than the one being migrated means the
    shell was set up for the app and the migration is about to land somewhere unintended.
    Only checked when the URL came from settings — an explicitly injected `sqlalchemy.url`
    (the test suite, `test_p4_backfill`, the `migrate-check` scratch database) is a
    deliberate override, and `DATABASE_URL` says nothing about it."""
    runtime_url = os.environ.get("DATABASE_URL")
    if not runtime_url:
        return
    runtime, migration = make_url(runtime_url), make_url(migration_url)
    if runtime.database == migration.database:
        return
    raise MigrationTargetError(
        "Refusing to migrate: DATABASE_URL and MIGRATION_DATABASE_URL name different "
        f"databases.\n  DATABASE_URL           -> {_describe(runtime_url)}\n"
        f"  MIGRATION_DATABASE_URL -> {_describe(migration_url)}\n"
        "Unset DATABASE_URL, or point MIGRATION_DATABASE_URL at the database you meant."
    )


def _resolve_url() -> tuple[str, str]:
    """Returns the URL to migrate and where it came from."""
    injected = config.get_main_option("sqlalchemy.url", "")
    if injected:
        return injected, "sqlalchemy.url (injected by the caller)"
    url = getattr(settings, "migration_database_url", settings.database_url)
    _assert_urls_agree(url)
    return url, "MIGRATION_DATABASE_URL"


_url, _source = _resolve_url()
config.set_main_option("sqlalchemy.url", _url.replace("%", "%%"))
# Printed on *every* run, to stderr so it survives `-q` and piping: the one line that says
# which database is about to be rewritten.
print(f"alembic: migrating {_describe(_url)}  [from {_source}]", file=sys.stderr, flush=True)

target_metadata = Base.metadata


def _targets_base() -> bool:
    """`get_revision_argument()` resolves `head` to a real revision number and `base` to
    `None` — so `None` *is* the "everything gets dropped" signal, and comparing the string
    "base" (the first version of this guard) silently never fires. `stamp base` lands here
    too, which is also worth refusing on a populated database."""
    try:
        return context.get_revision_argument() is None
    except KeyError:
        # `current`, `history`, `heads` and friends carry no destination at all — they run
        # env.py but change nothing, so there is nothing to refuse.
        return False


def _assert_downgrade_is_safe(connection: sa.engine.Connection) -> None:
    """Going to `base` drops every table. Refuse it on a database that still holds tenants
    unless the caller has said, in the environment, that they mean it."""
    if not _targets_base():
        return
    if os.environ.get(ALLOW_DESTRUCTIVE_ENV) == "1":
        return
    if connection.execute(sa.text("SELECT to_regclass('public.companies')")).scalar() is None:
        return  # nothing to lose — the schema is not there yet
    tenants = connection.execute(sa.text("SELECT count(*) FROM companies")).scalar_one()
    if tenants == 0:
        return
    raise MigrationTargetError(
        f"Refusing to migrate to base: {_describe(_url)} holds {tenants} tenant(s) in "
        "`companies`, and this would drop every table.\n"
        f"Set {ALLOW_DESTRUCTIVE_ENV}=1 if that is genuinely what you want."
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        _assert_downgrade_is_safe(connection)
        with context.begin_transaction():
            context.run_migrations()
        # Ensure application role has permissions on all tables/sequences after migration
        connection.execute(
            sa.text(
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vinea_app') THEN
                        GRANT USAGE ON SCHEMA public TO vinea_app;
                        GRANT SELECT, INSERT, UPDATE, DELETE
                            ON ALL TABLES IN SCHEMA public TO vinea_app;
                        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vinea_app;
                        GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO vinea_app;
                    END IF;
                END
                $$;
                """
            )
        )
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
