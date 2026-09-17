"""Item registration (decision 8) — one authority-side item per Vinea item per company.

Two rules, and the second is the one worth reading twice.

**The code is minted once and never re-minted.** `item_cd` encodes origin, product type,
packaging unit and quantity unit around a gapless sequence, and the authority keys every
receipt line by it. Changing any of those on the Vinea item re-registers *the same code* with
new attributes; it does not mint a second one, because a second code would orphan every
receipt already issued against the first. The composition itself belongs to the adapter — the
format is the authority's — and what this module supplies is the sequence.

**Registration is queued, not called.** An `item` row goes into the device's outbox on first
fiscal use and whenever the registered fields change, and it sits *ahead of the sale that needs
it* because creation order is queue order. That is why the hook runs before the sale is
enqueued and not beside it: RRA rejects a sale naming an item it has never been told about, and
FIFO is what makes "told about it first" true without anybody sequencing calls by hand.

The change detector is a hash of exactly the registered fields. A rename re-registers; an
account change does not, because the authority was never told the account.
"""

import hashlib
import json
from dataclasses import asdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fiscal import outbox
from app.fiscal.mapping import FiscalItemRegistration
from app.fiscal.protocol import FiscalizationAdapter
from app.kernel.sequences import DocType, claim_number
from app.models.fiscalization import (
    FiscalDevice,
    FiscalItem,
    FiscalItemTypeCode,
    FiscalOutboxKind,
    FiscalTaxType,
)
from app.models.inventory import Item, ItemBarcode, ItemType
from app.models.user import User

#: What an item registers as when nothing on the catalogue row says otherwise. Defaults rather
#: than required fields: a Rwandan company selling finished goods out of an unpackaged bin is
#: the ordinary case, and asking every item for both would be two clicks per item to say what
#: is already true. `fiscal_class_code` has **no** default and is refused instead — a class is
#: a claim about what the thing is, and the authority reports on it.
DEFAULT_ORIGIN_COUNTRY = "RW"
DEFAULT_PACKAGE_UNIT = "NT"

#: Vinea's item type → the authority's product type, when the item does not override it.
#: A stock item is a finished product; a service and a non-stock charge are services. A kit is
#: a finished product too: decision 3 sends the kit's parent line and never its components, so
#: what the authority registers is the bundle as sold.
DEFAULT_ITEM_TYPE: dict[ItemType, FiscalItemTypeCode] = {
    ItemType.STOCK: FiscalItemTypeCode.FINISHED_PRODUCT,
    ItemType.KIT: FiscalItemTypeCode.FINISHED_PRODUCT,
    ItemType.SERVICE: FiscalItemTypeCode.SERVICE,
    ItemType.NON_STOCK: FiscalItemTypeCode.SERVICE,
}


def registration_hash(registration: FiscalItemRegistration) -> str:
    """SHA-256 over exactly the fields the authority holds.

    The actor is excluded on purpose: who keyed the change is not part of what RRA knows about
    the item, and including it would re-register every item whenever a different person touched
    one.
    """
    fields = {
        key: str(value)
        for key, value in sorted(asdict(registration).items())
        if key not in {"actor_id", "actor_name"}
    }
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def first_barcode(db: Session, company_id: int, item_id: int) -> str | None:
    """The item's first active barcode, or nothing.

    First by id, which is the order they were added: a stable choice matters more than a clever
    one, because the value is part of the registration hash and a barcode that moved would
    re-register the item every time the list was re-read.
    """
    return db.scalar(
        select(ItemBarcode.barcode)
        .where(
            ItemBarcode.company_id == company_id,
            ItemBarcode.item_id == item_id,
            ItemBarcode.is_active.is_(True),
        )
        .order_by(ItemBarcode.id)
        .limit(1)
    )


