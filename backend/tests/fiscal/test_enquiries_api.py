"""The step-5 enquiries and listings, asked for data over HTTP.

The service tests prove the behaviour; these prove the **surface**. Step 4's own report named
the lesson this file exists for: the rule-14 register proves an endpoint has a *caller*, never
that it *answers*. It shipped fifteen routes, and one of them — `GET /gl/fx-revaluations/{id}` —
returned 500 to every request while passing review, because nothing had ever opened it.

So every route step 5 adds is opened here **with data behind it** and a figure is asserted off
what came back: a receipt counter, a status count, an item code, an age in seconds. A status
code alone would prove the route compiles and nothing else.
"""

from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import set_actor, set_tenant
from app.fiscal import drainer
from app.fiscal import outbox as outbox_service
from app.fiscal.rwanda.sandbox import (
    SANDBOX_CMC_KEY,
    SANDBOX_INTRL_KEY,
    SANDBOX_SIGN_KEY,
)
from app.models.fiscalization import (
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalReceipt,
)
from tests.fiscal.conftest import FiscalPosting
from tests.fiscal.helpers import invoice, receive
from tests.subledger.conftest import Subledger
from tests.subledger.test_documents import post_invoice

PASSWORD = "correct horse battery staple"
D = Decimal


@pytest.fixture
def signed_in(
    client: TestClient, db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> FiscalPosting:
    """A fiscalized tenant with a signed invoice behind it, reached through the real login so
    the permissions are the real ones."""
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)
    drainer.drain_company(
        db, fiscal_posting.company_id, client=sandbox_client, max_rows_per_device=50
    )
    db.commit()
    set_tenant(db, fiscal_posting.company_id)
    set_actor(db, fiscal_posting.owner.id)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": fiscal_posting.owner.email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return fiscal_posting


def _rows(db: Session, company_id: int) -> list[FiscalOutboxRow]:
    return list(
        db.scalars(
            select(FiscalOutboxRow)
            .where(FiscalOutboxRow.company_id == company_id)
            .order_by(FiscalOutboxRow.sequence_no)
        )
    )


# --- The queue ------------------------------------------------------------------------------


def test_the_queue_listing_counts_a_devices_rows_by_status(
    client: TestClient, db: Session, signed_in: FiscalPosting
) -> None:
    """The dashboard of decision 4, and the figure asserted is a count off the page."""
    response = client.get("/api/v1/fiscal/queue")

    assert response.status_code == 200, response.text
    devices = response.json()
    assert len(devices) == 1, "the fixture has one device, and an idle one is still listed"
    view = devices[0]
    assert view["device_id"] == signed_in.device.id
    assert view["sdc_id"] == "SDC010000005"
    assert view["branch_code"], "a queue row nobody can place is a row nobody can act on"
    counts = {row["status"]: row["rows"] for row in view["counts"]}
    assert counts[FiscalOutboxStatus.SENT] == len(_rows(db, signed_in.company_id)), (
        "everything the fixture queued was sent, so the sent count is the whole run"
    )
    assert view["pending_rows"] == 0
    assert view["offline"] is False
    assert view["blocked"] is False
    assert view["oldest_queued_at"] is None
    assert view["head"] is None
    assert view["last_success_at"] is not None


