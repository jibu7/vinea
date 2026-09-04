"""Shared fixtures.

The whole API test suite connects as a **non-superuser** role so Postgres actually
enforces the `tenant_isolation` policies — superusers (and, without FORCE, table owners)
bypass RLS, which would make the isolation tests vacuous.
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import URL, create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

ADMIN_URL = make_url(
    os.environ.get("DATABASE_URL", "postgresql+psycopg://vinea:vinea@localhost:5432/vinea")
)
TEST_DB_NAME = f"{ADMIN_URL.database}_test"
APP_ROLE = "vinea_app_test"
APP_PASSWORD = "vinea_app_test"

ADMIN_TEST_URL = ADMIN_URL.set(database=TEST_DB_NAME)
APP_TEST_URL = ADMIN_TEST_URL.set(username=APP_ROLE, password=APP_PASSWORD)

# app.config reads the environment at import time; point the application at the
# RLS-enforcing role before anything imports it.
os.environ["DATABASE_URL"] = APP_TEST_URL.render_as_string(hide_password=False)
os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET"] = "test-secret-that-is-long-enough-for-hs256"
os.environ["COOKIE_SECURE"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import engine, platform_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.services import email as email_service  # noqa: E402
from app.services.provisioning import ProvisionedTenant, provision_tenant  # noqa: E402

TABLES_IN_TRUNCATION_ORDER = (
    "audit_log",
    "membership_roles",
    "company_memberships",
    "roles",
    "accounting_periods",
    "fiscal_years",
    "tax_codes",
    "currencies",
    "branches",
    "refresh_tokens",
    "user_tokens",
    "companies",
    "users",
)


def _maintenance_url() -> URL:
    return ADMIN_URL.set(database="postgres")


def _recreate_test_database() -> None:
    maintenance = create_engine(_maintenance_url(), isolation_level="AUTOCOMMIT")
    with maintenance.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": TEST_DB_NAME},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
        role_exists = conn.scalar(
            text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE}
        )
        if not role_exists:
            # Utility statements cannot take bind parameters; both values are literals
            # defined in this file, never user input.
            conn.execute(
                text(
                    f"CREATE ROLE \"{APP_ROLE}\" LOGIN PASSWORD '{APP_PASSWORD}' "
                    "NOSUPERUSER NOCREATEDB NOBYPASSRLS"
                )
            )
    maintenance.dispose()


def _run_migrations() -> None:
    from alembic.config import Config

    from alembic import command

    config = Config("alembic.ini")
    # alembic.ini goes through configparser interpolation, so literal % must be escaped.
    url = ADMIN_TEST_URL.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def _grant_app_role() -> None:
    admin = create_engine(ADMIN_TEST_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        for statement in (
            f'GRANT USAGE ON SCHEMA public TO "{APP_ROLE}"',
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{APP_ROLE}"',
            f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{APP_ROLE}"',
        ):
            conn.execute(text(statement))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[None]:
    _recreate_test_database()
    _run_migrations()
    _grant_app_role()
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables() -> Iterator[None]:
    yield
    admin = create_engine(ADMIN_TEST_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(
            text(f"TRUNCATE {', '.join(TABLES_IN_TRUNCATION_ORDER)} RESTART IDENTITY CASCADE")
        )
    admin.dispose()
    email_service.outbox.clear()


@pytest.fixture
def db() -> Iterator[Session]:
    """Session on the RLS-enforcing app role — no tenant context until you set one."""
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def admin_engine() -> Iterator[object]:
    """Owner connection, used only for schema introspection in the RLS linter."""
    admin = create_engine(ADMIN_TEST_URL)
    try:
        yield admin
    finally:
        admin.dispose()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def make_tenant(
    db: Session,
    *,
    company_name: str,
    email: str,
    password: str = "correct horse battery staple",
    full_name: str = "Test Owner",
) -> ProvisionedTenant:
    tenant = provision_tenant(
        db,
        company_name=company_name,
        full_name=full_name,
        email_address=email,
        password=password,
    )
    db.commit()
    return tenant


@pytest.fixture
def two_tenants(db: Session) -> tuple[ProvisionedTenant, ProvisionedTenant]:
    first = make_tenant(db, company_name="Kigali Traders Ltd", email="owner@kigali.example")
    second = make_tenant(db, company_name="Musanze Supplies Ltd", email="owner@musanze.example")
    return first, second


@pytest.fixture
def platform_admin(db: Session):  # noqa: ANN201 - returns the ORM user
    from app.core.security import hash_password
    from app.models.user import User

    user = User(
        email="operator@vinea.example",
        hashed_password=hash_password("correct horse battery staple"),
        full_name="Platform Operator",
        is_platform_admin=True,
    )
    with platform_scope(db):
        db.add(user)
        db.commit()
    return user


def login(client: TestClient, email: str, password: str = "correct horse battery staple", **extra):  # noqa: ANN201
    return client.post("/api/v1/auth/login", json={"email": email, "password": password, **extra})


def company_names(db: Session) -> list[str]:
    from sqlalchemy import select

    with platform_scope(db):
        return list(db.scalars(select(Company.name)))