def build_registration(
    db: Session,
    company_id: int,
    *,
    item: Item,
    quantity_unit: str,
    tax_class: FiscalTaxType,
    item_code: str,
    price_inclusive: Decimal,
    actor: User | None = None,
) -> FiscalItemRegistration:
    return FiscalItemRegistration(
        item_code=item_code,
        item_class_code=item.fiscal_class_code or "",
        name=item.name,
        item_type=item.fiscal_item_type or DEFAULT_ITEM_TYPE[item.item_type],
        origin_country=item.fiscal_origin_country or DEFAULT_ORIGIN_COUNTRY,
        package_unit=item.fiscal_package_unit or DEFAULT_PACKAGE_UNIT,
        quantity_unit=quantity_unit,
        tax_class=tax_class,
        default_price_inclusive=price_inclusive,
        barcode=first_barcode(db, company_id, item.id),
        active=item.is_active,
        actor_id=str(actor.id) if actor is not None else "",
        actor_name=actor.email if actor is not None else "",
    )


def get_fiscal_item(db: Session, company_id: int, item_id: int) -> FiscalItem | None:
    return db.scalar(
        select(FiscalItem).where(
            FiscalItem.company_id == company_id, FiscalItem.item_id == item_id
        )
    )


def ensure_registered(
    db: Session,
    company_id: int,
    *,
    device: FiscalDevice,
    adapter: FiscalizationAdapter,
    item: Item,
    quantity_unit: str,
    tax_class: FiscalTaxType,
    price_inclusive: Decimal,
    actor: User | None = None,
) -> FiscalItem:
    """The item as the authority should hold it, queueing a registration when it differs.

    Returns the `fiscal_items` row whether or not anything was queued — the caller needs its
    `item_cd` and `item_cls_cd` for the sale line either way.
    """
    row = get_fiscal_item(db, company_id, item.id)
    if row is None:
        # The **item-code sequence is company-wide** (decision 5): an item is registered once
        # for the taxpayer, not once per shop, so no branch is passed here even though every
        # other fiscal run this device owns is branch-scoped.
        claimed = claim_number(db, company_id, DocType.FISCAL_ITEM)
        item_code = adapter.mint_item_code(
            origin_country=item.fiscal_origin_country or DEFAULT_ORIGIN_COUNTRY,
            product_type=str(item.fiscal_item_type or DEFAULT_ITEM_TYPE[item.item_type]),
            packaging_unit=item.fiscal_package_unit or DEFAULT_PACKAGE_UNIT,
            quantity_unit=quantity_unit,
            sequence_no=claimed.sequence_no,
        )
        row = FiscalItem(
            company_id=company_id,
            item_id=item.id,
            item_cd=item_code,
            item_cls_cd=item.fiscal_class_code or "",
            item_ty_cd=item.fiscal_item_type or DEFAULT_ITEM_TYPE[item.item_type],
            orgn_nat_cd=item.fiscal_origin_country or DEFAULT_ORIGIN_COUNTRY,
            pkg_unit_cd=item.fiscal_package_unit or DEFAULT_PACKAGE_UNIT,
            qty_unit_cd=quantity_unit,
            tax_ty_cd=tax_class,
            dft_prc=price_inclusive,
            use_yn=item.is_active,
        )
        db.add(row)
        db.flush()

    registration = build_registration(
        db,
        company_id,
        item=item,
        quantity_unit=quantity_unit,
        tax_class=tax_class,
        item_code=row.item_cd,
        price_inclusive=price_inclusive,
        actor=actor,
    )
    current = registration_hash(registration)
    if row.last_payload_hash == current:
        return row

    # The stored row follows the registration, not the other way round: what is queued is what
    # the authority is being told, and the row is Vinea's record of having told it.
    row.item_cls_cd = registration.item_class_code
    row.item_ty_cd = registration.item_type
    row.orgn_nat_cd = registration.origin_country
    row.pkg_unit_cd = registration.package_unit
    row.qty_unit_cd = registration.quantity_unit
    row.tax_ty_cd = registration.tax_class
    row.dft_prc = registration.default_price_inclusive
    row.bcd = registration.barcode
    row.use_yn = registration.active
    row.last_payload_hash = current
    db.flush()

    outbox.enqueue(
        db,
        company_id,
        device=device,
        kind=FiscalOutboxKind.ITEM,
        payload=adapter.render(device, FiscalOutboxKind.ITEM, registration),
        source_doc_type="item",
        source_doc_id=item.id,
    )
    return row
