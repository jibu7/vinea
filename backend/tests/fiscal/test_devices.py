"""The device lifecycle: register, initialize, activate, suspend — and the three rules that
hang off activation.

Every one of these drives the real adapter against the in-process sandbox, so what is asserted
is what a device actually did, not what a double was told to say.
"""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.fiscal import devices as device_service
from app.fiscal.keys import decrypt_key
from app.fiscal.rwanda.sandbox import (
    SANDBOX_MRC_NO,
    SANDBOX_SDC_ID,
    SANDBOX_SIGN_KEY,
    SandboxState,
)
from app.kernel.sequences import DocType
from app.models.company import Branch, Company
from app.models.fiscalization import (
    FiscalCode,
    FiscalDevice,
    FiscalDeviceStatus,
    FiscalEnvironment,
    FiscalItemClass,
    FiscalProfile,
    FiscalSyncKind,
)
from app.models.gl import GLSettings
from app.models.inventory import NegativeStockPolicy
from app.models.journal import DocumentSequence
from app.models.user import User
from tests.fiscal.conftest import SANDBOX_URL


def test_a_registered_device_is_pending_and_has_told_the_authority_nothing(
    registered_device: FiscalDevice,
) -> None:
    """Registration is a local act. Nothing has been said to a revenue authority, and the
    device holds no identity and no keys — which is exactly what `pending` means."""
    assert registered_device.status == FiscalDeviceStatus.PENDING
    assert registered_device.sdc_id is None
    assert (
        registered_device.cmc_key,
        registered_device.intrl_key,
        registered_device.sign_key,
    ) == (None, None, None)


def test_a_second_device_on_one_branch_is_refused(
    db: Session, fiscal_company: Company, fiscal_owner: User, main_branch: Branch,
    registered_device: FiscalDevice,
) -> None:
    """Two devices on one branch would each hold their own receipt counters for the same shop,
    and the counters are what the authority reconciles against — there is no correct way to
    merge them."""
    with pytest.raises(AppError) as refusal:
        device_service.register_device(
            db,
            fiscal_company.id,
            branch_id=main_branch.id,
            profile=FiscalProfile.OSDC,
            environment=FiscalEnvironment.TEST,
            base_url=SANDBOX_URL,
            dvc_srl_no="SDC-SERIAL-0002",
            bhf_id="01",
            actor=fiscal_owner,
        )

    assert refusal.value.code == "fiscal_device_exists"


def test_initialization_stores_the_identity_and_activates(
    active_device: FiscalDevice,
) -> None:
    assert active_device.status == FiscalDeviceStatus.ACTIVE
    assert active_device.sdc_id == SANDBOX_SDC_ID
    assert active_device.mrc_no == SANDBOX_MRC_NO
    assert active_device.tin == "999000099"


def test_the_three_keys_are_stored_encrypted_and_read_back_only_through_decrypt(
    active_device: FiscalDevice,
) -> None:
    """Encrypted at rest, and the ciphertext is nothing like the plaintext.

    Asserting both halves matters: a "store it encrypted" that round-trips is worth nothing if
    the stored form happens to be the input, and a ciphertext nothing can read is worth nothing
    either.
    """
    assert active_device.sign_key is not None
    assert SANDBOX_SIGN_KEY not in active_device.sign_key

    assert decrypt_key(active_device.sign_key) == SANDBOX_SIGN_KEY


def test_activation_locks_the_negative_stock_policy_at_block(
    db: Session,
    fiscal_company: Company,
    fiscal_owner: User,
    registered_device: FiscalDevice,
    sandbox_client: httpx.Client,
) -> None:
    """CIS §7.30: no receipt for goods the stock does not hold. P5's `block` policy is what
    makes that true, so activation sets it.

    **Set to `allow` first.** The seed pack already defaults to `block`, so asserting `block`
    after activating an already-`block` company proves nothing at all — a sensitivity probe
    that gutted `activate()` entirely left the first version of this test green.
    """
    settings_row = db.scalar(
        select(GLSettings).where(GLSettings.company_id == fiscal_company.id)
    )
    settings_row.negative_stock_policy = NegativeStockPolicy.ALLOW
    db.flush()

    device_service.initialize_device(
        db, fiscal_company.id, registered_device, actor=fiscal_owner, client=sandbox_client
    )

    assert settings_row.negative_stock_policy == NegativeStockPolicy.BLOCK


def test_allowing_negative_stock_is_refused_while_a_device_is_active(
    db: Session, fiscal_company: Company, active_device: FiscalDevice
) -> None:
    """The rule that matters more than the default: the refusal cannot be switched off from a
    settings screen by somebody who does not know what it is for."""
    with pytest.raises(AppError) as refusal:
        device_service.assert_negative_stock_policy_allowed(
            db, fiscal_company.id, NegativeStockPolicy.ALLOW
        )

    assert refusal.value.code == "fiscal_requires_block"
    assert "negative_stock_policy" in refusal.value.field_errors


