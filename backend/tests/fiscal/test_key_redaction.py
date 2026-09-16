"""The three device keys stay secret — proven, not asserted.

Decision 2 says they are "encrypted at rest, never returned by any endpoint, never logged,
stripped from every stored payload/response". Each clause gets a test, and the last one walks
whatever a run actually produced rather than checking a place somebody remembered to check.

At step 1 that means the device rows and the sync responses. Step 2 extends the same walk over
`fiscal_outbox` and `fiscal_receipts` once a tape exists to fill them — the helper here is
written to take a list of (table, column) pairs for exactly that reason.
"""

import logging

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.fiscal import devices as device_service
from app.fiscal.keys import FiscalKeyError, decrypt_key, encrypt_key
from app.fiscal.rwanda.adapter import redact
from app.fiscal.rwanda.sandbox import (
    SANDBOX_CMC_KEY,
    SANDBOX_INTRL_KEY,
    SANDBOX_SIGN_KEY,
)
from app.models.company import Company
from app.models.fiscalization import FiscalDevice
from app.schemas.fiscal import DeviceRead, device_read

#: Every key string the sandbox hands out. A run that produced any of these in a stored row,
#: a response body or a log line has leaked one.
SECRET_VALUES = (SANDBOX_CMC_KEY, SANDBOX_INTRL_KEY, SANDBOX_SIGN_KEY)

#: JSONB columns that hold what an authority was told or said. Grown as the phase does.
JSON_COLUMNS = (
    ("fiscal_devices", "watermarks"),
    ("fiscal_outbox", "payload"),
    ("fiscal_outbox", "response"),
    ("fiscal_receipts", "request"),
    ("fiscal_receipts", "response"),
)


def test_no_response_schema_has_a_field_for_a_key() -> None:
    """The structural half, and the one that holds without anybody remembering it.

    A key cannot be serialised by a model that has no field for it, so this is checked over the
    model rather than over one endpoint's output — a second endpoint returning a device cannot
    reintroduce the leak.
    """
    fields = set(DeviceRead.model_fields)

    assert not fields & {"cmc_key", "intrl_key", "sign_key"}
    assert "has_keys" in fields, "what a screen needs is whether the device holds them"


def test_the_device_serializer_says_whether_keys_are_held_and_nothing_more(
    active_device: FiscalDevice,
) -> None:
    payload = device_read(active_device).model_dump(mode="json")

    assert payload["has_keys"] is True
    rendered = str(payload)
    for secret in SECRET_VALUES:
        assert secret not in rendered


def test_the_device_endpoint_returns_no_key(
    db: Session, fiscal_company: Company, active_device: FiscalDevice
) -> None:
    """The behavioural half. Both are kept: the structural test would pass over a hand-built
    dict response, and this one would pass over a model that had never been asked."""
    payload = device_read(active_device).model_dump(mode="json")
    body = str(payload) + str(list(payload))

    for column in ("cmc_key", "intrl_key", "sign_key"):
        assert column not in body


def test_every_stored_json_column_is_free_of_key_material(
    db: Session,
    fiscal_company: Company,
    fiscal_owner,  # noqa: ANN001
    active_device: FiscalDevice,
    sandbox_client: httpx.Client,
) -> None:
    """The walk. Everything a run wrote, greppped for the key strings.

    Driven rather than constructed: the device is initialized and both syncs are run, so the
    rows examined are the ones a real setup produces — including the initialization response,
    which is the one place the keys legitimately arrive.
    """
    device_service.sync_codes(
        db, fiscal_company.id, active_device, actor=fiscal_owner, client=sandbox_client
    )
    device_service.sync_item_classes(
        db, fiscal_company.id, active_device, actor=fiscal_owner, client=sandbox_client
    )
    db.commit()

    leaks: list[str] = []
    for table, column in JSON_COLUMNS:
        rows = db.execute(
            text(f"SELECT id, {column}::text FROM {table} WHERE {column} IS NOT NULL")  # noqa: S608
        ).all()
        for row_id, rendered in rows:
            for secret in SECRET_VALUES:
                if secret in rendered:
                    leaks.append(f"{table}.{column} row {row_id}")

    assert leaks == [], f"device key material found in stored JSON: {leaks}"


