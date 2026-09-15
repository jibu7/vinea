"""Transactional email — P1 ships the stub; the real adapter lands with the job runner.

Non-production runs keep the last messages in `outbox` so tests can read the one-time
tokens without the API ever returning them.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
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
        _append_to_outbox_file(message)
    return message


def _append_to_outbox_file(message: SentEmail) -> None:
    """Also drop the message into a file, when one is configured.

    `outbox` above serves the backend's own tests, which run in this process. An end-to-end
    test does not: Playwright drives a browser against a container, and the one-time token it
    needs to click a reset or an invitation link exists nowhere it can reach — the token is
    **hashed** in `user_tokens`, so the database cannot give it back either.

    The alternative was an endpoint that hands the token out, which would turn a mailed secret
    into an API affordance and undo the reason it is mailed. This is a local mail catcher with
    no moving parts: same non-production guard, no new surface, and production refuses to boot
    if the setting is ever set (see `Settings._harden_production`).

    Failures are swallowed on purpose. A mail sink that cannot write must not take down the
    request that was sending mail — the email itself has already been handed over.
    """
    path = settings.email_outbox_file
    if not path:
        return
    try:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(message)) + "\n")
    except OSError:  # pragma: no cover - a broken sink must not break the send
        logger.warning("email.outbox_file_unwritable", extra={"path": path})


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
