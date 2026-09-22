"""Supplier payment runs: N ordinary P4 settlements, one bank line on the statement (P8
decision 7).

**The reading of the plan's "batch supplier payments → single bank line".** The run posts one
settlement per supplier through `post_document()` and one allocation per supplier through
`allocate()`, all in one transaction. Every payment is therefore an ordinary P4 document with
its own `PMT-` number, its own entry, its own open item and its own reversal, and the AP
subledger sees nothing it has not seen since P4. The *single line* is the **statement's**: a
bulk transfer shows at the bank as one debit carrying the run's number, and decision 4's
`payment_run` rule matches that one line to the run's N ledger lines.

A clearing account — post the N settlements to a clearing account, then one bank entry for the
total — was considered and rejected. It buys a literal single ledger line at the price of an
account, a control type and a kernel event, to restate a fact the bank already states; and a
bank that shows one line per beneficiary (which some do) would then need the reverse mapping.

**There are no draft rows.** The selection is previewed and posted in one call, P7's
preview → post shape, which is why `number` is claimed at posting — and why `plan()` raises
every refusal it is going to raise *before* the first write. A run refused for
`payment_exceeds_open` claims no number.

**Reversal is fallible-leg-first**, the rule P6 wrote and P4's `reverse_document` already
follows: `unallocate()` every allocation, then `reverse_document()` every settlement. Reversing
first would leave a posted reversal behind when the unallocation failed; in this order the leg
that can refuse (`document_allocated`, raised by `reverse_document` itself when an allocation
survived) arrives before anything is written. That refusal is the proof the order is right, and
`tests/banking/test_payment_runs.py` asserts it at the service level rather than trusting the
comment.

Nothing here writes a journal line: every posting goes through P4 (`tests/banking/
test_boundary.py`).
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.banking import accounts as accounts_service
from app.banking import matching
from app.core.errors import NotFoundError
from app.kernel.errors import LedgerStateError
from app.kernel.money import base_currency
from app.kernel.sequences import DocType, claim_number
from app.models.banking import (
    BankAccount,
    BankAccountKind,
    BankMatch,
    BankMatchJournalLine,
    PaymentRun,
    PaymentRunLine,
    PaymentRunStatus,
)
from app.models.currency import Currency
from app.models.journal import JournalLine
from app.models.partner import Partner, PartnerRole
from app.models.subledger import (
    Allocation,
    DocumentKind,
    DocumentStatus,
    InstrumentType,
    PartnerDocument,
)
from app.models.user import User
from app.services.audit import record_audit
from app.services.jobs import enqueue
from app.subledger import allocations as allocation_service
from app.subledger import documents as documents_service
from app.subledger.openitems import recompute_open_amount

ZERO = Decimal(0)

#: The job kind the remittance advices are produced under. Defined here rather than in
#: `remittance.py` so that this module does not import the renderer (and, with it, WeasyPrint)
#: to enqueue a job; `app.banking.remittance` registers the handler against the same string.
REMITTANCE_JOB = "remittance_pdf"

#: The generic instruction-file layout (decision 7). A bank-specific layout becomes a mapping on
#: `bank_accounts.instruction_format` when the owner supplies one; until then every bank gets
#: this, and the accountant re-keys whatever their portal wants.
INSTRUCTION_COLUMNS = (
    "beneficiary",
    "bank",
    "account number",
    "amount",
    "currency",
    "reference",
    "supplier code",
)


# --- What a run is asked for ------------------------------------------------------------------


@dataclass(frozen=True)
class RunLineInput:
    """One invoice to pay.

    `amount` is what comes off the invoice's **open amount**, not the cash that leaves the
    bank: a line settling 100 000 with a 2 000 discount taken pays 98 000 and closes the
    invoice. Keeping the input gross is what makes `payment_exceeds_open` a statement about the
    invoice rather than about the discount.
    """

    document_id: int
    amount: Decimal | None = None
    #: The settlement discount P4 computes is taken by default; the user may decline it per
    #: line, which is the only thing this flag does.
    take_discount: bool = True


@dataclass(frozen=True)
class SelectableDocument:
    """An open AP invoice a run could pay, with what it would be worth paying today."""

    document_id: int
    number: str
    partner_id: int
    partner_name: str
    supplier_code: str | None
    document_date: date
    due_date: date | None
    currency_id: int
    total_amount: Decimal
    open_amount: Decimal
    discount_available: Decimal


# --- What a run would do ------------------------------------------------------------------------


@dataclass(frozen=True)
class PreviewLine:
    document_id: int
    document_number: str
    due_date: date | None
    open_amount: Decimal
    #: What comes off the invoice.
    amount: Decimal
    discount_available: Decimal
    #: What is actually taken — zero when the line declined it.
    discount_amount: Decimal

    @property
    def cash_amount(self) -> Decimal:
        return self.amount - self.discount_amount


@dataclass(frozen=True)
class PreviewSupplier:
    partner_id: int
    partner_name: str
    supplier_code: str | None
    bank_name: str | None
    bank_account_number: str | None
    bank_account_holder: str | None
    lines: tuple[PreviewLine, ...]
    #: `bank_details_missing`, and `open_credits` naming the documents. Warnings, never
    #: refusals: the payment is owed either way, and netting a credit note into a payment is
    #: P4's allocation screen's job, not this one's.
    warnings: tuple[str, ...] = ()

    @property
    def total(self) -> Decimal:
        """What this supplier is paid — Σ of the lines net of discount."""
        return sum((line.cash_amount for line in self.lines), ZERO)

    @property
    def discount_total(self) -> Decimal:
        return sum((line.discount_amount for line in self.lines), ZERO)


@dataclass(frozen=True)
class RunPreview:
    bank_account_id: int
    bank_account_code: str
    payment_date: date
    currency_id: int
    currency_code: str
    suppliers: tuple[PreviewSupplier, ...] = field(default_factory=tuple)

    @property
    def total(self) -> Decimal:
        return sum((supplier.total for supplier in self.suppliers), ZERO)

    @property
    def discount_total(self) -> Decimal:
        return sum((supplier.discount_total for supplier in self.suppliers), ZERO)

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(
            warning for supplier in self.suppliers for warning in supplier.warnings
        )


# --- Reads ---------------------------------------------------------------------------------------


def get(db: Session, company_id: int, run_id: int) -> PaymentRun:
    run = db.get(PaymentRun, run_id)
    if run is None or run.company_id != company_id:
        raise NotFoundError("Payment run not found")
    return run


def list_runs(
    db: Session,
    company_id: int,
    *,
    bank_account_id: int | None = None,
    limit: int = 50,
) -> list[PaymentRun]:
    statement = select(PaymentRun).where(PaymentRun.company_id == company_id)
    if bank_account_id is not None:
        statement = statement.where(PaymentRun.bank_account_id == bank_account_id)
    return list(db.scalars(statement.order_by(PaymentRun.id.desc()).limit(limit)))


def lines_of(db: Session, company_id: int, run_id: int) -> list[PaymentRunLine]:
    return list(
        db.scalars(
            select(PaymentRunLine)
            .where(
                PaymentRunLine.company_id == company_id,
                PaymentRunLine.run_id == run_id,
            )
            .order_by(PaymentRunLine.id)
        )
    )


def run_of_settlement(
    db: Session, company_id: int, document_id: int
) -> PaymentRun | None:
    """The run that posted this `PMT-`, if a run did.

    Read by the AP document detail ("Paid in run PYR-n") and by the reversal guard below, which
    is the same question asked for two different reasons.
    """
    return db.scalar(
        select(PaymentRun)
        .join(
            PaymentRunLine,
            (PaymentRunLine.run_id == PaymentRun.id)
            & (PaymentRunLine.company_id == PaymentRun.company_id),
        )
        .where(
            PaymentRun.company_id == company_id,
            PaymentRunLine.settlement_document_id == document_id,
        )
    )


def selectable_documents(
    db: Session,
    company_id: int,
    *,
    bank_account_id: int,
    due_by: date | None = None,
    partner_id: int | None = None,
    on: date | None = None,
) -> list[SelectableDocument]:
    """Open AP invoices this account could pay, by supplier.

    In the **run's currency**, because a payment leaves a bank account in the currency that
    account holds and P4 allocates within one currency. `discount_available` is P4's own
    `max_discount` at `on` — the discount is a fact of the payment date, so a selection listed
    for the 10th and posted on the 20th offers nothing, correctly.
    """
    row = accounts_service.get(db, company_id, bank_account_id)
    currency = _currency(db, company_id, row)
    moment = on or date.today()
    statement = (
        select(PartnerDocument, Partner)
        .join(
            Partner,
            (Partner.id == PartnerDocument.partner_id)
            & (Partner.company_id == PartnerDocument.company_id),
        )
        .where(
            PartnerDocument.company_id == company_id,
            PartnerDocument.role == PartnerRole.AP,
            PartnerDocument.kind == DocumentKind.INVOICE,
            PartnerDocument.status == DocumentStatus.POSTED,
            PartnerDocument.open_amount > ZERO,
            PartnerDocument.currency_id == currency.id,
        )
        .order_by(Partner.name, PartnerDocument.id)
    )
    if due_by is not None:
        statement = statement.where(PartnerDocument.due_date <= due_by)
    if partner_id is not None:
        statement = statement.where(PartnerDocument.partner_id == partner_id)
    return [
        SelectableDocument(
            document_id=document.id,
            number=document.number,
            partner_id=partner.id,
            partner_name=partner.name,
            supplier_code=partner.supplier_code,
            document_date=document.document_date,
            due_date=document.due_date,
            currency_id=document.currency_id,
            total_amount=document.total_amount,
            open_amount=recompute_open_amount(db, document),
            discount_available=allocation_service.max_discount(
                db, document, on=moment, currency=currency
            ),
        )
        for document, partner in db.execute(statement).all()
    ]


# --- The plan, which is also the preview ---------------------------------------------------------


def plan(
    db: Session,
    company_id: int,
    *,
    bank_account_id: int,
    payment_date: date,
    lines: list[RunLineInput],
) -> RunPreview:
    """Everything the run would do, and **every refusal it is going to raise**, before a
    single write.

    That is the whole of why this function exists separately from `post_run`. A run posts N
    settlements and N allocations; a refusal discovered on the third supplier would leave two
    suppliers paid and a number claimed for a run that does not exist. So the selection is
    validated here in full — the account, the currency, each document's role, partner, status
    and open amount — and `post_run` calls this first and writes only afterwards.
    """
    row = accounts_service.get(db, company_id, bank_account_id)
    if row.kind != BankAccountKind.BANK:
        raise LedgerStateError(
            f"{row.code} is a cash account; a payment run moves money through a bank",
            code="payment_run_needs_bank",
            field_errors={"bank_account_id": ["a cash account cannot run payments"]},
        )
    if not lines:
        raise LedgerStateError(
            "A payment run needs at least one invoice",
            code="payment_run_empty",
            field_errors={"lines": ["select at least one invoice"]},
        )
    currency = _currency(db, company_id, row)

    grouped: dict[int, list[PreviewLine]] = {}
    partners: dict[int, Partner] = {}
    #: What each invoice has left **within this run**. A run may name a document twice — a
    #: screen with two rows open on it, a retry that appended rather than replaced — and
    #: without this the second line would read the same open amount as the first, pass, and
    #: then be refused by P4's `allocation_exceeds_open_amount` halfway through the posting.
    #: The refusal belongs here, with the rest of them, before the first write.
    remaining: dict[int, Decimal] = {}
    for index, line in enumerate(lines):
        document = db.get(PartnerDocument, line.document_id)
        if document is None or document.company_id != company_id:
            raise NotFoundError(f"Document {line.document_id} not found")
        if document.role != PartnerRole.AP or document.kind != DocumentKind.INVOICE:
            raise LedgerStateError(
                f"{document.number} is not a supplier invoice",
                code="payment_run_not_an_invoice",
                field_errors={f"lines.{index}.document_id": ["not a supplier invoice"]},
            )
        if document.currency_id != currency.id:
            raise LedgerStateError(
                f"{document.number} is not in {currency.code}, which is what {row.code} pays "
                "in",
                code="payment_run_currency_mismatch",
                field_errors={f"lines.{index}.document_id": ["wrong currency"]},
            )
        # Recomputed from the allocation slices rather than read off the cached column, for
        # decision 3's reason: a cache that has drifted must not be able to authorise a
        # payment.
        # **An invoice cannot be paid before it is raised**, and this is not pedantry: the
        # allocation would put the payment on the AP control account on a date the invoice is
        # not on it yet, so the control account and the open items disagree at every date
        # between the two — which is `assert_subledger_invariants`' first clause, and it fails.
        #
        # The property machine found this at step 3, and the state is **not** a payment-run
        # invention: a plain P4 payment dated before the invoice it is allocated to breaks the
        # same clause with no banking code involved. That is a finding against `allocate()` and
        # is recorded in `docs/p8-step-3-report.md` for the owner rather than fixed here, where
        # it would change AR and AP behaviour this step was not asked to touch. What is fixed
        # here is the path this step built.
        if payment_date < document.document_date:
            raise LedgerStateError(
                f"{document.number} is dated {document.document_date.isoformat()}; a run "
                "cannot pay an invoice before it is raised",
                code="payment_run_before_invoice",
                field_errors={
                    "payment_date": [f"before {document.document_date.isoformat()}"]
                },
            )
        open_amount = recompute_open_amount(db, document)
        if document.status != DocumentStatus.POSTED or open_amount <= ZERO:
            raise LedgerStateError(
                f"{document.number} is no longer open; it was settled or reversed after the "
                "selection was made",
                code="document_not_open",
                field_errors={f"lines.{index}.document_id": ["no longer open"]},
            )
        left = remaining.setdefault(document.id, open_amount)
        # `None` means "all of it" — the *document's* open amount, which is the figure the
        # screen put on the row. A second row on the same invoice therefore asks for the whole
        # thing again and is refused below, rather than quietly asking for the nothing that is
        # left and being refused as an invalid amount.
        amount = line.amount if line.amount is not None else open_amount
        if amount <= ZERO:
            raise LedgerStateError(
                f"{document.number}: pay a positive amount",
                code="invalid_amount",
                field_errors={f"lines.{index}.amount": ["invalid amount"]},
            )
        # Against what is left **within this run**, not against the document's open amount:
        # the two differ only when a run names the same invoice twice, and that is exactly the
        # case the plain reading would let through.
        if amount > left:
            raise LedgerStateError(
                f"{document.number} has only {left} open",
                code="payment_exceeds_open",
                field_errors={f"lines.{index}.amount": ["exceeds the open amount"]},
            )
        available = allocation_service.max_discount(
            db, document, on=payment_date, currency=currency
        )
        # Never more than the line pays: a discount larger than the settlement would make the
        # cash negative, and P4 would refuse the pair anyway. Capping here keeps the preview
        # arithmetic the same as the posting's.
        discount = min(available, amount) if line.take_discount else ZERO
        remaining[document.id] -= amount
        partners.setdefault(document.partner_id, _partner(db, company_id, document))
        grouped.setdefault(document.partner_id, []).append(
            PreviewLine(
                document_id=document.id,
                document_number=document.number,
                due_date=document.due_date,
                open_amount=open_amount,
                amount=amount,
                discount_available=available,
                discount_amount=discount,
            )
        )

    suppliers = tuple(
        PreviewSupplier(
            partner_id=partner_id,
            partner_name=partners[partner_id].name,
            supplier_code=partners[partner_id].supplier_code,
            bank_name=partners[partner_id].bank_name,
            bank_account_number=partners[partner_id].bank_account_number,
            bank_account_holder=partners[partner_id].bank_account_holder,
            lines=tuple(supplier_lines),
            warnings=_warnings(db, company_id, partners[partner_id], currency),
        )
        for partner_id, supplier_lines in grouped.items()
    )
    return RunPreview(
        bank_account_id=row.id,
        bank_account_code=row.code,
        payment_date=payment_date,
        currency_id=currency.id,
        currency_code=currency.code,
        suppliers=suppliers,
    )


def _partner(db: Session, company_id: int, document: PartnerDocument) -> Partner:
    partner = db.get(Partner, document.partner_id)
    if partner is None or partner.company_id != company_id:
        raise NotFoundError("Partner not found")
    return partner


def _warnings(
    db: Session, company_id: int, partner: Partner, currency: Currency
) -> tuple[str, ...]:
    """What the accountant needs told, and nothing that stops the run.

    `bank_details_missing` is the one decision 7 names: the payment still posts and the
    instruction file still carries the row, with the account fields empty, **because the
    payment was posted and the accountant has to know which beneficiary to key by hand**. A
    refusal here would have the ledger and the bank disagree instead.
    """
    warnings: list[str] = []
    if not partner.bank_account_number or not partner.bank_name:
        warnings.append("bank_details_missing")
    credits = db.scalars(
        select(PartnerDocument.number).where(
            PartnerDocument.company_id == company_id,
            PartnerDocument.partner_id == partner.id,
            PartnerDocument.role == PartnerRole.AP,
            PartnerDocument.status == DocumentStatus.POSTED,
            PartnerDocument.direction == 1,
            PartnerDocument.open_amount > ZERO,
            PartnerDocument.currency_id == currency.id,
        )
    ).all()
    if credits:
        warnings.append(f"open_credits: {', '.join(credits)}")
    return tuple(warnings)


# --- Posting -------------------------------------------------------------------------------------


def post_run(
    db: Session,
    company_id: int,
    *,
    bank_account_id: int,
    payment_date: date,
    lines: list[RunLineInput],
    actor: User,
    permissions: set[str] | None = None,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> PaymentRun:
    """Post the run: one settlement and one allocation per supplier, in one transaction.

    The number is claimed **after** `plan()` has raised everything it is going to raise, so a
    refused run leaves no hole in the `PYR-` series.
    """
    replayed = _replay(db, company_id, idempotency_key, idempotency_hash)
    if replayed is not None:
        return replayed
    preview = plan(
        db,
        company_id,
        bank_account_id=bank_account_id,
        payment_date=payment_date,
        lines=lines,
    )
    row = accounts_service.get(db, company_id, bank_account_id)
    number = claim_number(db, company_id, DocType.PAYMENT_RUN)
    run = PaymentRun(
        company_id=company_id,
        bank_account_id=row.id,
        number=number.number,
        payment_date=payment_date,
        currency_id=preview.currency_id,
        total=preview.total,
        reference=number.number,
        status=PaymentRunStatus.POSTED,
        posted_by=actor.id,
        posted_at=datetime.now(UTC),
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(run)
    db.flush()

    for supplier in preview.suppliers:
        settlement, _ = documents_service.post_document(
            db,
            company_id,
            PartnerRole.AP,
            documents_service.DocumentInput(
                kind=DocumentKind.SETTLEMENT,
                partner_id=supplier.partner_id,
                document_date=payment_date,
                description=f"Payment run {run.number}",
                # The run's own number on every settlement it posts. That is what the bank's
                # one line quotes and what decision 4's `payment_run` rule reads.
                reference=run.number,
                currency_id=preview.currency_id,
                amount=supplier.total,
                cash_account_id=row.gl_account_id,
                instrument_type=InstrumentType.BANK,
            ),
            actor=actor,
            permissions=permissions,
            request=request,
        )
        allocation, _ = allocation_service.allocate(
            db,
            company_id,
            PartnerRole.AP,
            partner_id=supplier.partner_id,
            allocation_date=payment_date,
            pairs=[
                allocation_service.PairInput(
                    # AP: the payment is the debit and the invoice the credit (the document
                    # matrix's directions, +1 and −1).
                    debit_document_id=settlement.id,
                    credit_document_id=line.document_id,
                    amount=line.cash_amount,
                    discount_amount=line.discount_amount,
                )
                for line in supplier.lines
            ],
            description=f"Payment run {run.number}",
            actor=actor,
            request=request,
        )
        for line in supplier.lines:
            db.add(
                PaymentRunLine(
                    company_id=company_id,
                    run_id=run.id,
                    partner_id=supplier.partner_id,
                    document_id=line.document_id,
                    amount=line.cash_amount,
                    discount_amount=line.discount_amount,
                    settlement_document_id=settlement.id,
                    allocation_id=allocation.id,
                )
            )
        enqueue(
            db,
            company_id,
            REMITTANCE_JOB,
            {"run_id": run.id, "partner_id": supplier.partner_id},
            actor=actor,
        )
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="payment_run.posted",
        entity="payment_runs",
        entity_id=run.id,
        after={
            "number": run.number,
            "bank_account": row.code,
            "payment_date": payment_date.isoformat(),
            "total": str(run.total),
            "suppliers": len(preview.suppliers),
            "discount": str(preview.discount_total),
        },
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )
    return run


# --- Reversal ------------------------------------------------------------------------------------


def reverse_run(
    db: Session,
    company_id: int,
    run_id: int,
    *,
    reason: str,
    on_date: date | None = None,
    actor: User,
    request: Request | None = None,
) -> PaymentRun:
    """Undo the whole run, **fallible leg first**.

    The order is `unallocate()` for every line, then `reverse_document()` for every settlement.
    It is not arbitrary: `reverse_document` refuses `document_allocated` while any allocation
    survives, so reversing first would post N reversals and then refuse — leaving settlements
    reversed in the ledger and their allocations still standing. In this order the only leg
    that can refuse runs before the first write, which is the same rule P4's own
    `reverse_document` follows for its stock companion.

    The refusals that belong to the *run* come before both: a run whose bank line is inside a
    locked reconciliation is not reversible (`reconciliation_locked` — reopen it first), and a
    run already reversed is not reversible twice.
    """
    run = get(db, company_id, run_id)
    if run.status == PaymentRunStatus.REVERSED:
        raise LedgerStateError(
            f"{run.number} was already reversed",
            code="payment_run_already_reversed",
        )
    lines = lines_of(db, company_id, run_id)
    # **Before anything.** The match holding this run's bank lines is released by the reversal
    # — the reversing entries are outstanding until the bank returns the money — but a match
    # inside a locked reconciliation is part of a proof somebody signed, and `matching.unmatch`
    # refuses it. Asking first means the refusal arrives before the unallocations rather than
    # after them.
    match_ids = _match_ids_of(db, company_id, run, lines)
    for match_id in match_ids:
        matching.assert_unmatchable(db, company_id, match_id)

    for allocation_id in _allocation_ids(lines):
        allocation = db.get(Allocation, allocation_id)
        if allocation is None or _already_unallocated(db, company_id, allocation):
            continue
        allocation_service.unallocate(
            db,
            allocation,
            on_date=on_date or run.payment_date,
            reason=reason,
            actor=actor,
            request=request,
        )
    for settlement_id in _settlement_ids(lines):
        settlement = db.get(PartnerDocument, settlement_id)
        if settlement is None or settlement.status != DocumentStatus.POSTED:
            continue
        documents_service.reverse_document(
            db,
            settlement,
            on_date=on_date or run.payment_date,
            reason=reason,
            actor=actor,
            in_payment_run=True,
            request=request,
        )
    for match_id in match_ids:
        matching.unmatch(db, company_id, match_id, actor=actor, request=request)

    run.status = PaymentRunStatus.REVERSED
    run.reversal_reason = reason
    run.reversed_by = actor.id
    run.reversed_at = datetime.now(UTC)
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="payment_run.reversed",
        entity="payment_runs",
        entity_id=run.id,
        before={"status": PaymentRunStatus.POSTED.value},
        after={
            "number": run.number,
            "status": run.status.value,
            "reason": reason,
            "matches_released": len(match_ids),
        },
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )
    return run


def _settlement_ids(lines: list[PaymentRunLine]) -> list[int]:
    """One settlement per supplier, so the ids repeat across a supplier's lines."""
    return _distinct(line.settlement_document_id for line in lines)


