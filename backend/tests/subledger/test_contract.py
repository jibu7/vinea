"""Review-gate proofs for the P4 contract.

Each test here exists because someone should be able to break the corresponding rule and
watch a named test fail:

* decision 3 — `open_amount` is a cache; every as-of path recomputes from `allocation_lines`;
* the control-account guard is a `(control_type, module)` registry, not an equality test;
* unallocation is refused into a closed period, with the same code whether or not the
  allocation moved money;
* `Idempotency-Key` covers allocate *and* unallocate;
* the credit-limit override writes an audit row naming the actor and the excess.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.kernel import posting
from app.kernel.errors import (
    SQLSTATE_CONTROL_ACCOUNT,
    LedgerStateError,
    PostingError,
    kernel_sqlstate,
)
from app.kernel.events import LineSpec, PartnerDocumentPosted
from app.kernel.sequences import DocType
from app.models.audit import AuditLog
from app.models.fiscal import PeriodStatus
from app.models.gl import ControlAccountModule, ControlType
from app.models.partner import PartnerRole
from app.models.subledger import Allocation, PartnerDocument
from app.subledger import allocations as allocations_service
from app.subledger.ageing import age_analysis
from app.subledger.openitems import open_items_as_of, verify_open_items
from app.subledger.statements import render_statement_html
from tests.subledger.conftest import MARCH, Subledger, set_credit_limit
from tests.subledger.test_documents import post_invoice, post_settlement

# --- Decision 3: open_amount is a cache, and nothing correctness-critical reads it ---------


def test_a_drifted_open_amount_is_caught_and_changes_no_as_of_figure(
    db: Session, subledger: Subledger
) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(10000), on=MARCH)
    later = MARCH + timedelta(days=10)
    receipt = post_settlement(db, subledger, amount=Decimal(4000), on=later)
    db.commit()
    allocations_service.allocate(
        db,
        subledger.company_id,
        PartnerRole.AR,
        partner_id=subledger.customer.id,
        allocation_date=later,
        pairs=[
            allocations_service.PairInput(
                debit_document_id=invoice.id,
                credit_document_id=receipt.id,
                amount=Decimal(4000),
            )
        ],
        actor=subledger.ledger.owner,
    )
    db.commit()

    as_of = later + timedelta(days=1)
    truth_ageing = age_analysis(db, subledger.company_id, PartnerRole.AR, as_of=as_of)
    truth_items = {
        item.document.id: item.open_amount
        for item in open_items_as_of(db, subledger.company_id, role=PartnerRole.AR, as_of=as_of)
    }
    truth_html = render_statement_html(
        db,
        subledger.company_id,
        PartnerRole.AR,
        partner_ids=[subledger.customer.id],
        as_of=as_of,
    )
    assert verify_open_items(db, subledger.company_id) == []

    # Corrupt the cache behind the service's back — the one thing `verify_open_items` exists
    # to detect. Raw SQL, because no service would ever write this.
    db.execute(
        text("UPDATE partner_documents SET open_amount = :bad WHERE id = :id"),
        {"bad": Decimal(9999), "id": invoice.id},
    )
    db.commit()
    db.expire_all()

    drift = verify_open_items(db, subledger.company_id)
    assert [(d.document_id, d.stored, d.recomputed) for d in drift] == [
        (invoice.id, Decimal(9999), Decimal(6000))
    ]

    # Every as-of path recomputes, so none of them moved.
    assert age_analysis(
        db, subledger.company_id, PartnerRole.AR, as_of=as_of
    ).grand_total == truth_ageing.grand_total == Decimal(6000)
    assert {
        item.document.id: item.open_amount
        for item in open_items_as_of(db, subledger.company_id, role=PartnerRole.AR, as_of=as_of)
    } == truth_items
    assert (
        render_statement_html(
            db,
            subledger.company_id,
            PartnerRole.AR,
            partner_ids=[subledger.customer.id],
            as_of=as_of,
        )
        == truth_html
    )


def test_the_cache_neither_grants_nor_withholds_allocation_capacity(
    db: Session, subledger: Subledger
) -> None:
    """`prepare()` recomputes from `allocation_lines`, so a drifted cache changes nothing.

    Drifting *upward* is impossible — `ck_partner_documents_open_amount_range` refuses it at
    the database — so the reachable failure mode is a cache that under-reports. It must not
    block a legitimate allocation, and the real open amount is still the ceiling.
    """
    invoice, _ = post_invoice(db, subledger, amount=Decimal(1000))
    receipt = post_settlement(db, subledger, amount=Decimal(5000))
    db.commit()

    with pytest.raises(DBAPIError):
        db.execute(
            text("UPDATE partner_documents SET open_amount = :bad WHERE id = :id"),
            {"bad": Decimal(5000), "id": invoice.id},
        )
    db.rollback()

    db.execute(
        text("UPDATE partner_documents SET open_amount = 0 WHERE id = :id"),
        {"id": invoice.id},
    )
    db.commit()
    db.expire_all()
    assert len(verify_open_items(db, subledger.company_id)) == 1

    def prepare(amount: Decimal):  # noqa: ANN202
        return allocations_service.prepare(
            db,
            subledger.company_id,
            PartnerRole.AR,
            partner_id=subledger.customer.id,
            allocation_date=MARCH,
            pairs=[
                allocations_service.PairInput(
                    debit_document_id=invoice.id,
                    credit_document_id=receipt.id,
                    amount=amount,
                )
            ],
        )

    # The cache says nothing is open; the allocation lines say 1000 is, and they win.
    assert prepare(Decimal(1000)).total_allocated == Decimal(1000)
    with pytest.raises(PostingError) as excinfo:
        prepare(Decimal(1001))
    assert excinfo.value.code == "allocation_exceeds_open_amount"
    db.rollback()


# --- The guard is a registry -----------------------------------------------------------------


def test_the_registry_seeds_the_subledger_pairs(db: Session, subledger: Subledger) -> None:
    rows = {
        (row.control_type, row.module)
        for row in db.scalars(select(ControlAccountModule)).all()
    }
    assert rows == {
        (ControlType.AR, "ar"),
        (ControlType.AP, "ap"),
        (ControlType.INVENTORY, "inv"),
    }
    assert posting.control_account_modules(db)[ControlType.AR] == frozenset({"ar"})


def test_registering_a_module_opens_the_control_account_to_it(
    db: Session, subledger: Subledger
) -> None:
    """The P10 case: POS receipts legitimately raise AR. Registering `('ar', 'pos')` is all
    it takes — the guard is not touched, and every other module is still denied."""
    ledger = subledger.ledger

    def post_as(module: str):  # noqa: ANN202
        return posting.post(
            db,
            PartnerDocumentPosted(
                module=module,
                doc_type=DocType.AR_INVOICE,
                entry_date=MARCH,
                description=f"{module} raising AR",
                lines=(
                    LineSpec(
                        amount=Decimal(100),
                        gl_account_id=ledger.acct("1200"),
                        partner_type="customer",
                        partner_id=subledger.customer.id,
                    ),
                    LineSpec(amount=Decimal(-100), gl_account_id=ledger.acct("4100")),
                ),
            ),
            company_id=ledger.company_id,
            actor=ledger.owner,
        )

    with pytest.raises(PostingError) as excinfo:
        post_as("pos")
    assert excinfo.value.code == "control_account_direct_posting"
    db.rollback()

    db.add(ControlAccountModule(control_type=ControlType.AR, module="pos"))
    db.flush()
    entry = post_as("pos")
    assert entry is not None and entry.module == "pos"

    # Default stays deny for everything not registered.
    with pytest.raises(PostingError) as excinfo:
        post_as("inv")
    assert excinfo.value.code == "control_account_direct_posting"
    db.rollback()


def test_the_db_guard_reads_the_same_registry(db: Session, subledger: Subledger) -> None:
    """Registering a module must move the database trigger too, or the two disagree."""
    ledger = subledger.ledger
    period_id = next(p.id for p in ledger.periods if p.start_date <= MARCH <= p.end_date)

    def raw_pos_line(number: str) -> None:
        db.execute(text("SELECT set_config('app.posting_engine', 'on', true)"))
        entry_id = db.execute(
            text(
                """
                INSERT INTO journal_entries (company_id, number, doc_type, event_type, module,
                                             entry_date, period_id, description, status)
                VALUES (:cid, :number, 'JE', 'manual_journal', 'pos', :on, :period,
                        'raw', 'draft')
                RETURNING id
                """
            ),
            {"cid": ledger.company_id, "number": number, "on": MARCH, "period": period_id},
        ).scalar_one()
        db.execute(
            text(
                """
                INSERT INTO journal_lines (company_id, entry_id, line_no, gl_account_id,
                                           branch_id, currency_id, exchange_rate, amount,
                                           base_amount, tax_amount, partner_type, partner_id)
                VALUES (:cid, :entry, 1, :account, :branch, :currency, 1, 100, 100, 0,
                        'customer', :partner)
                """
            ),
            {
                "cid": ledger.company_id,
                "entry": entry_id,
                "account": ledger.acct("1200"),
                "branch": ledger.main_branch.id,
                "currency": ledger.base.id,
                "partner": subledger.customer.id,
            },
        )

    with pytest.raises(DBAPIError) as excinfo:
        raw_pos_line("RAW-POS-1")
    assert kernel_sqlstate(excinfo.value) == SQLSTATE_CONTROL_ACCOUNT
    db.rollback()

    db.add(ControlAccountModule(control_type=ControlType.AR, module="pos"))
    db.flush()
    raw_pos_line("RAW-POS-2")  # the same statement now passes the trigger
    db.rollback()


# --- Unallocation across a closed period --------------------------------------------------


def _closed_period_setup(db: Session, subledger: Subledger, *, with_fx: bool):  # noqa: ANN202
    ledger = subledger.ledger
    if with_fx:
        from app.models.currency import ExchangeRate

        db.add(
            ExchangeRate(
                company_id=ledger.company_id,
                currency_id=ledger.cur("USD"),
                valid_from=MARCH,
                rate=Decimal(1250),
            )
        )
        db.flush()
        invoice, _ = post_invoice(
            db, subledger, amount=Decimal("100.00"), currency="USD", on=MARCH
        )
        receipt = post_settlement(
            db, subledger, amount=Decimal("100.00"), currency="USD", on=MARCH
        )
        amount = Decimal("100.00")
    else:
        invoice, _ = post_invoice(db, subledger, amount=Decimal(5000), on=MARCH)
        receipt = post_settlement(db, subledger, amount=Decimal(5000), on=MARCH)
        amount = Decimal(5000)
    db.commit()
    allocation, _ = allocations_service.allocate(
        db,
        subledger.company_id,
        PartnerRole.AR,
        partner_id=subledger.customer.id,
        allocation_date=MARCH,
        pairs=[
            allocations_service.PairInput(
                debit_document_id=invoice.id,
                credit_document_id=receipt.id,
                amount=amount,
            )
        ],
        actor=ledger.owner,
    )
    db.commit()
    march = next(p for p in ledger.periods if p.start_date <= MARCH <= p.end_date)
    march.status = PeriodStatus.CLOSED
    db.flush()
    return invoice, allocation


@pytest.mark.parametrize("with_fx", [False, True])
def test_unallocation_into_a_closed_period_is_refused(
    db: Session, subledger: Subledger, with_fx: bool
) -> None:
    """Same behaviour and the same code whether the allocation posted a journal entry or
    not: `period_not_open`. An allocation with no FX and no discount must not be able to
    slip through a closed month just because it had nothing to reverse."""
    _invoice, allocation = _closed_period_setup(db, subledger, with_fx=with_fx)
    with pytest.raises(PostingError) as excinfo:
        allocations_service.unallocate(
            db, allocation, on_date=MARCH, reason="wrong customer", actor=subledger.ledger.owner
        )
    assert excinfo.value.code == "period_not_open"
    db.rollback()


def test_unallocation_in_a_later_open_period_succeeds(
    db: Session, subledger: Subledger
) -> None:
    """The accountant's route out: unwind in the current open period, not the closed one."""
    invoice, allocation = _closed_period_setup(db, subledger, with_fx=True)
    db.commit()
    april = MARCH + timedelta(days=35)
    reversal = allocations_service.unallocate(
        db, allocation, on_date=april, reason="wrong customer", actor=subledger.ledger.owner
    )
    db.commit()
    assert reversal.allocation_date == april
    db.refresh(invoice)
    assert invoice.open_amount == invoice.total_amount


