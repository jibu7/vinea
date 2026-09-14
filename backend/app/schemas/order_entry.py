"""Order-entry API shapes (P6).

Step 1 carries the **Order defaults** screen only. The order, GRN and landed-cost documents
arrive with the services that write them.
"""

from pydantic import BaseModel

from app.models.gl import BackorderPolicy
from app.schemas.common import ApiModel


class OrderDefaultsRead(ApiModel):
    grn_accrual_account_id: int | None
    purchase_price_variance_account_id: int | None
    landed_cost_clearing_account_id: int | None
    backorder_policy: BackorderPolicy
    #: Read-only here. Sales and purchase orders default their warehouse from it, but it is
    #: the *inventory* default and the Inventory defaults screen owns writing it — two
    #: screens writing one key is how they come to disagree.
    default_warehouse_id: int | None


class OrderDefaultsUpdate(BaseModel):
    grn_accrual_account_id: int | None = None
    purchase_price_variance_account_id: int | None = None
    landed_cost_clearing_account_id: int | None = None
    backorder_policy: BackorderPolicy | None = None