def _allocation_ids(lines: list[PaymentRunLine]) -> list[int]:
    """One allocation per supplier, likewise — a supplier paying three invoices has three
    lines and **one** allocation, and unallocating it once per line would refuse the second
    time with `allocation_already_reversed`."""
    return _distinct(line.allocation_id for line in lines)


def _distinct(ids) -> list[int]:  # noqa: ANN001
    seen: list[int] = []
    for value in ids:
        if value is not None and value not in seen:
            seen.append(value)
    return seen


def _already_unallocated(db: Session, company_id: int, allocation: Allocation) -> bool:
    return (
        db.scalar(
            select(Allocation.id).where(
                Allocation.company_id == company_id,
                Allocation.reverses_allocation_id == allocation.id,
            )
        )
        is not None
    )


def _match_ids_of(
    db: Session, company_id: int, run: PaymentRun, lines: list[PaymentRunLine]
) -> list[int]:
    """The matches holding this run's bank lines — normally one, the bank's single debit."""
    entries = select(PartnerDocument.journal_entry_id).where(
        PartnerDocument.company_id == company_id,
        PartnerDocument.id.in_(_settlement_ids(lines) or [0]),
    )
    row = db.get(BankAccount, run.bank_account_id)
    assert row is not None
    found = db.scalars(
        select(BankMatch.id)
        .join(BankMatchJournalLine, BankMatchJournalLine.match_id == BankMatch.id)
        .join(JournalLine, JournalLine.id == BankMatchJournalLine.journal_line_id)
        .where(
            BankMatch.company_id == company_id,
            JournalLine.gl_account_id == row.gl_account_id,
            JournalLine.entry_id.in_(entries),
        )
        .distinct()
    ).all()
    return list(found)