def test_unallocation_cannot_predate_the_allocation(db: Session, subledger: Subledger) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(2000), on=MARCH)
    later = MARCH + timedelta(days=10)
    receipt = post_settlement(db, subledger, amount=Decimal(2000), on=later)
    db.commit()
    allocation, _ = allocations_service.allocate(
        db,
        subledger.company_id,
        PartnerRole.AR,
        partner_id=subledger.customer.id,
        allocation_date=later,
        pairs=[
            allocations_service.PairInput(
                debit_document_id=invoice.id,
                credit_document_id=receipt.id,
                amount=Decimal(2000),
            )
        ],
        actor=subledger.ledger.owner,
    )
    db.commit()
    with pytest.raises(LedgerStateError) as excinfo:
        allocations_service.unallocate(
            db, allocation, on_date=MARCH, reason="too early", actor=subledger.ledger.owner
        )
    assert excinfo.value.code == "reversal_before_original"
    db.rollback()


# --- Idempotency and the credit-limit audit trail ------------------------------------------


def _allocate_once(db: Session, subledger: Subledger, invoice, receipt, key: str):  # noqa: ANN001, ANN202
    return allocations_service.allocate(
        db,
        subledger.company_id,
        PartnerRole.AR,
        partner_id=subledger.customer.id,
        allocation_date=MARCH,
        pairs=[
            allocations_service.PairInput(
                debit_document_id=invoice.id,
                credit_document_id=receipt.id,
                amount=Decimal(1000),
            )
        ],
        actor=subledger.ledger.owner,
        idempotency_key=key,
        idempotency_hash="hash-a",
    )


