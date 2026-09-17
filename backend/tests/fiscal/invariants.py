"""The P7 phase invariants — what must be true of fiscalization after every posting and every
drain.

Six of them, and each exists because the opposite is a thing that could go unnoticed for a
month and then be a conversation with a revenue authority.

1. **One document, one row.** A posted AR document whose branch had a **live device when it
   posted** has exactly one non-cancelled `sale`/`refund` row, and every row names exactly one
   document. A document with two rows is a sale RRA would register twice; one with none is a
   sale RRA never heard about.

   The "when it posted" is the whole of the second half, and the first version of this file
   did not have it. It skipped any posted document with no row *and* no receipt, so that a
   company which turned a device on halfway through its life would not be told its earlier
   invoices were holes — and "no row and no receipt" is exactly the shape of the failure the
   clause exists for. The assertion under that skip was reachable only in a state that cannot
   occur, and none of the sensitivity tests covered it, which is how it survived review.
   `fiscal_devices.activated_at` (0024) is the discriminator: within the device's current
   continuous active period, a row is not optional.
2. **`sent` means signed.** Every `sent` sale or refund has exactly one receipt, and no row in
   any other state has one. A `sent` row without a receipt is a sale registered with nothing to
   print; a receipt on a `queued` row is a receipt nobody issued.
3. **`invc_no` is gapless per device.** RRA keys a sale by (taxpayer, branch, invoice number)
   and reconciles the run. A hole in it is a question.
4. **The counters only go up.** Per device, `rcpt_no` strictly increases within a receipt type
   and `tot_rcpt_no` across types. A receipt is a legal document with a number on it.
5. **FIFO holds.** No row of a later `sequence_no` is `sent` while an earlier row of the same
   device is still non-terminal. This is the one that catches a drainer "helpfully" skipping a
   stuck row — which RRA answers `921`/`922` to, and which puts a stock report before its sale.
6. **No key is anywhere.** The three device keys appear in no stored payload and no stored
   response. Walked over every row a test produced rather than asserted of one serializer,
   because the leak that matters is the one nobody thought to look for.

Step 3 adds four, for the purchase and stock reports:

7. **One document, one purchase row — or a confirmation instead, never both.** A posted AP
   invoice or return whose branch had a live device when it posted has exactly one
   non-cancelled `purchase` row; a document an **accepted feed row** is linked to has none,
   because the confirmation registers it from the supplier's side and `feed.accept` cancelled
   its own. Either mistake declares one supplier invoice twice and doubles the input VAT RRA
   holds against the taxpayer.
8. **`invc_no` is gapless per device on the purchase run too.** `FIP` is its own run and RRA
   reconciles it the same way it reconciles the sales run.
9. **`sar_no` is gapless per device.** The stock run, same argument: a hole in it is a question
   from Kigali about a movement nobody can produce.
10a. **One supplier invoice, one registration — quantified over the feed rather than over
   documents.** No accepted feed row with a live confirmation shares its (supplier TIN,
   supplier invoice number) with a live registration of Vinea's own. Invariant 7 asks the
   question from the document's side and only sees the pair the operator *linked*; this one
   asks it from the authority's side, where the pair is the identity RRA reconciles by, so an
   unlinked confirmation of an invoice already declared is caught as a **state** rather than
   refused at one entry point. `feed.accept` refuses it too, and both read
   `feed.declared_documents` so they cannot disagree about what a duplicate is.

11. **A movement never overtakes its document.** Every `stock_io` or `stock_master` row raised
   by a partner document **that has one** sits *behind* that document's own sale, refund or
   purchase row on the same device. A journal batch has none — it moves stock and is not a
   sale — and its movement is reported as an adjustment for that reason.

   VSDC §3.1 requires the invoice information first and RRA answers `921`/`922` when it does
   not get it. The reason this is an invariant rather than a comment is that the companion
   stock entry posts *first*, so the natural order is the wrong one.
"""

