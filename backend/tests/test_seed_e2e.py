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


# --- Where the credential comes from (P6 step 9) ---------------------------------------------


def test_the_password_falls_back_to_the_dotenv_docker_compose_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Step 8's drift, and the two-step that closes it.

    `.env` is the file this project asks you to put `E2E_PASSWORD` in, and until step 9 it
    reached `docker compose` and nothing else: the seed read only the exported variable, which
    `make db-reset` passes into the container with `-e`. Fill in `.env` and forget to export
    it and the seed refused to run at all; export one value and leave a different one in the
    file and the two sides used different strings. Both are `invalid_credentials` on every
    spec, which reads like a broken branch.

    Environment first, file second — the same order `frontend/e2e/support/fixtures.ts` uses.
    """
    dotenv = tmp_path / ".env"
    dotenv.write_text("APP_ENV=dev\nE2E_PASSWORD=from-the-file\nCOOKIE_SECURE=false\n")
    monkeypatch.setattr(seed_e2e, "DOTENV", dotenv)

    monkeypatch.delenv(seed_e2e.PASSWORD_ENV, raising=False)
    assert seed_e2e.fixture_password() == "from-the-file"

    # The environment still wins, because that is what CI sets and what `-e E2E_PASSWORD`
    # delivers into the container.
    monkeypatch.setenv(seed_e2e.PASSWORD_ENV, "from-the-environment")
    assert seed_e2e.fixture_password() == "from-the-environment"


def test_a_quoted_dotenv_value_is_read_the_way_compose_reads_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """`E2E_PASSWORD="a b c"` is one value, not a value with quotes in it.

    `docker compose` strips them, so a `.env` that works for compose has to work here — a
    password read as `"a b c"` including the quote characters is a different string, and the
    failure is once again a wall of `invalid_credentials` with nothing pointing at the cause.
    """
    monkeypatch.delenv(seed_e2e.PASSWORD_ENV, raising=False)
    for line, expected in (
        ('E2E_PASSWORD="quoted value"', "quoted value"),
        ("E2E_PASSWORD='single quoted'", "single quoted"),
        ("E2E_PASSWORD=bare", "bare"),
        ("E2E_PASSWORD=  padded  ", "padded"),
    ):
        dotenv = tmp_path / ".env"
        dotenv.write_text(line + "\n")
        monkeypatch.setattr(seed_e2e, "DOTENV", dotenv)
        assert seed_e2e.fixture_password() == expected, line


def test_neither_source_set_stops_the_seed_rather_than_inventing_a_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """There is no literal to fall back to, and there must not be: a default would be a
    credential in the tree that every deployment shares."""
    monkeypatch.delenv(seed_e2e.PASSWORD_ENV, raising=False)
    monkeypatch.setattr(seed_e2e, "DOTENV", tmp_path / "does-not-exist")

    with pytest.raises(SystemExit) as exit_info:
        seed_e2e.fixture_password()
    message = str(exit_info.value)
    # The message names both places it looked, because "not set" without "where" is what sent
    # the last person to the wrong file.
    assert "not in the environment" in message
    assert str(tmp_path / "does-not-exist") in message
    assert "make db-reset" not in message  # that is the *drift* fix, not the *unset* fix
