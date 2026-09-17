"""Partner documents — one service, six kinds (P4 step 3).

The `(role, kind)` matrix below is the whole of the AR/AP asymmetry: it decides the document
type, the sign the document puts on its control account, and the transaction-type key used
for account determination. Everything else — tax, numbering, credit limits, period
enforcement, reversal — is written once and runs for both roles.
"""

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import ConflictError, NotFoundError
from app.core.permissions import AP_CREDIT_LIMIT_OVERRIDE, AR_CREDIT_LIMIT_OVERRIDE
from app.fiscal import sales as fiscal_sales
from app.inventory import masters as inventory_masters
from app.inventory import stock as stock_service
from app.kernel import posting
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import InstrumentMatured, LineSpec, PartnerDocumentPosted
from app.kernel.money import (
    ZERO,
    base_currency,
    is_rounded,
    rate_on,
    resolve_tax_code,
    round_amount,
    split_tax,
    to_base,
)
from app.kernel.sequences import DocType
from app.models.currency import Currency
from app.models.fiscalization import PaymentMethod
from app.models.gl import CASHBOOK_CONTROL_TYPES, GLAccount
from app.models.inventory import GoodsReceivedNote, GoodsReceivedNoteLine, Item, ItemType
from app.models.journal import JournalEntry
from app.models.partner import Partner, PartnerRole, PaymentTerms, TaxMode
from app.models.subledger import (
    DocumentKind,
    DocumentStatus,
    InstrumentType,
    PartnerDocument,
    PartnerDocumentLine,
)
from app.models.user import User
from app.order_entry import companion as order_companion
from app.order_entry import kits as order_kits
from app.order_entry import matching as order_matching
from app.order_entry import orders as order_service
from app.order_entry import pricing as order_pricing
from app.order_entry import quantities as order_quantities
from app.subledger import masters
from app.subledger.common import (
    PARTNER_TYPE_FOR_ROLE,
    audit,
    control_account_for,
    exposure_direction,
    role_accounts,
)
from app.subledger.openitems import open_items_as_of

ONE = Decimal(1)
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class DocumentType:
    doc_type: str
    # Sign this document puts on the control account: +1 debit, −1 credit.
    direction: int
    transaction_type: str
    label: str


DOCUMENT_MATRIX: dict[tuple[PartnerRole, DocumentKind], DocumentType] = {
    (PartnerRole.AR, DocumentKind.INVOICE): DocumentType(
        DocType.AR_INVOICE, 1, "INV", "Invoice"
    ),
    (PartnerRole.AR, DocumentKind.CREDIT_NOTE): DocumentType(
        DocType.AR_CREDIT_NOTE, -1, "CRN", "Credit note"
    ),
    (PartnerRole.AR, DocumentKind.SETTLEMENT): DocumentType(
        DocType.AR_RECEIPT, -1, "RCT", "Receipt"
    ),
    (PartnerRole.AP, DocumentKind.INVOICE): DocumentType(
        DocType.AP_INVOICE, -1, "INV", "Supplier invoice"
    ),
    (PartnerRole.AP, DocumentKind.CREDIT_NOTE): DocumentType(
        DocType.AP_DEBIT_NOTE, 1, "DBN", "Return to supplier"
    ),
    (PartnerRole.AP, DocumentKind.SETTLEMENT): DocumentType(
        DocType.AP_PAYMENT, 1, "PMT", "Payment"
    ),
}

#: A journal batch line posts an invoice- or credit-note-shaped document — the control account
#: moves the same way — but under the JNL transaction type, and it must draw its number from
#: the journal sequence. A journal debit consuming an invoice number would leave a hole in the
#: INV- series that an audit cannot explain.
JOURNAL_TRANSACTION_TYPE = "JNL"
JOURNAL_DOC_TYPE = {
    PartnerRole.AR: DocType.AR_JOURNAL,
    PartnerRole.AP: DocType.AP_JOURNAL,
}

CREDIT_LIMIT_OVERRIDE = {
    PartnerRole.AR: AR_CREDIT_LIMIT_OVERRIDE,
    PartnerRole.AP: AP_CREDIT_LIMIT_OVERRIDE,
}


@dataclass(frozen=True)
class LineInput:
    """A GL line, or an **item line** when `item_id` is set (P6 decision 1).

    One shape, one service. What an item adds is a catalogue to default from — the price, the
    tax code and the account all fall back to the item's — and, for a *stock* item, a
    companion stock move. Service and non-stock items are ordinary lines that happen to carry
    an item dimension, which is what lets P10 report on them.

    `unit_price` stays required for a GL line and becomes optional for an item line, where
    `None` means "take the item's selling price", converted between inclusive and exclusive to
    suit the document's tax mode.
    """

    unit_price: Decimal | None = None
    quantity: Decimal = ONE
    discount_percent: Decimal = ZERO
    description: str | None = None
    gl_account_id: int | None = None
    transaction_type: str | None = None
    tax_code_id: int | None = None
    branch_id: int | None = None
    project_id: int | None = None
    # --- P6 item line ------------------------------------------------------------------
    item_id: int | None = None
    #: The unit `quantity` is keyed in; defaults to the item's base unit.
    uom_id: int | None = None
    #: Where the stock moves from or to. Only a stock item uses it.
    warehouse_id: int | None = None
    #: The GRN line this supplier-invoice line matches (decision 6).
    grn_line_id: int | None = None
    #: The invoice line a credit-note line returns, so the return is valued at the cost that
    #: was actually issued rather than at today's average.
    returns_line_id: int | None = None
    #: The sales order line this AR invoice line fulfils (decision 7). The quantity may be
    #: lowered from what the order has left and never raised above it (`invoice_exceeds_order`).
    sales_order_line_id: int | None = None
    #: The purchase order line this AP line fulfils — a service line, which an invoice is the
    #: only way to receive, or a direct purchase whose goods arrived on the invoice itself.
    purchase_order_line_id: int | None = None
    #: On a **kit** line only: the explosion to use instead of the catalogue definition, each
    #: component keyed in its own base unit and carrying its own order link.
    #:
    #: `None` means "explode from the definition", which is what a kit keyed straight onto an
    #: invoice does (decision 8). An invoice raised **from a sales order** passes the components
    #: the order stored, because that order recorded what was promised — Breakup may have edited
    #: it, and the definition may have moved since. Re-exploding there would ship a different
    #: bundle from the one that was sold, and the order's component lines would never be
    #: invoiced at all.
    kit_components: tuple["LineInput", ...] | None = None


@dataclass(frozen=True)
class DocumentInput:
    kind: DocumentKind
    partner_id: int
    document_date: date
    description: str
    due_date: date | None = None
    currency_id: int | None = None
    exchange_rate: Decimal | None = None
    branch_id: int | None = None
    project_id: int | None = None
    payment_terms_id: int | None = None
    sales_rep_id: int | None = None
    tax_mode: TaxMode | None = None
    reference: str | None = None
    #: Overrides the transaction type the (role, kind) matrix would give. Only `JNL` is
    #: accepted, and only for invoice/credit-note kinds — see `JOURNAL_TRANSACTION_TYPE`.
    transaction_type: str | None = None
    lines: tuple[LineInput, ...] = field(default_factory=tuple)
    # Settlements carry an amount and a cash account instead of lines.
    amount: Decimal | None = None
    cash_account_id: int | None = None
    instrument_type: InstrumentType | None = None
    maturity_date: date | None = None
    # --- P7 fiscalization (decision 7) -------------------------------------------------
    #: How the document is paid. Defaulted from the payment terms when it is not supplied —
    #: `credit` with terms, `cash` without — and stored on both roles.
    payment_method: PaymentMethod | None = None
    #: The customer's EBM purchase code. Required on a fiscalized sale to a customer with a
    #: TIN (`purchase_code_required`), and meaningless without one.
    purchase_code: str | None = None
    #: On a credit note: the invoice it refunds, when its lines do not say so themselves.
    refund_of_document_id: int | None = None
    #: The authority's refund reason code. Required on a fiscalized credit note — only the
    #: person issuing it can answer "why", and a default would put the same answer on all.
    refund_reason: str | None = None