import json
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fiscal import feed as feed_service
from app.fiscal import outbox as outbox_service
from app.fiscal.keys import decrypt_key
from app.models.fiscalization import (
    FiscalDevice,
    FiscalDeviceStatus,
    FiscalFeedDecision,
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalPurchaseFeedRow,
    FiscalReceipt,
)
from app.models.journal import JournalEntry
from app.models.subledger import DocumentKind, DocumentStatus, PartnerDocument, PartnerRole

DOCUMENT_SOURCE = outbox_service.DOCUMENT_SOURCE

#: The runs a journal batch posts under. Not sales, so not rows — decision 3.
JOURNAL_DOC_TYPES = frozenset({"ARJN", "APJN"})

SALE_KINDS = (FiscalOutboxKind.SALE, FiscalOutboxKind.REFUND)
PURCHASE_KINDS = (FiscalOutboxKind.PURCHASE, FiscalOutboxKind.PURCHASE_CONFIRM)
#: The kinds a partner document raises *about* a movement rather than about itself.
MOVEMENT_KINDS = (FiscalOutboxKind.STOCK_IO, FiscalOutboxKind.STOCK_MASTER)

#: What a purchase feed confirmation's row points at — a row of RRA's own record rather than a
#: Vinea document, so it is excluded from "one document, one row" on purpose.
FEED_SOURCE = "fiscal_purchase_feed"


def assert_fiscal_invariants(db: Session, company_id: int) -> None:
    rows = list(
        db.scalars(
            select(FiscalOutboxRow)
            .where(FiscalOutboxRow.company_id == company_id)
            .order_by(FiscalOutboxRow.sequence_no)
        )
    )
    receipts = list(
        db.scalars(select(FiscalReceipt).where(FiscalReceipt.company_id == company_id))
    )
    devices = list(
        db.scalars(select(FiscalDevice).where(FiscalDevice.company_id == company_id))
    )

    _assert_one_document_one_row(db, company_id, rows)
    _assert_sent_means_signed(rows, receipts)
    _assert_invoice_numbers_gapless(rows)
    _assert_counters_only_rise(receipts)
    _assert_fifo(rows)
    _assert_no_key_leaked(devices, rows, receipts)
    _assert_one_document_one_purchase_row(db, company_id, rows)
    _assert_one_registration_per_supplier_invoice(db, company_id, rows)
    _assert_purchase_numbers_gapless(rows)
    _assert_stock_numbers_gapless(rows)
    _assert_movements_follow_their_document(rows)


def _assert_one_document_one_row(
    db: Session, company_id: int, rows: list[FiscalOutboxRow]
) -> None:
    by_document: dict[tuple[str | None, int | None], list[FiscalOutboxRow]] = defaultdict(list)
    for row in rows:
        if row.kind not in SALE_KINDS or row.status == FiscalOutboxStatus.CANCELLED:
            continue
        by_document[(row.source_doc_type, row.source_doc_id)].append(row)

    for (source_type, source_id), group in by_document.items():
        assert source_id is not None, (
            f"a {group[0].kind} queue row names no document: a row RRA would register against "
            "nothing"
        )
        assert len(group) == 1, (
            f"document {source_id} has {len(group)} live {source_type} rows — RRA would "
            "register the same sale twice"
        )

    live_since = _device_live_since(db, company_id)
    posted_at = _posting_moments(db, company_id)
    candidates = db.scalars(
        select(PartnerDocument).where(
            PartnerDocument.company_id == company_id,
            PartnerDocument.role == PartnerRole.AR,
            PartnerDocument.kind.in_((DocumentKind.INVOICE, DocumentKind.CREDIT_NOTE)),
            PartnerDocument.status == DocumentStatus.POSTED,
        )
    )
    for document in candidates:
        # **A journal batch is not a sale** (decision 3): `ARJN` documents are opening balances
        # and corrections keyed under the `JNL` transaction type, and the posting hook skips
        # them, so they are legitimately row-less however live the device was.
        if document.doc_type in JOURNAL_DOC_TYPES:
            continue
        since = live_since.get(document.branch_id)
        moment = posted_at.get(document.journal_entry_id)
        if since is None or moment is None or moment < since:
            # Nothing was fiscalizing on this branch when this posted. Not a hole: the company
            # had no device, or it was suspended, and `post_document` never reached the hook.
            continue
        assert by_document.get((outbox_service.DOCUMENT_SOURCE, document.id)), (
            f"{document.number} posted at {moment} on a branch whose device has been live "
            f"since {since}, and it has no queue row — a sale that reached the ledger and "
            "never reached RRA, which is the failure this whole phase exists to prevent"
        )


