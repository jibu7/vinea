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
from dataclasses import asdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fiscal import outbox
from app.fiscal.mapping import FiscalItemRegistration
from app.fiscal.protocol import FiscalizationAdapter
from app.kernel.errors import PostingError
from app.kernel.money import fingerprint_material
from app.kernel.sequences import DocType, claim_number
from app.models.fiscalization import (
    FiscalDevice,
    FiscalItem,
    FiscalItemTypeCode,
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalTaxType,
)
from app.models.inventory import Item, ItemBarcode, ItemType, Uom
from app.models.tax import TaxCode
from app.models.user import User

ZERO = Decimal(0)
HUNDRED = Decimal(100)

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

    **Over values, not representations**, which is `fingerprint_material`'s whole job and the
    one thing this hash got wrong. Decimals reach it by two routes that agree about the number
    and disagree about its exponent: computed from the catalogue (`2000.000000 × 1.18` → ten
    decimals) or read back off the `NUMERIC(20,6)` column that stored it (six). A `str()` of
    each tells them apart, so the hash did — and an item was re-registered on the next document
    that happened to arrive by the other route, telling RRA nothing it did not already know.
    Found by the step-3 stock report, which reads the stored row on purpose
    (`ensure_registered_for_report`), and guarded by `tests/test_fingerprints.py`.
    """
    fields = {
        key: value
        for key, value in asdict(registration).items()
        if key not in {"actor_id", "actor_name"}
    }
    return hashlib.sha256(fingerprint_material(fields).encode()).hexdigest()


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


def catalogue_price_inclusive(item: Item, rate_pct: Decimal) -> Decimal:
    """What the authority lists the item at — the **catalogue** price, VAT-inclusive, in base.

    Not the price a line happened to be sold at, and the difference is not cosmetic: the
    registered price is part of the hash that decides whether an item is re-registered, so a
    line price would queue an `item` row on every sale at a new figure. A shop that negotiates
    would spend its queue telling RRA about its own discounts.

    `items.selling_price` is kept in base currency and `price_includes_tax` says which side of
    the tax it is on, so the conversion is the programmed rate applied once.
    """
    if item.price_includes_tax or rate_pct == ZERO:
        return item.selling_price
    return item.selling_price * (HUNDRED + rate_pct) / HUNDRED


def registration_tax_code(db: Session, company_id: int, item: Item) -> TaxCode | None:
    """The tax code an item is *registered* under, when no line supplies one.

    Sales first, purchases second. The authority holds one tax class per item and it is a
    claim about how the thing is sold — so a raw material that is only ever bought falls back
    to its purchase code rather than being refused, and an item with both registers under the
    one a receipt would print.
    """
    for tax_code_id in (item.default_sales_tax_code_id, item.default_purchase_tax_code_id):
        if tax_code_id is None:
            continue
        tax_code = db.get(TaxCode, tax_code_id)
        if tax_code is not None and tax_code.company_id == company_id:
            return tax_code
    return None


def told_about(db: Session, company_id: int, device_id: int, item_id: int) -> bool:
    """Has **this device** been told about this item?

    Read off the outbox, which is the durable record of the conversation, rather than from a
    column: the authority holds items per (taxpayer, branch) — `saveItems` carries `bhfId` —
    while `fiscal_items` holds one registration per *company*, because the item code is
    company-wide and a second code would orphan every receipt issued against the first.

    Without this, a second branch's device would report a stock movement for an item RRA has
    never heard of at that branch, and RRA would refuse the movement. Cancelled rows do not
    count: a cancelled row is one the authority never received.
    """
    return (
        db.scalar(
            select(FiscalOutboxRow.id).where(
                FiscalOutboxRow.company_id == company_id,
                FiscalOutboxRow.device_id == device_id,
                FiscalOutboxRow.kind == FiscalOutboxKind.ITEM,
                FiscalOutboxRow.source_doc_id == item_id,
                FiscalOutboxRow.status != FiscalOutboxStatus.CANCELLED,
            )
        )
        is not None
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
    if row.last_payload_hash == current and told_about(db, company_id, device.id, item.id):
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


def ensure_registered_for_report(
    db: Session,
    company_id: int,
    *,
    device: FiscalDevice,
    adapter: FiscalizationAdapter,
    item: Item,
    actor: User | None = None,
) -> FiscalItem:
    """Register an item the authority has never been told about — and never *re*-register one.

    Used by the purchase and stock reports, which both need an `itemCd` for a line and neither
    of which is a statement about how the item is sold. A **sale** is what changes what the
    authority holds about an item (decision 8's hash is over the registered fields, and the
    price in it is the catalogue price); a stock movement that rewrote the registration would
    put an *input* tax class and a purchase-side price on a catalogue row, and two paths
    disagreeing about what an item is would re-register it on every second document.

    So: if the item already has a `fiscal_items` row, it is returned exactly as it stands —
    except that a device which has never been told about it gets a row, because RRA holds
    items per branch (see `told_about`).
    """
    row = get_fiscal_item(db, company_id, item.id)
    if row is not None:
        # The row's **own stored values**, so this path cannot change what the authority holds:
        # what it can do is queue a registration for a device that has never been told, which
        # is what `told_about` inside `ensure_registered` decides.
        return ensure_registered(
            db,
            company_id,
            device=device,
            adapter=adapter,
            item=item,
            quantity_unit=row.qty_unit_cd,
            tax_class=row.tax_ty_cd,
            price_inclusive=row.dft_prc,
            actor=actor,
        )

    tax_code = registration_tax_code(db, company_id, item)
    if tax_code is None or tax_code.fiscal_tax_type is None:
        raise PostingError(
            f"{item.code} has no tax code with an EBM tax class, so RRA has nothing to hold "
            "it under. Set a default sales or purchase tax code on the item, and an EBM class "
            "(A, B, C or D) on that tax code.",
            code="tax_class_unmapped",
            field_errors={"item_id": ["the item has no tax code with an EBM tax class"]},
        )
    if not item.fiscal_class_code:
        raise PostingError(
            f"{item.code} has no EBM item class. Choose one on the item — RRA holds one item "
            "record per class, and a stock movement is reported against it.",
            code="fiscal_class_missing",
            field_errors={"item_id": ["the item has no EBM item class"]},
        )
    base_uom = db.get(Uom, item.base_uom_id)
    if base_uom is None or not base_uom.fiscal_quantity_unit:
        raise PostingError(
            f"{item.code}'s base unit has no EBM quantity unit. Map the unit on the Units of "
            "measure screen — the authority holds one quantity per item, in the unit it was "
            "registered with.",
            code="fiscal_uom_unmapped",
            field_errors={"uom_id": ["the base unit has no EBM quantity unit"]},
        )
    return ensure_registered(
        db,
        company_id,
        device=device,
        adapter=adapter,
        item=item,
        quantity_unit=base_uom.fiscal_quantity_unit,
        tax_class=tax_code.fiscal_tax_type,
        price_inclusive=catalogue_price_inclusive(item, tax_code.rate_pct),
        actor=actor,
    )


def resync(
    db: Session,
    company_id: int,
    *,
    device: FiscalDevice,
    adapter: FiscalizationAdapter,
    item: Item,
    actor: User | None = None,
) -> FiscalItem | None:
    """Re-register an item the authority already holds, when its registered fields changed.

    This is the other half of decision 8 — "whenever the registered fields change (hash
    differs)" — reached from the Items screen rather than from a sale. **Deactivating an item
    is the case that matters**: `useYn` is a registered field, so switching an item off tells
    the authority to stop accepting it, and an item nobody can sell that RRA still lists is
    exactly the drift the hash exists to prevent.

    `None` when the item was never registered: there is nothing to re-register, and minting a
    code for an item on the way *out* of the catalogue would be the wrong moment to start.
    """
    row = get_fiscal_item(db, company_id, item.id)
    if row is None:
        return None
    tax_code = registration_tax_code(db, company_id, item)
    rate_pct = tax_code.rate_pct if tax_code is not None else ZERO
    return ensure_registered(
        db,
        company_id,
        device=device,
        adapter=adapter,
        item=item,
        # The registration's own stored values, not a re-derivation: what may have changed is
        # what the screen edited, and re-resolving the unit or the class from scratch would
        # let an unrelated master edit re-register the item as a side effect.
        quantity_unit=row.qty_unit_cd,
        tax_class=row.tax_ty_cd,
        price_inclusive=catalogue_price_inclusive(item, rate_pct),
        actor=actor,
    )