@dataclass
class _ComputedLine:
    source: LineInput
    gl_account_id: int | None
    transaction_type: str | None
    tax_code_id: int | None
    branch_id: int | None
    project_id: int | None
    net: Decimal
    tax: Decimal
    # --- P6 item line ------------------------------------------------------------------
    item: Item | None = None
    uom_id: int | None = None
    base_quantity: Decimal | None = None
    warehouse_id: int | None = None
    unit_price: Decimal = ZERO
    #: Base-currency value the companion stock posting moved for this line: the cost issued
    #: on a sale, the cost received on a return or an unmatched purchase. Filled in after the
    #: companion posts, which is why the companion posts first.
    stock_value: Decimal | None = None
    #: What this line took off the GRN accrual, when it matched one.
    accrual_relieved: Decimal | None = None
    #: The id this line will be written with, reserved before the companion posts so its
    #: moves can point back at it.
    line_id: int | None = None
    #: The GRN line this one matched, once resolved.
    matched_grn_line: object | None = None
    #: Index into `computed` of the kit line this component was exploded from — a position
    #: rather than an id, because the ids are reserved after the explosion has happened.
    kit_parent_index: int | None = None
    #: The branch of the warehouse the goods actually moved through.
    #:
    #: **Every GRN-accrual line carries this, not the document's branch.** The accrual is
    #: proved per branch, and goods received into a depot in one branch must be relieved in
    #: that same branch however the invoice was keyed — otherwise a receipt in B billed on a
    #: document defaulting to A leaves +X in A and −X in B, an accrual that nets to zero in
    #: total and is wrong in both places. On a match it is the *GRN line's* warehouse, not
    #: this line's: the invoice says what the goods cost, the receipt says where they went.
    #: Purchase price variance is not this — a price disagreement belongs to the document
    #: that noticed it, so PPV stays on the document's branch.
    accrual_branch_id: int | None = None

    @property
    def gross(self) -> Decimal:
        return self.net + self.tax

    @property
    def is_item_line(self) -> bool:
        return self.item is not None

    @property
    def moves_stock(self) -> bool:
        """Only a stock item moves stock, and a matched purchase line moves none: the goods
        arrived on the GRN, and the invoice only says what they cost.

        A **kit** parent line moves nothing either — a kit is a bundle and there is no such
        thing on a shelf — which falls out of the item-type test rather than needing a clause:
        it is the component lines beside it that are stock items and that ship.
        """
        return (
            self.item is not None
            and self.item.item_type == ItemType.STOCK
            and self.source.grn_line_id is None
        )

    @property
    def is_kit_component(self) -> bool:
        return self.kit_parent_index is not None

    @property
    def posts_a_ledger_line(self) -> bool:
        """A kit component carries no money: the kit's revenue and tax are entirely on the
        parent line, so the component has a stock move and no journal line at all. Posting a
        zero-amount line instead would be a line an auditor cannot read and a row every report
        has to filter out."""
        return not (self.is_kit_component and self.net == ZERO and self.tax == ZERO)


# --- Reads ----------------------------------------------------------------------------------


def get_document(db: Session, company_id: int, document_id: int) -> PartnerDocument:
    document = db.get(PartnerDocument, document_id)
    if document is None or document.company_id != company_id:
        raise NotFoundError("Document not found")
    return document


def list_documents(
    db: Session,
    company_id: int,
    *,
    role: PartnerRole,
    partner_id: int | None = None,
    kind: DocumentKind | None = None,
    status: DocumentStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    open_only: bool = False,
    cursor: int | None = None,
    limit: int = 100,
) -> tuple[list[PartnerDocument], int | None]:
    statement = select(PartnerDocument).where(
        PartnerDocument.company_id == company_id, PartnerDocument.role == role
    )
    if partner_id is not None:
        statement = statement.where(PartnerDocument.partner_id == partner_id)
    if kind is not None:
        statement = statement.where(PartnerDocument.kind == kind)
    if status is not None:
        statement = statement.where(PartnerDocument.status == status)
    if date_from is not None:
        statement = statement.where(PartnerDocument.document_date >= date_from)
    if date_to is not None:
        statement = statement.where(PartnerDocument.document_date <= date_to)
    if open_only:
        statement = statement.where(PartnerDocument.open_amount > 0)
    if cursor is not None:
        statement = statement.where(PartnerDocument.id > cursor)
    rows = list(
        db.scalars(statement.order_by(PartnerDocument.id).limit(limit + 1))
    )
    next_cursor = rows[limit - 1].id if len(rows) > limit else None
    return rows[:limit], next_cursor


# --- Posting --------------------------------------------------------------------------------