def test_allocate_is_idempotent(db: Session, subledger: Subledger) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(3000))
    receipt = post_settlement(db, subledger, amount=Decimal(3000))
    db.commit()

    first, replayed_first = _allocate_once(db, subledger, invoice, receipt, "alc-key")
    db.commit()
    second, replayed_second = _allocate_once(db, subledger, invoice, receipt, "alc-key")
    db.commit()
    assert (replayed_first, replayed_second) == (False, True)
    assert first.id == second.id
    db.refresh(invoice)
    assert invoice.open_amount == Decimal(2000)


def test_allocate_refuses_a_reused_key_with_a_different_payload(
    db: Session, subledger: Subledger
) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(3000))
    receipt = post_settlement(db, subledger, amount=Decimal(3000))
    db.commit()
    _allocate_once(db, subledger, invoice, receipt, "alc-key")
    db.commit()
    with pytest.raises(LedgerStateError) as excinfo:
        allocations_service.allocate(
            db,
            subledger.company_id,
            PartnerRole.AR,
            partner_id=subledger.customer.id,
            allocation_date=MARCH,
            pairs=[
                allocations_service.PairInput(
                    debit_document_id=invoice.id,
                    credit_document_id=receipt.id,
                    amount=Decimal(2000),
                )
            ],
            actor=subledger.ledger.owner,
            idempotency_key="alc-key",
            idempotency_hash="hash-b",
        )
    assert excinfo.value.code == "idempotency_key_reused"
    db.rollback()


