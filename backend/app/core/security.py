"""Password hashing, opaque token helpers and JWT issue/verify (ADR-03)."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

from app.config import settings

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

_hasher = PasswordHasher()


class TokenError(Exception):
    """Raised when a JWT is missing, malformed, expired or of the wrong type."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hashed_password: str, password: str) -> bool:
    try:
        _hasher.verify(hashed_password, password)
    except Argon2Error:
        return False
    return True


def needs_rehash(hashed_password: str) -> bool:
    return _hasher.check_needs_rehash(hashed_password)


def generate_opaque_token() -> str:
    """URL-safe secret handed out in emails/links; only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _encode(claims: dict[str, Any], expires_at: datetime) -> str:
    payload = {
        **claims,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(
    *,
    user_id: int,
    company_id: int | None,
    membership_id: int | None,
    impersonated_by: int | None = None,
) -> tuple[str, datetime]:
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.access_token_ttl_minutes)
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "typ": ACCESS_TOKEN_TYPE,
        "cid": company_id,
        "mid": membership_id,
    }
    if impersonated_by is not None:
        claims["imp"] = impersonated_by
    return _encode(claims, expires_at), expires_at


def create_refresh_token(
    *,
    user_id: int,
    company_id: int | None,
    impersonated_by: int | None = None,
) -> tuple[str, str, datetime]:
    """Returns `(token, jti, expires_at)`. The jti is stored hashed for rotation."""
    jti = uuid.uuid4().hex
    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "typ": REFRESH_TOKEN_TYPE,
        "cid": company_id,
        "jti": jti,
    }
    if impersonated_by is not None:
        claims["imp"] = impersonated_by
    return _encode(claims, expires_at), jti, expires_at


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("typ") != expected_type:
        raise TokenError("unexpected token type")
    return payload
