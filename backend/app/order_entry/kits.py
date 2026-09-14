"""Kit explosion and Breakup (P6 decision 8).

A **kit is a virtual bundle, not a manufactured thing.** It has no moves, is never received,
and `kit_not_purchasable` refuses it on every AP path. What a kit line *is*, wherever it
appears, is two things at once: a parent line carrying the kit item, its quantity, its price
and its tax — the revenue — and the component lines it explodes into, which carry quantity and
no money and are where commitment and cost actually happen.

**The explosion happens at line entry**, and that is the whole of why `item_kit_components` is
a definition rather than a posting. An order records what was promised on the day it was taken.
Editing the catalogue afterwards changes what the *next* kit line explodes into and restates
nothing — no order is rewritten, no invoice is re-costed, and an auditor reading an old order
sees the bundle as it was sold.

**Breakup** is the screen that edits one order's explosion against the definition's defaults —
substituting a component, changing a quantity — and `sales_order_lines.kit_breakup_edited`
records that it happened. That flag is not a total and not a cache: it is the one fact no
arithmetic can recover, because an explosion that happens to match the current definition and
one that was edited to match it are indistinguishable from the rows alone, and the definition
itself moves.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import masters as inventory_masters
from app.kernel.errors import LedgerStateError
from app.models.inventory import Item, ItemKitComponent, ItemType

ZERO = Decimal(0)


@dataclass(frozen=True)
class ComponentInput:
    """One component of one kit line, as a person keyed it on Breakup.

    `base_quantity` is the **total** for the line, not a per-kit rate: Breakup edits what this
    order ships, and asking an operator to think in rates when they are looking at a delivery
    is how the wrong number gets typed.
    """

    item_id: int
    base_quantity: Decimal


@dataclass(frozen=True)
class ExplodedComponent:
    item: Item
    #: In the component item's base unit — kit quantity x `quantity_per_kit`.
    base_quantity: Decimal
    line_order: int


def is_kit(item: Item) -> bool:
    return item.item_type == ItemType.KIT


def definition(db: Session, company_id: int, kit_item_id: int) -> list[ItemKitComponent]:
    return list(
        db.scalars(
            select(ItemKitComponent)
            .where(
                ItemKitComponent.company_id == company_id,
                ItemKitComponent.kit_item_id == kit_item_id,
            )
            .order_by(ItemKitComponent.line_no)
        )
    )


def explode(
    db: Session,
    company_id: int,
    kit: Item,
    kit_base_quantity: Decimal,
    *,
    field_prefix: str,
) -> list[ExplodedComponent]:
    """The kit's definition, scaled to this line's quantity.

    `quantity_per_kit` is stored in the **component's own base unit**, so this is a
    multiplication and never a unit conversion — which is what keeps the arithmetic exact at a
    scale where a conversion factor would not be.

    A kit with no components is refused (`kit_without_components`) rather than exploded into
    nothing. A line that moved no stock and carried the kit's whole price would post revenue
    with no cost against it, and it would look exactly like a correctly sold kit on every
    screen in the product.
    """
    rows = definition(db, company_id, kit.id)
    if not rows:
        raise LedgerStateError(
            f"{kit.code} is a kit with no components defined",
            code="kit_without_components",
            field_errors={f"{field_prefix}.item_id": ["the kit has no components"]},
        )
    exploded: list[ExplodedComponent] = []
    for index, row in enumerate(rows, 1):
        component = inventory_masters.get_item(db, company_id, row.component_item_id)
        exploded.append(
            ExplodedComponent(
                item=component,
                base_quantity=kit_base_quantity * row.quantity_per_kit,
                line_order=index,
            )
        )
    return exploded


def resolve_breakup(
    db: Session,
    company_id: int,
    kit: Item,
    components: tuple[ComponentInput, ...],
    *,
    field_prefix: str = "components",
) -> list[ExplodedComponent]:
    """Validate a hand-edited explosion and turn it into the same shape `explode` produces.

    Substitution is allowed — the definition supplies the *defaults*, and a depot that is out
    of one bottle and ships another has not sold a different kit. What is refused is a
    component that is itself a kit (no nesting in v1), the kit item appearing inside its own
    explosion, a duplicate component, and an explosion with nothing in it.
    """
    if not components:
        raise LedgerStateError(
            "A kit line must explode into at least one component",
            code="kit_without_components",
            field_errors={field_prefix: ["at least one component required"]},
        )
    seen: set[int] = set()
    resolved: list[ExplodedComponent] = []
    for index, entry in enumerate(components):
        if entry.base_quantity <= ZERO:
            raise LedgerStateError(
                "A component quantity must be greater than zero",
                code="invalid_quantity",
                field_errors={
                    f"{field_prefix}.{index}.base_quantity": ["must be greater than zero"]
                },
            )
        if entry.item_id == kit.id:
            raise LedgerStateError(
                "A kit cannot contain itself",
                code="kit_is_its_own_component",
                field_errors={f"{field_prefix}.{index}.item_id": ["is this kit"]},
            )
        if entry.item_id in seen:
            raise LedgerStateError(
                "That component is already on this line",
                code="duplicate_kit_component",
                field_errors={f"{field_prefix}.{index}.item_id": ["already on this line"]},
            )
        seen.add(entry.item_id)
        component = inventory_masters.get_item(db, company_id, entry.item_id)
        if component.item_type == ItemType.KIT:
            raise LedgerStateError(
                f"{component.code} is itself a kit — kits do not nest",
                code="nested_kit",
                field_errors={f"{field_prefix}.{index}.item_id": ["is a kit"]},
            )
        if not component.is_active:
            raise LedgerStateError(
                f"{component.code} is not active",
                code="item_not_active",
                field_errors={f"{field_prefix}.{index}.item_id": ["not active"]},
            )
        resolved.append(
            ExplodedComponent(
                item=component, base_quantity=entry.base_quantity, line_order=index + 1
            )
        )
    return resolved
