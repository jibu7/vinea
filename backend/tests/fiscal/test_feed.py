"""Decision 9's second half: the EBM purchase feed, and the rule that one supplier invoice is
registered once.

The feed is a purchase somebody **else** registered — their device sold to this taxpayer, and
the operator's job is to say whether it happened. The sandbox serves one fixture row (supplier
TIN `100000003`, invoice 77, B bucket 11 800 / 1 800), which is the tape's row 8.

The four paths of `accept` are what this file is mostly about, because the interesting one is
the third: a linked document whose own registration RRA already holds gets **no confirmation at
all**, because confirming it would be the second registration of one invoice under a second
`FIP` number.
"""

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fiscal import drainer
from app.fiscal import feed as feed_service
from app.fiscal.devices import FiscalSetupError
from app.models.fiscalization import (
    FiscalFeedDecision,
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalPurchaseFeedRow,
    FiscalSyncKind,
)
from app.models.partner import Partner
from app.subledger import documents as documents_service
from app.subledger import masters as partner_masters
from tests.fiscal.conftest import FiscalPosting
from tests.fiscal.helpers import APRIL, supplier_invoice
from tests.fiscal.invariants import assert_fiscal_invariants

D = Decimal

#: §4.12 — `regTyCd A`, "this is a confirmation of something you already hold".
AUTOMATIC = "A"
#: §4.11 — approved, and cancelled.
APPROVED = "02"
CANCELLED = "04"

FEED_SUPPLIER_TIN = "100000003"
FEED_INVOICE_NO = 77


def _fetch(db: Session, fixture: FiscalPosting, client: httpx.Client) -> int:
    return feed_service.fetch(
        db,
        fixture.company_id,
        fixture.device,
        actor=fixture.owner,
        client=client,
    )


def _rows(db: Session, company_id: int) -> list[FiscalOutboxRow]:
    return list(
        db.scalars(
            select(FiscalOutboxRow)
            .where(FiscalOutboxRow.company_id == company_id)
            .order_by(FiscalOutboxRow.sequence_no)
        )
    )


def _confirmations(db: Session, company_id: int) -> list[FiscalOutboxRow]:
    return [
        row
        for row in _rows(db, company_id)
        if row.kind == FiscalOutboxKind.PURCHASE_CONFIRM
    ]


# --- Fetching -------------------------------------------------------------------------------