def _device_live_since(db: Session, company_id: int) -> dict[int, datetime]:
    """branch → when its device was last made live, for the devices that are live now.

    A suspended device is absent, deliberately: it makes the company unfiscalized, the hook is
    never reached, and a document posted in that window has no row and should not.
    """
    return {
        device.branch_id: _aware(device.activated_at)
        for device in db.scalars(
            select(FiscalDevice).where(FiscalDevice.company_id == company_id)
        )
        if device.status == FiscalDeviceStatus.ACTIVE and device.activated_at is not None
    }


def _posting_moments(db: Session, company_id: int) -> dict[int, datetime]:
    """entry id → when it was posted. The document's own `document_date` is a *date the user
    chose*; what decides whether a device was live is when the posting actually happened."""
    rows = db.execute(
        select(JournalEntry.id, JournalEntry.posted_at).where(
            JournalEntry.company_id == company_id, JournalEntry.posted_at.is_not(None)
        )
    ).all()
    return {entry_id: _aware(moment) for entry_id, moment in rows}


def _aware(moment: datetime | None) -> datetime | None:
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def _assert_sent_means_signed(
    rows: list[FiscalOutboxRow], receipts: list[FiscalReceipt]
) -> None:
    by_outbox: dict[int, list[FiscalReceipt]] = defaultdict(list)
    for receipt in receipts:
        by_outbox[receipt.outbox_id].append(receipt)
    for row in rows:
        held = by_outbox.get(row.id, [])
        if row.kind in SALE_KINDS and row.status == FiscalOutboxStatus.SENT:
            assert len(held) == 1, (
                f"queue row {row.id} is sent and holds {len(held)} receipts — 'sent' has to "
                "mean 'RRA signed it', or nothing else in the phase can rely on it"
            )
        else:
            assert not held, (
                f"queue row {row.id} is '{row.status}' and holds a receipt — a receipt nobody "
                "issued"
            )


def _assert_invoice_numbers_gapless(rows: list[FiscalOutboxRow]) -> None:
    by_device: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        if row.kind in SALE_KINDS and row.invc_no is not None:
            by_device[row.device_id].append(row.invc_no)
    for device_id, numbers in by_device.items():
        ordered = sorted(numbers)
        assert ordered == list(range(1, len(ordered) + 1)), (
            f"device {device_id}'s invoice numbers are {ordered}, not 1..{len(ordered)} — RRA "
            "reconciles this run, and a hole in it is a question from Kigali"
        )


def _assert_counters_only_rise(receipts: list[FiscalReceipt]) -> None:
    by_type: dict[tuple[int, str], list[tuple[int, int]]] = defaultdict(list)
    by_device: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for receipt in receipts:
        by_type[(receipt.device_id, str(receipt.receipt_type))].append(
            (receipt.id, receipt.rcpt_no)
        )
        by_device[receipt.device_id].append((receipt.id, receipt.tot_rcpt_no))

    for (device_id, receipt_type), pairs in by_type.items():
        counters = [counter for _, counter in sorted(pairs)]
        assert counters == sorted(set(counters)) and len(counters) == len(set(counters)), (
            f"device {device_id}'s {receipt_type} counters are {counters} — a receipt number "
            "that went backwards or repeated is a thing somebody has to explain to RRA"
        )
    for device_id, pairs in by_device.items():
        counters = [counter for _, counter in sorted(pairs)]
        assert counters == sorted(set(counters)) and len(counters) == len(set(counters)), (
            f"device {device_id}'s total receipt counters are {counters}, which is not "
            "strictly increasing"
        )


