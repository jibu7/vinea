"""Transactional email — P1 ships the stub; the real adapter lands with the job runner.

Non-production runs keep the last messages in `outbox` so tests can read the one-time
tokens without the API ever returning them.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

logger = logging.getLogger("vinea.email")

_OUTBOX_LIMIT = 100


@dataclass(frozen=True)
class SentEmail:
    to: str
    subject: str
    body: str
    context: dict[str, Any] = field(default_factory=dict)


outbox: list[SentEmail] = []


def send_email(*, to: str, subject: str, body: str, **context: Any) -> SentEmail:
    message = SentEmail(to=to, subject=subject, body=body, context=context)
    logger.info("email.send", extra={"to": to, "subject": subject})
    if not settings.is_production:
        outbox.append(message)
        del outbox[:-_OUTBOX_LIMIT]
    return message


def send_invitation_email(*, to: str, company_name: str, token: str) -> SentEmail:
    url = f"{settings.frontend_base_url}/invitations/accept?token={token}"
    return send_email(
        to=to,
        subject=f"You have been invited to {company_name} on Vinea",
        body=f"Accept your invitation to {company_name}: {url}",
        token=token,
        company_name=company_name,
    )


def send_password_reset_email(*, to: str, token: str) -> SentEmail:
    url = f"{settings.frontend_base_url}/reset-password?token={token}"
    return send_email(
        to=to,
        subject="Reset your Vinea password",
        body=f"Reset your password: {url}",
        token=token,
    )


def send_email_verification(*, to: str, token: str) -> SentEmail:
    url = f"{settings.frontend_base_url}/verify-email?token={token}"
    return send_email(
        to=to,
        subject="Verify your Vinea email address",
        body=f"Verify your email address: {url}",
        token=token,
    )