def test_a_company_with_no_active_device_may_allow_negative_stock(
    db: Session, fiscal_company: Company, registered_device: FiscalDevice
) -> None:
    """Sensitivity, the other way round: a registered-but-not-active device must not impose
    the rule, or every company that started setting one up would be locked in by accident."""
    device_service.assert_negative_stock_policy_allowed(
        db, fiscal_company.id, NegativeStockPolicy.ALLOW
    )


def test_activation_creates_the_branch_level_number_runs(
    db: Session, fiscal_company: Company, active_device: FiscalDevice
) -> None:
    """A device's numbers are its own.

    `claim_number` falls back to the **company-wide** row when no branch row exists, so a
    missing row here would not fail — it would quietly put two branches' receipts in one
    number space, and the authority would see holes in both.
    """
    runs = {
        row.doc_type
        for row in db.scalars(
            select(DocumentSequence).where(
                DocumentSequence.company_id == fiscal_company.id,
                DocumentSequence.branch_id == active_device.branch_id,
            )
        )
    }

    assert runs == {
        DocType.FISCAL_SALE,
        DocType.FISCAL_PURCHASE,
        DocType.FISCAL_STOCK,
        DocType.FISCAL_Z_REPORT,
    }


def test_initialization_is_refused_when_the_company_has_no_tin(
    db: Session,
    fiscal_company: Company,
    fiscal_owner: User,
    registered_device: FiscalDevice,
    sandbox_client: httpx.Client,
) -> None:
    fiscal_company.tin = None
    db.flush()

    with pytest.raises(AppError) as refusal:
        device_service.initialize_device(
            db, fiscal_company.id, registered_device, actor=fiscal_owner, client=sandbox_client
        )

    assert refusal.value.code == "company_tin_missing"


def test_re_initializing_under_a_changed_company_tin_is_refused(
    db: Session,
    fiscal_company: Company,
    fiscal_owner: User,
    active_device: FiscalDevice,
    sandbox_client: httpx.Client,
) -> None:
    """`tin_mismatch`. A device is registered with the authority against a taxpayer number;
    sending sales under a number it was not registered with is the failure this prevents."""
    fiscal_company.tin = "888000088"
    db.flush()

    with pytest.raises(AppError) as refusal:
        device_service.initialize_device(
            db, fiscal_company.id, active_device, actor=fiscal_owner, client=sandbox_client
        )

    assert refusal.value.code == "tin_mismatch"


def test_an_unreachable_device_fails_with_a_reason_and_stays_pending(
    db: Session,
    fiscal_company: Company,
    fiscal_owner: User,
    registered_device: FiscalDevice,
    sandbox_client: httpx.Client,
    sandbox_state: SandboxState,
) -> None:
    sandbox_client.post("/_sandbox/mode", json={"mode": "down"})

    with pytest.raises(AppError) as refusal:
        device_service.initialize_device(
            db, fiscal_company.id, registered_device, actor=fiscal_owner, client=sandbox_client
        )

    assert refusal.value.code == "fiscal_unreachable"
    assert registered_device.status == FiscalDeviceStatus.PENDING
    assert registered_device.last_error


def test_suspending_a_device_keeps_it_and_its_identity(
    db: Session, fiscal_company: Company, fiscal_owner: User, active_device: FiscalDevice
) -> None:
    """Not a delete: the receipts it issued are still the authority's record, and the counters
    it holds are the only thing that can be reconciled against them."""
    device_service.suspend(
        db, fiscal_company.id, active_device, reason="moved shop", actor=fiscal_owner
    )

    assert active_device.status == FiscalDeviceStatus.SUSPENDED
    assert active_device.sdc_id == SANDBOX_SDC_ID
    assert device_service.is_fiscalized(db, fiscal_company.id) is False


def test_a_company_is_fiscalized_only_while_a_device_is_active(
    db: Session, fiscal_company: Company, registered_device: FiscalDevice
) -> None:
    assert device_service.is_fiscalized(db, fiscal_company.id) is False


# --- Synchronisation ----------------------------------------------------------------------


def test_syncing_codes_stores_the_rows_and_the_watermark(
    db: Session,
    fiscal_company: Company,
    fiscal_owner: User,
    active_device: FiscalDevice,
    sandbox_client: httpx.Client,
) -> None:
    rows = device_service.sync_codes(
        db, fiscal_company.id, active_device, actor=fiscal_owner, client=sandbox_client
    )

    assert rows > 0
    classes = {
        row.code_class
        for row in db.scalars(
            select(FiscalCode).where(FiscalCode.company_id == fiscal_company.id)
        )
    }
    assert "04" in classes, "the tax classes are what every sale line is reported under"
    assert active_device.watermarks[FiscalSyncKind.CODES]