def _assert_fifo(rows: list[FiscalOutboxRow]) -> None:
    blocked: dict[int, FiscalOutboxRow] = {}
    for row in sorted(rows, key=lambda candidate: candidate.sequence_no):
        earlier = blocked.get(row.device_id)
        if earlier is not None and row.status == FiscalOutboxStatus.SENT:
            raise AssertionError(
                f"queue row {row.id} (seq {row.sequence_no}) is sent while row {earlier.id} "
                f"(seq {earlier.sequence_no}) on the same device is still "
                f"'{earlier.status}' — RRA needs the sale before the movement it caused, and "
                "answers 921/922 when it does not get it"
            )
        if earlier is None and row.status in outbox_service.NON_TERMINAL:
            blocked[row.device_id] = row


def _assert_no_key_leaked(
    devices: list[FiscalDevice],
    rows: list[FiscalOutboxRow],
    receipts: list[FiscalReceipt],
) -> None:
    secrets = {
        plain
        for device in devices
        for plain in (
            decrypt_key(device.cmc_key),
            decrypt_key(device.intrl_key),
            decrypt_key(device.sign_key),
        )
        if plain
    }
    if not secrets:
        return
    corpus: list[tuple[str, str]] = []
    for row in rows:
        corpus.append((f"fiscal_outbox {row.id} payload", json.dumps(row.payload)))
        corpus.append((f"fiscal_outbox {row.id} response", json.dumps(row.response or {})))
    for receipt in receipts:
        corpus.append((f"fiscal_receipts {receipt.id} request", json.dumps(receipt.request)))
        corpus.append((f"fiscal_receipts {receipt.id} response", json.dumps(receipt.response)))
    for where, text in corpus:
        for secret in secrets:
            assert secret not in text, (
                f"a device key is stored in {where}. The keys are the only secrets this phase "
                "holds; a payload in a database is a payload in a backup."
            )


def _assert_one_document_one_purchase_row(
    db: Session, company_id: int, rows: list[FiscalOutboxRow]
) -> None:
    """Invariant 7. A posted AP invoice or return declares itself exactly once.

    Journal batches are excluded for the reason they are on the sale side — an `APJN` document
    is an opening balance or a correction, not a purchase — and so are the feed confirmations,
    which point at a row of RRA's own record and often at no Vinea document at all.
    """
    # Keyed on (source type, document) the way the sale side is, and for the same reason: the
    # **opposite declaration a reversal owes** is a different fact about the same document
    # rather than a second declaration of it, so it carries `partner_document_reversal`.
    by_document: dict[tuple[str | None, int | None], list[FiscalOutboxRow]] = defaultdict(list)
    for row in rows:
        if row.kind not in PURCHASE_KINDS or row.status == FiscalOutboxStatus.CANCELLED:
            continue
        if row.source_doc_type == FEED_SOURCE:
            continue
        assert row.source_doc_id is not None, (
            "a purchase queue row names no document: a declaration RRA would hold against "
            "nothing"
        )
        by_document[(row.source_doc_type, row.source_doc_id)].append(row)

    for (source_type, document_id), group in by_document.items():
        assert len(group) == 1, (
            f"document {document_id} has {len(group)} live {source_type} rows — RRA would "
            "register the same supplier invoice twice, and the input VAT with it"
        )

    # **Declared once: by its own registration, or by a confirmation, never by both.** An
    # accepted feed row linked to a document says RRA is registering that purchase from the
    # supplier's side, and `feed.accept` cancels the document's own row in favour of it — so
    # for those documents the correct number of live rows is *zero*, and one would be the
    # double registration decision 9 forbids.
    confirmed = _documents_confirmed_by_the_feed(db, company_id, rows)
    live_since = _device_live_since(db, company_id)
    posted_at = _posting_moments(db, company_id)
    candidates = db.scalars(
        select(PartnerDocument).where(
            PartnerDocument.company_id == company_id,
            PartnerDocument.role == PartnerRole.AP,
            PartnerDocument.kind.in_((DocumentKind.INVOICE, DocumentKind.CREDIT_NOTE)),
            # `POSTED` only, as on the sale side: a **reversed** document's declaration is
            # legitimately gone — RRA never received it, so the row was cancelled — or it
            # stands beside the opposite declaration that undid it. Neither is a hole.
            PartnerDocument.status == DocumentStatus.POSTED,
        )
    )
    for document in candidates:
        if document.doc_type in JOURNAL_DOC_TYPES:
            continue
        since = live_since.get(document.branch_id)
        moment = posted_at.get(document.journal_entry_id)
        if since is None or moment is None or moment < since:
            continue
        held = by_document.get((DOCUMENT_SOURCE, document.id))
        if document.id in confirmed:
            assert not held, (
                f"{document.number} is registered with RRA by an accepted EBM feed row *and* "
                "by its own declaration — one supplier invoice, registered twice"
            )
            continue
        assert held, (
            f"{document.number} posted at {moment} on a branch whose device has been live "
            f"since {since}, and it has no purchase row — a purchase that reached the ledger "
            "and never reached RRA"
        )