def post_document(
    db: Session,
    company_id: int,
    role: PartnerRole,
    data: DocumentInput,
    *,
    actor: User,
    permissions: set[str] | None = None,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> tuple[PartnerDocument, bool]:
    """Returns the document and whether this was an idempotent replay. Never commits."""
    if idempotency_key:
        replayed = _replay(db, company_id, idempotency_key, idempotency_hash)
        if replayed is not None:
            return replayed, True

    spec = DOCUMENT_MATRIX[(role, data.kind)]
    if data.transaction_type is not None and data.transaction_type != spec.transaction_type:
        if data.transaction_type != JOURNAL_TRANSACTION_TYPE:
            raise LedgerStateError(
                f"{data.transaction_type} is not a transaction type this document kind can "
                f"post under",
                code="transaction_type_not_allowed",
                field_errors={"transaction_type": ["not allowed for this kind"]},
            )
        if data.kind == DocumentKind.SETTLEMENT:
            raise LedgerStateError(
                "A receipt or payment cannot post as a journal",
                code="transaction_type_not_allowed",
                field_errors={"transaction_type": ["not allowed for a settlement"]},
            )
        spec = replace(
            spec,
            doc_type=JOURNAL_DOC_TYPE[role],
            transaction_type=JOURNAL_TRANSACTION_TYPE,
            label="Journal",
        )
    partner = masters.get_partner(db, company_id, data.partner_id)
    if not partner.has_role(role):
        raise LedgerStateError(
            f"{partner.name} is not a {'customer' if role == PartnerRole.AR else 'supplier'}",
            code="partner_role_missing",
            field_errors={"partner_id": ["wrong role"]},
        )
    if not partner.is_active:
        raise LedgerStateError(f"{partner.name} is inactive", code="partner_inactive")

    settings = masters.role_settings_or_default(db, company_id, partner.id, role)
    if settings.is_on_hold:
        raise LedgerStateError(f"{partner.name} is on hold", code="partner_on_hold")

    accounts = role_accounts(db, company_id, role)
    gl_settings = posting.gl_settings_for(db, company_id)
    control_account_id = control_account_for(db, company_id, role, settings.control_account_id)
    currency = _resolve_currency(db, company_id, data.currency_id or partner.currency_id)
    tax_mode = data.tax_mode or settings.tax_mode
    branch_id = data.branch_id or settings.default_branch_id
    project_id = data.project_id or settings.default_project_id

    terms = _resolve_terms(db, company_id, data.payment_terms_id or settings.payment_terms_id)
    due_date = data.due_date or masters.due_date_for(terms, data.document_date)

    if data.kind == DocumentKind.SETTLEMENT:
        computed: list[_ComputedLine] = []
        total = _require_settlement_amount(data, currency)
        net_total, tax_total = total, ZERO
    else:
        computed = _compute_lines(
            db,
            company_id,
            data,
            role=role,
            currency=currency,
            tax_mode=tax_mode,
            settings=settings,
            branch_id=branch_id,
            project_id=project_id,
            default_warehouse_id=gl_settings.default_warehouse_id,
            accrual_account_id=gl_settings.grn_accrual_account_id,
        )
        _assert_order_links(db, company_id, role, data.kind, computed, partner_id=partner.id)
        net_total = sum((line.net for line in computed), ZERO)
        tax_total = sum((line.tax for line in computed), ZERO)
        total = net_total + tax_total
    if total <= ZERO:
        raise PostingError(
            "A document must total more than zero",
            code="invalid_amount",
            field_errors={"lines": ["document total must be positive"]},
        )

    # **The fiscal contract, before the first write** (decision 2/3/7/8). Here rather than
    # after the posting because every one of its refusals is a refusal about the *document* —
    # a line with no item, a customer with a TIN and no purchase code, a credit note that
    # refunds two invoices — and a refusal that arrives after the companion stock entry has
    # posted is a rollback where a `422` would have done. It writes nothing and reads only
    # what `_compute_lines` already resolved.
    payment_method = data.payment_method or fiscal_sales.default_payment_method(
        terms is not None
    )
    fiscal_plan = fiscal_sales.plan(
        db,
        company_id,
        role=role,
        kind=data.kind,
        is_journal=spec.transaction_type == JOURNAL_TRANSACTION_TYPE,
        partner=partner,
        computed=computed,
        branch_id=branch_id,
        tax_mode=tax_mode,
        purchase_code=data.purchase_code,
        refund_of_document_id=data.refund_of_document_id,
        refund_reason=data.refund_reason,
        payment_method=payment_method,
    )

    _check_credit_limit(
        db,
        company_id,
        role=role,
        partner=partner,
        settings=settings,
        direction=spec.direction,
        total=total,
        currency=currency,
        document_date=data.document_date,
        exchange_rate=data.exchange_rate,
        actor=actor,
        permissions=permissions or set(),
        request=request,
    )

    # The companion stock entry goes first (decision 2): a return to supplier and an
    # unmatched purchase both need the value the stock ledger actually moved before their
    # partner side can be built at all. Its `source_doc_id` is this document's, which does
    # not exist yet — so the id is reserved the way `inventory_documents` reserves its own.
    booking_rate = _booking_rate(db, currency, data)
    document_id = _reserve_document_id(db)
    # And one id per line that will be written. The companion's moves carry `source_line_id`
    # so a return can later find the exact cost its original line was issued at, and
    # `stock_moves` refuses UPDATE — so the ids have to exist before the moves do.
    line_ids = [_reserve_document_line_id(db) for _ in computed]
    for line, line_id in zip(computed, line_ids, strict=True):
        line.line_id = line_id
    companion = order_companion.post_companion(
        db,
        company_id,
        role=role,
        kind=data.kind,
        computed=computed,
        document_date=data.document_date,
        description=data.description,
        reference=data.reference,
        document_id=document_id,
        rate=booking_rate,
        accrual_account_id=gl_settings.grn_accrual_account_id,
        cogs_account_id_for=lambda item: _cogs_account_id(item, gl_settings),
        actor=actor,
    )
    for index, value in companion.values.items():
        computed[index].stock_value = value

    # The three-way match (decision 6). A matched line moved no stock — the goods arrived on
    # the GRN — so this runs after the companion and touches only the ledger.
    base = base_currency(db, company_id)
    for index, line in enumerate(computed):
        if line.source.grn_line_id is None:
            continue
        if role != PartnerRole.AP or data.kind != DocumentKind.INVOICE:
            raise LedgerStateError(
                "Only a supplier invoice line can match a goods receipt",
                code="match_not_allowed",
                field_errors={f"lines.{index}.grn_line_id": ["not a supplier invoice"]},
            )
        grn_line, relieved = order_matching.resolve_match(
            db,
            company_id,
            index=index,
            grn_line_id=line.source.grn_line_id,
            base_quantity=line.base_quantity or ZERO,
            supplier_id=partner.id,
            decimal_places=base.decimal_places,
        )
        line.accrual_relieved = relieved
        line.matched_grn_line = grn_line
        # The receipt says where the goods went; the invoice only says what they cost. So the
        # relieving leg posts to the *GRN line's* branch, whatever branch this document was
        # keyed on — otherwise one branch is credited and another debited, and the accrual
        # nets to zero in total while being wrong in both.
        line.accrual_branch_id = inventory_masters.get_warehouse(
            db, company_id, grn_line.warehouse_id
        ).branch_id

    partner_dimension = {
        "partner_type": PARTNER_TYPE_FOR_ROLE[role],
        "partner_id": partner.id,
    }
    common = {
        "currency_id": currency.id,
        "exchange_rate": data.exchange_rate,
        "branch_id": branch_id,
        **partner_dimension,
    }
    specs: list[LineSpec] = [
        LineSpec(
            amount=spec.direction * total,
            gl_account_id=control_account_id,
            project_id=project_id,
            description=data.reference or data.description,
            **common,
        )
    ]
    # Position of each document line's net spec, so the account the engine resolved can be
    # read back onto the stored line instead of being derived a second time. One entry per
    # computed line, `None` where the line posts no journal line at all — a kit component — so
    # the list stays index-aligned with `computed` however many lines are skipped.
    net_positions: list[int | None] = []
    if data.kind == DocumentKind.SETTLEMENT:
        specs.append(
            LineSpec(
                amount=-spec.direction * total,
                gl_account_id=_settlement_account(
                    db, company_id, data, accounts.post_dated_account_id
                ),
                project_id=project_id,
                description=data.description,
                **common,
            )
        )
    else:
        for line in computed:
            if not line.posts_a_ledger_line:
                # A kit component: quantity on the stock side, nothing on the ledger side.
                net_positions.append(None)
                continue
            if (
                role == PartnerRole.AP
                and data.kind == DocumentKind.CREDIT_NOTE
                and line.stock_value is not None
            ):
                # Goods going back. The companion already debited the accrual with what they
                # actually cost us; this credits it with the same amount, so the accrual nets
                # to **zero inside the document**. What the supplier is credited with is what
                # we are claiming back, and the difference between claim and cost is purchase
                # price variance — the same account a price movement on the way in lands in.
                issued = abs(line.stock_value)
                claim_base = round_amount(line.net * booking_rate, base.decimal_places)
                variance = claim_base - issued
                net_positions.append(len(specs))
                specs.append(
                    LineSpec(
                        amount=-spec.direction * issued,
                        gl_account_id=line.gl_account_id,
                        transaction_type=line.transaction_type or spec.transaction_type,
                        project_id=line.project_id,
                        item_id=line.item.id if line.item is not None else None,
                        description=line.source.description or data.description,
                        currency_id=base.id,
                        exchange_rate=ONE,
                        # The branch the goods moved through, not the one the document was keyed on.
                        branch_id=line.accrual_branch_id or line.branch_id,
                        **partner_dimension,
                    )
                )
                if variance != ZERO:
                    specs.append(
                        LineSpec(
                            amount=-spec.direction * variance,
                            gl_account_id=_variance_account_id(gl_settings),
                            project_id=line.project_id,
                            item_id=line.item.id if line.item is not None else None,
                            description=line.source.description or data.description,
                            currency_id=base.id,
                            exchange_rate=ONE,
                            branch_id=line.branch_id,
                            **partner_dimension,
                        )
                    )
                if line.tax != ZERO:
                    tax_code = resolve_tax_code(
                        db, company_id, line.tax_code_id, data.document_date
                    )
                    if tax_code.gl_account_id is None:
                        raise PostingError(
                            f"Tax code {tax_code.code} has no GL account",
                            code="tax_code_without_account",
                            field_errors={"tax_code_id": ["no GL account"]},
                        )
                    specs.append(
                        LineSpec(
                            amount=-spec.direction * line.tax,
                            gl_account_id=tax_code.gl_account_id,
                            project_id=line.project_id,
                            tax_code_id=line.tax_code_id,
                            description=line.source.description or data.description,
                            currency_id=currency.id,
                            exchange_rate=data.exchange_rate,
                            branch_id=line.branch_id,
                            **partner_dimension,
                        )
                    )
                continue
            if line.accrual_relieved is not None:
                # A matched line posts **two** legs instead of one: the accrual comes off for
                # exactly what the receipt put there, and whatever the invoice disagrees by
                # goes to purchase price variance. Both are base-currency lines beside the
                # invoice's document-currency ones (decision 14). They are *posted* in base
                # at a rate of one rather than carrying a `base_amount` override — the kernel
                # reserves that override for reversals — which gets the same frozen amount
                # through the conversion the engine already does.
                net_base = round_amount(line.net * booking_rate, base.decimal_places)
                variance = net_base - line.accrual_relieved
                net_positions.append(len(specs))
                specs.append(
                    LineSpec(
                        amount=-spec.direction * line.accrual_relieved,
                        gl_account_id=line.gl_account_id,
                        transaction_type=line.transaction_type or spec.transaction_type,
                        project_id=line.project_id,
                        item_id=line.item.id if line.item is not None else None,
                        description=line.source.description or data.description,
                        currency_id=base.id,
                        exchange_rate=ONE,
                        # The branch the goods moved through, not the one the document was keyed on.
                        branch_id=line.accrual_branch_id or line.branch_id,
                        **partner_dimension,
                    )
                )
                if variance != ZERO:
                    specs.append(
                        LineSpec(
                            amount=-spec.direction * variance,
                            gl_account_id=_variance_account_id(gl_settings),
                            project_id=line.project_id,
                            item_id=line.item.id if line.item is not None else None,
                            description=line.source.description or data.description,
                            currency_id=base.id,
                            exchange_rate=ONE,
                            branch_id=line.branch_id,
                            **partner_dimension,
                        )
                    )
                if line.tax != ZERO:
                    tax_code = resolve_tax_code(
                        db, company_id, line.tax_code_id, data.document_date
                    )
                    if tax_code.gl_account_id is None:
                        raise PostingError(
                            f"Tax code {tax_code.code} has no GL account",
                            code="tax_code_without_account",
                            field_errors={"tax_code_id": ["no GL account"]},
                        )
                    specs.append(
                        LineSpec(
                            amount=-spec.direction * line.tax,
                            gl_account_id=tax_code.gl_account_id,
                            project_id=line.project_id,
                            tax_code_id=line.tax_code_id,
                            description=line.source.description or data.description,
                            currency_id=currency.id,
                            exchange_rate=data.exchange_rate,
                            branch_id=line.branch_id,
                            **partner_dimension,
                        )
                    )
                continue
            net_positions.append(len(specs))
            # A direct purchase's goods line *is* an accrual leg — the goods arrive on this
            # document rather than on a GRN — so it follows the same branch rule as every
            # other accrual line. Any other item line keeps the document's branch.
            line_branch_id = (
                line.accrual_branch_id or line.branch_id
                if line.gl_account_id == gl_settings.grn_accrual_account_id
                else line.branch_id
            )
            specs.append(
                LineSpec(
                    amount=-spec.direction * line.net,
                    gl_account_id=line.gl_account_id,
                    transaction_type=line.transaction_type or spec.transaction_type,
                    project_id=line.project_id,
                    # Decision 1: the revenue or expense line of an item line carries the
                    # item, which is what P10's sales analysis reads — and what the VN008
                    # guard demands the moment that account is the accrual.
                    item_id=line.item.id if line.item is not None else None,
                    tax_code_id=line.tax_code_id,
                    tax_amount=-spec.direction * line.tax,
                    description=line.source.description or data.description,
                    currency_id=currency.id,
                    exchange_rate=data.exchange_rate,
                    branch_id=line_branch_id,
                    **partner_dimension,
                )
            )
            if line.tax != ZERO:
                tax_code = resolve_tax_code(db, company_id, line.tax_code_id, data.document_date)  # type: ignore[arg-type]
                if tax_code.gl_account_id is None:
                    raise PostingError(
                        f"Tax code {tax_code.code} has no GL account",
                        code="tax_code_without_account",
                        field_errors={"tax_code_id": ["no GL account"]},
                    )
                specs.append(
                    LineSpec(
                        amount=-spec.direction * line.tax,
                        gl_account_id=tax_code.gl_account_id,
                        project_id=line.project_id,
                        tax_code_id=line.tax_code_id,
                        description=line.source.description or data.description,
                        currency_id=currency.id,
                        exchange_rate=data.exchange_rate,
                        branch_id=line.branch_id,
                        **partner_dimension,
                    )
                )

    entry = posting.post(
        db,
        PartnerDocumentPosted(
            module=role.value,
            doc_type=spec.doc_type,
            entry_date=data.document_date,
            description=f"{spec.label}: {data.description}",
            reference=data.reference,
            branch_id=branch_id,
            source_doc_type="partner_document",
            idempotency_key=idempotency_key,
            idempotency_hash=idempotency_hash,
            lines=tuple(specs),
        ),
        company_id=company_id,
        actor=actor,
    )
    assert entry is not None

    document = PartnerDocument(
        id=document_id,
        company_id=company_id,
        role=role,
        kind=data.kind,
        number=entry.number,
        doc_type=spec.doc_type,
        transaction_type=spec.transaction_type,
        partner_id=partner.id,
        journal_entry_id=entry.id,
        document_date=data.document_date,
        due_date=due_date,
        currency_id=currency.id,
        exchange_rate=entry.lines[0].exchange_rate,
        branch_id=entry.lines[0].branch_id,
        project_id=project_id,
        payment_terms_id=terms.id if terms is not None else None,
        sales_rep_id=data.sales_rep_id or settings.sales_rep_id,
        tax_mode=tax_mode,
        control_account_id=control_account_id,
        reference=data.reference,
        description=data.description,
        net_amount=net_total,
        tax_amount=tax_total,
        total_amount=total,
        base_total_amount=abs(entry.lines[0].base_amount),
        open_amount=total,
        direction=spec.direction,
        instrument_type=data.instrument_type,
        maturity_date=data.maturity_date,
        payment_method=payment_method,
        purchase_code=data.purchase_code,
        refund_of_document_id=data.refund_of_document_id,
        refund_reason=data.refund_reason,
        cash_account_id=data.cash_account_id,
        stock_entry_id=companion.entry_id,
        status=DocumentStatus.POSTED,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(document)
    db.flush()
    db.add_all(
        [
            PartnerDocumentLine(
                id=line.line_id,
                company_id=company_id,
                document_id=document.id,
                line_no=index,
                description=line.source.description,
                quantity=line.source.quantity,
                # The **resolved** price, not the keyed one: an item line may have taken it
                # from the catalogue, and the line has to record what it was actually priced
                # at rather than the blank the operator left.
                unit_price=line.unit_price,
                discount_percent=line.source.discount_percent,
                gl_account_id=_line_account_id(
                    entry,
                    net_positions[index - 1],
                    line.gl_account_id,
                    parent_position=(
                        net_positions[line.kit_parent_index]
                        if line.kit_parent_index is not None
                        else None
                    ),
                ),
                # Denormalised from the document above it, and held equal to it by a composite
                # foreign key — which is what lets the AR/AP order-link rule be a CHECK rather
                # than a trigger (decision 1).
                role=role,
                tax_code_id=line.tax_code_id,
                branch_id=line.branch_id or entry.lines[0].branch_id,
                project_id=line.project_id,
                net_amount=line.net,
                tax_amount=line.tax,
                gross_amount=line.gross,
                item_id=line.item.id if line.item is not None else None,
                uom_id=line.uom_id,
                base_quantity=line.base_quantity,
                warehouse_id=line.warehouse_id,
                sales_order_line_id=line.source.sales_order_line_id,
                purchase_order_line_id=line.source.purchase_order_line_id,
                grn_line_id=line.source.grn_line_id,
                returns_line_id=line.source.returns_line_id,
                kit_parent_line_id=(
                    line_ids[line.kit_parent_index]
                    if line.kit_parent_index is not None
                    else None
                ),
                accrual_relieved=line.accrual_relieved,
            )
            for index, line in enumerate(computed, 1)
        ]
    )
    db.flush()
    # **In the posting transaction, before this function returns** (decision 4). The row and
    # the document commit together or neither does, which is the whole guarantee: nothing
    # posted is ever lost and nothing unposted is ever sent.
    if fiscal_plan is not None:
        fiscal_sales.enqueue(
            db,
            company_id,
            plan=fiscal_plan,
            document=document,
            partner=partner,
            currency=currency,
            base=base,
            posted_at=entry.posted_at,
            actor=actor,
        )
    _refresh_matched_receipts(db, computed)
    _refresh_fulfilled_orders(db, company_id, computed)
    audit(
        db,
        company_id,
        f"partner_document.{data.kind.value}_posted",
        "partner_documents",
        document.id,
        actor=actor,
        after={
            "role": role.value,
            "number": document.number,
            "partner_id": partner.id,
            "total_amount": str(total),
            "currency_id": currency.id,
        },
        request=request,
    )
    return document, False


def _assert_order_links(
    db: Session,
    company_id: int,
    role: PartnerRole,
    kind: DocumentKind,
    computed: list[_ComputedLine],
    *,
    partner_id: int,
) -> None:
    """A line that fulfils an order may not take it past what was ordered (decision 7).

    Checked **before the companion posts** and therefore before anything is written, which is
    what lets a caller treat a refusal as a no-op. The order rules themselves live in
    `order_entry.orders`: "you cannot invoice more than was ordered" is a fact about the order,
    and this service should no more re-derive it than it re-derives a tax rate.

    The role check is the declarative one from the schema, stated early so the failure names the
    line rather than arriving as a constraint violation with no field on it.
    """
    for index, line in enumerate(computed):
        source = line.source
        if source.sales_order_line_id is not None:
            if role != PartnerRole.AR or kind != DocumentKind.INVOICE:
                raise LedgerStateError(
                    "Only a customer invoice line can fulfil a sales order",
                    code="order_link_not_allowed",
                    field_errors={
                        f"lines.{index}.sales_order_line_id": ["not a customer invoice"]
                    },
                )
            order_service.assert_sales_line_within_order(
                db,
                company_id,
                index=index,
                line_id=source.sales_order_line_id,
                base_quantity=line.base_quantity or ZERO,
                partner_id=partner_id,
            )
        if source.purchase_order_line_id is not None:
            if role != PartnerRole.AP or kind != DocumentKind.INVOICE:
                raise LedgerStateError(
                    "Only a supplier invoice line can fulfil a purchase order",
                    code="order_link_not_allowed",
                    field_errors={
                        f"lines.{index}.purchase_order_line_id": ["not a supplier invoice"]
                    },
                )
            if source.grn_line_id is not None:
                # A matched line receives nothing: the goods arrived on the GRN, which has
                # already counted against the order. The link is kept for the drill-down.
                continue
            order_service.assert_purchase_line_within_order(
                db,
                company_id,
                index=index,
                line_id=source.purchase_order_line_id,
                base_quantity=line.base_quantity or ZERO,
                partner_id=partner_id,
                field=f"lines.{index}.purchase_order_line_id",
            )


def _line_account_id(
    entry: JournalEntry,
    position: int | None,
    fallback: int | None,
    *,
    parent_position: int | None = None,
) -> int:
    """The engine resolved the account (line override, partner default, transaction type);
    read it back rather than re-deriving it, so the document and the ledger agree.

    A kit component posts no journal line, so it has no position of its own and records the
    account the kit's revenue actually went to — the **parent's**, read back off the entry for
    the same reason the parent reads its own back. Taking it from the explosion instead was
    wrong twice over: the parent's account is not resolved until the engine runs, so at
    explosion time it is whatever the line was keyed with, and a kit line is normally keyed
    with nothing at all. That left the component with `None` and the assertion below firing —
    a 500 on every kit line whose item carries no sales account of its own, which is most of
    them, since the point of a default is not having to set one per item.
    """
    if position is not None:
        return entry.lines[position].gl_account_id
    if parent_position is not None:
        return entry.lines[parent_position].gl_account_id
    assert fallback is not None, "a line with no journal line must carry its own account"
    return fallback


def _replay(
    db: Session, company_id: int, key: str, request_hash: str | None
) -> PartnerDocument | None:
    document = db.scalar(
        select(PartnerDocument).where(
            PartnerDocument.company_id == company_id, PartnerDocument.idempotency_key == key
        )
    )
    if document is None:
        return None
    if request_hash is not None and document.idempotency_hash != request_hash:
        raise LedgerStateError(
            f"Idempotency-Key {key} was already used for a different request "
            f"({document.number}); use a new key",
            code="idempotency_key_reused",
        )
    return document


def _resolve_currency(db: Session, company_id: int, currency_id: int | None) -> Currency:
    if currency_id is None:
        return base_currency(db, company_id)
    currency = db.get(Currency, currency_id)
    if currency is None or currency.company_id != company_id or not currency.is_active:
        raise PostingError(
            "Currency is not available",
            code="currency_not_found",
            field_errors={"currency_id": ["unknown or inactive currency"]},
        )
    return currency


def _resolve_terms(db: Session, company_id: int, terms_id: int | None) -> PaymentTerms | None:
    if terms_id is None:
        return None
    return masters.get_payment_terms(db, company_id, terms_id)


def _require_settlement_amount(data: DocumentInput, currency: Currency) -> Decimal:
    if data.amount is None or data.amount <= ZERO:
        raise PostingError(
            "A receipt or payment needs a positive amount",
            code="invalid_amount",
            field_errors={"amount": ["must be positive"]},
        )
    if not is_rounded(data.amount, currency.decimal_places):
        raise PostingError(
            f"{currency.code} allows {currency.decimal_places} decimals",
            code="amount_precision",
            field_errors={"amount": ["too many decimal places"]},
        )
    return data.amount


def _settlement_account(
    db: Session, company_id: int, data: DocumentInput, post_dated_account_id: int | None
) -> int:
    """A post-dated instrument is a real claim, but the cash has not landed: it posts to the
    post-dated account and moves to the bank at maturity (§B.2)."""
    account = db.get(GLAccount, data.cash_account_id)
    if account is None or account.company_id != company_id:
        raise PostingError(
            "Cash account not found",
            code="account_not_found",
            field_errors={"cash_account_id": ["unknown account"]},
        )
    if account.control_type not in CASHBOOK_CONTROL_TYPES:
        raise PostingError(
            f"Account {account.code} is not a bank or cash account",
            code="not_a_cash_account",
            field_errors={"cash_account_id": ["must be a bank/cash control account"]},
        )
    if data.maturity_date is not None and data.maturity_date > data.document_date:
        if post_dated_account_id is None:
            raise PostingError(
                "Set the post-dated receivable/payable account in AR/AP defaults",
                code="gl_setting_missing",
                field_errors={"maturity_date": ["no post-dated account configured"]},
            )
        return post_dated_account_id
    return account.id


def _resolve_item_line(
    db: Session,
    company_id: int,
    role: PartnerRole,
    index: int,
    line: LineInput,
    *,
    tax_mode: TaxMode,
    default_warehouse_id: int | None,
    accrual_account_id: int | None,
    document_date: date,
) -> tuple[Item, int, Decimal, int | None, int | None, Decimal, int | None, int | None]:
    """Everything an item line takes from the catalogue (decision 1).

    Returns `(item, uom_id, base_quantity, warehouse_id, warehouse_branch_id, unit_price,
    tax_code_id, gl_account_id)`. Each default is the 3rd link of the ADR-05 chain: a value
    keyed on the line always wins, and only where the line is silent does the item speak.

    **The account depends on the side and on the kind of item**, and the asymmetry is the
    whole GRV design. An AR line credits revenue. An AP line for a *stock* item debits the
    **GRN accrual**, not an expense: the cost is already in inventory — it arrived with the
    goods — and the invoice only relieves what was accrued for them. An AP line for a service
    or non-stock item has no goods behind it and expenses to the item's purchase account.
    """
    item = inventory_masters.get_item(db, company_id, line.item_id)  # type: ignore[arg-type]
    if not item.is_active:
        raise LedgerStateError(
            f"{item.code} is not active",
            code="item_not_active",
            field_errors={f"lines.{index}.item_id": ["not active"]},
        )
    if item.item_type == ItemType.KIT and role == PartnerRole.AP:
        # A kit is a virtual bundle that exists to be sold, never bought (decision 8).
        raise LedgerStateError(
            f"{item.code} is a kit and cannot be purchased",
            code="kit_not_purchasable",
            field_errors={f"lines.{index}.item_id": ["a kit cannot be purchased"]},
        )

    uom_id = line.uom_id or item.base_uom_id
    uom = inventory_masters.get_uom(db, company_id, uom_id)
    base_quantity = inventory_masters.to_base_quantity(line.quantity, uom, item)

    warehouse_id: int | None = None
    warehouse_branch_id: int | None = None
    if item.item_type == ItemType.STOCK:
        warehouse_id = line.warehouse_id or default_warehouse_id
        if warehouse_id is None:
            raise LedgerStateError(
                "No warehouse on the line or in the defaults",
                code="warehouse_required",
                field_errors={f"lines.{index}.warehouse_id": ["required"]},
            )
        warehouse = inventory_masters.get_warehouse(db, company_id, warehouse_id)
        if warehouse.is_in_transit:
            raise LedgerStateError(
                "The in-transit warehouse is not selectable on a document",
                code="in_transit_warehouse_locked",
                field_errors={f"lines.{index}.warehouse_id": ["not selectable"]},
            )
        warehouse_branch_id = warehouse.branch_id

    tax_code_id = line.tax_code_id
    if tax_code_id is None:
        tax_code_id = (
            item.default_sales_tax_code_id
            if role == PartnerRole.AR
            else item.default_purchase_tax_code_id
        )

    # Price: the line's, or the item's selling price turned to suit the document's tax mode.
    # `price_includes_tax` is a fact about the *catalogue* price; `tax_mode` is a fact about
    # the document. When they disagree the price has to be converted, or an inclusive catalogue
    # sold on an exclusive document silently charges tax twice. The conversion lives in
    # `order_entry.pricing` and is shared with the order service, so an invoice raised from a
    # sales order reproduces the order's figures rather than approximating them. The tax code
    # is resolved first because the conversion needs its rate.
    unit_price = line.unit_price
    if unit_price is None:
        unit_price = (
            order_pricing.catalogue_unit_price(
                db,
                company_id,
                item,
                tax_mode=tax_mode,
                tax_code_id=tax_code_id,
                on_date=document_date,
            )
            if role == PartnerRole.AR
            else ZERO
        )

    gl_account_id = line.gl_account_id
    if gl_account_id is None:
        if role == PartnerRole.AR:
            gl_account_id = item.sales_account_id
        elif item.item_type == ItemType.STOCK:
            gl_account_id = accrual_account_id
        else:
            gl_account_id = item.purchase_account_id
    return (
        item,
        uom.id,
        base_quantity,
        warehouse_id,
        warehouse_branch_id,
        unit_price,
        tax_code_id,
        gl_account_id,
    )


def _compute_lines(
    db: Session,
    company_id: int,
    data: DocumentInput,
    *,
    role: PartnerRole,
    currency: Currency,
    tax_mode: TaxMode,
    settings: object,
    branch_id: int | None,
    project_id: int | None,
    default_warehouse_id: int | None = None,
    accrual_account_id: int | None = None,
) -> list[_ComputedLine]:
    """Per-line tax, half-up to the document currency's decimals (decision 11). Exclusive:
    the entered amount is net. Inclusive: it is gross and the tax is carved out of it.

    An **item line** resolves its defaults from the catalogue first (`_resolve_item_line`) and
    is then priced exactly like a GL line — one code path, as decision 2 asks, rather than a
    second service that would have to agree with this one about tax forever.

    A **kit** line explodes here, at line entry (decision 8): the parent carries the kit item,
    its quantity, its price and its tax, and the component lines that follow it carry quantity
    and nothing else. That is why commitment and cost come from the components and revenue from
    the parent, and why editing the catalogue afterwards restates no invoice that has posted.
    """
    places = currency.decimal_places
    default_tax_code_id = getattr(settings, "default_tax_code_id", None)
    computed: list[_ComputedLine] = []
    for index, line in enumerate(data.lines):
        item: Item | None = None
        warehouse_branch_id: int | None = None
        uom_id: int | None = None
        base_quantity: Decimal | None = None
        warehouse_id: int | None = None
        unit_price = line.unit_price
        tax_code_id = line.tax_code_id
        gl_account_id = line.gl_account_id
        if line.item_id is not None:
            (
                item,
                uom_id,
                base_quantity,
                warehouse_id,
                warehouse_branch_id,
                unit_price,
                tax_code_id,
                gl_account_id,
            ) = _resolve_item_line(
                db,
                company_id,
                role,
                index,
                line,
                tax_mode=tax_mode,
                default_warehouse_id=default_warehouse_id,
                accrual_account_id=accrual_account_id,
                document_date=data.document_date,
            )
        if unit_price is None:
            raise PostingError(
                f"Line {index + 1} has no price",
                code="invalid_amount",
                field_errors={f"lines.{index}.unit_price": ["required"]},
            )

        gross_or_net = round_amount(
            line.quantity * unit_price * (ONE - line.discount_percent / HUNDRED), places
        )
        # A line the *caller* keyed must be worth something; a kit's component lines are worth
        # nothing by construction and are appended below rather than keyed, so they never reach
        # this check. A kit whose parent is priced at zero still fails here, which is right.
        if gross_or_net <= ZERO:
            raise PostingError(
                f"Line {index + 1} must be worth more than zero",
                code="invalid_amount",
                field_errors={f"lines.{index}.unit_price": ["must be positive"]},
            )
        if tax_code_id is None:
            tax_code_id = default_tax_code_id
        net, tax = gross_or_net, ZERO
        if tax_code_id is not None:
            tax_code = resolve_tax_code(db, company_id, tax_code_id, data.document_date)
            split = split_tax(
                gross_or_net,
                tax_code.rate_pct,
                inclusive=tax_mode == TaxMode.INCLUSIVE,
                decimal_places=places,
            )
            net, tax = split.net, split.tax
        computed.append(
            _ComputedLine(
                source=line,
                gl_account_id=gl_account_id,
                transaction_type=line.transaction_type,
                tax_code_id=tax_code_id,
                branch_id=line.branch_id or branch_id,
                project_id=line.project_id or project_id,
                net=net,
                tax=tax,
                item=item,
                uom_id=uom_id,
                base_quantity=base_quantity,
                warehouse_id=warehouse_id,
                unit_price=unit_price,
                accrual_branch_id=warehouse_branch_id,
            )
        )
        if item is not None and item.item_type == ItemType.KIT:
            computed.extend(
                _kit_component_lines(
                    db,
                    company_id,
                    parent=computed[-1],
                    parent_index=len(computed) - 1,
                    index=index,
                    base_quantity=base_quantity or ZERO,
                    default_warehouse_id=default_warehouse_id,
                    branch_id=branch_id,
                    project_id=project_id,
                )
            )
    return computed


def _kit_component_lines(
    db: Session,
    company_id: int,
    *,
    parent: _ComputedLine,
    parent_index: int,
    index: int,
    base_quantity: Decimal,
    default_warehouse_id: int | None,
    branch_id: int | None,
    project_id: int | None,
) -> list[_ComputedLine]:
    """The kit's components, turned into lines that move stock and no money.

    Two sources, one shape. A kit keyed straight onto an invoice explodes from the catalogue
    definition (decision 8). A kit line raised **from a sales order** arrives with the components
    that order stored, because they are what was promised — Breakup may have edited them and the
    definition may have moved since — and each of them carries its own order link, without which
    the order's component lines would never register as invoiced.

    Each component's `gl_account_id` is the **parent's** account. The component posts no journal
    line at all — `posts_a_ledger_line` is false for it — so the column is only what the stored
    document line records, and recording the account the kit's revenue actually went to is the
    honest answer: an item's own sales account would name a revenue line that does not exist.
    """
    assert parent.item is not None
    supplied = parent.source.kit_components
    if supplied is None:
        components = order_kits.explode(
            db, company_id, parent.item, base_quantity, field_prefix=f"lines.{index}"
        )
        sources: list[LineInput | None] = [None] * len(components)
    else:
        components = order_kits.resolve_breakup(
            db,
            company_id,
            parent.item,
            tuple(
                order_kits.ComponentInput(item_id=one.item_id, base_quantity=one.quantity)
                for one in supplied
            ),
            field_prefix=f"lines.{index}.kit_components",
        )
        sources = list(supplied)

    lines: list[_ComputedLine] = []
    for component, source in zip(components, sources, strict=True):
        keyed_warehouse_id = (
            source.warehouse_id if source is not None and source.warehouse_id else None
        )
        warehouse_id = keyed_warehouse_id or parent.source.warehouse_id or default_warehouse_id
        component_warehouse_id: int | None = None
        component_branch_id: int | None = None
        if component.item.item_type == ItemType.STOCK:
            warehouse = inventory_masters.get_warehouse(
                db,
                company_id,
                _require_component_warehouse(warehouse_id, index, component.item),
            )
            if warehouse.is_in_transit:
                raise LedgerStateError(
                    "The in-transit warehouse is not selectable on a document",
                    code="in_transit_warehouse_locked",
                    field_errors={f"lines.{index}.warehouse_id": ["not selectable"]},
                )
            component_warehouse_id = warehouse.id
            component_branch_id = warehouse.branch_id
        lines.append(
            _ComputedLine(
                source=replace(
                    source
                    if source is not None
                    else LineInput(item_id=component.item.id, quantity=component.base_quantity),
                    item_id=component.item.id,
                    quantity=component.base_quantity,
                    unit_price=ZERO,
                    warehouse_id=component_warehouse_id,
                    description=(
                        source.description
                        if source is not None and source.description
                        else component.item.name
                    ),
                    kit_components=None,
                ),
                gl_account_id=parent.gl_account_id,
                transaction_type=parent.transaction_type,
                tax_code_id=None,
                branch_id=parent.branch_id or branch_id,
                project_id=parent.project_id or project_id,
                net=ZERO,
                tax=ZERO,
                item=component.item,
                uom_id=component.item.base_uom_id,
                base_quantity=component.base_quantity,
                warehouse_id=component_warehouse_id,
                unit_price=ZERO,
                accrual_branch_id=component_branch_id,
                kit_parent_index=parent_index,
            )
        )
    return lines


def _require_component_warehouse(warehouse_id: int | None, index: int, item: Item) -> int:
    if warehouse_id is None:
        raise LedgerStateError(
            f"{item.code} is a stock component and needs a warehouse",
            code="warehouse_required",
            field_errors={f"lines.{index}.warehouse_id": ["required"]},
        )
    return warehouse_id


# --- Credit limit (decision 8) ---------------------------------------------------------------


def _check_credit_limit(
    db: Session,
    company_id: int,
    *,
    role: PartnerRole,
    partner: Partner,
    settings: object,
    direction: int,
    total: Decimal,
    currency: Currency,
    document_date: date,
    exchange_rate: Decimal | None,
    actor: User,
    permissions: set[str],
    request: Request | None,
) -> None:
    limit: Decimal | None = getattr(settings, "credit_limit", None)
    # The document that *builds* exposure is the one pointing the same way as the role's
    # invoice: an AR invoice debits the customer (+1), a supplier invoice credits the
    # supplier (-1). Testing `direction < 0` instead read AR's signs on both sides, which
    # left the AP limit dead on supplier invoices while blocking returns to supplier — the
    # only two documents it could reach, and both the wrong way round.
    builds_exposure = exposure_direction(role)
    if limit is None or direction != builds_exposure:
        return  # no limit, or a document that reduces exposure

    converted = to_base(db, total, currency, document_date, rate=exchange_rate)
    # Exposure in the role's own sense — what the customer owes us, what we owe the supplier.
    # Open items are signed by their side of the control account, so the invoice direction is
    # what turns both into a positive amount outstanding.
    exposure = (
        builds_exposure
        * sum(
            (
                item.signed_base_amount
                for item in open_items_as_of(
                    db, company_id, role=role, partner_id=partner.id, as_of=document_date
                )
            ),
            ZERO,
        )
        + converted.base_amount
    )
    if exposure <= limit:
        return
    override = CREDIT_LIMIT_OVERRIDE[role]
    if override not in permissions:
        raise PostingError(
            f"{partner.name} would exceed the credit limit of {limit} by {exposure - limit}",
            code="credit_limit_exceeded",
            field_errors={"partner_id": ["credit limit exceeded"]},
        )
    audit(
        db,
        company_id,
        "partner_document.credit_limit_override",
        "partners",
        partner.id,
        actor=actor,
        after={
            "role": role.value,
            "credit_limit": str(limit),
            "exposure": str(exposure),
            "excess": str(exposure - limit),
        },
        request=request,
    )


# --- Reversal and maturity --------------------------------------------------------------------


def reverse_document(
    db: Session,
    document: PartnerDocument,
    *,
    on_date: date,
    reason: str,
    actor: User,
    refund_reason: str | None = None,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> PartnerDocument:
    """The kernel reversal, the open-item unwind, **and the companion stock entry**.

    A document that has been allocated must be unallocated first — otherwise the reversal
    would leave the counterparty's open item pointing at a document that no longer exists in
    the ledger.

    **Both entries come back or neither does** (decision 2). A stock-bearing document posted
    two entries; reversing only the partner side leaves the stock where it is and the accrual
    or the inventory account holding a leg whose counterpart is gone. The property suite found
    exactly that on a three-step sequence — receive one, return it to the supplier, reverse the
    return — where the accrual read zero while the unreversed receipt still said one.

    The companion is reversed **through the inventory service**, which opens its own
    `module_reversal` window: this function never opens the `inv` window itself, because the
    stock half of that reversal is the inventory module's to do and reversing the ledger alone
    is the defect `reverse_via_module_document` exists to prevent.

    **The companion goes first**, because it is the only half that can fail — see the comment
    at the call. That makes this function refuse before it writes, which is the premise every
    caller in the phase relies on.
    """
    if document.status != DocumentStatus.POSTED:
        raise LedgerStateError(
            f"{document.number} was already reversed", code="document_already_reversed"
        )
    if document.open_amount != document.total_amount:
        raise LedgerStateError(
            f"{document.number} is allocated; unallocate it before reversing",
            code="document_allocated",
        )
    if document.matured_entry_id is not None:
        raise LedgerStateError(
            f"{document.number} has already matured into the bank; reverse the maturity first",
            code="instrument_matured",
        )
    # **The fiscal decision, before anything is written** (decision 7). Two refusals live here
    # — a refund RRA has already signed cannot be reversed, and a row whose outcome is
    # unresolved must not be — and they have to arrive before the stock half, which is the
    # other refusal this function can raise. `plan_reversal_refund` reads and refuses;
    # `on_reverse` cancels the row when RRA never held it, which is a write and therefore
    # second.
    fiscal_refund = fiscal_sales.plan_reversal_refund(
        db, document.company_id, document, refund_reason=refund_reason
    )
    fiscal_sales.on_reverse(db, document.company_id, document, reason=reason)
    # **The companion goes first, and that ordering is the whole of whether this function can
    # be trusted to refuse before it writes.**
    #
    # Only the stock half can fail. Undoing a receipt takes goods back off a shelf they may
    # since have left, and under `block` that is `insufficient_stock` — a refusal that arrives
    # after the work, not before it. With the partner side posted first, a caller who caught
    # that refusal was left holding a posted reversal entry for a document still marked posted
    # and still fully open: the ledger said one thing and the open items another, by exactly the
    # document's value. The property machine found it as `AR control account is 16.000000 but
    # open items total 15.000000`.
    #
    # Nothing depends on the order — the two entries are independent, and neither reads the
    # other — so putting the fallible half first costs nothing and makes the failure arrive
    # before the first write. Note that this is **not** the mirror of the posting order that
    # decision 2 describes; see the step-3 report.
    #
    # The window is the inventory service's own, opened by it — and the reversing moves are at
    # the original values, so the two sides cancel exactly rather than re-costing at today's
    # average and leaving a difference behind.
    if document.stock_entry_id is not None:
        stock_service.reverse_stock_posting(
            db,
            document.company_id,
            entry_id=document.stock_entry_id,
            on_date=on_date,
            reason=reason,
            actor=actor,
        )
    # The module's own reversal window: this is the half of the reversal the kernel cannot do,
    # so the kernel only lets the ledger half through from here.
    with posting.module_reversal(document.role.value):
        reversal = posting.reverse(
            db,
            document.journal_entry_id,
            company_id=document.company_id,
            on_date=on_date,
            reason=reason,
            actor=actor,
            idempotency_key=idempotency_key,
            idempotency_hash=idempotency_hash,
        )
    document.status = DocumentStatus.REVERSED
    document.reversal_entry_id = reversal.id
    document.reversed_on = on_date
    document.open_amount = ZERO
    db.flush()
    # A sale RRA signed cannot be un-signed: what reverses it there is a **refund**, queued
    # here in the same transaction as the reversal that owes it.
    if fiscal_refund is not None:
        fiscal_sales.enqueue(
            db,
            document.company_id,
            plan=fiscal_refund,
            document=document,
            partner=masters.get_partner(db, document.company_id, document.partner_id),
            currency=_resolve_currency(db, document.company_id, document.currency_id),
            base=base_currency(db, document.company_id),
            posted_at=reversal.posted_at,
            actor=actor,
            source_doc_type=fiscal_sales.REVERSAL_SOURCE,
        )
    # `matched`, `invoiced` and `received` are queries over posted, unreversed lines, so
    # reversing this document has already changed all three. The stored status on every receipt
    # and every order it touched has to follow.
    _refresh_reversed_receipts(db, document)
    _refresh_reversed_orders(db, document)
    audit(
        db,
        document.company_id,
        "partner_document.reversed",
        "partner_documents",
        document.id,
        actor=actor,
        after={"reversal_entry_id": reversal.id, "reason": reason, "on": on_date.isoformat()},
        request=request,
    )
    return document


def pending_instruments(
    db: Session, company_id: int, *, as_of: date, role: PartnerRole | None = None
) -> list[PartnerDocument]:
    """Instruments *due* on or before `as_of` — what a maturity run would move."""
    return _outstanding_instruments(db, company_id, role=role, due_by=as_of)


def outstanding_instruments(
    db: Session, company_id: int, *, role: PartnerRole | None = None
) -> list[PartnerDocument]:
    """Every post-dated instrument whose cash has not landed, due or not.

    `pending_instruments` answers "what would a run as at this date move?"; a screen has to
    show the ones still ahead of their maturity date too, or the only way to know a cheque
    exists is to run maturity and see whether anything happens."""
    return _outstanding_instruments(db, company_id, role=role, due_by=None)


def _outstanding_instruments(
    db: Session, company_id: int, *, role: PartnerRole | None, due_by: date | None
) -> list[PartnerDocument]:
    statement = select(PartnerDocument).where(
        PartnerDocument.company_id == company_id,
        PartnerDocument.status == DocumentStatus.POSTED,
        PartnerDocument.maturity_date.is_not(None),
        PartnerDocument.matured_entry_id.is_(None),
    )
    if due_by is not None:
        statement = statement.where(PartnerDocument.maturity_date <= due_by)
    if role is not None:
        statement = statement.where(PartnerDocument.role == role)
    return list(db.scalars(statement.order_by(PartnerDocument.maturity_date, PartnerDocument.id)))


@dataclass(frozen=True)
class SkippedInstrument:
    document: PartnerDocument
    #: `no_post_dated_account` (the company has no post-dated account configured for the role)
    #: or `no_cash_account` (the instrument names none, so there is nowhere to move it to).
    reason: str


@dataclass(frozen=True)
class MaturityRun:
    """What a run did, and — just as importantly — what it left alone.

    A run used to return only the documents it banked, which made "nothing happened" and
    "nothing was due" and "three cheques have no cash account" the same empty list. Callers
    get all three now: `matured` moved, `skipped` were due but unbankable and say why, and
    `waiting` had not matured at `as_of` and were never candidates."""

    as_of: date
    matured: list[PartnerDocument]
    skipped: list[SkippedInstrument]
    waiting: list[PartnerDocument]


def mature_instruments(
    db: Session,
    company_id: int,
    *,
    as_of: date,
    actor: User,
    role: PartnerRole | None = None,
    request: Request | None = None,
) -> MaturityRun:
    """Move matured post-dated instruments from the post-dated account into the bank. The
    transfer uses the document's own booking rate, so it produces no exchange difference.

    Only instruments with `maturity_date <= as_of` are touched. Everything else outstanding is
    returned in `waiting`, untouched — a run is not a "bank everything" button."""
    due_ids = {
        document.id for document in pending_instruments(db, company_id, as_of=as_of, role=role)
    }
    waiting = [
        document
        for document in outstanding_instruments(db, company_id, role=role)
        if document.id not in due_ids
    ]
    matured: list[PartnerDocument] = []
    skipped: list[SkippedInstrument] = []
    for document in pending_instruments(db, company_id, as_of=as_of, role=role):
        accounts = role_accounts(db, company_id, document.role)
        if accounts.post_dated_account_id is None:
            skipped.append(SkippedInstrument(document, "no_post_dated_account"))
            continue
        if document.cash_account_id is None:
            skipped.append(SkippedInstrument(document, "no_cash_account"))
            continue
        # Mirror of the original settlement: clear the post-dated account, debit/credit bank.
        sign = -document.direction
        common = {
            "currency_id": document.currency_id,
            "exchange_rate": document.exchange_rate,
            "branch_id": document.branch_id,
            "partner_type": PARTNER_TYPE_FOR_ROLE[document.role],
            "partner_id": document.partner_id,
            "project_id": document.project_id,
            "source_doc_type": "partner_document",
            "source_doc_id": document.id,
        }
        entry = posting.post(
            db,
            InstrumentMatured(
                module=document.role.value,
                entry_date=max(document.maturity_date or as_of, document.document_date),
                description=f"Maturity of {document.number}",
                reference=document.reference,
                branch_id=document.branch_id,
                source_doc_type="partner_document",
                source_doc_id=document.id,
                lines=(
                    LineSpec(
                        amount=-sign * document.total_amount,
                        gl_account_id=accounts.post_dated_account_id,
                        description=f"Post-dated {document.number} matured",
                        **common,
                    ),
                    LineSpec(
                        amount=sign * document.total_amount,
                        gl_account_id=document.cash_account_id,
                        description=f"Post-dated {document.number} matured",
                        **common,
                    ),
                ),
            ),
            company_id=company_id,
            actor=actor,
        )
        assert entry is not None
        document.matured_entry_id = entry.id
        matured.append(document)
        audit(
            db,
            company_id,
            "partner_document.instrument_matured",
            "partner_documents",
            document.id,
            actor=actor,
            after={"journal_entry_id": entry.id, "as_of": as_of.isoformat()},
            request=request,
        )
    db.flush()
    return MaturityRun(as_of=as_of, matured=matured, skipped=skipped, waiting=waiting)


# --- Journal batches -------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchLineInput:
    partner_id: int
    contra_account_id: int
    #: Signed in the partner's normal direction: positive increases what an AR customer owes
    #: you, or what you owe an AP supplier. Negative is the credit side.
    amount: Decimal
    description: str
    tax_code_id: int | None = None
    branch_id: int | None = None
    project_id: int | None = None
    due_date: date | None = None


@dataclass(frozen=True)
class BatchInput:
    batch_date: date
    lines: tuple[BatchLineInput, ...]
    reference: str | None = None
    description: str | None = None


def post_batch(
    db: Session,
    company_id: int,
    role: PartnerRole,
    data: BatchInput,
    *,
    actor: User,
    permissions: set[str] | None = None,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> tuple[list[PartnerDocument], bool]:
    """A journal batch: many partners charged or credited in one entry session.

    Each line posts as its own partner document under the JNL transaction type, so each
    creates an open item that can be allocated and aged like any other. They are separate
    documents but one unit of work — the caller commits once, so a line that fails (a partner
    on hold, a closed period, a control account in the contra column) takes the whole batch
    with it rather than leaving half of it posted.

    Direction comes from the sign, which is why this reuses the existing kinds rather than
    inventing a journal kind whose direction would have to vary per line: positive is the
    partner's normal side (invoice-shaped), negative is the other (credit-note-shaped).
    """
    if not data.lines:
        raise LedgerStateError("A batch needs at least one line", code="empty_batch")

    if idempotency_key:
        replayed = _replay_batch(db, company_id, idempotency_key, idempotency_hash)
        if replayed:
            return replayed, True

    documents: list[PartnerDocument] = []
    for index, line in enumerate(data.lines):
        if line.amount == ZERO:
            raise LedgerStateError(
                f"Line {index + 1} has no amount", code="empty_batch_line",
                field_errors={f"lines.{index}.amount": ["required"]},
            )
        kind = DocumentKind.INVOICE if line.amount > ZERO else DocumentKind.CREDIT_NOTE
        try:
            document, _ = post_document(
                db,
                company_id,
                role,
                DocumentInput(
                    kind=kind,
                    partner_id=line.partner_id,
                    document_date=data.batch_date,
                    due_date=line.due_date,
                    description=line.description,
                    reference=data.reference,
                    branch_id=line.branch_id,
                    project_id=line.project_id,
                    transaction_type=JOURNAL_TRANSACTION_TYPE,
                    lines=(
                        LineInput(
                            unit_price=abs(line.amount),
                            gl_account_id=line.contra_account_id,
                            tax_code_id=line.tax_code_id,
                            description=line.description,
                            branch_id=line.branch_id,
                            project_id=line.project_id,
                        ),
                    ),
                ),
                actor=actor,
                permissions=permissions,
                # The batch owns idempotency as a whole; its lines are not separately keyed.
                idempotency_key=f"{idempotency_key}:{index}" if idempotency_key else None,
                idempotency_hash=idempotency_hash,
                request=request,
            )
        except (LedgerStateError, PostingError) as err:
            # Re-point the error at the offending line so the grid can show it in place.
            raise type(err)(
                f"Line {index + 1}: {err.message}",
                code=err.code,
                field_errors={
                    f"lines.{index}.{key}": value for key, value in err.field_errors.items()
                }
                or {f"lines.{index}": [err.code]},
            ) from err
        documents.append(document)
    return documents, False


def _replay_batch(
    db: Session, company_id: int, idempotency_key: str, idempotency_hash: str | None
) -> list[PartnerDocument]:
    """Batch lines are keyed `<batch key>:<index>`, so a replay finds them all in order."""
    rows = list(
        db.scalars(
            select(PartnerDocument)
            .where(
                PartnerDocument.company_id == company_id,
                PartnerDocument.idempotency_key.like(f"{idempotency_key}:%"),
            )
            .order_by(PartnerDocument.id)
        )
    )
    if rows and idempotency_hash is not None:
        for row in rows:
            if row.idempotency_hash not in (None, idempotency_hash):
                raise ConflictError(
                    "This Idempotency-Key was used with a different batch",
                    code="idempotency_key_reused",
                )
    return rows


def _refresh_matched_receipts(db: Session, computed: list) -> None:
    """Bring every GRN this document touched back in line with what its lines now imply.

    Decision 4 makes the GRN status a **stored workflow column written only by the order
    service** — so the service that changes what has matched is the service that must write
    it. Leaving it to callers meant every test and every endpoint had to remember, and
    `verify_order_statuses()` would report drift for a posting that was otherwise perfectly
    correct. It is a cache over a query, and the query just moved.
    """
    from app.order_entry import grn as grn_service

    seen: set[int] = set()
    for line in computed:
        grn_line = getattr(line, "matched_grn_line", None)
        if grn_line is None or grn_line.grn_id in seen:
            continue
        seen.add(grn_line.grn_id)
        grn = db.get(GoodsReceivedNote, grn_line.grn_id)
        if grn is not None:
            grn_service.refresh_status(db, grn)


def _refresh_fulfilled_orders(db: Session, company_id: int, computed: list) -> None:
    """Bring every order this document fulfilled back in line with what its lines now imply.

    The same rule as `_refresh_matched_receipts`, one table along: the order status is a stored
    workflow column **written only by the order service** (decision 4), so the service that
    changes what has been fulfilled is the service that must write it. Leaving it to callers
    means every endpoint and every test has to remember, and `verify_order_statuses()` reports
    drift for a posting that was otherwise perfectly correct.
    """
    order_quantities.refresh_sales_orders_for_lines(
        db,
        company_id,
        [
            line.source.sales_order_line_id
            for line in computed
            if line.source.sales_order_line_id is not None
        ],
    )
    order_quantities.refresh_purchase_orders_for_lines(
        db,
        company_id,
        [
            line.source.purchase_order_line_id
            for line in computed
            if line.source.purchase_order_line_id is not None
        ],
    )


def _refresh_reversed_orders(db: Session, document: PartnerDocument) -> None:
    """The reversal half of `_refresh_fulfilled_orders`. `invoiced` and `received` are queries
    over posted, unreversed lines, so reversing this document has already changed both; the
    stored status on every order it touched has to follow."""
    order_quantities.refresh_sales_orders_for_lines(
        db,
        document.company_id,
        [line.sales_order_line_id for line in document.lines if line.sales_order_line_id],
    )
    order_quantities.refresh_purchase_orders_for_lines(
        db,
        document.company_id,
        [line.purchase_order_line_id for line in document.lines if line.purchase_order_line_id],
    )


def _refresh_reversed_receipts(db: Session, document: PartnerDocument) -> None:
    """The reversal half of `_refresh_matched_receipts`."""
    from app.order_entry import grn as grn_service

    seen: set[int] = set()
    for line in document.lines:
        if line.grn_line_id is None:
            continue
        grn_line = db.get(GoodsReceivedNoteLine, line.grn_line_id)
        if grn_line is None or grn_line.grn_id in seen:
            continue
        seen.add(grn_line.grn_id)
        grn = db.get(GoodsReceivedNote, grn_line.grn_id)
        if grn is not None:
            grn_service.refresh_status(db, grn)


def _reserve_document_id(db: Session) -> int:
    """Take the next `partner_documents.id` before anything is written.

    The same cycle-breaker `inventory_documents` uses, and for the same reason: the companion
    stock entry's moves carry `source_doc_id` and `stock_moves` refuses UPDATE, so the id has
    to exist before the companion posts — and the companion posts before the partner side,
    which is what produces the number the document is finally written with.
    """
    return int(
        db.execute(
            text("SELECT nextval(pg_get_serial_sequence('partner_documents', 'id'))")
        ).scalar_one()
    )


def _reserve_document_line_id(db: Session) -> int:
    """One `partner_document_lines.id`, reserved before the companion posts. A rolled-back
    posting burns one, which is what a sequence is for — the *document number* comes from
    `document_sequences` and stays gapless."""
    return int(
        db.execute(
            text("SELECT nextval(pg_get_serial_sequence('partner_document_lines', 'id'))")
        ).scalar_one()
    )


def _booking_rate(db: Session, currency: Currency, data: DocumentInput) -> Decimal:
    """The rate the document books at — the keyed one, or the dated one for its date."""
    if data.exchange_rate is not None:
        return data.exchange_rate
    return rate_on(db, currency, data.document_date)


def _variance_account_id(gl_settings: object) -> int:
    """Where the difference between what was accrued and what was billed lands."""
    account_id = getattr(gl_settings, "purchase_price_variance_account_id", None)
    if account_id is None:
        raise LedgerStateError(
            "No purchase price variance account is configured — set it on Order defaults",
            code="gl_setting_missing",
            field_errors={"purchase_price_variance_account_id": ["required"]},
        )
    return int(account_id)


def _cogs_account_id(item: Item, gl_settings: object) -> int:
    """Where a sale's cost lands: the item's own COGS account, then the company default
    (decision 2). A stock item that resolves to neither cannot be sold, and saying so here is
    better than an entry that quietly charges the wrong account."""
    account_id = item.cogs_account_id or getattr(gl_settings, "cogs_account_id", None)
    if account_id is None:
        raise LedgerStateError(
            f"{item.code} has no COGS account and no company default is set",
            code="gl_setting_missing",
            field_errors={"cogs_account_id": ["required"]},
        )
    return int(account_id)
