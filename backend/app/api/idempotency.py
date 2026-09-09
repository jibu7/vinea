"""`Idempotency-Key` handling shared by every posting endpoint (ADR-11)."""

import json
from datetime import date
from decimal import Decimal
from hashlib import sha256

from fastapi import Header
from pydantic import BaseModel

IdempotencyKey = Header(
    alias="Idempotency-Key",
    min_length=1,
    max_length=64,
    description="Client-generated key; replaying it returns the original entry (ADR-11).",
)


def canonical(value: object) -> str:
    """Cosmetic differences must not look like a different request: `1000` and `1000.00`
    are the same amount, and `2026-03-15` the same date, however the client spelled them."""
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"no canonical form for {type(value).__name__}")


def fingerprint(kind: str, payload: BaseModel) -> str:
    """Identifies the *request*, so the same key sent with a different body is caught.
    Key order (in the body or in the schema) is not part of the identity."""
    body = json.dumps(
        payload.model_dump(), sort_keys=True, separators=(",", ":"), default=canonical
    )
    return sha256(f"{kind}:{body}".encode()).hexdigest()