def _documents_confirmed_by_the_feed(
    db: Session, company_id: int, rows: list[FiscalOutboxRow]
) -> set[int]:
    """AP documents an accepted feed row is registering from the supplier's side.

    A **live confirmation** is what counts, not merely an accepted link. `feed.accept` does two
    different things depending on what RRA already holds: where the document's own registration
    had not been sent it cancels that and queues a confirmation, and where it *had* been sent it
    records the link and queues nothing, because a confirmation would then be the second
    registration. Both are "declared once"; reading only the link would call the second one a
    hole.
    """
    live_confirmations = {
        row.source_doc_id
        for row in rows
        if row.kind == FiscalOutboxKind.PURCHASE_CONFIRM
        and row.source_doc_type == FEED_SOURCE
        and row.status != FiscalOutboxStatus.CANCELLED
    }
    return {
        feed_row.ap_document_id
        for feed_row in db.scalars(
            select(FiscalPurchaseFeedRow).where(
                FiscalPurchaseFeedRow.company_id == company_id,
                FiscalPurchaseFeedRow.decision == FiscalFeedDecision.ACCEPTED,
                FiscalPurchaseFeedRow.ap_document_id.is_not(None),
            )
        )
        if feed_row.id in live_confirmations
    }


def _assert_purchase_numbers_gapless(rows: list[FiscalOutboxRow]) -> None:
    """Invariant 8 — the `FIP` run, per device, including cancelled rows."""
    _assert_gapless(
        rows,
        kinds=PURCHASE_KINDS,
        number=lambda row: row.invc_no,
        run="purchase invoice",
    )


def _assert_stock_numbers_gapless(rows: list[FiscalOutboxRow]) -> None:
    """Invariant 9 — the `FSAR` run. `stock_master` carries no number of its own: it is a
    snapshot beside a movement rather than a movement."""
    _assert_gapless(
        rows,
        kinds=(FiscalOutboxKind.STOCK_IO,),
        number=lambda row: row.sar_no,
        run="stock movement",
    )


def _assert_gapless(
    rows: list[FiscalOutboxRow],
    *,
    kinds: tuple[FiscalOutboxKind, ...],
    number,  # noqa: ANN001 - row -> int | None
    run: str,
) -> None:
    by_device: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        if row.kind in kinds and number(row) is not None:
            by_device[row.device_id].append(number(row))
    for device_id, numbers in by_device.items():
        ordered = sorted(numbers)
        assert ordered == list(range(1, len(ordered) + 1)), (
            f"device {device_id}'s {run} numbers are {ordered}, not 1..{len(ordered)} — RRA "
            "reconciles this run, and a hole in it is a question from Kigali"
        )


