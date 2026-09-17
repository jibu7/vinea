"""The gapless checker's two new capabilities, exercised before anything relies on them.

P7 is the first phase whose numbers leave the building as *numbers* — a revenue authority
receives `invcNo: 7`, not `FIS-000007` — and the first whose runs are genuinely **per branch**.
Both were handled by `assert_ledger_invariants` doing something other than checking: it parsed
trailing digits out of a formatted string, and it skipped branch-scoped runs outright with a
comment saying nothing claimed one yet.

Step 2 is where the outbox starts claiming these numbers. This is the step that proves the
checker can count them, by writing the rows by hand — because a capability first exercised by
the code that depends on it is a capability nobody has checked.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.kernel.sequences import SEQUENCE_CLAIMANTS, DocType, claim_number
from app.models.company import Branch, Company
from app.models.fiscalization import FiscalDevice
from app.models.user import User
from tests.kernel.invariants import assert_ledger_invariants


def _enqueue(db: Session, device: FiscalDevice, *, kind: str, invc_no: int) -> None:
    """One outbox row, written straight into the table.

    The outbox *service* arrives at step 2; what is under test here is the checker, and it
    reads rows rather than services. `fiscal_outbox` carries no immutability trigger — it is
    a queue, not a posted record — so this takes no route the product cannot take.
    """
    db.execute(
        text(
            """
            INSERT INTO fiscal_outbox
                (company_id, device_id, kind, sequence_no, invc_no, payload, status)
            VALUES (:company_id, :device_id, CAST(:kind AS fiscal_outbox_kind),
                    :sequence_no, :invc_no, '{}'::jsonb, 'queued')
            """
        ),
        {
            "company_id": device.company_id,
            "device_id": device.id,
            "kind": kind,
            "sequence_no": invc_no,
            "invc_no": invc_no,
        },
    )
    db.flush()


def test_the_sale_run_is_registered_numeric_and_branch_scoped() -> None:
    """The registry entry itself, because everything below depends on it being right and the
    registry is strings the kernel never imports."""
    (claimant,) = SEQUENCE_CLAIMANTS[DocType.FISCAL_SALE]

    assert claimant.table == "fiscal_outbox"
    assert claimant.number_column == "invc_no"
    assert claimant.numeric is True
    assert claimant.branch_expression is not None


def test_the_checker_counts_an_integer_column_and_finds_it_gapless(
    db: Session, fiscal_company: Company, active_device: FiscalDevice, main_branch: Branch
) -> None:
    """Three sales claimed and three rows holding them: 1, 2, 3 with the sequence at 4.

    Before `numeric`, this run would have been checked by running a trailing-digit regex over
    an integer and failing on the type — which is why the alternative on the table was to leave
    these runs out of the check altogether.
    """
    for _ in range(3):
        claimed = claim_number(
            db, fiscal_company.id, DocType.FISCAL_SALE, branch_id=main_branch.id
        )
        _enqueue(db, active_device, kind="sale", invc_no=claimed.sequence_no)
    db.commit()

    assert_ledger_invariants(db, fiscal_company.id)


def test_a_hole_in_the_fiscal_invoice_numbers_is_caught(
    db: Session, fiscal_company: Company, active_device: FiscalDevice, main_branch: Branch
) -> None:
    """Anti-vacuity, and the reason this run is checked at all.

    A fiscal invoice number with a hole in it is a question from a revenue authority: it means
    a sale was numbered and never registered, or registered twice. The checker has to say so.
    """
    for _ in range(3):
        claim_number(db, fiscal_company.id, DocType.FISCAL_SALE, branch_id=main_branch.id)
    # Rows for 1 and 3. Number 2 was claimed and nothing kept it.
    _enqueue(db, active_device, kind="sale", invc_no=1)
    _enqueue(db, active_device, kind="refund", invc_no=3)
    db.commit()

    with pytest.raises(AssertionError) as caught:
        assert_ledger_invariants(db, fiscal_company.id)

    assert "FIS" in str(caught.value)


def test_a_sale_and_a_purchase_share_a_column_and_are_told_apart_by_kind(
    db: Session, fiscal_company: Company, active_device: FiscalDevice, main_branch: Branch
) -> None:
    """`invc_no` serves two runs — sales and purchases each start at 1 — so the claimant's
    `where` clause is what keeps them apart. Without it every company that had posted one of
    each would look like it had claimed the same number twice.
    """
    for kind, doc_type in (
        ("sale", DocType.FISCAL_SALE),
        ("purchase", DocType.FISCAL_PURCHASE),
    ):
        claimed = claim_number(db, fiscal_company.id, doc_type, branch_id=main_branch.id)
        assert claimed.sequence_no == 1, "each run starts at 1 in its own space"
        _enqueue(db, active_device, kind=kind, invc_no=claimed.sequence_no)
    db.commit()

    assert_ledger_invariants(db, fiscal_company.id)


def test_a_second_branch_keeps_its_own_number_space(
    db: Session,
    fiscal_company: Company,
    fiscal_owner: User,
    active_device: FiscalDevice,
    main_branch: Branch,
    sandbox_client,  # noqa: ANN001
) -> None:
    """Two devices, two branches, and both holding `invc_no` 1.

    This is the case the checker used to skip. Counting them together would see `[1, 1]` and
    report a duplicate claim; counting them apart sees two runs of one, which is what they are
    — a revenue authority keys a sale by (taxpayer, branch, invoice number).
    """
    from app.fiscal import devices as device_service
    from app.models.fiscalization import FiscalEnvironment, FiscalProfile
    from tests.fiscal.conftest import SANDBOX_URL

    depot = Branch(
        company_id=fiscal_company.id, code="DEP", name="Depot", is_main=False, is_active=True
    )
    db.add(depot)
    db.flush()
    second = device_service.register_device(
        db,
        fiscal_company.id,
        branch_id=depot.id,
        profile=FiscalProfile.VSDC,
        environment=FiscalEnvironment.TEST,
        base_url=SANDBOX_URL,
        dvc_srl_no="SDC-SERIAL-0002",
        bhf_id="01",
        actor=fiscal_owner,
    )
    device_service.initialize_device(
        db, fiscal_company.id, second, actor=fiscal_owner, client=sandbox_client
    )

    for device, branch in ((active_device, main_branch), (second, depot)):
        claimed = claim_number(
            db, fiscal_company.id, DocType.FISCAL_SALE, branch_id=branch.id
        )
        assert claimed.sequence_no == 1
        _enqueue(db, device, kind="sale", invc_no=claimed.sequence_no)
    db.commit()

    assert_ledger_invariants(db, fiscal_company.id)


def test_a_branch_scoped_run_whose_claimant_cannot_say_which_branch_fails_loudly(
    db: Session, fiscal_company: Company, active_device: FiscalDevice, main_branch: Branch
) -> None:
    """The guard against the old behaviour returning by accident.

    Skipping a branch-scoped run was defensible while nothing claimed one. Now that something
    does, a run whose claimant has no `branch_expression` must be an assertion failure naming
    the fix — not a silent skip that looks like coverage.
    """
    # `ARIN` is company-wide and its claimant is `journal_entries`, which has no branch
    # expression. A branch-level row for it is the shape this refusal exists for.
    db.execute(
        text(
            "INSERT INTO document_sequences (company_id, branch_id, doc_type, prefix, "
            "next_number) VALUES (:company_id, :branch_id, 'ARIN', 'INV-', 1)"
        ),
        {"company_id": fiscal_company.id, "branch_id": main_branch.id},
    )
    db.commit()

    with pytest.raises(AssertionError) as caught:
        assert_ledger_invariants(db, fiscal_company.id)

    assert "branch_expression" in str(caught.value)