def test_the_feed_lands_as_rows_and_advances_the_watermark(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    assert _fetch(db, fiscal_posting, sandbox_client) == 1

    row = db.scalars(
        select(FiscalPurchaseFeedRow).where(
            FiscalPurchaseFeedRow.company_id == fiscal_posting.company_id
        )
    ).one()
    assert row.spplr_tin == FEED_SUPPLIER_TIN
    assert row.spplr_invc_no == FEED_INVOICE_NO
    assert row.spplr_nm == "Feed Supplier Ltd"
    assert row.total_taxable_amount == D(11_800)
    assert row.total_tax_amount == D(1_800)
    assert row.decision == FiscalFeedDecision.PENDING
    assert row.ap_document_id is None
    assert fiscal_posting.device.watermarks[FiscalSyncKind.PURCHASES]
    # Nothing is queued by a fetch: reading what RRA holds says nothing back to it.
    assert _confirmations(db, fiscal_posting.company_id) == []


def test_a_second_fetch_updates_rather_than_duplicates(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """A watermark slightly behind the server re-fetches a few rows on purpose, so the upsert
    is what keeps that harmless. (Supplier TIN, invoice number) is RRA's own identity for the
    row and the unique key the schema carries."""
    _fetch(db, fiscal_posting, sandbox_client)
    _fetch(db, fiscal_posting, sandbox_client)

    assert (
        db.scalars(
            select(FiscalPurchaseFeedRow).where(
                FiscalPurchaseFeedRow.company_id == fiscal_posting.company_id
            )
        ).all()
        != []
    )
    assert len(feed_service.list_rows(db, fiscal_posting.company_id)) == 1


def test_a_decided_row_is_not_re_opened_by_a_later_fetch(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The figures are RRA's and have not changed; overwriting the decision would un-answer a
    question somebody answered."""
    _fetch(db, fiscal_posting, sandbox_client)
    row = feed_service.list_rows(db, fiscal_posting.company_id)[0]
    feed_service.reject(db, fiscal_posting.company_id, row, actor=fiscal_posting.owner)

    _fetch(db, fiscal_posting, sandbox_client)

    assert row.decision == FiscalFeedDecision.REJECTED


# --- Deciding -------------------------------------------------------------------------------


def test_accepting_an_unlinked_row_queues_a_confirmation(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The supplier's invoice with no Vinea document behind it — a purchase this company has
    not keyed, and may never. The confirmation is all RRA gets, with **RRA's own figures**."""
    _fetch(db, fiscal_posting, sandbox_client)
    row = feed_service.list_rows(db, fiscal_posting.company_id)[0]

    decided = feed_service.accept(
        db, fiscal_posting.company_id, row, actor=fiscal_posting.owner
    )

    assert row.decision == FiscalFeedDecision.ACCEPTED
    assert row.decided_by == fiscal_posting.owner.id
    assert row.ap_document_id is None
    assert decided.cancelled is None

    confirmation = decided.confirmation
    payload = confirmation.payload
    assert payload["regTyCd"] == AUTOMATIC
    assert payload["pchsSttsCd"] == APPROVED
    assert payload["spplrTin"] == FEED_SUPPLIER_TIN
    assert payload["spplrInvcNo"] == FEED_INVOICE_NO
    assert D(payload["totTaxblAmt"]) == D(11_800)
    assert D(payload["totTaxAmt"]) == D(1_800)
    assert confirmation.invc_no == 1, "a confirmation takes the next FIP number"
    assert confirmation.source_doc_type == feed_service.FEED_SOURCE
    assert confirmation.source_doc_id == row.id
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_rejecting_a_row_declines_it_with_the_same_call(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """`pchsSttsCd 04` — cancelled. The same endpoint says yes and no."""
    _fetch(db, fiscal_posting, sandbox_client)
    row = feed_service.list_rows(db, fiscal_posting.company_id)[0]

    decided = feed_service.reject(
        db, fiscal_posting.company_id, row, actor=fiscal_posting.owner
    )

    assert row.decision == FiscalFeedDecision.REJECTED
    assert row.ap_document_id is None, "a rejection is never linked to a document"
    assert decided.confirmation.payload["pchsSttsCd"] == CANCELLED
    assert decided.confirmation.payload["regTyCd"] == AUTOMATIC
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_row_decided_twice_is_refused(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    _fetch(db, fiscal_posting, sandbox_client)
    row = feed_service.list_rows(db, fiscal_posting.company_id)[0]
    feed_service.accept(db, fiscal_posting.company_id, row, actor=fiscal_posting.owner)

    with pytest.raises(FiscalSetupError) as refusal:
        feed_service.reject(db, fiscal_posting.company_id, row, actor=fiscal_posting.owner)
    assert refusal.value.code == "fiscal_feed_already_decided"


# --- One supplier invoice, one registration ---------------------------------------------------


def test_a_linked_document_whose_row_is_queued_is_cancelled_in_favour_of_the_confirmation(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """Decision 9's rule, in the case it names: the document's own registration has not been
    sent, so RRA never received it and the confirmation is the better of the two — it carries
    RRA's own figures and the supplier's invoice number, which is what RRA reconciles the pair
    by.

    Proven sensitive by the assertion on the *cancelled* row: without it the AP document's
    `regTyCd M` registration and this `regTyCd A` confirmation would both reach RRA, and one
    supplier invoice would be registered twice.
    """
    document = supplier_invoice(fiscal_posting, db)
    _fetch(db, fiscal_posting, sandbox_client)
    row = feed_service.list_rows(db, fiscal_posting.company_id)[0]

    decided = feed_service.accept(
        db,
        fiscal_posting.company_id,
        row,
        ap_document_id=document.id,
        actor=fiscal_posting.owner,
    )

    assert row.ap_document_id == document.id
    assert decided.cancelled is not None
    assert decided.cancelled.status == FiscalOutboxStatus.CANCELLED
    assert "in favour of the EBM feed confirmation" in decided.cancelled.resolution_note
    assert decided.confirmation is not None
    live = [
        candidate
        for candidate in _rows(db, fiscal_posting.company_id)
        if candidate.kind
        in (FiscalOutboxKind.PURCHASE, FiscalOutboxKind.PURCHASE_CONFIRM)
        and candidate.status != FiscalOutboxStatus.CANCELLED
    ]
    assert len(live) == 1, "one supplier invoice, one live registration"
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_linked_document_already_registered_is_not_confirmed_again(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The case decision 9 leaves to the step-3 report, and the answer it gives.

    RRA already holds this purchase under the document's own `regTyCd M` registration. A
    confirmation would arrive under a *second* `FIP` number for the same supplier invoice,
    which is the double registration the rule forbids — so the link is recorded, the row is
    accepted, and nothing is queued. The reason travels back to the caller rather than being
    swallowed, so the screen can say why no confirmation was sent.
    """
    document = supplier_invoice(fiscal_posting, db)
    drainer.drain_company(db, fiscal_posting.company_id, client=sandbox_client)
    _fetch(db, fiscal_posting, sandbox_client)
    row = feed_service.list_rows(db, fiscal_posting.company_id)[0]

    decided = feed_service.accept(
        db,
        fiscal_posting.company_id,
        row,
        ap_document_id=document.id,
        actor=fiscal_posting.owner,
    )

    assert row.decision == FiscalFeedDecision.ACCEPTED
    assert row.ap_document_id == document.id
    assert decided.confirmation is None, "already registered; a confirmation would be the second"
    assert decided.cancelled is None
    assert "already registered with RRA" in decided.note
    assert _confirmations(db, fiscal_posting.company_id) == []
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_linked_document_whose_outcome_is_unknown_refuses_the_decision(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """Cancelling the registration or confirming over it both need the same question answered
    first: does RRA hold it? Until it is answered, neither is safe."""
    document = supplier_invoice(fiscal_posting, db)
    registration = next(
        candidate
        for candidate in _rows(db, fiscal_posting.company_id)
        if candidate.kind == FiscalOutboxKind.PURCHASE
    )
    registration.status = FiscalOutboxStatus.UNKNOWN
    db.flush()
    _fetch(db, fiscal_posting, sandbox_client)
    row = feed_service.list_rows(db, fiscal_posting.company_id)[0]

    with pytest.raises(FiscalSetupError) as refusal:
        feed_service.accept(
            db,
            fiscal_posting.company_id,
            row,
            ap_document_id=document.id,
            actor=fiscal_posting.owner,
        )
    assert refusal.value.code == "fiscal_status_unresolved"
    assert row.decision == FiscalFeedDecision.PENDING

    # Proven sensitive: resolved back to `queued`, and the same decision goes through.
    registration.status = FiscalOutboxStatus.QUEUED
    db.flush()
    decided = feed_service.accept(
        db,
        fiscal_posting.company_id,
        row,
        ap_document_id=document.id,
        actor=fiscal_posting.owner,
    )
    assert decided.cancelled is not None


def test_a_confirmation_is_acknowledged_and_carries_no_receipt(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The whole round trip against the sandbox, which answers `savePurchases` for both
    registration types on the same path."""
    _fetch(db, fiscal_posting, sandbox_client)
    row = feed_service.list_rows(db, fiscal_posting.company_id)[0]
    feed_service.accept(db, fiscal_posting.company_id, row, actor=fiscal_posting.owner)

    drainer.drain_company(db, fiscal_posting.company_id, client=sandbox_client)

    confirmation = _confirmations(db, fiscal_posting.company_id)[0]
    assert confirmation.status == FiscalOutboxStatus.SENT
    assert confirmation.last_result_cd == "000"
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_an_unlinked_accept_of_an_invoice_already_declared_is_refused(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The route the link was supposed to close, and did not.

    The link is optional and the **coincidence** is not: a posted AP document for the same
    supplier carrying the same supplier invoice number, with a live registration, is the
    purchase this feed row is the other side of whether or not anybody said so. Accepting
    without the link would register it twice by exactly the route the link exists to prevent —
    the three linked cases were each covered and this one was not.

    The refusal names the document, because the operator's next move depends on which it is:
    link it (and replace the registration with the confirmation), or correct one of the two
    references because they really are different invoices.

    Proven sensitive by deleting the `_refuse_a_duplicate_of_an_undeclared_link` call, which
    makes this test's first half `DID NOT RAISE`. That the *state* is unreachable rather than merely
    refused here is a separate proof, and it is
    `test_one_supplier_invoice_declared_twice_is_caught_as_a_state`: it patches the refusal out,
    walks through the door, and asserts invariant 10a catches what is left behind.
    """
    document = supplier_invoice(
        fiscal_posting,
        db,
        partner_id=_feed_supplier(db, fiscal_posting).id,
        reference=str(FEED_INVOICE_NO),
    )
    _fetch(db, fiscal_posting, sandbox_client)
    row = feed_service.list_rows(db, fiscal_posting.company_id)[0]

    with pytest.raises(FiscalSetupError) as refusal:
        feed_service.accept(db, fiscal_posting.company_id, row, actor=fiscal_posting.owner)
    assert refusal.value.code == "purchase_already_declared"
    assert document.number in str(refusal.value)
    assert "ap_document_id" in refusal.value.field_errors
    assert row.decision == FiscalFeedDecision.PENDING
    assert _confirmations(db, fiscal_posting.company_id) == []

    # Proven sensitive the other way: **linked**, the same accept goes through and cancels the
    # document's own registration in favour of the confirmation.
    decided = feed_service.accept(
        db,
        fiscal_posting.company_id,
        row,
        ap_document_id=document.id,
        actor=fiscal_posting.owner,
    )
    assert decided.cancelled is not None
    assert decided.confirmation is not None
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_reference_with_no_numeric_form_is_not_a_duplicate(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """`INV/2026/0042` is not a number the authority can key by, so there is nothing for this
    build to match on either — and refusing on a reference RRA never received would block an
    accept over a coincidence that is not one.

    The pair is reconciled by hand in that case, which is what the step-3 report says the
    Supplier invoice screen should tell the person keying it.
    """
    supplier_invoice(
        fiscal_posting,
        db,
        partner_id=_feed_supplier(db, fiscal_posting).id,
        reference="INV/2026/0077",
    )
    _fetch(db, fiscal_posting, sandbox_client)
    row = feed_service.list_rows(db, fiscal_posting.company_id)[0]

    decided = feed_service.accept(
        db, fiscal_posting.company_id, row, actor=fiscal_posting.owner
    )

    assert decided.confirmation is not None
    assert row.decision == FiscalFeedDecision.ACCEPTED
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def _feed_supplier(db: Session, fixture: FiscalPosting) -> Partner:
    """A supplier carrying the feed fixture's own TIN.

    The fixture's supplier is `100000002` and the sandbox's feed row comes from `100000003`, so
    a duplicate can only be built by giving Vinea a supplier the feed row is actually about —
    which is the realistic case: the invoice was keyed *and* the supplier's device registered it.
    """
    supplier = partner_masters.create_partner(
        db,
        fixture.company_id,
        partner_masters.PartnerInput(
            name="Feed Supplier Ltd",
            supplier_code="FEEDSUP",
            tin=FEED_SUPPLIER_TIN,
        ),
        actor=fixture.owner,
    )
    db.flush()
    return supplier


def test_a_reversed_declaration_leaves_the_invoice_free_to_confirm(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """A reversed document declares nothing, so its invoice is not a duplicate.

    Reversing a declared purchase declares the **opposite** (decision 6), so RRA holds a `P`
    and an `R` for the same invoice and they net to nothing held. Confirming it from the
    supplier's side is then exactly right, and refusing would block an accept over a
    registration that was already undone.

    Proven sensitive by dropping the `status == POSTED` clause in `declared_documents`: this
    accept is refused `purchase_already_declared` over an invoice RRA is no longer holding.
    """
    document = supplier_invoice(
        fiscal_posting,
        db,
        partner_id=_feed_supplier(db, fiscal_posting).id,
        reference=str(FEED_INVOICE_NO),
    )
    drainer.drain_company(db, fiscal_posting.company_id, client=sandbox_client)
    documents_service.reverse_document(
        db, document, on_date=APRIL, reason="keyed twice", actor=fiscal_posting.owner
    )
    _fetch(db, fiscal_posting, sandbox_client)
    row = feed_service.list_rows(db, fiscal_posting.company_id)[0]

    decided = feed_service.accept(
        db, fiscal_posting.company_id, row, actor=fiscal_posting.owner
    )

    assert decided.confirmation is not None
    assert row.decision == FiscalFeedDecision.ACCEPTED
    assert_fiscal_invariants(db, fiscal_posting.company_id)