def _assert_movements_follow_their_document(rows: list[FiscalOutboxRow]) -> None:
    """Invariant 11. A partner document's movement is behind the document's own row.

    The one ordering that is *not* automatic. Everything else in the queue is in creation order
    and creation order is the right order — but the companion stock entry posts **before** the
    partner side (P6 decision 2), so a movement reported from inside the stock service would sit
    ahead of the sale that caused it. `post_document` reports it afterwards instead, and this is
    what holds that true.
    """
    document_rows: dict[tuple[str | None, int | None], list[FiscalOutboxRow]] = defaultdict(list)
    for row in rows:
        if row.kind in SALE_KINDS or row.kind in PURCHASE_KINDS:
            document_rows[(row.source_doc_type, row.source_doc_id)].append(row)

    for row in rows:
        if row.kind not in MOVEMENT_KINDS or row.source_doc_type != DOCUMENT_SOURCE:
            continue
        on_this_device = [
            candidate
            for key, group in document_rows.items()
            if key[1] == row.source_doc_id
            for candidate in group
            if candidate.device_id == row.device_id
        ]
        if not on_this_device:
            # **A journal batch moves stock and is not a sale** (decision 3), so it has no
            # document row for its movement to sit behind — which is exactly why that movement
            # is reported as an *adjustment* rather than as a sale RRA has no invoice for. A
            # sale that lost its row is a different failure, and invariant 1 is what catches it.
            continue
        earlier = [
            candidate
            for candidate in on_this_device
            if candidate.sequence_no < row.sequence_no
        ]
        assert earlier, (
            f"queue row {row.id} reports a movement of document {row.source_doc_id} on device "
            f"{row.device_id} with no sale, refund or purchase of that document ahead of it — "
            "RRA needs the invoice information first and answers 921/922 without it"
        )


def _assert_one_registration_per_supplier_invoice(
    db: Session, company_id: int, rows: list[FiscalOutboxRow]
) -> None:
    """Invariant 10a. Quantified over **feed rows**, not over documents.

    Invariant 7 asks "does this document have one registration", which can only see the pair
    the operator linked. RRA reconciles a purchase by (supplier TIN, supplier invoice number),
    so the state that must be unreachable is *that pair* reaching the authority twice — once as
    Vinea's own `regTyCd M` registration and once as a `regTyCd A` confirmation of the
    supplier's own sale. An unlinked accept is the route, and `feed.accept` refuses it; this is
    what makes the refusal a property of the data rather than a check at one door.
    """
    live_confirmations = {
        row.source_doc_id
        for row in rows
        if row.kind == FiscalOutboxKind.PURCHASE_CONFIRM
        and row.source_doc_type == FEED_SOURCE
        and row.status != FiscalOutboxStatus.CANCELLED
    }
    if not live_confirmations:
        return
    declared = feed_service.declared_documents(db, company_id)
    for feed_row in db.scalars(
        select(FiscalPurchaseFeedRow).where(
            FiscalPurchaseFeedRow.company_id == company_id,
            FiscalPurchaseFeedRow.decision == FiscalFeedDecision.ACCEPTED,
        )
    ):
        if feed_row.id not in live_confirmations:
            continue
        clash = declared.get((feed_row.spplr_tin, feed_row.spplr_invc_no))
        assert clash is None, (
            f"supplier invoice {feed_row.spplr_invc_no} from TIN {feed_row.spplr_tin} reaches "
            f"RRA twice: as {clash.number}'s own registration and as the confirmation of feed "
            f"row {feed_row.id}. One purchase, registered twice, and the input VAT with it"
        )
