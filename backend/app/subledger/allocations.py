"""Allocations: matching debit documents against credit documents, with realized FX and
settlement discount posted on the allocation date (P4 step 4, decisions 4–6).

Both sides convert at **their own document's booking rate**; the base-currency difference is
realized FX and posts through the PostingEngine against the control account. That posting is
precisely what keeps `SUM(open items × booking rate) == control account balance` true, which
is the invariant the whole subledger rests on.

Unallocation posts a mirror allocation with negated lines and a frozen-base journal reversal
— one reversal only, matching the P2 rule for journal entries.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import NotFoundError
from app.kernel import posting
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import AllocationPosted, LineSpec
from app.kernel.money import ZERO, base_currency, is_rounded, round_amount
from app.kernel.periods import assert_period_open, find_period
from app.kernel.sequences import DocType, claim_number
from app.models.currency import Currency
from app.models.partner import PartnerRole, PaymentTerms
from app.models.subledger import (
    Allocation,
    AllocationLine,
    DocumentKind,
    DocumentStatus,
    PartnerDocument,
)
from app.models.user import User
from app.subledger import masters
from app.subledger.common import PARTNER_TYPE_FOR_ROLE, audit, role_accounts
from app.subledger.openitems import recompute_open_amount

HUNDRED = Decimal(100)


@dataclass(frozen=True)
class PairInput:
    debit_document_id: int
    credit_document_id: int
    amount: Decimal
    discount_amount: Decimal = ZERO


@dataclass
class _Pair:
    debit: PartnerDocument
    credit: PartnerDocument
    amount: Decimal
    discount_amount: Decimal
    discount_document: PartnerDocument | None
    fx_base_amount: Decimal


@dataclass(frozen=True)
class Posting:
    gl_account_id: int
    description: str
    base_amount: Decimal
    is_control: bool = False


@dataclass(frozen=True)
class Preview:
    currency: Currency
    pairs: list[_Pair]
    postings: list[Posting]

    @property
    def total_allocated(self) -> Decimal:
        return sum((pair.amount for pair in self.pairs), ZERO)

    @property
    def total_discount(self) -> Decimal:
        return sum((pair.discount_amount for pair in self.pairs), ZERO)

    @property
    def total_fx_base(self) -> Decimal:
        return sum((pair.fx_base_amount for pair in self.pairs), ZERO)


# --- Preparation -----------------------------------------------------------------------------


def _load_document(db: Session, company_id: int, document_id: int) -> PartnerDocument:
    document = db.get(PartnerDocument, document_id)
    if document is None or document.company_id != company_id:
        raise NotFoundError(f"Document {document_id} not found")
    if document.status != DocumentStatus.POSTED:
        raise LedgerStateError(
            f"{document.number} is reversed and cannot be allocated",
            code="document_reversed",
        )
    return document


def _terms_of(db: Session, document: PartnerDocument) -> PaymentTerms | None:
    if document.payment_terms_id is None:
        return None
    return masters.get_payment_terms(db, document.company_id, document.payment_terms_id)


def max_discount(
    db: Session, document: PartnerDocument, *, on: date, currency: Currency
) -> Decimal:
    """The settlement discount a document still qualifies for on `on` (decision 6). Zero once
    the discount window has closed — the discount is a fact of the *allocation date*."""
    terms = _terms_of(db, document)
    if terms is None or terms.discount_percent <= ZERO:
        return ZERO
    deadline = terms.discount_deadline(document.document_date)
    if deadline is None or on > deadline:
        return ZERO
    return min(
        round_amount(
            document.total_amount * terms.discount_percent / HUNDRED, currency.decimal_places
        ),
        recompute_open_amount(db, document),
    )


def prepare(
    db: Session,
    company_id: int,
    role: PartnerRole,
    *,
    partner_id: int,
    allocation_date: date,
    pairs: list[PairInput],
) -> Preview:
    """Validates a set of pairs and computes the realized FX and discount they will post.
    The allocation screen calls this for its "preview before Post"."""
    if not pairs:
        raise PostingError("An allocation needs at least one pair", code="no_allocation_lines")
    partner = masters.get_partner(db, company_id, partner_id)
    base = base_currency(db, company_id)
    accounts = role_accounts(db, company_id, role)

    currency: Currency | None = None
    prepared: list[_Pair] = []
    # Documents can appear in several pairs; track consumption across the whole allocation.
    remaining: dict[int, Decimal] = {}

    for index, pair in enumerate(pairs):
        debit = _load_document(db, company_id, pair.debit_document_id)
        credit = _load_document(db, company_id, pair.credit_document_id)
        for document in (debit, credit):
            if document.role != role or document.partner_id != partner.id:
                raise LedgerStateError(
                    f"{document.number} belongs to a different partner or role",
                    code="allocation_partner_mismatch",
                )
            # Recomputed, never the stored `open_amount` column: a cache that has drifted
            # must not be able to authorise an over-allocation (decision 3).
            remaining.setdefault(document.id, recompute_open_amount(db, document))
        if debit.direction != 1 or credit.direction != -1:
            raise LedgerStateError(
                "An allocation matches one debit document against one credit document",
                code="allocation_direction_mismatch",
            )
        if debit.currency_id != credit.currency_id:
            raise LedgerStateError(
                f"{debit.number} is in a different currency from {credit.number}",
                code="cross_currency_allocation_unsupported",
            )
        if currency is None:
            currency = db.get(Currency, debit.currency_id)
            assert currency is not None
        elif currency.id != debit.currency_id:
            raise LedgerStateError(
                "Every pair in one allocation must share a currency",
                code="cross_currency_allocation_unsupported",
            )

        if pair.amount <= ZERO or not is_rounded(pair.amount, currency.decimal_places):
            raise PostingError(
                f"Pair {index + 1}: allocate a positive amount in {currency.code}",
                code="invalid_amount",
                field_errors={f"pairs.{index}.amount": ["invalid amount"]},
            )

        discount_document = _discount_side(debit, credit)
        discount = pair.discount_amount
        if discount != ZERO:
            if discount_document is None:
                raise LedgerStateError(
                    "A settlement discount needs an invoice on one side of the pair",
                    code="discount_without_invoice",
                )
            if accounts.discount_account_id is None:
                raise PostingError(
                    "Set the settlement discount account in AR/AP defaults",
                    code="gl_setting_missing",
                    field_errors={"discount_amount": ["no discount account configured"]},
                )
            allowed = max_discount(db, discount_document, on=allocation_date, currency=currency)
            if discount < ZERO or discount > allowed:
                raise PostingError(
                    f"Pair {index + 1}: the discount on {discount_document.number} may not "
                    f"exceed {allowed}",
                    code="discount_exceeds_terms",
                    field_errors={f"pairs.{index}.discount_amount": ["exceeds the terms"]},
                )
        else:
            discount_document = None

        for document in (debit, credit):
            consumed = pair.amount + (
                discount if discount_document is not None and discount_document.id == document.id
                else ZERO
            )
            if consumed > remaining[document.id]:
                raise PostingError(
                    f"{document.number} has only {remaining[document.id]} open",
                    code="allocation_exceeds_open_amount",
                    field_errors={f"pairs.{index}.amount": ["exceeds the open amount"]},
                )
            remaining[document.id] -= consumed

        fx_base = round_amount(
            pair.amount * debit.exchange_rate, base.decimal_places
        ) - round_amount(pair.amount * credit.exchange_rate, base.decimal_places)
        prepared.append(
            _Pair(
                debit=debit,
                credit=credit,
                amount=pair.amount,
                discount_amount=discount,
                discount_document=discount_document,
                fx_base_amount=fx_base,
            )
        )

    assert currency is not None
    return Preview(
        currency=currency, pairs=prepared, postings=_postings(db, role, base, prepared)
    )


def _discount_side(debit: PartnerDocument, credit: PartnerDocument) -> PartnerDocument | None:
    """The discount belongs to the document that carries the payment terms — the invoice,
    whichever side of the control account it sits on."""
    for document in (debit, credit):
        if document.kind == DocumentKind.INVOICE:
            return document
    return None


def _postings(
    db: Session, role: PartnerRole, base: Currency, pairs: list[_Pair]
) -> list[Posting]:
    """Base-currency GL effect of an allocation: control against FX gain/loss and the
    settlement discount account. Returns an empty list when nothing needs to post."""
    accounts = role_accounts(db, base.company_id, role)
    by_account: dict[int, Decimal] = {}
    descriptions: dict[int, str] = {}
    control_totals: dict[int, Decimal] = {}

    def add(account_id: int, amount: Decimal, description: str) -> None:
        by_account[account_id] = by_account.get(account_id, ZERO) + amount
        descriptions.setdefault(account_id, description)

    for pair in pairs:
        if pair.debit.control_account_id != pair.credit.control_account_id:
            raise LedgerStateError(
                f"{pair.debit.number} and {pair.credit.number} use different control accounts",
                code="control_account_mismatch",
            )
        control_id = pair.debit.control_account_id
        control_totals.setdefault(control_id, ZERO)
        if pair.fx_base_amount != ZERO:
            gain = pair.fx_base_amount < ZERO
            account_id = accounts.fx_gain_account_id if gain else accounts.fx_loss_account_id
            if account_id is None:
                raise PostingError(
                    "Set the realized exchange gain/loss accounts in AR/AP defaults",
                    code="gl_setting_missing",
                )
            add(
                account_id,
                pair.fx_base_amount,
                "Realized exchange gain" if gain else "Realized exchange loss",
            )
            control_totals[control_id] -= pair.fx_base_amount
        if pair.discount_amount != ZERO and pair.discount_document is not None:
            assert accounts.discount_account_id is not None
            discount_base = round_amount(
                pair.discount_amount * pair.discount_document.exchange_rate, base.decimal_places
            )
            # Signed by the invoice's side of the control account: AR grants a discount
            # (credit the customer, debit an expense), AP receives one (debit the supplier,
            # credit income). One expression, both roles.
            signed = pair.discount_document.direction * discount_base
            add(
                accounts.discount_account_id,
                signed,
                "Settlement discount granted"
                if role == PartnerRole.AR
                else "Settlement discount received",
            )
            control_totals[control_id] -= signed

    postings = [
        Posting(
            gl_account_id=account_id,
            description=descriptions[account_id],
            base_amount=amount,
        )
        for account_id, amount in sorted(by_account.items())
        if amount != ZERO
    ]
    control_postings = [
        Posting(
            gl_account_id=account_id,
            description="Allocation",
            base_amount=amount,
            is_control=True,
        )
        for account_id, amount in sorted(control_totals.items())
        if amount != ZERO
    ]
    if not postings and not control_postings:
        return []
    return control_postings + postings


# --- Posting ----------------------------------------------------------------------------------


def allocate(
    db: Session,
    company_id: int,
    role: PartnerRole,
    *,
    partner_id: int,
    allocation_date: date,
    pairs: list[PairInput],
    description: str | None = None,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> tuple[Allocation, bool]:
    if idempotency_key:
        replayed = _replay(db, company_id, idempotency_key, idempotency_hash)
        if replayed is not None:
            return replayed, True

    preview = prepare(
        db,
        company_id,
        role,
        partner_id=partner_id,
        allocation_date=allocation_date,
        pairs=pairs,
    )
    base = base_currency(db, company_id)
    entry = _post_postings(
        db,
        company_id,
        role,
        partner_id=partner_id,
        on=allocation_date,
        base=base,
        postings=preview.postings,
        description=description or "Allocation",
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
        actor=actor,
    )

    claimed = claim_number(db, company_id, DocType.ALLOCATION)
    allocation = Allocation(
        company_id=company_id,
        number=claimed.number,
        role=role,
        partner_id=partner_id,
        currency_id=preview.currency.id,
        allocation_date=allocation_date,
        journal_entry_id=entry.id if entry is not None else None,
        description=description,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(allocation)
    db.flush()
    _write_lines(db, allocation, preview.pairs, sign=1)
    audit(
        db,
        company_id,
        "allocation.posted",
        "allocations",
        allocation.id,
        actor=actor,
        after={
            "role": role.value,
            "number": allocation.number,
            "partner_id": partner_id,
            "allocated": str(preview.total_allocated),
            "discount": str(preview.total_discount),
            "realized_fx_base": str(preview.total_fx_base),
        },
        request=request,
    )
    return allocation, False


def unallocate(
    db: Session,
    allocation: Allocation,
    *,
    on_date: date,
    reason: str,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> Allocation:
    """Mirror allocation with negated lines, plus the frozen-base reversal of its FX and
    discount entry. One reversal only, enforced by `uq_allocations_reverses_allocation_id`.

    `on_date` must fall in an **open** period. That is checked here even when the allocation
    posted nothing to the ledger, so an allocation with no FX and no discount cannot be
    unwound into a closed month while one that moved money cannot — the kernel would refuse
    the reversal with `period_not_open` and the two paths would disagree.
    """
    if allocation.reverses_allocation_id is not None:
        raise LedgerStateError(
            f"{allocation.number} is itself a reversal", code="cannot_reverse_a_reversal"
        )
    if idempotency_key:
        replayed = _replay(db, allocation.company_id, idempotency_key, idempotency_hash)
        if replayed is not None:
            return replayed
    existing = db.scalar(
        select(Allocation.id).where(
            Allocation.company_id == allocation.company_id,
            Allocation.reverses_allocation_id == allocation.id,
        )
    )
    if existing is not None:
        raise LedgerStateError(
            f"{allocation.number} was already unallocated", code="allocation_already_reversed"
        )
    if on_date < allocation.allocation_date:
        raise LedgerStateError(
            f"An unallocation cannot be dated before {allocation.number}",
            code="reversal_before_original",
        )
    assert_period_open(find_period(db, allocation.company_id, on_date))

    entry = None
    if allocation.journal_entry_id is not None:
        entry = posting.reverse(
            db,
            allocation.journal_entry_id,
            company_id=allocation.company_id,
            on_date=on_date,
            reason=reason,
            actor=actor,
            idempotency_key=idempotency_key,
            idempotency_hash=idempotency_hash,
        )

    claimed = claim_number(db, allocation.company_id, DocType.ALLOCATION)
    reversal = Allocation(
        company_id=allocation.company_id,
        number=claimed.number,
        role=allocation.role,
        partner_id=allocation.partner_id,
        currency_id=allocation.currency_id,
        allocation_date=on_date,
        journal_entry_id=entry.id if entry is not None else None,
        reverses_allocation_id=allocation.id,
        description=f"Unallocation of {allocation.number}: {reason}",
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(reversal)
    db.flush()

    documents = {}
    for line in allocation.lines:
        for document_id in (line.debit_document_id, line.credit_document_id):
            if document_id not in documents:
                documents[document_id] = db.get(PartnerDocument, document_id)
    db.add_all(
        [
            AllocationLine(
                company_id=allocation.company_id,
                allocation_id=reversal.id,
                line_no=line.line_no,
                debit_document_id=line.debit_document_id,
                credit_document_id=line.credit_document_id,
                amount=-line.amount,
                discount_amount=-line.discount_amount,
                discount_document_id=line.discount_document_id,
                fx_base_amount=-line.fx_base_amount,
            )
            for line in allocation.lines
        ]
    )
    db.flush()
    _refresh_open_amounts(db, list(documents.values()))
    audit(
        db,
        allocation.company_id,
        "allocation.reversed",
        "allocations",
        allocation.id,
        actor=actor,
        after={"reversal_id": reversal.id, "reason": reason, "on": on_date.isoformat()},
        request=request,
    )
    return reversal


def auto_allocate_pairs(
    db: Session,
    company_id: int,
    role: PartnerRole,
    *,
    partner_id: int,
    allocation_date: date,
    credit_document_id: int | None = None,
) -> list[PairInput]:
    """Oldest-first: the earliest open debit documents are settled by the earliest open
    credit documents, taking any settlement discount the terms still allow."""
    open_documents = list(
        db.scalars(
            select(PartnerDocument)
            .where(
                PartnerDocument.company_id == company_id,
                PartnerDocument.role == role,
                PartnerDocument.partner_id == partner_id,
                PartnerDocument.status == DocumentStatus.POSTED,
                PartnerDocument.open_amount > 0,
                PartnerDocument.document_date <= allocation_date,
            )
            .order_by(PartnerDocument.document_date, PartnerDocument.id)
        )
    )
    debits = [d for d in open_documents if d.direction == 1]
    credits = [d for d in open_documents if d.direction == -1]
    if credit_document_id is not None:
        credits = [d for d in credits if d.id == credit_document_id]

    remaining = {d.id: recompute_open_amount(db, d) for d in open_documents}
    pairs: list[PairInput] = []
    for credit in credits:
        for debit in debits:
            if remaining[credit.id] <= ZERO:
                break
            if remaining[debit.id] <= ZERO or debit.currency_id != credit.currency_id:
                continue
            currency = db.get(Currency, debit.currency_id)
            assert currency is not None
            discount_document = _discount_side(debit, credit)
            discount = ZERO
            if discount_document is not None:
                allowed = max_discount(
                    db, discount_document, on=allocation_date, currency=currency
                )
                discount = min(allowed, remaining[discount_document.id])
            debit_capacity = remaining[debit.id] - (
                discount if discount_document is debit else ZERO
            )
            credit_capacity = remaining[credit.id] - (
                discount if discount_document is credit else ZERO
            )
            amount = min(debit_capacity, credit_capacity)
            if amount <= ZERO:
                continue
            pairs.append(
                PairInput(
                    debit_document_id=debit.id,
                    credit_document_id=credit.id,
                    amount=amount,
                    discount_amount=discount,
                )
            )
            remaining[debit.id] -= amount + (discount if discount_document is debit else ZERO)
            remaining[credit.id] -= amount + (discount if discount_document is credit else ZERO)
    return pairs


# --- Internals ---------------------------------------------------------------------------------


def _replay(
    db: Session, company_id: int, key: str, request_hash: str | None
) -> Allocation | None:
    allocation = db.scalar(
        select(Allocation).where(
            Allocation.company_id == company_id, Allocation.idempotency_key == key
        )
    )
    if allocation is None:
        return None
    if request_hash is not None and allocation.idempotency_hash != request_hash:
        raise LedgerStateError(
            f"Idempotency-Key {key} was already used for a different request "
            f"({allocation.number}); use a new key",
            code="idempotency_key_reused",
        )
    return allocation


def _post_postings(
    db: Session,
    company_id: int,
    role: PartnerRole,
    *,
    partner_id: int,
    on: date,
    base: Currency,
    postings: list[Posting],
    description: str,
    idempotency_key: str | None,
    idempotency_hash: str | None,
    actor: User,
):  # noqa: ANN201 - JournalEntry | None
    if not postings:
        return None
    specs = tuple(
        LineSpec(
            amount=item.base_amount,
            gl_account_id=item.gl_account_id,
            currency_id=base.id,
            description=item.description,
            partner_type=PARTNER_TYPE_FOR_ROLE[role] if item.is_control else None,
            partner_id=partner_id if item.is_control else None,
        )
        for item in postings
    )
    return posting.post(
        db,
        AllocationPosted(
            module=role.value,
            entry_date=on,
            description=description,
            source_doc_type="allocation",
            idempotency_key=idempotency_key,
            idempotency_hash=idempotency_hash,
            lines=specs,
        ),
        company_id=company_id,
        actor=actor,
    )


def _write_lines(db: Session, allocation: Allocation, pairs: list[_Pair], *, sign: int) -> None:
    db.add_all(
        [
            AllocationLine(
                company_id=allocation.company_id,
                allocation_id=allocation.id,
                line_no=index,
                debit_document_id=pair.debit.id,
                credit_document_id=pair.credit.id,
                amount=sign * pair.amount,
                discount_amount=sign * pair.discount_amount,
                discount_document_id=(
                    pair.discount_document.id if pair.discount_document is not None else None
                ),
                fx_base_amount=sign * pair.fx_base_amount,
            )
            for index, pair in enumerate(pairs, 1)
        ]
    )
    db.flush()
    documents: dict[int, PartnerDocument] = {}
    for pair in pairs:
        documents[pair.debit.id] = pair.debit
        documents[pair.credit.id] = pair.credit
    _refresh_open_amounts(db, list(documents.values()))


def _refresh_open_amounts(db: Session, documents: list[PartnerDocument]) -> None:
    """The one place `partner_documents.open_amount` is ever written."""
    for document in documents:
        if document is None:
            continue
        document.open_amount = recompute_open_amount(db, document)
    db.flush()
