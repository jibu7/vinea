"""The AR/AP invariant suite (P4 step 2). `assert_subledger_invariants` is the acceptance
contract for the subledger, exactly as `assert_ledger_invariants` is for the kernel — every
failure here means AR/AP no longer reconciles to the general ledger."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.gl import ControlType, GLAccount
from app.models.journal import JournalEntry, JournalLine, JournalStatus
from app.models.partner import PartnerRole
from app.models.subledger import Allocation, AllocationLine, DocumentStatus, PartnerDocument
from app.subledger.openitems import open_items_as_of, verify_open_items

ZERO = Decimal(0)

CONTROL_TYPE_FOR_ROLE = {PartnerRole.AR: ControlType.AR, PartnerRole.AP: ControlType.AP}
PARTNER_TYPE_FOR_ROLE = {PartnerRole.AR: "customer", PartnerRole.AP: "supplier"}


def control_balance(
    db: Session, company_id: int, control_account_id: int, *, as_of: date
) -> Decimal:
    total = db.scalar(
        select(func.coalesce(func.sum(JournalLine.base_amount), ZERO))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id == control_account_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date <= as_of,
        )
    )
    return total or ZERO


def assert_subledger_invariants(db: Session, company_id: int) -> None:
    """1. Every control account reconciles to the partner open items, at every date on which
       anything was posted.
    2. No open item is negative.
    3. Allocations against a document never exceed it.
    4. `verify_open_items()` reports no drift between the stored column and the recomputation.
    5. Every journal line on an AR/AP control account carries the right partner dimension and
       came from the matching module.
    """
    documents = list(
        db.scalars(select(PartnerDocument).where(PartnerDocument.company_id == company_id))
    )

    # 4. The stored `open_amount` is exactly the recomputation from allocation lines.
    drift = verify_open_items(db, company_id)
    assert drift == [], f"open-item drift: {drift[:3]}"

    # 2 + 3. Open items stay within [0, total]; allocations never over-consume a document.
    consumed: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for debit_id, credit_id, amount, discount_id, discount in db.execute(
        select(
            AllocationLine.debit_document_id,
            AllocationLine.credit_document_id,
            AllocationLine.amount,
            AllocationLine.discount_document_id,
            AllocationLine.discount_amount,
        ).where(AllocationLine.company_id == company_id)
    ).all():
        consumed[debit_id] += amount
        consumed[credit_id] += amount
        if discount_id is not None:
            consumed[discount_id] += discount
    for document in documents:
        assert document.open_amount >= ZERO, f"{document.number} has a negative open item"
        assert document.open_amount <= document.total_amount, (
            f"{document.number} is open for more than it was posted for"
        )
        used = consumed.get(document.id, ZERO)
        assert used <= document.total_amount, (
            f"{document.number} is allocated beyond its amount ({used} > {document.total_amount})"
        )
        if document.status == DocumentStatus.REVERSED:
            assert document.open_amount == ZERO, f"{document.number} is reversed but still open"

    # 1. Control account balance == Σ signed open items, in base currency, at every date on
    #    which the subledger moved. Realized FX at allocation time is what makes this hold.
    dates = sorted(
        {document.document_date for document in documents}
        | {
            allocation_date
            for (allocation_date,) in db.execute(
                select(Allocation.allocation_date).where(Allocation.company_id == company_id)
            ).all()
        }
        | {document.reversed_on for document in documents if document.reversed_on is not None}
    )
    control_accounts = {
        (document.role, document.control_account_id) for document in documents
    }
    for as_of in dates:
        for role, control_account_id in sorted(control_accounts, key=lambda pair: pair[1]):
            expected = sum(
                (
                    item.signed_base_amount
                    for item in open_items_as_of(db, company_id, role=role, as_of=as_of)
                    if item.document.control_account_id == control_account_id
                ),
                ZERO,
            )
            actual = control_balance(db, company_id, control_account_id, as_of=as_of)
            assert actual == expected, (
                f"{role.value.upper()} control account {control_account_id} is {actual} "
                f"as of {as_of} but open items total {expected}"
            )

    # 5. Every control-account line carries its partner and came from the matching module.
    rows = db.execute(
        select(
            JournalLine.id,
            JournalLine.partner_type,
            JournalLine.partner_id,
            GLAccount.control_type,
            JournalEntry.module,
        )
        .join(GLAccount, GLAccount.id == JournalLine.gl_account_id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            GLAccount.control_type.in_([ControlType.AR, ControlType.AP]),
        )
    ).all()
    for line_id, partner_type, partner_id, control_type, module in rows:
        expected_partner = "customer" if control_type == ControlType.AR else "supplier"
        assert module == str(control_type), (
            f"line {line_id} touches a {control_type} control account from module {module}"
        )
        assert partner_id is not None and partner_type == expected_partner, (
            f"line {line_id} on a {control_type} control account has no {expected_partner}"
        )
