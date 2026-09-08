from logging.config import fileConfig

import sqlalchemy as sa
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from alembic import context
from app.config import settings
from app.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# alembic.ini leaves the URL empty; callers (tests, CI) may inject their own.
# Use migration_database_url (superuser) for executing DDL migrations.
if not config.get_main_option("sqlalchemy.url", ""):
    config.set_main_option(
        "sqlalchemy.url",
        getattr(settings, "migration_database_url", settings.database_url),
    )
target_metadata = Base.metadata


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
