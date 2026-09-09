"""Partner documents — one service, six kinds (P4 step 3).

The `(role, kind)` matrix below is the whole of the AR/AP asymmetry: it decides the document
type, the sign the document puts on its control account, and the transaction-type key used
for account determination. Everything else — tax, numbering, credit limits, period
enforcement, reversal — is written once and runs for both roles.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import NotFoundError
from app.core.permissions import AP_CREDIT_LIMIT_OVERRIDE, AR_CREDIT_LIMIT_OVERRIDE
from app.kernel import posting
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import InstrumentMatured, LineSpec, PartnerDocumentPosted
from app.kernel.money import (
    ZERO,
    base_currency,
    is_rounded,
    resolve_tax_code,
    round_amount,
    split_tax,
    to_base,
)
from app.kernel.sequences import DocType
from app.models.currency import Currency
from app.models.gl import CASHBOOK_CONTROL_TYPES, GLAccount
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
from app.subledger import masters
from app.subledger.common import PARTNER_TYPE_FOR_ROLE, audit, control_account_for, role_accounts
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

CREDIT_LIMIT_OVERRIDE = {
    PartnerRole.AR: AR_CREDIT_LIMIT_OVERRIDE,
    PartnerRole.AP: AP_CREDIT_LIMIT_OVERRIDE,
}


@dataclass(frozen=True)
class LineInput:
    unit_price: Decimal
    quantity: Decimal = ONE
    discount_percent: Decimal = ZERO
    description: str | None = None
    gl_account_id: int | None = None
    transaction_type: str | None = None
    tax_code_id: int | None = None
    branch_id: int | None = None
    project_id: int | None = None


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
    lines: tuple[LineInput, ...] = field(default_factory=tuple)
    # Settlements carry an amount and a cash account instead of lines.
    amount: Decimal | None = None
    cash_account_id: int | None = None
    instrument_type: InstrumentType | None = None
    maturity_date: date | None = None


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

    @property
    def gross(self) -> Decimal:
        return self.net + self.tax


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
            currency=currency,
            tax_mode=tax_mode,
            settings=settings,
            branch_id=branch_id,
            project_id=project_id,
        )
        net_total = sum((line.net for line in computed), ZERO)
        tax_total = sum((line.tax for line in computed), ZERO)
        total = net_total + tax_total
    if total <= ZERO:
        raise PostingError(
            "A document must total more than zero",
            code="invalid_amount",
            field_errors={"lines": ["document total must be positive"]},
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
    # read back onto the stored line instead of being derived a second time.
    net_positions: list[int] = []
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
            net_positions.append(len(specs))
            specs.append(
                LineSpec(
                    amount=-spec.direction * line.net,
                    gl_account_id=line.gl_account_id,
                    transaction_type=line.transaction_type or spec.transaction_type,
                    project_id=line.project_id,
                    tax_code_id=line.tax_code_id,
                    tax_amount=-spec.direction * line.tax,
                    description=line.source.description or data.description,
                    currency_id=currency.id,
                    exchange_rate=data.exchange_rate,
                    branch_id=line.branch_id,
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
        company_id=company_id,
        role=role,
        kind=data.kind,
        number=entry.number,
        doc_type=spec.doc_type,
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
        cash_account_id=data.cash_account_id,
        status=DocumentStatus.POSTED,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(document)
    db.flush()
    db.add_all(
        [
            PartnerDocumentLine(
                company_id=company_id,
                document_id=document.id,
                line_no=index,
                description=line.source.description,
                quantity=line.source.quantity,
                unit_price=line.source.unit_price,
                discount_percent=line.source.discount_percent,
                gl_account_id=_line_account_id(entry, net_positions[index - 1]),
                tax_code_id=line.tax_code_id,
                branch_id=line.branch_id or entry.lines[0].branch_id,
                project_id=line.project_id,
                net_amount=line.net,
                tax_amount=line.tax,
                gross_amount=line.gross,
            )
            for index, line in enumerate(computed, 1)
        ]
    )
    db.flush()
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


def _line_account_id(entry: JournalEntry, position: int) -> int:
    """The engine resolved the account (line override, partner default, transaction type);
    read it back rather than re-deriving it, so the document and the ledger agree."""
    return entry.lines[position].gl_account_id


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


def _compute_lines(
    db: Session,
    company_id: int,
    data: DocumentInput,
    *,
    currency: Currency,
    tax_mode: TaxMode,
    settings: object,
    branch_id: int | None,
    project_id: int | None,
) -> list[_ComputedLine]:
    """Per-line tax, half-up to the document currency's decimals (decision 11). Exclusive:
    the entered amount is net. Inclusive: it is gross and the tax is carved out of it."""
    places = currency.decimal_places
    default_tax_code_id = getattr(settings, "default_tax_code_id", None)
    computed: list[_ComputedLine] = []
    for index, line in enumerate(data.lines):
        gross_or_net = round_amount(
            line.quantity * line.unit_price * (ONE - line.discount_percent / HUNDRED), places
        )
        if gross_or_net <= ZERO:
            raise PostingError(
                f"Line {index + 1} must be worth more than zero",
                code="invalid_amount",
                field_errors={f"lines.{index}.unit_price": ["must be positive"]},
            )
        tax_code_id = line.tax_code_id if line.tax_code_id is not None else default_tax_code_id
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
                gl_account_id=line.gl_account_id,
                transaction_type=line.transaction_type,
                tax_code_id=tax_code_id,
                branch_id=line.branch_id or branch_id,
                project_id=line.project_id or project_id,
                net=net,
                tax=tax,
            )
        )
    return computed


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
    builds_exposure = DOCUMENT_MATRIX[(role, DocumentKind.INVOICE)].direction
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
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> PartnerDocument:
    """The kernel reversal plus the open-item unwind. A document that has been allocated
    must be unallocated first — otherwise the reversal would leave the counterparty's open
    item pointing at a document that no longer exists in the ledger."""
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
    statement = select(PartnerDocument).where(
        PartnerDocument.company_id == company_id,
        PartnerDocument.status == DocumentStatus.POSTED,
        PartnerDocument.maturity_date.is_not(None),
        PartnerDocument.maturity_date <= as_of,
        PartnerDocument.matured_entry_id.is_(None),
    )
    if role is not None:
        statement = statement.where(PartnerDocument.role == role)
    return list(db.scalars(statement.order_by(PartnerDocument.maturity_date, PartnerDocument.id)))


def mature_instruments(
    db: Session,
    company_id: int,
    *,
    as_of: date,
    actor: User,
    role: PartnerRole | None = None,
    request: Request | None = None,
) -> list[PartnerDocument]:
    """Move matured post-dated instruments from the post-dated account into the bank. The
    transfer uses the document's own booking rate, so it produces no exchange difference."""
    matured: list[PartnerDocument] = []
    for document in pending_instruments(db, company_id, as_of=as_of, role=role):
        accounts = role_accounts(db, company_id, document.role)
        if accounts.post_dated_account_id is None or document.cash_account_id is None:
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
    return matured
