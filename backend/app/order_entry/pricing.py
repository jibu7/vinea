"""The catalogue price, converted to suit the document it lands on (P6 decision 1).

`items.price_includes_tax` is a fact about the **catalogue**: it says whether the 2 000 written
against a bottle already has VAT in it. `tax_mode` is a fact about the **document**: it says
whether the amounts keyed on this invoice are net or gross. The two are independent, and where
they disagree the price has to be converted before it becomes a line — otherwise an inclusive
catalogue sold on an exclusive document charges the tax twice, and an exclusive catalogue sold
on an inclusive one under-charges it by the same proportion.

One helper, called by both the order service and the partner-document service, because the
alternative is two implementations that have to keep agreeing about tax forever. A price the
operator keyed is never touched: they typed what the document asked for.

The conversion rounds to six decimals — the scale of `unit_price` itself — and not to the
currency's. Rounding a converted unit price to whole francs first and multiplying afterwards
loses up to half a franc *per unit*: 2 000 inclusive at 18 % is 1 694.915254 exclusive, and a
line of 40 priced at a rounded 1 695 grosses 80 001 where the catalogue says 80 000. The money
rounding happens once, on the line total, exactly as it does for a keyed price.
"""

from decimal import Decimal, localcontext

from sqlalchemy.orm import Session

from app.kernel.money import MONEY_PRECISION, resolve_tax_code, round_amount
from app.models.inventory import Item
from app.models.partner import TaxMode

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)
#: `unit_price` is NUMERIC(20, 6) everywhere it is stored.
PRICE_SCALE = 6


def catalogue_unit_price(
    db: Session,
    company_id: int,
    item: Item,
    *,
    tax_mode: TaxMode,
    tax_code_id: int | None,
    on_date,  # noqa: ANN001 - date; loose to avoid an import cycle through the document service
) -> Decimal:
    """`items.selling_price`, stated the way `tax_mode` wants it.

    With no tax code there is no rate to convert by and the price is taken as written — which
    is correct rather than a fallback: a zero-rated or exempt line's inclusive and exclusive
    prices are the same number.
    """
    price = item.selling_price
    if price == ZERO or tax_code_id is None:
        return price
    inclusive_catalogue = bool(item.price_includes_tax)
    inclusive_document = tax_mode == TaxMode.INCLUSIVE
    if inclusive_catalogue == inclusive_document:
        return price
    rate_pct = resolve_tax_code(db, company_id, tax_code_id, on_date).rate_pct
    if rate_pct == ZERO:
        return price
    with localcontext() as ctx:
        ctx.prec = MONEY_PRECISION
        factor = ONE + rate_pct / HUNDRED
        converted = price * factor if inclusive_document else price / factor
        return round_amount(converted, PRICE_SCALE)
