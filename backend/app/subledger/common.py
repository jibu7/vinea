"""Shared subledger plumbing: the audit helper and the role-scoped GL defaults."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.kernel.errors import PostingError
from app.kernel.posting import gl_settings_for
from app.models.gl import ControlType, GLAccount, GLSettings
from app.models.partner import PartnerRole
from app.models.user import User
from app.services.audit import record_audit

CONTROL_TYPE_FOR_ROLE = {PartnerRole.AR: ControlType.AR, PartnerRole.AP: ControlType.AP}
PARTNER_TYPE_FOR_ROLE = {PartnerRole.AR: "customer", PartnerRole.AP: "supplier"}


def audit(
    db: Session,
    company_id: int,
    action: str,
    entity: str,
    entity_id: int,
    *,
    actor: User,
    before: dict | None = None,
    after: dict | None = None,
    request: Request | None = None,
) -> None:
    record_audit(
        db,
        company_id=company_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=before,
        after=after,
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )


@dataclass(frozen=True)
class RoleAccounts:
    """The GL accounts a role posts against, resolved once per document/allocation."""

    control_account_id: int
    discount_account_id: int | None
    post_dated_account_id: int | None
    fx_gain_account_id: int | None
    fx_loss_account_id: int | None
    rounding_account_id: int | None


def _required(settings: GLSettings, field: str, label: str) -> int:
    value = getattr(settings, field)
    if value is None:
        raise PostingError(
            f"Set the {label} in AR/AP defaults before posting",
            code="gl_setting_missing",
            field_errors={field: ["required"]},
        )
    return int(value)


#: Which way a role's exposure points on its control account. An AR invoice debits the
#: customer (+1); a supplier invoice credits the supplier (-1). Balances and open items are
#: signed by the control account's side, so this is the turn that puts both roles into their
#: own sense — what the customer owes us, what we owe the supplier — and both the posting-time
#: credit-limit check and the enquiry's headroom go through it.
#:
#: It must agree with `DOCUMENT_MATRIX[(role, INVOICE)].direction`, which is the authoritative
#: statement of the same fact; `test_exposure_direction_matches_the_invoice_row` holds them
#: together. Declared here rather than derived from the matrix because `documents` imports
#: this module, not the other way round.
_EXPOSURE_DIRECTION: dict[PartnerRole, int] = {PartnerRole.AR: 1, PartnerRole.AP: -1}


def exposure_direction(role: PartnerRole) -> int:
    return _EXPOSURE_DIRECTION[role]


def role_accounts(db: Session, company_id: int, role: PartnerRole) -> RoleAccounts:
    settings = gl_settings_for(db, company_id)
    if role == PartnerRole.AR:
        return RoleAccounts(
            control_account_id=_required(settings, "ar_control_account_id", "AR control account"),
            discount_account_id=settings.settlement_discount_granted_account_id,
            post_dated_account_id=settings.post_dated_receivable_account_id,
            fx_gain_account_id=settings.realized_fx_gain_account_id,
            fx_loss_account_id=settings.realized_fx_loss_account_id,
            rounding_account_id=settings.rounding_difference_account_id,
        )
    return RoleAccounts(
        control_account_id=_required(settings, "ap_control_account_id", "AP control account"),
        discount_account_id=settings.settlement_discount_received_account_id,
        post_dated_account_id=settings.post_dated_payable_account_id,
        fx_gain_account_id=settings.realized_fx_gain_account_id,
        fx_loss_account_id=settings.realized_fx_loss_account_id,
        rounding_account_id=settings.rounding_difference_account_id,
    )


def control_account_for(
    db: Session, company_id: int, role: PartnerRole, override_account_id: int | None
) -> int:
    """A partner may override the company control account; the override must still be a
    control account of the matching type or the subledger stops reconciling."""
    if override_account_id is None:
        return role_accounts(db, company_id, role).control_account_id
    account = db.scalar(
        select(GLAccount).where(
            GLAccount.company_id == company_id, GLAccount.id == override_account_id
        )
    )
    expected = CONTROL_TYPE_FOR_ROLE[role]
    if account is None or account.control_type != expected:
        raise PostingError(
            f"The control account override must be a {expected.value.upper()} control account",
            code="invalid_control_account",
            field_errors={"control_account_id": ["not an AR/AP control account"]},
        )
    return account.id


def transaction_type_names(db: Session, company_id: int, role: PartnerRole) -> dict[str, str]:
    """`{code: name}` for a role's module, so a read can label documents by what they *are*.

    A journal debit is invoice-shaped, so `kind` would call it "Customer invoice". Every
    user-facing surface reads the transaction type instead — and so must any figure derived
    from sales or purchases, which is why documents store the code rather than deriving it.
    """
    from app.models.gl import GLTransactionType

    return {
        row.code: row.name
        for row in db.scalars(
            select(GLTransactionType).where(
                GLTransactionType.company_id == company_id,
                GLTransactionType.module == role.value,
            )
        )
    }
