"""The migration target guards in `alembic/env.py`.

`alembic downgrade base` against the dev database drops every table in it, and the two URLs
(`DATABASE_URL` for the app role, `MIGRATION_DATABASE_URL` for the superuser) are easy to
confuse in a shell. Both refusals are tested here, plus the banner that names the database
every run resolved — the line that makes a wrong target visible before the DDL runs.
"""

import os
import subprocess
import sys
from collections.abc import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from alembic import command
from tests.conftest import ADMIN_TEST_URL, ADMIN_URL

GUARD_DB = f"{ADMIN_URL.database}_guard"
GUARD_URL = ADMIN_URL.set(database=GUARD_DB)


def _config(url_string: str) -> Config:
    config = Config("alembic.ini")
    # alembic.ini goes through configparser interpolation, so a literal % must be escaped.
    config.set_main_option("sqlalchemy.url", url_string.replace("%", "%%"))
    return config


def _drop_and_create(name: str) -> None:
    admin = create_engine(ADMIN_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": name},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        if name:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()


@pytest.fixture
def guard_engine() -> Iterator[Engine]:
    """A throwaway database at head, isolated from the suite's own `*_test` database so a
    successful downgrade here cannot take the running tests with it."""
    _drop_and_create(GUARD_DB)
    command.upgrade(_config(GUARD_URL.render_as_string(hide_password=False)), "head")
    engine = create_engine(GUARD_URL, isolation_level="AUTOCOMMIT")
    try:
        yield engine
    finally:
        engine.dispose()
        admin = create_engine(ADMIN_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": GUARD_DB},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{GUARD_DB}"'))
        admin.dispose()


def _add_tenant(engine: Engine) -> None:
    """A raw insert, deliberately: this is a schema-level fixture for a migration guard, not
    an application path — the service layer is not what `env.py` reads."""
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO companies (name, vat_registered, fiscal_country, status, "
                "coa_template) VALUES ('Guard Ltd', false, 'RW', 'active', 'rw_sme_v1')"
            )
        )


def _tables(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        ).scalar_one()


def _schema_is_gone(engine: Engine) -> bool:
    """`downgrade base` leaves `alembic_version` behind by design — every domain table going
    is the thing being asserted, not a table count of zero."""
    with engine.connect() as conn:
        remaining = {
            row.table_name
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
        }
    return remaining <= {"alembic_version"}


def test_downgrade_to_base_is_refused_while_tenants_exist(guard_engine: Engine) -> None:
    _add_tenant(guard_engine)
    before = _tables(guard_engine)

    with pytest.raises(Exception) as excinfo:  # noqa: PT011 - env.py's own error type
        command.downgrade(_config(GUARD_URL.render_as_string(hide_password=False)), "base")

    message = str(excinfo.value)
    assert "Refusing to migrate to base" in message
    assert GUARD_DB in message
    assert "ALEMBIC_ALLOW_DESTRUCTIVE=1" in message
    # The refusal is not advisory: nothing was dropped.
    assert _tables(guard_engine) == before


def test_downgrade_to_base_runs_when_the_override_is_set(
    guard_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_tenant(guard_engine)
    monkeypatch.setenv("ALEMBIC_ALLOW_DESTRUCTIVE", "1")

    command.downgrade(_config(GUARD_URL.render_as_string(hide_password=False)), "base")

    assert _schema_is_gone(guard_engine)


def test_downgrade_to_base_runs_on_a_database_with_no_tenants(guard_engine: Engine) -> None:
    """The migration gate (`make migrate-check`) downgrades a scratch database on every run;
    the guard must not stand in its way."""
    command.downgrade(_config(GUARD_URL.render_as_string(hide_password=False)), "base")

    assert _schema_is_gone(guard_engine)


def _run_alembic(
    args: list[str], env_overrides: dict[str, str | None]
) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()}
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_mismatched_database_urls_are_refused_naming_both() -> None:
    """`DATABASE_URL` left over from an app shell, pointing somewhere else, is the exact
    footgun the Makefile warns about. Run out-of-process: `env.py` resolves the URL from
    `settings`, which pydantic reads once at import time."""
    result = _run_alembic(
        ["upgrade", "head"],
        {
            "DATABASE_URL": "postgresql+psycopg://vinea_app:secret@localhost:5432/production_db",
            "MIGRATION_DATABASE_URL": "postgresql+psycopg://vinea:secret@localhost:5432/vinea",
        },
    )

    assert result.returncode != 0, result.stdout
    assert "DATABASE_URL and MIGRATION_DATABASE_URL name different databases" in result.stderr
    assert "production_db" in result.stderr and "/vinea" in result.stderr
    # Never the password, in either URL.
    assert "secret" not in result.stderr
    # It refuses before announcing a target, so no misleading banner is printed either.
    assert "alembic: migrating" not in result.stderr


def test_env_announces_the_resolved_database_on_every_run() -> None:
    """`alembic current` is read-only, so this proves the banner without touching schema."""
    result = _run_alembic(
        ["current"],
        {
            "DATABASE_URL": None,
            "MIGRATION_DATABASE_URL": ADMIN_TEST_URL.render_as_string(hide_password=False),
        },
    )

    assert result.returncode == 0, result.stderr
    banner = next(
        line for line in result.stderr.splitlines() if line.startswith("alembic: migrating")
    )
    # Full equality, not a substring check: the dev password happens to equal the dev
    # username, so "the password is absent" is only provable by pinning the whole line.
    port = f":{ADMIN_TEST_URL.port}" if ADMIN_TEST_URL.port else ""
    assert banner == (
        f"alembic: migrating {ADMIN_TEST_URL.username}@{ADMIN_TEST_URL.host}{port}"
        f"/{ADMIN_TEST_URL.database}  [from MIGRATION_DATABASE_URL]"
    )
