from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# Deterministic constraint names — required for clean Alembic autogenerate/downgrade
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Postgres GUCs read by the `tenant_isolation` RLS policies (ADR-01).
TENANT_GUC = "app.company_id"
PLATFORM_GUC = "app.platform_mode"

_SET_CONTEXT = text(
    "SELECT set_config(:tenant_guc, :company_id, true), set_config(:platform_guc, :platform, true)"
)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _context_params(session: Session) -> dict[str, str]:
    company_id = session.info.get("company_id")
    return {
        "tenant_guc": TENANT_GUC,
        "company_id": "" if company_id is None else str(company_id),
        "platform_guc": PLATFORM_GUC,
        "platform": "on" if session.info.get("platform_mode") else "off",
    }


@event.listens_for(Session, "after_begin")
def _apply_context_on_begin(session: Session, transaction: object, connection: object) -> None:
    """Re-apply the tenant GUCs on every transaction — `SET LOCAL` dies at commit."""
    connection.execute(_SET_CONTEXT, _context_params(session))  # type: ignore[attr-defined]


def _reapply_context(session: Session) -> None:
    session.execute(_SET_CONTEXT, _context_params(session))


def set_tenant(session: Session, company_id: int | None) -> None:
    """Bind the session to one tenant; every RLS-protected read/write is scoped to it."""
    session.info["company_id"] = company_id
    _reapply_context(session)


@contextmanager
def platform_scope(session: Session) -> Iterator[None]:
    """Escape hatch for cross-tenant platform work (auth, invitation accept, provisioning,
    operator console). Never use it inside a request that serves tenant data."""
    previous = session.info.get("platform_mode", False)
    session.info["platform_mode"] = True
    _reapply_context(session)
    try:
        yield
    finally:
        session.info["platform_mode"] = previous
        _reapply_context(session)


def set_actor(session: Session, user_id: int | None) -> None:
    session.info["actor_user_id"] = user_id


@event.listens_for(Session, "before_flush")
def _stamp_audit_columns(session: Session, flush_context: object, instances: object) -> None:
    """ADR-09 audit columns — created_by/updated_by from the request actor."""
    actor_id = session.info.get("actor_user_id")
    now = datetime.now(UTC)
    for obj in session.new:
        if getattr(obj, "__audited__", False):
            if obj.created_by is None:
                obj.created_by = actor_id
            obj.updated_by = actor_id
    for obj in session.dirty:
        if getattr(obj, "__audited__", False) and session.is_modified(obj):
            obj.updated_by = actor_id
            obj.updated_at = now


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
