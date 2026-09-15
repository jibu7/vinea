"""The mail sink that lets an end-to-end test read a mailed token.

`email.outbox` serves the backend's own tests, which run in the same process as the sender.
Playwright does not: it drives a browser against a container, and the one-time tokens it needs
to click a reset link or accept an invitation live only in that container's memory — the
database stores them **hashed**, so it cannot give them back either.

An endpoint that returned the token would make a mailed secret into an API affordance, which
is precisely what mailing it is meant to prevent. This is a local mail catcher instead.
"""

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.services import email as email_service


@pytest.fixture
def sink(tmp_path, monkeypatch) -> Path:  # noqa: ANN001
    path = tmp_path / "outbox.jsonl"
    monkeypatch.setattr(email_service.settings, "email_outbox_file", str(path))
    return path


def test_a_sent_message_lands_in_the_file_with_its_token(sink: Path) -> None:
    email_service.send_password_reset_email(to="owner@rugari.example", token="tok_abcdef123456")

    written = [json.loads(line) for line in sink.read_text(encoding="utf-8").splitlines()]
    assert len(written) == 1
    assert written[0]["to"] == "owner@rugari.example"
    assert written[0]["context"]["token"] == "tok_abcdef123456"


def test_messages_append_rather_than_replace(sink: Path) -> None:
    """An e2e reads the *last* message for an address, so earlier ones have to survive."""
    email_service.send_password_reset_email(to="a@rugari.example", token="tok_firstfirst11")
    email_service.send_email_verification(to="b@rugari.example", token="tok_secondsecond")

    lines = sink.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["to"] for line in lines] == ["a@rugari.example", "b@rugari.example"]


def test_nothing_is_written_when_no_sink_is_configured(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    """The default. Normal running writes no tokens anywhere."""
    monkeypatch.setattr(email_service.settings, "email_outbox_file", None)
    email_service.send_password_reset_email(to="c@rugari.example", token="tok_nothingwritten")
    assert list(tmp_path.iterdir()) == []


def test_an_unwritable_sink_does_not_break_the_send(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    """The mail has already been handed over; a broken catcher must not fail the request."""
    monkeypatch.setattr(
        email_service.settings, "email_outbox_file", str(tmp_path / "nope" / "outbox.jsonl")
    )
    message = email_service.send_password_reset_email(
        to="d@rugari.example", token="tok_unwritable1"
    )
    assert message.context["token"] == "tok_unwritable1"


def test_production_refuses_to_start_with_a_sink_configured() -> None:
    """A test affordance that writes one-time tokens to disk must not be switchable on in
    production by an environment variable. Same shape as the JWT secret check beside it."""
    with pytest.raises(ValueError, match="EMAIL_OUTBOX_FILE"):
        Settings(
            app_env="prod",
            jwt_secret="a real secret, set outside development",
            email_outbox_file="/tmp/outbox.jsonl",
        )
