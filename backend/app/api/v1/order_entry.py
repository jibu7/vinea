"""Order Entry (P6).

Step 1 serves the **Order defaults** screen and nothing else: the orders, the GRN, the match
and the landed-cost document arrive with the steps that build them. The router exists now so
the settings the rest of the phase reads have a home from the start.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_db, get_tenant_context
from app.core import permissions
from app.core.errors import PermissionDeniedError
from app.order_entry import masters
from app.schemas.order_entry import OrderDefaultsRead, OrderDefaultsUpdate

router = APIRouter(prefix="/oe", tags=["order-entry"])

#: Reading the defaults opens to anyone who may look at an order-entry screen at all; writing
#: them is `oe:setup_manage`, the same split every other module's defaults use.
VIEW_PERMISSIONS = (
    permissions.OE_SETUP_MANAGE,
    permissions.OE_SALES_ORDERS_MANAGE,
    permissions.OE_PURCHASE_ORDERS_MANAGE,
    permissions.OE_GRV_PROCESS,
    permissions.OE_REPORTS_VIEW,
)


def _require_view(auth: AuthContext) -> None:
    if not any(permission in auth.permissions for permission in VIEW_PERMISSIONS):
        raise PermissionDeniedError(
            f"Missing required permission(s): {' or '.join(VIEW_PERMISSIONS)}"
        )


@router.get("/defaults")
def get_defaults(
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> OrderDefaultsRead:
    _require_view(auth)
    return OrderDefaultsRead.model_validate(masters.order_defaults(db, auth.company_id))


@router.put("/defaults")
def update_defaults(
    payload: OrderDefaultsUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.OE_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> OrderDefaultsRead:
    settings = masters.update_order_defaults(
        db,
        auth.company_id,
        payload.model_dump(exclude_unset=True),
        actor=auth.user,
        request=request,
    )
    db.commit()
    return OrderDefaultsRead.model_validate(settings)