# --- The instruction file ------------------------------------------------------------------------


def instruction_rows(
    db: Session, company_id: int, run: PaymentRun
) -> list[tuple[object, ...]]:
    """One row per supplier — per *beneficiary*, which is what a bank portal takes.

    A supplier without bank details still gets a row, with the account fields empty. The
    payment is posted; hiding the row would hide the one beneficiary the accountant has to key
    by hand.
    """
    currency = db.get(Currency, run.currency_id)
    assert currency is not None
    rows: list[tuple[object, ...]] = []
    for partner_id, total in _totals_by_partner(db, company_id, run.id).items():
        partner = db.get(Partner, partner_id)
        assert partner is not None
        rows.append(
            (
                partner.bank_account_holder or partner.name,
                partner.bank_name or "",
                partner.bank_account_number or "",
                str(_quantised(total, currency)),
                currency.code,
                run.number,
                partner.supplier_code or "",
            )
        )
    return rows


def _quantised(amount: Decimal, currency: Currency) -> Decimal:
    """`236000`, not `236000.000000`. The column is `NUMERIC(20,6)` for every currency there
    is; what a bank portal parses is the amount at *this* currency's scale, and a trailing six
    zeros on a franc is a field some portals reject and others read as cents."""
    return amount.quantize(Decimal(1).scaleb(-currency.decimal_places))


