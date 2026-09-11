"""`seed_e2e` is idempotent, and that has to include the credential.

The script's whole contract is that the seed and the Playwright suite share one value from
`E2E_PASSWORD`, so neither has a literal to fall back to and they cannot disagree silently.
CI mints a fresh one per run, and a developer's database already holds these fixtures from
the last run — so a second seed has to *move* the fixture users to the new password. Leaving
an existing row alone made the script print its success JSON while every later login came
back `invalid_credentials`: the disagreement the design exists to prevent, one layer in.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.db import platform_scope
from app.models.user import User
from app.scripts import seed_e2e

FIXTURE_EMAILS = (
    seed_e2e.PRIMARY_EMAIL,
    seed_e2e.SECONDARY_EMAIL,
    seed_e2e.READONLY_EMAIL,
    seed_e2e.POSTER_EMAIL,
)


def _hashed_password(db: Session, email: str) -> str:
    with platform_scope(db):
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None, f"{email} was not seeded"
        return user.hashed_password


def test_a_second_seed_moves_every_fixture_user_to_the_new_password(
    db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(seed_e2e.PASSWORD_ENV, "first-run-credential")
    seed_e2e.main()

    monkeypatch.setenv(seed_e2e.PASSWORD_ENV, "second-run-credential")
    seed_e2e.main()
    capsys.readouterr()  # both runs print their fixture JSON to stdout

    for email in FIXTURE_EMAILS:
        hashed = _hashed_password(db, email)
        assert verify_password(hashed, "second-run-credential"), (
            f"{email} still carries an older password: a re-seed reported success but the "
            "suite that shares E2E_PASSWORD with it could not sign in"
        )
        assert not verify_password(hashed, "first-run-credential")


def test_seeding_twice_with_the_same_password_leaves_the_hash_alone(
    db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A matching password is not re-hashed — the seed should not churn rows it agrees with,
    and a changed hash here would mean the check is verifying nothing."""
    monkeypatch.setenv(seed_e2e.PASSWORD_ENV, "unchanged-credential")
    seed_e2e.main()
    before = {email: _hashed_password(db, email) for email in FIXTURE_EMAILS}

    seed_e2e.main()
    capsys.readouterr()

    assert {email: _hashed_password(db, email) for email in FIXTURE_EMAILS} == before
