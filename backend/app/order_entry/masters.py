"""Order-entry settings — the **Order defaults** screen (P6 decision 10).

There is no second settings store. The four keys this phase adds live on the same
`gl_settings` row every other module default lives on, and this module is the order-entry
view of it, exactly as `app.inventory.masters.inventory_defaults` is the inventory one.

The validation rules differ between the three accounts, and the difference is the phase's
design rather than an oversight:

* **GRN accrual** must be a `grn_accrual` control account. Only `inv` and `ap` may post to
  it and every line on it carries an item, and both of those guarantees come from the
  control type — pointing this key at an ordinary account would give order entry an
  unguarded account to accrue into, and the accrual proof would stop being provable.
* **Purchase price variance** must be an ordinary postable account, and specifically *not*
  the accrual: PPV is the difference left over when the two disagree, so an entry with both
  legs on the accrual would move nothing and hide the variance it exists to expose.
* **Landed cost clearing** must be an ordinary postable account too, and deliberately not a
  control account of any kind. Freight arrives on a forwarder's supplier invoice as a GL
  line and duty as a cashbook payment; a control account would refuse both, and the account
  would be impossible to get money into.
"""

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.kernel.errors import LedgerStateError
from app.kernel.posting import gl_settings_for
from app.models.gl import BackorderPolicy, ControlType, GLAccount, GLSettings
from app.models.user import User
from app.services.audit import record_audit

#: Must carry `ControlType.GRN_ACCRUAL`.
ORDER_CONTROL_SETTINGS = ("grn_accrual_account_id",)
#: Must be ordinary postable accounts.
ORDER_CONTRA_SETTINGS = (
    "purchase_price_variance_account_id",
    "landed_cost_clearing_account_id",
)
ORDER_ACCOUNT_SETTINGS = (*ORDER_CONTROL_SETTINGS, *ORDER_CONTRA_SETTINGS)
#: Everything the Order defaults screen may write.
ORDER_SETTINGS_FIELDS = (*ORDER_ACCOUNT_SETTINGS, "backorder_policy", "default_warehouse_id")


def order_defaults(db: Session, company_id: int) -> GLSettings:
    return gl_settings_for(db, company_id)


def update_order_defaults(
    db: Session,
    company_id: int,
    changes: dict[str, object],
    *,
    actor: User,
    request: Request | None = None,
) -> GLSettings:
    """Set the order-entry keys on the one settings row. Only keys present in `changes` are
    touched, and clearing an account is refused with `required_setting`.

    That refusal is P4's reasoning, unchanged and now three phases old: a NULL here does not
    fail at this call, it fails at whichever posting next needs it — the first GRN, or the
    first match that has a variance — long after the operator who cleared it has gone.

    `default_warehouse_id` is **read** by sales and purchase orders as their line default but
    is not written here: it is the inventory default, it has a screen of its own, and two
    screens writing one key is how they come to disagree.
    """
    settings = gl_settings_for(db, company_id)
    unknown = set(changes) - {*ORDER_ACCOUNT_SETTINGS, "backorder_policy"}
    if unknown:
        raise LedgerStateError(
            f"Not an order-entry default: {', '.join(sorted(unknown))}",
            code="unknown_gl_setting",
        )
    before = {field: _value(settings, field) for field in ORDER_SETTINGS_FIELDS}

    for field in ORDER_ACCOUNT_SETTINGS:
        if field not in changes:
            continue
        account_id = changes[field]
        if account_id is None:
            raise LedgerStateError(
                f"The {field.removesuffix('_id').replace('_', ' ')} is required — "
                "order entry cannot post without it",
                code="required_setting",
                field_errors={field: ["required"]},
            )
        if field in ORDER_CONTROL_SETTINGS:
            _assert_grn_accrual_account(db, company_id, int(account_id), field)
        else:
            _assert_postable_contra(db, company_id, int(account_id), field)
        setattr(settings, field, int(account_id))

    policy = changes.get("backorder_policy")
    if policy is not None:
        settings.backorder_policy = BackorderPolicy(policy)

    db.flush()
    after = {field: _value(settings, field) for field in ORDER_SETTINGS_FIELDS}
    if after != before:
        record_audit(
            db,
            company_id=company_id,
            action="order_defaults.updated",
            entity="gl_settings",
            entity_id=settings.id,
            before=before,
            after=after,
            # A real actor, never a placeholder — the rule every audited call in the
            # product follows.
            actor_user_id=actor.id,
            actor_email=actor.email,
            request=request,
        )
    return settings


def _value(settings: GLSettings, field: str) -> object:
    value = getattr(settings, field)
    return value.value if isinstance(value, BackorderPolicy) else value


def _get_account(db: Session, company_id: int, account_id: int) -> GLAccount:
    account = db.scalar(
        select(GLAccount).where(GLAccount.company_id == company_id, GLAccount.id == account_id)
    )
    if account is None:
        raise NotFoundError("Account not found")
    return account


def _assert_grn_accrual_account(
    db: Session, company_id: int, account_id: int, field: str
) -> None:
    account = _get_account(db, company_id, account_id)
    if account.control_type != ControlType.GRN_ACCRUAL:
        raise LedgerStateError(
            f"Account {account.code} is not a GRN accrual control account",
            code="invalid_grn_accrual_account",
            field_errors={field: ["not a GRN accrual control account"]},
        )


def _assert_postable_contra(
    db: Session, company_id: int, account_id: int, field: str
) -> None:
    account = _get_account(db, company_id, account_id)
    if not account.is_postable or not account.is_active:
        raise LedgerStateError(
            f"Account {account.code} is not an active postable account",
            code="account_not_postable",
            field_errors={field: ["not an active postable account"]},
        )
    if account.control_type is not None:
        # Freight reaches the clearing account on a supplier invoice and duty on a cashbook
        # payment; a control account of any kind would refuse both, and PPV on the accrual
        # would post an entry that moves nothing.
        raise LedgerStateError(
            f"Account {account.code} is a control account",
            code="contra_is_a_control_account",
            field_errors={field: ["cannot be a control account"]},
        )
