"""The P7 phase invariants — what must be true of fiscalization after every posting and every
drain.

Six of them, and each exists because the opposite is a thing that could go unnoticed for a
month and then be a conversation with a revenue authority.

1. **One document, one row.** A posted fiscalized AR document has exactly one non-cancelled
   `sale`/`refund` row, and every row names exactly one document. A document with two rows is
   a sale RRA would register twice; one with none is a sale RRA never heard about.
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
"""

import json
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fiscal import outbox as outbox_service
from app.fiscal.keys import decrypt_key
from app.models.fiscalization import (
    FiscalDevice,
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalReceipt,
)
from app.models.subledger import DocumentKind, DocumentStatus, PartnerDocument, PartnerRole

SALE_KINDS = (FiscalOutboxKind.SALE, FiscalOutboxKind.REFUND)


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

    fiscalized = db.scalars(
        select(PartnerDocument).where(
            PartnerDocument.company_id == company_id,
            PartnerDocument.role == PartnerRole.AR,
            PartnerDocument.kind.in_((DocumentKind.INVOICE, DocumentKind.CREDIT_NOTE)),
            PartnerDocument.status == DocumentStatus.POSTED,
        )
    )
    for document in fiscalized:
        # Only documents this company fiscalized: a tenant that turned a device on halfway
        # through its life has invoices before it that were never RRA's to see, and they are
        # not a hole in anything.
        group = by_document.get((outbox_service.DOCUMENT_SOURCE, document.id))
        if group is None and document.fiscal_receipt_id is None:
            continue
        assert group, (
            f"{document.number} is a posted fiscal document with no live queue row — a sale "
            "that reached the ledger and never reached RRA"
        )


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