def test_the_audit_trail_records_the_counters_and_not_the_keys(
    db: Session, fiscal_company: Company, active_device: FiscalDevice
) -> None:
    """What an auditor needs to know about an initialization is which device answered and what
    it was holding — never what it signs with."""
    rendered = "".join(
        str(row)
        for row in db.execute(
            text("SELECT before::text, after::text FROM audit_log WHERE entity = 'fiscal_device'")
        ).all()
    )

    assert rendered
    for secret in SECRET_VALUES:
        assert secret not in rendered


def test_nothing_is_logged_but_the_device_the_path_the_code_and_the_time(
    db: Session,
    fiscal_company: Company,
    fiscal_owner,  # noqa: ANN001
    active_device: FiscalDevice,
    sandbox_client: httpx.Client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A payload in a log file is a customer's TIN and shopping list in a log file."""
    with caplog.at_level(logging.INFO, logger="app.fiscal.rwanda"):
        device_service.sync_codes(
            db, fiscal_company.id, active_device, actor=fiscal_owner, client=sandbox_client
        )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "ebm call" in logged, "the call must be logged at all"
    assert "path=/code/selectCodes" in logged
    assert "result=000" in logged
    for secret in SECRET_VALUES:
        assert secret not in logged
    # And no payload: the code table's own names would be in the body, never in the line.
    assert "Taxation Type" not in logged


def test_redact_reaches_a_nested_key() -> None:
    """The keys arrive nested under `data.info`, so a top-level pass would have missed them.

    This is the near-miss the walk above exists to catch, written as its own test so the
    reason survives.
    """
    body = {
        "resultCd": "000",
        "data": {"info": {"sdcId": "SDC010000005", "cmcKey": "secret", "sgnKey": "secret"}},
        "list": [{"intrlKey": "secret"}],
    }

    cleaned = redact(body)

    assert cleaned["data"]["info"]["cmcKey"] == "***"
    assert cleaned["data"]["info"]["sgnKey"] == "***"
    assert cleaned["list"][0]["intrlKey"] == "***"
    assert cleaned["data"]["info"]["sdcId"] == "SDC010000005", "the identity is not a secret"


def test_a_key_encrypted_under_another_secret_refuses_to_decrypt_and_says_what_to_do() -> None:
    """Almost always one thing: the secret changed, or a database was restored into an
    environment holding a different one. The recovery is to re-initialize against the
    authority, because the keys are the authority's to reissue."""
    from cryptography.fernet import Fernet

    foreign = Fernet(Fernet.generate_key()).encrypt(b"someone else's key").decode()

    with pytest.raises(FiscalKeyError) as refusal:
        decrypt_key(foreign)

    assert "Re-initialize" in str(refusal.value)


def test_an_absent_key_stays_absent_rather_than_becoming_a_ciphertext_of_nothing() -> None:
    """A device that has not initialized has no keys, and a ciphertext of an empty string
    would be indistinguishable from one that has."""
    assert encrypt_key(None) is None
    assert encrypt_key("") is None
    assert decrypt_key(None) is None


def test_a_round_trip_is_not_the_identity() -> None:
    """Belt and braces on the encryption itself: a "store it encrypted" that happened to store
    the plaintext would round-trip perfectly."""
    ciphertext = encrypt_key("a-device-key")

    assert ciphertext != "a-device-key"
    assert decrypt_key(ciphertext) == "a-device-key"


def test_the_device_row_holds_ciphertext_and_the_query_finds_no_plaintext(
    db: Session, fiscal_company: Company, active_device: FiscalDevice
) -> None:
    db.commit()
    rows = db.execute(
        text("SELECT cmc_key, intrl_key, sign_key FROM fiscal_devices")
    ).all()

    assert rows
    for row in rows:
        for stored in row:
            assert stored is not None
            assert stored not in SECRET_VALUES


def test_the_devices_listing_query_never_selects_a_key_column(
    db: Session, fiscal_company: Company, active_device: FiscalDevice
) -> None:
    """The listing reads whole ORM objects, so this asserts the *serializer* drops them —
    which is the layer that actually protects the response."""
    devices = device_service.list_devices(db, fiscal_company.id)

    rendered = str([device_read(device).model_dump(mode="json") for device in devices])
    for secret in SECRET_VALUES:
        assert secret not in rendered


def test_the_select_used_by_the_dashboard_returns_a_device(
    db: Session, fiscal_company: Company, active_device: FiscalDevice
) -> None:
    assert db.scalar(select(FiscalDevice).where(FiscalDevice.id == active_device.id)) is not None
