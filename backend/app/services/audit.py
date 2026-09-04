"""Audit trail writer (ADR-09)."""

from typing import Any

from sqlalchemy.orm import Session
from starlette.requests import Request

from app.models.audit import AuditLog


def record_audit(
    db: Session,
    *,
    company_id: int,
    action: str,
    entity: str,
    entity_id: str | int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    actor_user_id: int | None = None,
    actor_email: str | None = None,
    impersonated_by_user_id: int | None = None,
    request: Request | None = None,
) -> AuditLog:
    user_agent = request.headers.get("user-agent") if request is not None else None
    entry = AuditLog(
        company_id=company_id,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        impersonated_by_user_id=impersonated_by_user_id,
        action=action,
        entity=entity,
        entity_id=None if entity_id is None else str(entity_id),
        before=before,
        after=after,
        ip_address=request.client.host if request is not None and request.client else None,
        user_agent=user_agent[:255] if user_agent else None,
    )
    db.add(entry)
    return entry
