"""The EBM purchase feed (decision 9) — purchases somebody else registered against this TIN.

A feed row **is not a document and posts nothing.** It is the other side of a supplier's own
sale: their device registered it, RRA holds it against this taxpayer, and the operator's job is
to say whether it happened. Accepting queues a confirmation with RRA's own figures; rejecting
queues the same call with a status that declines it. Neither moves stock, neither touches the
ledger, and neither creates an AP document — a purchase Vinea has no record of is a
conversation with RRA, not an invoice.

**One supplier invoice is registered once.** Two things can tell RRA about the same purchase:
this company's own AP document (`app/fiscal/purchases.py`, `regTyCd M`) and the supplier's
feed row confirmed here (`regTyCd A`). Registering both would double the input VAT RRA holds
against the taxpayer, so linking an AP document to a feed row resolves it — see `accept`, which
is where the whole rule lives.

**Watermarks are stored only after a success**, in the same call that stored the rows, which is
the discipline `app/fiscal/devices.py` keeps for every other synced kind and for the same
reason: a failed fetch that advanced the watermark would skip every row RRA published between
the two calls, silently and for good.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import NotFoundError
from app.fiscal import outbox
from app.fiscal import purchases as purchase_service
from app.fiscal.devices import (
    FiscalSetupError,
    FiscalUpstreamError,
    call_sync,
    record_watermark,
)
from app.fiscal.mapping import FiscalPurchaseConfirmation, FiscalPurchaseFeedEntry
from app.fiscal.registry import adapter_for
from app.kernel.sequences import DocType, claim_number
from app.models.company import Company
from app.models.fiscalization import (
    FiscalDevice,
    FiscalFeedDecision,
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalPurchaseFeedRow,
    FiscalSyncKind,
)
from app.models.partner import Partner
from app.models.subledger import DocumentStatus, PartnerDocument, PartnerRole
from app.models.user import User
from app.services.audit import record_audit

#: What a confirmation's queue row points at. Its own table rather than a partner document,
#: because a feed row is RRA's record and may have no Vinea document at all.
FEED_SOURCE = "fiscal_purchase_feed"


@dataclass(frozen=True)
class FeedDecision:
    """What accepting or rejecting a row actually did.

    `confirmation` is `None` in exactly one case, and it is the interesting one: the row was
    accepted against an AP document whose own registration RRA already holds, so confirming it
    would be the second registration of one invoice. `cancelled` names the document's row when
    the decision cancelled it instead.
    """

    row: FiscalPurchaseFeedRow
    confirmation: FiscalOutboxRow | None
    cancelled: FiscalOutboxRow | None = None
    note: str = ""


# --- Queries --------------------------------------------------------------------------------


def list_rows(
    db: Session,
    company_id: int,
    *,
    device_id: int | None = None,
    decision: FiscalFeedDecision | None = None,
) -> Sequence[FiscalPurchaseFeedRow]:
    query = select(FiscalPurchaseFeedRow).where(
        FiscalPurchaseFeedRow.company_id == company_id
    )
    if device_id is not None:
        query = query.where(FiscalPurchaseFeedRow.device_id == device_id)
    if decision is not None:
        query = query.where(FiscalPurchaseFeedRow.decision == decision)
    return list(
        db.scalars(
            query.order_by(
                FiscalPurchaseFeedRow.fetched_at.desc(), FiscalPurchaseFeedRow.id.desc()
            )
        )
    )


def get_row(db: Session, company_id: int, row_id: int) -> FiscalPurchaseFeedRow:
    row = db.scalar(
        select(FiscalPurchaseFeedRow).where(
            FiscalPurchaseFeedRow.company_id == company_id,
            FiscalPurchaseFeedRow.id == row_id,
        )
    )
    if row is None:
        raise NotFoundError("Feed row not found", code="fiscal_feed_row_not_found")
    return row


# --- Fetching -------------------------------------------------------------------------------


def fetch(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    *,
    actor: User,
    request: Request | None = None,
    client: httpx.Client | None = None,
) -> int:
    """Pull the feed by watermark and upsert what came back. Returns how many rows landed."""
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None, client=client)
    since = device.watermarks.get(FiscalSyncKind.PURCHASES)
    result, synced = call_sync(adapter.fetch_purchase_feed, device, since=since)
    if not result.ok:
        device.last_error = f"{result.code}: {result.message}"
        raise FiscalUpstreamError(
            f"The purchase feed was refused ({result.code}): {result.message}",
            code="fiscal_sync_refused",
        )
    count = _store(db, company_id, device, synced.purchases)
    record_watermark(device, FiscalSyncKind.PURCHASES, synced.watermark)
    record_audit(
        db,
        company_id=company_id,
        action="fiscal_feed.fetch",
        entity="fiscal_device",
        entity_id=device.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={"rows": count, "watermark": synced.watermark},
        request=request,
    )
    return count


def _store(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    entries: Sequence[FiscalPurchaseFeedEntry],
) -> int:
    """Upsert by (device, supplier TIN, supplier invoice number) — RRA's own identity for the
    row, and the unique key the schema carries.

    Upsert rather than insert, because a watermark slightly behind the server re-fetches a few
    rows on purpose (`devices._now_watermark`), and **a row an operator has already decided is
    left alone**: the figures are RRA's and have not changed, and overwriting the decision would
    un-answer a question somebody answered.
    """
    if not entries:
        return 0
    existing = {
        (row.spplr_tin, row.spplr_invc_no): row
        for row in db.scalars(
            select(FiscalPurchaseFeedRow).where(
                FiscalPurchaseFeedRow.company_id == company_id,
                FiscalPurchaseFeedRow.device_id == device.id,
            )
        )
    }
    now = datetime.now(UTC)
    for entry in entries:
        key = (entry.supplier.tin or "", entry.supplier_invoice_no)
        row = existing.get(key)
        if row is None:
            row = FiscalPurchaseFeedRow(
                company_id=company_id,
                device_id=device.id,
                spplr_tin=key[0],
                spplr_invc_no=entry.supplier_invoice_no,
                decision=FiscalFeedDecision.PENDING,
            )
            db.add(row)
        elif row.decision != FiscalFeedDecision.PENDING:
            continue
        row.spplr_nm = entry.supplier.name or None
        row.spplr_bhf_id = entry.supplier_branch_id
        row.sales_dt = entry.document_date
        row.payload = entry.source
        row.total_taxable_amount = entry.total_taxable
        row.total_tax_amount = entry.total_tax
        row.total_amount = entry.total_amount
        row.fetched_at = now
    db.flush()
    return len(entries)


# --- Deciding -------------------------------------------------------------------------------


def accept(
    db: Session,
    company_id: int,
    row: FiscalPurchaseFeedRow,
    *,
    ap_document_id: int | None = None,
    actor: User,
    request: Request | None = None,
) -> FeedDecision:
    """Confirm a purchase RRA is holding, optionally linking the AP document it became.

    **The no-double-registration rule** (decision 9), in full, because it is the whole reason
    the link exists:

    * **no link** — the purchase has no AP document (a supplier's invoice this company has not
      keyed, or never will). The confirmation is all RRA gets, and it is queued.
    * **no link, but Vinea has already declared this invoice** — refused,
      `purchase_already_declared`, naming the document. The link is optional and the
      *coincidence* is not: a posted AP document for the same supplier carrying the same
      supplier invoice number, with a live registration, is the purchase this feed row is the
      other side of, whether or not anybody said so. Confirming without the link would
      register it twice by exactly the route the link exists to prevent. The refusal names the
      document so the operator can link it — which is the case above — or say explicitly that
      it is a different invoice by correcting one of the two references.
    * **linked, and the document's own `purchase` row is `queued` or `failed`** — RRA never
      received it, so the row is **cancelled in favour of the confirmation** and the
      confirmation is queued. The confirmation is the better of the two: it carries RRA's own
      figures and the supplier's invoice number, which is what RRA reconciles the pair by.
    * **linked, and the document's row is `sent`** — RRA already holds this purchase, under a
      `regTyCd M` registration of Vinea's own. A confirmation would be a *second* registration
      of one invoice under a second `FIP` number, so nothing is queued: the link is recorded,
      the row is accepted, and the reason is on the audit trail. Doing it the other way round —
      confirming and leaving the registration — is what "must never be registered twice" rules
      out.
    * **linked, and the document's row is `unknown` or `needs_receipt`** — refused. RRA may be
      holding the registration and may not; deciding between cancelling it and confirming over
      it needs that question answered first.
    """
    _assert_pending(row)
    document = _linked_document(db, company_id, ap_document_id)
    if document is None:
        _refuse_a_duplicate_of_an_undeclared_link(db, company_id, row)
    cancelled: FiscalOutboxRow | None = None
    note = ""
    queue_confirmation = True
    if document is not None:
        rows = purchase_service.rows_for(db, company_id, document.id)
        held = rows[-1] if rows else None
        if held is not None and held.status in (
            FiscalOutboxStatus.UNKNOWN,
            FiscalOutboxStatus.NEEDS_RECEIPT,
        ):
            raise FiscalSetupError(
                f"{document.number} has an EBM queue row in state '{held.status}', so it is "
                "not yet known whether RRA holds its registration. Resolve the row on the EBM "
                "queue screen before accepting this purchase.",
                code="fiscal_status_unresolved",
                field_errors={"ap_document_id": [f"queue row is '{held.status}'"]},
            )
        if held is not None and held.status == FiscalOutboxStatus.SENT:
            queue_confirmation = False
            note = (
                f"{document.number} is already registered with RRA under purchase "
                f"{held.invc_no}; confirming this row would register the same supplier "
                "invoice twice."
            )
        elif held is not None:
            held.status = FiscalOutboxStatus.CANCELLED
            held.resolution_note = (
                f"cancelled in favour of the EBM feed confirmation of supplier invoice "
                f"{row.spplr_invc_no}"
            )
            cancelled = held
            note = (
                f"{document.number}'s own registration was cancelled in favour of this "
                "confirmation, which carries RRA's own figures."
            )

    confirmation = (
        _queue_confirmation(db, company_id, row, accepted=True, actor=actor)
        if queue_confirmation
        else None
    )
    _decide(
        db,
        company_id,
        row,
        decision=FiscalFeedDecision.ACCEPTED,
        ap_document_id=document.id if document is not None else None,
        actor=actor,
        request=request,
        note=note,
        confirmation=confirmation,
        cancelled=cancelled,
    )
    return FeedDecision(row=row, confirmation=confirmation, cancelled=cancelled, note=note)


def reject(
    db: Session,
    company_id: int,
    row: FiscalPurchaseFeedRow,
    *,
    actor: User,
    request: Request | None = None,
) -> FeedDecision:
    """Decline a purchase RRA is holding — the same call, with a status that says no.

    Never linked to an AP document: a rejection says this purchase did not happen, and a
    document saying it did would contradict it.
    """
    _assert_pending(row)
    confirmation = _queue_confirmation(db, company_id, row, accepted=False, actor=actor)
    _decide(
        db,
        company_id,
        row,
        decision=FiscalFeedDecision.REJECTED,
        ap_document_id=None,
        actor=actor,
        request=request,
        note="",
        confirmation=confirmation,
        cancelled=None,
    )
    return FeedDecision(row=row, confirmation=confirmation)


def declared_documents(
    db: Session, company_id: int
) -> dict[tuple[str, int], PartnerDocument]:
    """(supplier TIN, supplier invoice number) → the AP document declaring it.

    Only documents holding a **live** registration, because that is what "already declared"
    means: a cancelled row is one RRA never received. Read by `accept` to refuse the
    coincidence and by `assert_fiscal_invariants` to prove the state unreachable — one
    query, so the refusal and the invariant cannot disagree about what a duplicate is.

    Keyed on the pair RRA itself reconciles by, which is why a reference with no numeric form
    is absent: there is nothing for the authority or for this build to match on.
    """
    declared: dict[tuple[str, int], PartnerDocument] = {}
    rows = db.scalars(
        select(FiscalOutboxRow).where(
            FiscalOutboxRow.company_id == company_id,
            FiscalOutboxRow.kind.in_(
                (FiscalOutboxKind.PURCHASE, FiscalOutboxKind.PURCHASE_CONFIRM)
            ),
            FiscalOutboxRow.source_doc_type != FEED_SOURCE,
            FiscalOutboxRow.status != FiscalOutboxStatus.CANCELLED,
            FiscalOutboxRow.source_doc_id.is_not(None),
        )
    )
    for outbox_row in rows:
        document = db.scalar(
            select(PartnerDocument).where(
                PartnerDocument.company_id == company_id,
                PartnerDocument.id == outbox_row.source_doc_id,
                # **A reversed document declares nothing.** Reversing a declared purchase
                # declares the opposite (decision 6), so RRA holds a `P` and an `R` for the
                # same invoice and they net to nothing held. The invoice is then free to be
                # confirmed from the supplier's side, and treating it as still declared would
                # refuse an accept over a registration that was already undone.
                PartnerDocument.status == DocumentStatus.POSTED,
            )
        )
        if document is None:
            continue
        number = purchase_service.supplier_reference_number(document.reference)
        if number is None:
            continue
        partner = db.scalar(
            select(Partner).where(
                Partner.company_id == company_id, Partner.id == document.partner_id
            )
        )
        if partner is None or not partner.tin:
            continue
        declared[(partner.tin, number)] = document
    return declared


def _refuse_a_duplicate_of_an_undeclared_link(
    db: Session, company_id: int, row: FiscalPurchaseFeedRow
) -> None:
    """Refuse an unlinked accept whose invoice Vinea has already declared.

    Raises or returns; it never hands back a document, because the operator has not said this
    feed row *is* that document — that is the link, and the point of the refusal is to ask for
    it rather than to assume it.
    """
    document = declared_documents(db, company_id).get(
        (row.spplr_tin, row.spplr_invc_no)
    )
    if document is None:
        return None
    raise FiscalSetupError(
        f"{document.number} already declares supplier invoice {row.spplr_invc_no} from this "
        "supplier to RRA. Confirming this feed row as well would register one purchase twice. "
        f"Link it to {document.number} to replace that registration with this confirmation, or "
        "correct one of the two references if they are different invoices.",
        code="purchase_already_declared",
        field_errors={
            "ap_document_id": [f"already declared by {document.number}"],
        },
    )


def _assert_pending(row: FiscalPurchaseFeedRow) -> None:
    """Before anything is claimed or queued.

    `_decide` checks it too and would refuse a second decision either way — but by then a
    confirmation row has been inserted and an `FIP` number spent on it, which would leave a
    hole in a run RRA reconciles. The check that matters is the one that happens first.
    """
    if row.decision != FiscalFeedDecision.PENDING:
        raise FiscalSetupError(
            f"This purchase was already {row.decision}.",
            code="fiscal_feed_already_decided",
        )


def _linked_document(
    db: Session, company_id: int, ap_document_id: int | None
) -> PartnerDocument | None:
    if ap_document_id is None:
        return None
    document = db.scalar(
        select(PartnerDocument).where(
            PartnerDocument.company_id == company_id,
            PartnerDocument.id == ap_document_id,
        )
    )
    if document is None or document.role != PartnerRole.AP:
        raise NotFoundError(
            "Supplier document not found", code="partner_document_not_found"
        )
    if document.status != DocumentStatus.POSTED:
        raise FiscalSetupError(
            f"{document.number} is not posted, so it is not a purchase RRA could be holding.",
            code="document_not_posted",
            field_errors={"ap_document_id": ["not a posted document"]},
        )
    return document


def _queue_confirmation(
    db: Session,
    company_id: int,
    row: FiscalPurchaseFeedRow,
    *,
    accepted: bool,
    actor: User,
) -> FiscalOutboxRow:
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None)
    device = db.scalar(
        select(FiscalDevice).where(
            FiscalDevice.company_id == company_id, FiscalDevice.id == row.device_id
        )
    )
    claimed = claim_number(db, company_id, DocType.FISCAL_PURCHASE, branch_id=device.branch_id)
    confirmation = FiscalPurchaseConfirmation(
        invoice_no=claimed.sequence_no,
        source=row.payload,
        accepted=accepted,
        actor_id=str(actor.id),
        actor_name=actor.email,
    )
    return outbox.enqueue(
        db,
        company_id,
        device=device,
        kind=FiscalOutboxKind.PURCHASE_CONFIRM,
        payload=adapter.render(device, FiscalOutboxKind.PURCHASE_CONFIRM, confirmation),
        source_doc_type=FEED_SOURCE,
        source_doc_id=row.id,
        invc_no=claimed.sequence_no,
    )


def _decide(
    db: Session,
    company_id: int,
    row: FiscalPurchaseFeedRow,
    *,
    decision: FiscalFeedDecision,
    ap_document_id: int | None,
    actor: User,
    request: Request | None,
    note: str,
    confirmation: FiscalOutboxRow | None,
    cancelled: FiscalOutboxRow | None,
) -> None:
    _assert_pending(row)
    row.decision = decision
    row.decided_by = actor.id
    row.decided_at = datetime.now(UTC)
    row.ap_document_id = ap_document_id
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action=f"fiscal_feed.{decision}",
        entity="fiscal_purchase_feed",
        entity_id=row.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={
            "decision": str(decision),
            "ap_document_id": ap_document_id,
            "confirmation_row_id": confirmation.id if confirmation is not None else None,
            "cancelled_row_id": cancelled.id if cancelled is not None else None,
            "note": note or None,
        },
        request=request,
    )
