"""`Idempotency-Key` handling shared by every posting endpoint (ADR-11)."""

from hashlib import sha256

from fastapi import Header
from pydantic import BaseModel

from app.kernel.money import fingerprint_material

IdempotencyKey = Header(
    alias="Idempotency-Key",
    min_length=1,
    max_length=64,
    description="Client-generated key; replaying it returns the original entry (ADR-11).",
)


def fingerprint(kind: str, payload: BaseModel) -> str:
    """Identifies the *request*, so the same key sent with a different body is caught.
    Key order (in the body or in the schema) is not part of the identity.

    The canonical form of a value lives in `app.kernel.money` — one implementation, shared with
    P7's item-registration hash, because "`1000` and `1000.00` are one amount" is a money rule
    and two copies of it is how one of them comes to be wrong.

    `kind` is a **value in the material** rather than a prefix glued to it, so this function
    assembles no text at all. That is what `tests/test_fingerprints.py` requires of a
    fingerprint, and it is the stricter rule for the same reason the canonicaliser is strict:
    text assembled beside a hash is where a representation gets in. The scoping is unchanged —
    two endpoints given one key still disagree — because the kind is still part of what is
    hashed.
    """
    return sha256(
        fingerprint_material({"kind": kind, "body": payload.model_dump()}).encode()
    ).hexdigest()