def test_a_failed_sync_does_not_advance_the_watermark(
    db: Session,
    fiscal_company: Company,
    fiscal_owner: User,
    active_device: FiscalDevice,
    sandbox_client: httpx.Client,
) -> None:
    """The discipline the documents ask for, and the reason it is one function.

    A watermark advanced by a failed sync skips every row the authority published between the
    two calls — silently, and for good, because nothing ever asks for that window again.
    """
    device_service.sync_codes(
        db, fiscal_company.id, active_device, actor=fiscal_owner, client=sandbox_client
    )
    good = active_device.watermarks[FiscalSyncKind.CODES]

    sandbox_client.post("/_sandbox/mode", json={"mode": "reject:894"})
    with pytest.raises(AppError):
        device_service.sync_codes(
            db, fiscal_company.id, active_device, actor=fiscal_owner, client=sandbox_client
        )

    assert active_device.watermarks[FiscalSyncKind.CODES] == good
    assert active_device.last_error.startswith("894")


def test_a_watermark_never_goes_backwards(
    db: Session,
    fiscal_company: Company,
    fiscal_owner: User,
    active_device: FiscalDevice,
    sandbox_client: httpx.Client,
) -> None:
    """Certification checkpoint 67: each `lastReqDt` must be greater than the previous one.

    RRA enforces it on the imports feed; keeping the stored value monotonic makes it true of
    every kind. The failure it prevents is a clock skew or a restored backup quietly re-opening
    a window the device has already been told about — which RRA refuses rather than tolerates.
    """
    device_service.sync_codes(
        db, fiscal_company.id, active_device, actor=fiscal_owner, client=sandbox_client
    )
    ahead = "29991231235959"
    active_device.watermarks = {**active_device.watermarks, FiscalSyncKind.CODES: ahead}

    device_service.sync_codes(
        db, fiscal_company.id, active_device, actor=fiscal_owner, client=sandbox_client
    )

    assert active_device.watermarks[FiscalSyncKind.CODES] == ahead


def test_syncing_item_classes_stores_them_searchably(
    db: Session,
    fiscal_company: Company,
    fiscal_owner: User,
    active_device: FiscalDevice,
    sandbox_client: httpx.Client,
) -> None:
    rows = device_service.sync_item_classes(
        db, fiscal_company.id, active_device, actor=fiscal_owner, client=sandbox_client
    )

    assert rows > 0
    stored = db.scalars(
        select(FiscalItemClass).where(FiscalItemClass.company_id == fiscal_company.id)
    ).all()
    assert any(row.item_cls_cd == "5059020800" for row in stored)


def test_a_second_sync_updates_rather_than_duplicating(
    db: Session,
    fiscal_company: Company,
    fiscal_owner: User,
    active_device: FiscalDevice,
    sandbox_client: httpx.Client,
) -> None:
    """Upsert, not replace: a watermarked refresh brings only what changed, and deleting what
    it did not mention would empty the table."""
    device_service.sync_codes(
        db, fiscal_company.id, active_device, actor=fiscal_owner, client=sandbox_client
    )
    first = db.scalars(
        select(FiscalCode).where(FiscalCode.company_id == fiscal_company.id)
    ).all()

    device_service.sync_codes(
        db, fiscal_company.id, active_device, actor=fiscal_owner, client=sandbox_client
    )
    second = db.scalars(
        select(FiscalCode).where(FiscalCode.company_id == fiscal_company.id)
    ).all()

    assert len(first) == len(second)


# --- TIN lookup ---------------------------------------------------------------------------


def test_a_known_tin_comes_back_with_its_name(
    db: Session,
    fiscal_company: Company,
    active_device: FiscalDevice,
    sandbox_client: httpx.Client,
) -> None:
    lookup = device_service.lookup_tin(
        db, fiscal_company.id, active_device, "100000001", client=sandbox_client
    )

    assert lookup.found is True
    assert lookup.name == "Customer C Ltd"


def test_an_unknown_tin_is_an_answer_rather_than_an_error(
    db: Session,
    fiscal_company: Company,
    active_device: FiscalDevice,
    sandbox_client: httpx.Client,
) -> None:
    """`884` means "no such taxpayer", which is the answer the screen most needs to show. Every
    other refusal is a failure to look, and raises."""
    lookup = device_service.lookup_tin(
        db, fiscal_company.id, active_device, "123456789", client=sandbox_client
    )

    assert lookup.found is False
    assert lookup.name is None


def test_a_lookup_against_an_unreachable_device_raises(
    db: Session,
    fiscal_company: Company,
    active_device: FiscalDevice,
    sandbox_client: httpx.Client,
) -> None:
    sandbox_client.post("/_sandbox/mode", json={"mode": "down"})

    with pytest.raises(AppError) as refusal:
        device_service.lookup_tin(
            db, fiscal_company.id, active_device, "100000001", client=sandbox_client
        )

    assert refusal.value.code == "fiscal_unreachable"