def test_unallocate_is_idempotent(db: Session, subledger: Subledger) -> None:
    invoice, _ = post_invoice(db, subledger, amount=Decimal(3000))
    receipt = post_settlement(db, subledger, amount=Decimal(3000))
    db.commit()
    allocation, _ = _allocate_once(db, subledger, invoice, receipt, "alc-key")
    db.commit()

    first = allocations_service.unallocate(
        db,
        allocation,
        on_date=MARCH,
        reason="retry me",
        actor=subledger.ledger.owner,
        idempotency_key="unalc-key",
        idempotency_hash="hash-u",
    )
    db.commit()
    second = allocations_service.unallocate(
        db,
        allocation,
        on_date=MARCH,
        reason="retry me",
        actor=subledger.ledger.owner,
        idempotency_key="unalc-key",
        idempotency_hash="hash-u",
    )
    db.commit()
    assert first.id == second.id
    assert (
        db.scalars(
            select(Allocation.id).where(Allocation.reverses_allocation_id == allocation.id)
        ).all()
        == [first.id]
    )
    db.refresh(invoice)
    assert invoice.open_amount == Decimal(3000)


def test_the_credit_limit_override_audit_names_the_actor_and_the_excess(
    db: Session, subledger: Subledger
) -> None:
    set_credit_limit(db, subledger, PartnerRole.AR, Decimal(40000))
    post_invoice(
        db, subledger, amount=Decimal(65000), permissions={"ar:credit_limit_override"}
    )
    db.commit()

    row = db.scalars(
        select(AuditLog).where(AuditLog.action == "partner_document.credit_limit_override")
    ).one()
    assert row.actor_user_id == subledger.ledger.owner.id
    assert row.actor_email == subledger.ledger.owner.email
    assert row.entity == "partners" and row.entity_id == str(subledger.customer.id)
    assert Decimal(row.after["credit_limit"]) == Decimal(40000)
    assert Decimal(row.after["exposure"]) == Decimal(65000)
    assert Decimal(row.after["excess"]) == Decimal(25000)


def test_documents_remain_idempotent(db: Session, subledger: Subledger) -> None:
    first, replayed_first = post_invoice(db, subledger, idempotency_key="doc-key")
    db.commit()
    second, replayed_second = post_invoice(db, subledger, idempotency_key="doc-key")
    db.commit()
    assert (replayed_first, replayed_second) == (False, True)
    assert first.id == second.id
    assert db.scalars(select(PartnerDocument.id)).all() == [first.id]