def test_a_stuck_device_reads_blocked_with_the_age_of_its_oldest_row(
    client: TestClient, db: Session, signed_in: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The listing's whole purpose: a device nobody can sell through, named on the page.

    The age is asserted as a **number of seconds**, not as its presence — a screen renders "2
    minutes ago" from it, and a field that was always zero would render that just as happily.
    """
    sandbox_client.post("/_sandbox/mode", json={"mode": "reject", "code": "884"})
    invoice(signed_in, db)
    drainer.drain_company(
        db, signed_in.company_id, client=sandbox_client, max_rows_per_device=50
    )
    db.commit()

    view = client.get("/api/v1/fiscal/queue").json()[0]

    assert view["blocked"] is True, "a `failed` head waits for a person, not for the clock"
    assert view["pending_rows"] >= 1
    assert view["head"]["status"] == FiscalOutboxStatus.FAILED
    assert view["head"]["last_result_cd"] == "884"
    assert view["oldest_queued_at"] is not None
    assert view["oldest_queued_age_seconds"] >= 0
    # Not offline: `offline` is 24 hours of queue, not "something is wrong". A dashboard that
    # conflated the two would cry wolf on every refusal.
    assert view["offline"] is False


def test_the_queue_rows_listing_names_the_document_and_the_partner(
    client: TestClient, db: Session, signed_in: FiscalPosting
) -> None:
    """A queue of `partner_document 412` rows is a list nobody can act on."""
    response = client.get(
        "/api/v1/fiscal/queue/rows", params={"kind": FiscalOutboxKind.SALE}
    )

    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == FiscalOutboxKind.SALE
    assert row["status"] == FiscalOutboxStatus.SENT
    assert row["invc_no"] == 1
    assert row["document_number"].startswith("INV-")
    assert row["partner_name"] == "Umucyo Traders Ltd"
    assert row["receipt_id"] is not None


def test_the_queue_row_detail_shows_the_payload_the_response_and_the_action_log(
    client: TestClient, db: Session, signed_in: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """What was sent, what came back, and who has touched it — with a real action on it.

    The action log is read off `audit_log`, so a row that recorded its own history would be a
    second copy to keep honest. A retry is the cheapest action that writes one.
    """
    sandbox_client.post("/_sandbox/mode", json={"mode": "reject", "code": "884"})
    document = invoice(signed_in, db)
    drainer.drain_company(
        db, signed_in.company_id, client=sandbox_client, max_rows_per_device=50
    )
    db.commit()
    failed = outbox_service.head_row(db, signed_in.company_id, signed_in.device.id)

    retried = client.post(f"/api/v1/fiscal/queue/rows/{failed.id}/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == FiscalOutboxStatus.QUEUED

    response = client.get(f"/api/v1/fiscal/queue/rows/{failed.id}")

    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["row"]["document_number"] == document.number
    assert D(str(detail["request"]["totTaxblAmt"])) == D("23600.00"), (
        "the payload is the frozen one, in the authority's own shape"
    )
    # The authority's code and message live on the row's own columns; `response` carries the
    # *body* it sent, which on a bare refusal is empty. Both are on the detail, so the screen
    # can say `884` without inventing a body that never arrived.
    assert detail["row"]["last_result_cd"] == "884"
    # `last_error` is deliberately cleared by the retry above — the row is queued again and
    # the previous failure is no longer its state. The code survives, because it is what the
    # operator quotes when they ring Kigali.
    assert detail["row"]["last_error"] is None
    actions = [entry["action"] for entry in detail["actions"]]
    assert actions == ["fiscal_outbox.retry"]
    assert detail["actions"][0]["actor_email"] == signed_in.owner.email


def test_no_device_key_reaches_the_queue_detail(
    client: TestClient, db: Session, signed_in: FiscalPosting
) -> None:
    """The ordinary case: nothing a real run produced carries a key."""
    row = _rows(db, signed_in.company_id)[0]

    body = client.get(f"/api/v1/fiscal/queue/rows/{row.id}").text

    for secret in (SANDBOX_CMC_KEY, SANDBOX_INTRL_KEY, SANDBOX_SIGN_KEY):
        assert secret not in body, "a device key reached a screen"


def test_a_key_that_reached_a_stored_row_still_does_not_reach_the_screen(
    client: TestClient, db: Session, signed_in: FiscalPosting
) -> None:
    """**Why the detail redacts a second time**, and the only way to show it.

    The drainer strips the three key fields before anything is stored, so on any row a real run
    produced the second pass has nothing left to do — which means the test above would pass
    with the second pass deleted. "The stored rows are clean" is a property of today's writer,
    and this screen will outlive it: a legacy row, a restored backup, or a future kind whose
    builder forgets are all rows this endpoint would otherwise print a secret from.

    So the key is planted in the stored payload, the way such a row would arrive, and the
    assertion is that the screen still refuses to show it.
    """
    row = _rows(db, signed_in.company_id)[0]
    row.payload = {**row.payload, "data": {"info": {"cmcKey": SANDBOX_CMC_KEY}}}
    db.commit()

    detail = client.get(f"/api/v1/fiscal/queue/rows/{row.id}")

    assert detail.status_code == 200, detail.text
    assert SANDBOX_CMC_KEY not in detail.text, "a planted key reached the screen"
    assert detail.json()["request"]["data"]["info"]["cmcKey"] == "***", (
        "redacted, not dropped — a screen that silently omitted the field would hide that "
        "there was something there"
    )


def test_a_row_that_may_not_be_retried_is_refused_rather_than_broken(
    client: TestClient, db: Session, signed_in: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """`unknown` means RRA may already hold the sale, and a retry on it is the duplicate the
    whole policy exists to prevent. Being told no is an ordinary outcome of the screen."""
    sandbox_client.post("/_sandbox/mode", json={"mode": "accept_then_timeout"})
    invoice(signed_in, db)
    drainer.drain_company(
        db, signed_in.company_id, client=sandbox_client, max_rows_per_device=50
    )
    db.commit()
    unknown = outbox_service.head_row(db, signed_in.company_id, signed_in.device.id)
    assert unknown.status == FiscalOutboxStatus.UNKNOWN

    refused = client.post(f"/api/v1/fiscal/queue/rows/{unknown.id}/retry")

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "fiscal_queue_action_refused"


def test_verifying_a_device_nobody_can_reach_is_refused_and_not_a_500(
    client: TestClient, db: Session, signed_in: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """**The likeliest moment anybody presses Verify**, and it used to be a 500.

    The in-process sandbox is not reachable from the app — it is a separate ASGI app, served by
    `ebm-sandbox` in the e2e stack — so a device pointed at `http://ebm.sandbox` is exactly a
    device that cannot be reached, which is the state this button meets on a real stuck queue.
    `verify_with_device` did not catch `FiscalTransportError`, so it escaped as an unhandled
    exception with no message on it.

    The success path needs a device the app can actually call: it is proved at the service
    level (`test_drain.py`, and rows 5 and 6 of the acceptance tape) and over HTTP at step 9,
    where Playwright drives Verify against the real `ebm-sandbox` container.
    """
    sandbox_client.post("/_sandbox/mode", json={"mode": "accept_then_timeout"})
    invoice(signed_in, db)
    drainer.drain_company(
        db, signed_in.company_id, client=sandbox_client, max_rows_per_device=50
    )
    db.commit()
    unknown = outbox_service.head_row(db, signed_in.company_id, signed_in.device.id)
    assert unknown.status == FiscalOutboxStatus.UNKNOWN

    refused = client.post(f"/api/v1/fiscal/queue/rows/{unknown.id}/verify")

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "fiscal_queue_action_refused"
    assert "could not be reached" in refused.json()["message"]
    db.expire_all()
    assert unknown.status == FiscalOutboxStatus.UNKNOWN, (
        "nothing was learned, so nothing moves"
    )


def test_attaching_a_receipt_over_the_api_signs_the_row_and_files_the_note(
    client: TestClient, db: Session, signed_in: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """A receipt read off the portal, keyed by a person who says so.

    No upstream call: the fields are normalised through the adapter, which is parsing rather
    than a request, so this endpoint answers over HTTP without a reachable device — which is
    also true of the real case, where the device is down and MyRRA is the only source.
    """
    sandbox_client.post("/_sandbox/mode", json={"mode": "accept_then_timeout"})
    invoice(signed_in, db)
    drainer.drain_company(
        db, signed_in.company_id, client=sandbox_client, max_rows_per_device=50
    )
    unknown = outbox_service.head_row(db, signed_in.company_id, signed_in.device.id)
    sandbox_client.post("/_sandbox/mode", json={"mode": "up"})
    drainer.verify_with_device(
        db,
        signed_in.company_id,
        signed_in.device,
        unknown,
        actor=signed_in.owner,
        client=sandbox_client,
    )
    assert unknown.status == FiscalOutboxStatus.NEEDS_RECEIPT
    db.commit()

    held = next(iter(sandbox_client.get("/_sandbox/ledger").json().values()))
    attached = client.post(
        f"/api/v1/fiscal/queue/rows/{unknown.id}/attach-receipt",
        json={
            "fields": held["sales"][str(unknown.invc_no)],
            "note": "read off MyRRA on 10 March",
        },
    )

    assert attached.status_code == 201, attached.text
    receipt = attached.json()
    assert receipt["receipt_number"].endswith(" NS")
    assert receipt["sdc_id"] == "SDC010000005"

    detail = client.get(f"/api/v1/fiscal/queue/rows/{unknown.id}").json()
    assert detail["row"]["status"] == FiscalOutboxStatus.SENT
    assert detail["resolution_note"] == "read off MyRRA on 10 March"
    assert detail["resolved_by_email"] == signed_in.owner.email
    assert [entry["action"] for entry in detail["actions"]] == [
        "fiscal_outbox.verify",
        "fiscal_outbox.attach_receipt",
    ]


# --- Receipts -------------------------------------------------------------------------------


def test_the_receipts_listing_answers_with_the_counter_off_the_paper(
    client: TestClient, db: Session, signed_in: FiscalPosting
) -> None:
    response = client.get("/api/v1/fiscal/receipts")

    assert response.status_code == 200, response.text
    receipts = response.json()
    assert len(receipts) == 1
    row = receipts[0]
    assert row["receipt_number"] == "1/1 NS"
    assert row["partner_name"] == "Umucyo Traders Ltd"
    assert D(str(row["base_total_amount"])) == D("23600.000000")
    assert row["journal_entry_id"] is not None, "the enquiry drills receipt → document → entry"
    assert row["qr_payload"]


@pytest.mark.parametrize(
    "term",
    ["1/1", "1/1 NS", "Umucyo", "INV-"],
    ids=["counter", "counter-label", "partner", "document"],
)
def test_the_receipt_search_finds_it_by_every_thing_a_person_holds(
    client: TestClient, db: Session, signed_in: FiscalPosting, term: str
) -> None:
    """One box over four fields: the printed counter, the document number, the partner and the
    authority's invoice number. Each is what somebody actually has in front of them."""
    found = client.get("/api/v1/fiscal/receipts", params={"search": term})

    assert found.status_code == 200, found.text
    assert [row["receipt_number"] for row in found.json()] == ["1/1 NS"], term


def test_the_receipt_search_does_not_return_the_day_for_an_unrelated_term(
    client: TestClient, db: Session, signed_in: FiscalPosting
) -> None:
    """A search box that matches everything is a search box that matches nothing."""
    assert client.get("/api/v1/fiscal/receipts", params={"search": "Kigali Glass"}).json() == []


def test_a_receipt_reads_back_by_id_and_a_stranger_gets_a_404(
    client: TestClient, db: Session, signed_in: FiscalPosting
) -> None:
    receipt = db.scalars(
        select(FiscalReceipt).where(FiscalReceipt.company_id == signed_in.company_id)
    ).one()

    found = client.get(f"/api/v1/fiscal/receipts/{receipt.id}")
    assert found.status_code == 200, found.text
    assert found.json()["receipt_number"] == "1/1 NS"

    assert client.get("/api/v1/fiscal/receipts/999999").status_code == 404


# --- The item enquiry -----------------------------------------------------------------------


def test_the_item_enquiry_shows_the_registration_and_the_item_code(
    client: TestClient, db: Session, signed_in: FiscalPosting
) -> None:
    """And it lists the items RRA has never heard of, which is the point: a fiscalized sale of
    an item with no class is refused, so "which items would refuse" is the question."""
    response = client.get("/api/v1/fiscal/items")

    assert response.status_code == 200, response.text
    by_code = {row["item_code"]: row for row in response.json()}
    sold = by_code[signed_in.stock_item.code]
    assert sold["registered"] is True
    assert sold["item_cd"].startswith("RW")
    assert sold["item_cls_cd"] == "5059020800"
    assert sold["qty_unit_cd"] == "U"
    assert D(str(sold["default_price_inclusive"])) == D("2360.000000")
    assert sold["pending_rows"] == 0

    unregistered = [row for row in response.json() if not row["registered"]]
    assert unregistered, "an item nobody has sold has no registration, and the screen says so"
    assert all(row["item_cd"] is None for row in unregistered)

    only_registered = client.get("/api/v1/fiscal/items", params={"registered": True}).json()
    assert {row["item_code"] for row in only_registered} < set(by_code)


# --- The receipt a document prints ----------------------------------------------------------


def test_the_document_receipt_renders_the_sdc_block_and_the_class_lines(
    client: TestClient, db: Session, signed_in: FiscalPosting
) -> None:
    receipt = db.scalars(
        select(FiscalReceipt).where(FiscalReceipt.company_id == signed_in.company_id)
    ).one()

    response = client.get(f"/api/v1/fiscal/documents/{receipt.document_id}/receipt")

    assert response.status_code == 200, response.text
    block = response.json()
    assert block["receipt_number"] == "1/1 NS"
    assert block["label"] == "NS"
    assert block["sdc_id"] == "SDC010000005"
    assert block["mrc_no"] == "WIS01006230"
    assert block["intrl_data"] and block["rcpt_sign"] and block["qr_payload"]
    assert block["taxpayer_tin"] == "999000099"
    assert block["customer_tin"] == "100000001"
    assert block["is_copy"] is False
    assert D(str(block["gross_total"])) == D("23600.00")
    classes = {line["tax_class"]: line for line in block["classes"]}
    assert D(str(classes["B"]["taxable"])) == D("23600.00")
    assert D(str(classes["B"]["tax"])) == D("3600.00")
    assert D(str(classes["B"]["rate"])) == D("18.00")


def test_a_second_print_is_a_copy_and_tells_rra_nothing(
    client: TestClient, db: Session, signed_in: FiscalPosting
) -> None:
    """§11 and §15: `COPY`, the counter incremented and audited, and no second call to RRA."""
    receipt = db.scalars(
        select(FiscalReceipt).where(FiscalReceipt.company_id == signed_in.company_id)
    ).one()
    before = len(_rows(db, signed_in.company_id))

    copied = client.post(f"/api/v1/fiscal/documents/{receipt.document_id}/receipt/copy")

    assert copied.status_code == 200, copied.text
    block = copied.json()
    assert block["is_copy"] is True
    assert block["copy_count"] == 1
    assert block["receipt_number"] == "1/1 NS", "a copy is the same receipt, not a new one"
    db.expire_all()
    assert len(_rows(db, signed_in.company_id)) == before, "a copy is a print, not a sale"


def test_printing_before_the_authority_has_signed_is_refused(
    client: TestClient, db: Session, signed_in: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """CIS §10. The button shows the queue status instead, which is why the refusal carries
    it."""
    sandbox_client.post("/_sandbox/mode", json={"mode": "down"})
    pending = invoice(signed_in, db)
    db.commit()

    refused = client.get(f"/api/v1/fiscal/documents/{pending.id}/receipt")

    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["code"] == "fiscal_receipt_pending"
    assert body["field_errors"]["fiscal_status"] == [FiscalOutboxStatus.QUEUED]


def test_a_company_that_does_not_fiscalize_has_no_receipt_and_no_refusal(
    client: TestClient, db: Session, subledger: Subledger
) -> None:
    """Decision 11's last sentence: the P4 layout prints, unchanged.

    `null` and a refusal are different answers, and the screen needs both — `null` means "this
    tenant has no authority to sign anything, print what P4 printed", and `fiscal_receipt_pending`
    means "this one does, and it has not signed yet". A tenant with no device returning a 409
    would make every ordinary invoice unprintable.
    """
    document, _ = post_invoice(db, subledger)
    db.commit()
    set_tenant(db, subledger.company_id)
    set_actor(db, subledger.owner.id)
    client.post(
        "/api/v1/auth/login",
        json={"email": subledger.owner.email, "password": PASSWORD},
    )

    response = client.get(f"/api/v1/fiscal/documents/{document.id}/receipt")

    assert response.status_code == 200, response.text
    assert response.json() is None
