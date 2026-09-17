"""Fiscalization API schemas (P7 step 1).

**No schema here carries a device key**, and that is load-bearing rather than an oversight to
be tidied later: `cmc_key`, `intrl_key` and `sign_key` are the only secrets this phase holds,
and the thing that keeps them out of a response is that no response model has a field for one.
`tests/fiscal/test_key_redaction.py` asserts it over the model rather than over one endpoint's
output, so a second endpoint serialising a device cannot reintroduce it.

`has_keys` is what a screen actually needs — "is this device holding its keys" — and answers it
without going near the values.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.fiscalization import (
    FiscalDeviceStatus,
    FiscalEnvironment,
    FiscalProfile,
)


class DeviceCreate(BaseModel):
    branch_id: int
    profile: FiscalProfile
    environment: FiscalEnvironment = FiscalEnvironment.TEST
    base_url: str = Field(min_length=1, max_length=300)
    #: The serial the owner registered on the authority's portal.
    dvc_srl_no: str = Field(min_length=1, max_length=100)
    #: The authority's branch identifier; the head office is `00`.
    bhf_id: str = Field(default="00", min_length=2, max_length=2)


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    branch_id: int
    profile: FiscalProfile
    environment: FiscalEnvironment
    base_url: str
    tin: str | None
    bhf_id: str
    dvc_srl_no: str
    mrc_no: str | None
    sdc_id: str | None
    dvc_id: str | None
    status: FiscalDeviceStatus
    watermarks: dict[str, str] = Field(default_factory=dict)
    last_success_at: datetime | None
    last_error: str | None
    #: Whether the device holds its three keys — never which, and never their values.
    has_keys: bool = False


class DeviceSuspend(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class DeviceSyncResult(BaseModel):
    """What a sync brought back. The watermark is included because an operator staring at a
    stale code table needs to see what the device thinks it last fetched."""

    device_id: int
    kind: str
    rows: int
    watermark: str | None = None


class TinLookupRead(BaseModel):
    tin: str
    found: bool
    name: str | None = None
    status: str | None = None


def device_read(device) -> DeviceRead:  # noqa: ANN001 - a FiscalDevice; typing it is a cycle
    """The one place a device becomes a response.

    A function rather than `DeviceRead.model_validate(device)` at four call sites, so that
    `has_keys` is computed once — and so that adding a field to the model cannot accidentally
    pick up a column by name.
    """
    payload = DeviceRead.model_validate(device)
    payload.has_keys = bool(device.cmc_key or device.intrl_key or device.sign_key)
    return payload


class DrainResult(BaseModel):
    """What one drain pass did. `rows` counts attempts, `sent` the ones RRA signed — the two
    differ exactly when a device is stuck, which is the number an operator wants."""

    rows: int
    sent: int
    outcomes: list[dict] = []