def instruction_csv(db: Session, company_id: int, run: PaymentRun) -> str:
    """RFC 4180 endings, `Decimal` written by `str` — the same rules as the VAT annexes."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(INSTRUCTION_COLUMNS)
    for row in instruction_rows(db, company_id, run):
        writer.writerow(row)
    return buffer.getvalue()


def _totals_by_partner(
    db: Session, company_id: int, run_id: int
) -> dict[int, Decimal]:
    totals: dict[int, Decimal] = {}
    for line in lines_of(db, company_id, run_id):
        totals[line.partner_id] = totals.get(line.partner_id, ZERO) + line.amount
    return totals


# --- Plumbing ------------------------------------------------------------------------------------


def _currency(db: Session, company_id: int, row: BankAccount) -> Currency:
    currency = db.get(Currency, row.currency_id)
    if currency is None or currency.company_id != company_id:
        return base_currency(db, company_id)
    return currency


def _replay(
    db: Session, company_id: int, key: str | None, request_hash: str | None
) -> PaymentRun | None:
    if not key:
        return None
    run = db.scalar(
        select(PaymentRun).where(
            PaymentRun.company_id == company_id,
            PaymentRun.idempotency_key == key,
        )
    )
    if run is None:
        return None
    if request_hash is not None and run.idempotency_hash != request_hash:
        raise LedgerStateError(
            f"Idempotency-Key {key} was already used for a different request "
            f"({run.number}); use a new key",
            code="idempotency_key_reused",
        )
    return run
