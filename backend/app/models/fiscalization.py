"""Fiscalization tables (Master Plan §5 P7, ADR-10, rule 12).

**Fiscalization adds no ledger and no second truth.** The journal is what happened; the fiscal
receipt is the revenue authority's record of it; `fiscal_outbox` is the bridge, and it exists
so that nothing posted is ever lost and nothing unposted is ever sent. Every table here is
derived from, or a record of a conversation about, something the ledger already holds.

Named `fiscalization` rather than `fiscal` because `app/models/fiscal.py` has held the *fiscal
calendar* — years and accounting periods — since P2, and two meanings of "fiscal" in one
import line is how a module gets imported for the wrong reason.

**No country logic lives here.** Columns spell what Vinea means (`fiscal_class_code`,
`payment_method`), not what any one revenue authority's JSON calls it; the mapping from these
to `itemClsCd` and `pmtTyCd` is `app/fiscal/rwanda/`, and nothing outside that package knows
those names. The columns that *do* hold an authority's code — `fiscal_codes.code`,
`fiscal_items.item_cd`, `tax_codes.fiscal_tax_type` — hold it as data, which is the point: a
second country is rows and an adapter, not a schema change.
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import AuditedMixin, CompanyScopedMixin, pg_enum

MONEY = Numeric(20, 6)
RATE = Numeric(20, 10)
QUANTITY = Numeric(20, 6)

#: The module that owns the VAT settlement entry (decision 12). `gl` owns the revaluation.
TAX_MODULE = "tax"


# --- Device vocabulary --------------------------------------------------------------------


class FiscalProfile(enum.StrEnum):
    """Which route table a device speaks (decision 1).

    One payload vocabulary, two sets of paths: `vsdc` is the v1.0.5 contract a Java VSDC
    exposes locally, `osdc` the v1.0.1 one spoken directly to the EBM 2.1 API server by a
    system that cannot host the VSDC. Which one RRA certifies for a cloud vendor is a
    conversation with the authority, not a decision the code may take — so it is a column.
    """

    VSDC = "vsdc"
    OSDC = "osdc"


class FiscalEnvironment(enum.StrEnum):
    TEST = "test"
    PRODUCTION = "production"


class FiscalDeviceStatus(enum.StrEnum):
    #: Registered in Vinea, not yet initialized against the authority.
    PENDING = "pending"
    #: Initialized, holding keys, and fiscalizing. A company is *fiscalized* when it has one.
    ACTIVE = "active"
    #: Deliberately stopped. Its queue is not drained and its branch cannot invoice.
    SUSPENDED = "suspended"


class FiscalSyncKind(enum.StrEnum):
    """What a device keeps a `lastReqDt` watermark for.

    The authority's discipline is: send the last watermark you succeeded with, and store the
    new one **only after a success**. A failed sync that advanced the watermark would skip
    every row published between the two calls, silently and forever — which is why the
    watermark is written in the same place the success is recorded and nowhere else.
    """

    CODES = "codes"
    ITEM_CLASSES = "item_classes"
    BRANCHES = "branches"
    NOTICES = "notices"
    PURCHASES = "purchases"
    IMPORTS = "imports"
    STOCK_MOVES = "stock_moves"


class FiscalOutboxKind(enum.StrEnum):
    ITEM = "item"
    SALE = "sale"
    REFUND = "refund"
    PURCHASE = "purchase"
    PURCHASE_CONFIRM = "purchase_confirm"
    STOCK_IO = "stock_io"
    STOCK_MASTER = "stock_master"
    IMPORT_UPDATE = "import_update"


class FiscalOutboxStatus(enum.StrEnum):
    """Seven states, and the three that are *not* terminal are the whole design.

    `queued` retries on a backoff. `failed` is a refusal the authority will give again, so it
    does not. `unknown` is the dangerous one — the request left and no answer came back, so
    the authority may be holding the sale and a resend would be a duplicate that returns no
    receipt data. It is resolved by *asking the device what it has*, never by resending.
    `needs_receipt` is the state after that question came back "I have it": the sale is
    registered, Vinea has no receipt for it, and a person attaches one read off the portal.
    """

    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    UNKNOWN = "unknown"
    NEEDS_RECEIPT = "needs_receipt"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {FiscalOutboxStatus.SENT, FiscalOutboxStatus.CANCELLED}


class FiscalReceiptType(enum.StrEnum):
    """The CIS §5 receipt labels this phase issues. Copies are a *print*, not a receipt
    (decision 11), and training and proforma are out of scope."""

    NORMAL_SALE = "NS"
    NORMAL_REFUND = "NR"


class FiscalTaxType(enum.StrEnum):
    """The tax class a line is reported under. `tax_codes.fiscal_tax_type` maps Vinea's tax
    codes onto it, and a sale line whose code has none is refused rather than guessed at."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"


class FiscalItemTypeCode(enum.StrEnum):
    """What kind of thing an item is, in the authority's vocabulary (product type).

    Values are the wire codes because that is what they are — a mapping column, whose whole
    job is to carry a code the adapter sends. The *names* say what each one means.
    """

    RAW_MATERIAL = "1"
    FINISHED_PRODUCT = "2"
    SERVICE = "3"


class PaymentMethod(enum.StrEnum):
    """How a document is paid (decision 7). Country-neutral names; the adapter maps them onto
    the authority's numeric codes.

    Defaulted rather than asked for: an invoice with payment terms is `credit`, one without is
    `cash`. A default that is right nearly always beats a required field nobody knows the
    answer to at the moment they are keying.
    """

    CASH = "cash"
    CREDIT = "credit"
    CASH_CREDIT = "cash_credit"
    BANK_CHEQUE = "bank_cheque"
    CARD = "card"
    MOBILE_MONEY = "mobile_money"
    OTHER = "other"


class FiscalFeedDecision(enum.StrEnum):
    """What the operator did with a row of the authority's purchase feed."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class FiscalImportStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class VatReturnStatus(enum.StrEnum):
    POSTED = "posted"
    REVERSED = "reversed"


class FxRevaluationStatus(enum.StrEnum):
    POSTED = "posted"
    REVERSED = "reversed"


class FxRevaluationRole(enum.StrEnum):
    """Which open items a revaluation run covers. `both` is one run over AR and AP together —
    the common case at a month end, and one entry rather than two."""

    AR = "ar"
    AP = "ap"
    BOTH = "both"


fiscal_profile_type = pg_enum(FiscalProfile, "fiscal_profile")
fiscal_environment_type = pg_enum(FiscalEnvironment, "fiscal_environment")
fiscal_device_status_type = pg_enum(FiscalDeviceStatus, "fiscal_device_status")
fiscal_outbox_kind_type = pg_enum(FiscalOutboxKind, "fiscal_outbox_kind")
fiscal_outbox_status_type = pg_enum(FiscalOutboxStatus, "fiscal_outbox_status")
fiscal_receipt_type_type = pg_enum(FiscalReceiptType, "fiscal_receipt_type")
fiscal_tax_type_type = pg_enum(FiscalTaxType, "fiscal_tax_type")
fiscal_item_type_code_type = pg_enum(FiscalItemTypeCode, "fiscal_item_type_code")
payment_method_type = pg_enum(PaymentMethod, "payment_method")
fiscal_feed_decision_type = pg_enum(FiscalFeedDecision, "fiscal_feed_decision")
fiscal_import_status_type = pg_enum(FiscalImportStatus, "fiscal_import_status")
vat_return_status_type = pg_enum(VatReturnStatus, "vat_return_status")
fx_revaluation_status_type = pg_enum(FxRevaluationStatus, "fx_revaluation_status")
fx_revaluation_role_type = pg_enum(FxRevaluationRole, "fx_revaluation_role")


# --- Devices ------------------------------------------------------------------------------


class FiscalDevice(AuditedMixin, CompanyScopedMixin, Base):
    """One device per branch (decision 2).

    A company is **fiscalized** when it has at least one `active` device, and an AR invoice
    for a branch without one is refused rather than posted un-fiscalized: a sale that reached
    the ledger and never reached the authority is the failure mode the whole phase exists to
    make impossible, and it is cheaper to refuse the posting than to reconcile it later.

    `tin` is copied from the company at initialization and must equal `companies.tin`. It is
    stored rather than read through because it is what the authority keyed the device by —
    a company that edits its TIN afterwards must re-initialize, not silently start sending
    under a number the device was not registered with.

    **The three keys are secrets.** `cmc_key`, `intrl_key` and `sign_key` are encrypted at
    rest, never returned by any endpoint, never logged, and stripped from every stored payload
    and response. `tests/fiscal/test_key_redaction.py` is the proof.
    """

    __tablename__ = "fiscal_devices"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_fiscal_devices_company_id_id"),
        # One device per branch. Two devices on one branch would each hold their own receipt
        # counters for the same physical shop, and the counters are what the authority
        # reconciles against — there is no correct way to merge them.
        UniqueConstraint("company_id", "branch_id", name="uq_fiscal_devices_company_branch"),
        ForeignKeyConstraint(
            ["company_id", "branch_id"],
            ["branches.company_id", "branches.id"],
            name="fk_fiscal_devices_branch",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(bhf_id) = 2", name="bhf_id_is_two_characters"),
        CheckConstraint(
            "status <> 'active' OR (sdc_id IS NOT NULL AND tin IS NOT NULL)",
            name="active_device_is_initialized",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    branch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    profile: Mapped[FiscalProfile] = mapped_column(fiscal_profile_type, nullable=False)
    environment: Mapped[FiscalEnvironment] = mapped_column(
        fiscal_environment_type, nullable=False, default=FiscalEnvironment.TEST
    )
    base_url: Mapped[str] = mapped_column(String(300), nullable=False)
    tin: Mapped[str | None] = mapped_column(String(20))
    #: The authority's branch identifier. `00` is the head office.
    bhf_id: Mapped[str] = mapped_column(String(2), nullable=False, default="00")
    #: The serial the owner registered on the authority's portal. Not a secret — it is on the
    #: device — but it is what the authority matches the initialization call against.
    dvc_srl_no: Mapped[str] = mapped_column(String(100), nullable=False)
    mrc_no: Mapped[str | None] = mapped_column(String(30))
    sdc_id: Mapped[str | None] = mapped_column(String(30))
    dvc_id: Mapped[str | None] = mapped_column(String(30))
    #: Fernet ciphertext, never plaintext. See `app.fiscal.keys`.
    cmc_key: Mapped[str | None] = mapped_column(Text)
    intrl_key: Mapped[str | None] = mapped_column(Text)
    sign_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[FiscalDeviceStatus] = mapped_column(
        fiscal_device_status_type, nullable=False, default=FiscalDeviceStatus.PENDING
    )
    #: `{FiscalSyncKind: "yyyyMMddHHmmss"}` — one JSONB map rather than seven columns or a
    #: child table, because the kinds are a closed enum written and read as a unit by the sync
    #: that owns them, and a new kind should not be a migration. Written only on a success.
    watermarks: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    @property
    def is_active(self) -> bool:
        return self.status == FiscalDeviceStatus.ACTIVE


class FiscalCode(AuditedMixin, CompanyScopedMixin, Base):
    """The authority's code tables, synced per company.

    Rows, not enums, because the authority publishes them and revises them: a packaging unit
    added next year should arrive through `sync_codes`, not through a release. The enums in
    `app/fiscal/rwanda/codes.py` are the *seed* and the names the adapter reasons with; these
    rows are what a screen offers and what a refreshed table replaces.
    """

    __tablename__ = "fiscal_codes"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "code_class", "code", name="uq_fiscal_codes_company_class_code"
        ),
        Index("ix_fiscal_codes_company_class", "company_id", "code_class"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code_class: Mapped[str] = mapped_column(String(10), nullable=False)
    code_class_name: Mapped[str | None] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int | None] = mapped_column(SmallInteger)
    #: The authority's three free columns on a code row. Carried because some classes use
    #: them to qualify the code (a rate against a tax type, for instance) and dropping them
    #: would make the synced table less useful than the one it came from.
    user_defined_1: Mapped[str | None] = mapped_column(String(100))
    user_defined_2: Mapped[str | None] = mapped_column(String(100))
    user_defined_3: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FiscalItemClass(AuditedMixin, CompanyScopedMixin, Base):
    """The authority's item classification (its own table, and a large one — tens of thousands
    of rows — which is why it is synced by watermark and searched rather than listed)."""

    __tablename__ = "fiscal_item_classes"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "item_cls_cd", name="uq_fiscal_item_classes_company_code"
        ),
        Index("ix_fiscal_item_classes_company_name", "company_id", "item_cls_nm"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_cls_cd: Mapped[str] = mapped_column(String(20), nullable=False)
    item_cls_nm: Mapped[str] = mapped_column(String(300), nullable=False)
    item_cls_lvl: Mapped[int | None] = mapped_column(SmallInteger)
    #: The tax type the authority associates with the class. Advisory: the tax a line actually
    #: attracts comes from the Vinea tax code it was posted with, because that is what the
    #: ledger holds and the ledger is the truth.
    tax_ty_cd: Mapped[str | None] = mapped_column(String(2))
    mjr_tg_yn: Mapped[bool | None] = mapped_column(Boolean)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FiscalItem(AuditedMixin, CompanyScopedMixin, Base):
    """An item as the authority knows it — one row per Vinea item per company (decision 8).

    `item_cd` is built once and never rebuilt: it encodes origin, type, packaging and quantity
    unit, and the authority keys every sale line by it. Changing any of those on the Vinea item
    re-registers the *same* code with new attributes (the hash differs, an `item` outbox row is
    queued); it does not mint a second one, because a second code would orphan every receipt
    already issued against the first.
    """

    __tablename__ = "fiscal_items"
    __table_args__ = (
        UniqueConstraint("company_id", "item_id", name="uq_fiscal_items_company_item"),
        UniqueConstraint("company_id", "item_cd", name="uq_fiscal_items_company_item_cd"),
        ForeignKeyConstraint(
            ["company_id", "item_id"],
            ["items.company_id", "items.id"],
            name="fk_fiscal_items_item",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    item_cd: Mapped[str] = mapped_column(String(30), nullable=False)
    item_cls_cd: Mapped[str] = mapped_column(String(20), nullable=False)
    item_ty_cd: Mapped[FiscalItemTypeCode] = mapped_column(
        fiscal_item_type_code_type, nullable=False
    )
    orgn_nat_cd: Mapped[str] = mapped_column(String(2), nullable=False)
    pkg_unit_cd: Mapped[str] = mapped_column(String(5), nullable=False)
    qty_unit_cd: Mapped[str] = mapped_column(String(5), nullable=False)
    tax_ty_cd: Mapped[FiscalTaxType] = mapped_column(fiscal_tax_type_type, nullable=False)
    #: Default selling price, VAT-inclusive, in base currency — what the authority lists the
    #: item at. Not a price list: Vinea's price lists are P10, and this is one number.
    dft_prc: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    bcd: Mapped[str | None] = mapped_column(String(50))
    use_yn: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: SHA-256 of the registered fields. Re-registration is "the hash differs", so a change
    #: that does not reach the authority's copy costs no call.
    last_payload_hash: Mapped[str | None] = mapped_column(String(64))


# --- The outbox ---------------------------------------------------------------------------


class FiscalOutboxRow(AuditedMixin, CompanyScopedMixin, Base):
    """The durable queue (ADR-10, decision 4). One row per thing the authority must be told.

    **Written in the same transaction as the thing it reports.** `post_document()` inserts the
    `sale` row before it returns; the stock service inserts `stock_io` beside the moves. That
    is the whole guarantee: a document that reached the ledger has a row, a row that exists had
    its document committed, and no request handler ever calls the authority directly.

    **Per-device FIFO, one in flight.** `sequence_no` is the row id, so creation order is queue
    order, and a `failed`, `unknown` or `needs_receipt` row *blocks the device's queue behind
    it*. That is deliberate and it is not a bug to work around: the authority requires a stock
    movement to follow the sale that caused it, and the receipt counters are a sequence.
    Sending out of order is worse than waiting.

    `payload` is frozen at enqueue, so a retry resends the same bytes — a retry that rebuilt
    the payload from today's masters would send something the original posting never said.
    """

    __tablename__ = "fiscal_outbox"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_fiscal_outbox_company_id_id"),
        ForeignKeyConstraint(
            ["company_id", "device_id"],
            ["fiscal_devices.company_id", "fiscal_devices.id"],
            name="fk_fiscal_outbox_device",
            ondelete="RESTRICT",
        ),
        # The drainer's query: the device's oldest non-terminal row.
        Index(
            "ix_fiscal_outbox_device_pending",
            "company_id",
            "device_id",
            "sequence_no",
            postgresql_where=text("status NOT IN ('sent', 'cancelled')"),
        ),
        Index(
            "ix_fiscal_outbox_source",
            "company_id",
            "source_doc_type",
            "source_doc_id",
            postgresql_where=text("source_doc_id IS NOT NULL"),
        ),
        CheckConstraint("attempts >= 0", name="attempts_not_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    device_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[FiscalOutboxKind] = mapped_column(fiscal_outbox_kind_type, nullable=False)
    #: What produced this row, named the way every other `source_doc_type` in the schema is —
    #: `partner_document`, `item`, and at step 3 the stock postings. Fifty characters, as
    #: `journal_lines` and `stock_moves` carry (0023).
    source_doc_type: Mapped[str | None] = mapped_column(String(50))
    source_doc_id: Mapped[int | None] = mapped_column(BigInteger)
    #: FIFO position within the device. Set from the row id at enqueue, so it is the order the
    #: postings committed in and nothing can renumber it.
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The authority's invoice number for this row's kind, claimed from `FIS`/`FIP` in the
    #: posting transaction so a queue cannot reorder them.
    invc_no: Mapped[int | None] = mapped_column(Integer)
    #: The stock-movement number, from `FSAR`.
    sar_no: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[FiscalOutboxStatus] = mapped_column(
        fiscal_outbox_status_type, nullable=False, default=FiscalOutboxStatus.QUEUED
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_result_cd: Mapped[str | None] = mapped_column(String(10))
    last_error: Mapped[str | None] = mapped_column(Text)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Who resolved an `unknown` or `needs_receipt` row by hand, and what they said. An
    #: attached receipt is a human assertion about what the authority holds; it is audited
    #: like one.
    resolved_by: Mapped[int | None] = mapped_column(BigInteger)
    resolution_note: Mapped[str | None] = mapped_column(Text)


class FiscalReceipt(AuditedMixin, CompanyScopedMixin, Base):
    """What the authority signed, one per fiscalized document (decision 5). Immutable — a
    database trigger, not a convention, exactly as posted journal rows are.

    The counters are **proven, not trusted**: `assert_fiscal_invariants` asserts that per
    device `rcpt_no` strictly increases within a receipt type and `tot_rcpt_no` across types.
    A receipt is a legal document with a number on it, and a number that went backwards is a
    thing somebody has to explain to a revenue authority.
    """

    __tablename__ = "fiscal_receipts"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_fiscal_receipts_company_id_id"),
        # One receipt per document **per type** (0023). A document holds its `NS`, and — when
        # a signed sale is reversed — the `NR` that reversed it: RRA cannot un-sign a sale, so
        # the reversal is a second receipt about the same invoice. It is stored rather than
        # left in the queue row, because the Z report counts receipts and a refund RRA signed
        # that the Z could not see would make the close disagree with the authority.
        UniqueConstraint(
            "company_id",
            "document_id",
            "receipt_type",
            name="uq_fiscal_receipts_company_document_type",
        ),
        UniqueConstraint("company_id", "outbox_id", name="uq_fiscal_receipts_company_outbox"),
        ForeignKeyConstraint(
            ["company_id", "document_id"],
            ["partner_documents.company_id", "partner_documents.id"],
            name="fk_fiscal_receipts_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "device_id"],
            ["fiscal_devices.company_id", "fiscal_devices.id"],
            name="fk_fiscal_receipts_device",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "outbox_id"],
            ["fiscal_outbox.company_id", "fiscal_outbox.id"],
            name="fk_fiscal_receipts_outbox",
            ondelete="RESTRICT",
        ),
        Index("ix_fiscal_receipts_device_counter", "company_id", "device_id", "tot_rcpt_no"),
        CheckConstraint("copy_count >= 0", name="copy_count_not_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    device_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    outbox_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    receipt_type: Mapped[FiscalReceiptType] = mapped_column(
        fiscal_receipt_type_type, nullable=False
    )
    invc_no: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The original's `invc_no` on a refund; NULL on a sale.
    org_invc_no: Mapped[int | None] = mapped_column(Integer)
    #: The counter within the receipt type, and the counter across all types — the `A/B RT`
    #: pair the receipt prints (CIS §7.25).
    rcpt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    tot_rcpt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    intrl_data: Mapped[str] = mapped_column(String(100), nullable=False)
    rcpt_sign: Mapped[str] = mapped_column(String(100), nullable=False)
    sdc_id: Mapped[str] = mapped_column(String(30), nullable=False)
    mrc_no: Mapped[str | None] = mapped_column(String(30))
    #: The *device's* clock, as it signed. Deliberately not the posting time: the receipt
    #: prints both, and they are two different facts.
    sdc_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    qr_payload: Mapped[str] = mapped_column(Text, nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: How many times it has been reprinted. The only mutable column on the row, and it is
    #: excluded from the immutability trigger by name so that a reprint stays auditable
    #: without making the receipt editable.
    copy_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FiscalDailyReport(AuditedMixin, CompanyScopedMixin, Base):
    """A stored Z close per device (decision 11). X is the same computation from the last Z to
    now and is **never stored** — an X is a question, a Z is an act.

    Immutable once written, and `figures` holds the §19.1 content computed from the receipts
    the range covers. **Pending queue rows are not receipts**: a Z counts what the authority
    signed, and records how many rows were still queued when it was taken, so a Z that closed
    over an unsent sale says so on its face.
    """

    __tablename__ = "fiscal_daily_reports"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_fiscal_daily_reports_company_id_id"),
        UniqueConstraint(
            "company_id", "device_id", "report_no", name="uq_fiscal_daily_reports_device_number"
        ),
        ForeignKeyConstraint(
            ["company_id", "device_id"],
            ["fiscal_devices.company_id", "fiscal_devices.id"],
            name="fk_fiscal_daily_reports_device",
            ondelete="RESTRICT",
        ),
        CheckConstraint("to_at > from_at", name="range_is_forward"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    device_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: From the `FZR` run, branch-scoped: a device's Z numbers are its own.
    report_no: Mapped[int] = mapped_column(Integer, nullable=False)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    from_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    to_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    figures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: What was still in the queue when the day closed.
    queued_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    closed_by: Mapped[int | None] = mapped_column(BigInteger)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FiscalPurchaseFeedRow(AuditedMixin, CompanyScopedMixin, Base):
    """A purchase the authority says somebody registered against this taxpayer (decision 9).

    It is not a document and it posts nothing: it is the other side of somebody else's sale,
    and the operator's job is to say whether it happened. Accepting it queues a confirmation;
    rejecting it queues a refusal. Linking a posted AP document is optional and is how the
    same supplier invoice is kept from being registered twice.
    """

    __tablename__ = "fiscal_purchase_feed"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_fiscal_purchase_feed_company_id_id"),
        # The authority's own identity for the row. A feed re-fetched over an overlapping
        # watermark must update rather than duplicate.
        UniqueConstraint(
            "company_id",
            "device_id",
            "spplr_tin",
            "spplr_invc_no",
            name="uq_fiscal_purchase_feed_supplier_invoice",
        ),
        ForeignKeyConstraint(
            ["company_id", "device_id"],
            ["fiscal_devices.company_id", "fiscal_devices.id"],
            name="fk_fiscal_purchase_feed_device",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "ap_document_id"],
            ["partner_documents.company_id", "partner_documents.id"],
            name="fk_fiscal_purchase_feed_ap_document",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    device_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spplr_tin: Mapped[str] = mapped_column(String(20), nullable=False)
    spplr_nm: Mapped[str | None] = mapped_column(String(200))
    spplr_bhf_id: Mapped[str | None] = mapped_column(String(2))
    spplr_invc_no: Mapped[int] = mapped_column(Integer, nullable=False)
    sales_dt: Mapped[date | None] = mapped_column(Date)
    #: The header buckets and the item list exactly as fetched. Kept whole because the
    #: confirmation must echo the authority's own figures, not a re-derivation of them.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    total_taxable_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decision: Mapped[FiscalFeedDecision] = mapped_column(
        fiscal_feed_decision_type, nullable=False, default=FiscalFeedDecision.PENDING
    )
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ap_document_id: Mapped[int | None] = mapped_column(BigInteger)


class FiscalImportDeclaration(AuditedMixin, CompanyScopedMixin, Base):
    """A customs declaration line the authority is holding against this taxpayer (decision 9).

    Approving one is a **compliance acknowledgment**: it moves no stock and posts nothing —
    the goods reached the ledger through a goods receipt, and saying so twice would double
    them. What approval does is tell the authority which Vinea item the declared line became.
    """

    __tablename__ = "fiscal_import_declarations"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_fiscal_import_declarations_company_id_id"),
        UniqueConstraint(
            "company_id",
            "device_id",
            "task_cd",
            "dcl_no",
            "item_seq",
            name="uq_fiscal_import_declarations_line",
        ),
        ForeignKeyConstraint(
            ["company_id", "device_id"],
            ["fiscal_devices.company_id", "fiscal_devices.id"],
            name="fk_fiscal_import_declarations_device",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "item_id"],
            ["items.company_id", "items.id"],
            name="fk_fiscal_import_declarations_item",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    device_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_cd: Mapped[str] = mapped_column(String(30), nullable=False)
    dcl_no: Mapped[str] = mapped_column(String(50), nullable=False)
    dcl_de: Mapped[date | None] = mapped_column(Date)
    item_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    hs_cd: Mapped[str | None] = mapped_column(String(30))
    item_nm: Mapped[str | None] = mapped_column(String(300))
    orgn_nat_cd: Mapped[str | None] = mapped_column(String(2))
    pkg: Mapped[Decimal | None] = mapped_column(QUANTITY)
    pkg_unit_cd: Mapped[str | None] = mapped_column(String(5))
    qty: Mapped[Decimal | None] = mapped_column(QUANTITY)
    qty_unit_cd: Mapped[str | None] = mapped_column(String(5))
    spplr_nm: Mapped[str | None] = mapped_column(String(200))
    agnt_nm: Mapped[str | None] = mapped_column(String(200))
    invc_fcur_amt: Mapped[Decimal | None] = mapped_column(MONEY)
    invc_fcur_cd: Mapped[str | None] = mapped_column(String(5))
    invc_fcur_exc_rt: Mapped[Decimal | None] = mapped_column(RATE)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[FiscalImportStatus] = mapped_column(
        fiscal_import_status_type, nullable=False, default=FiscalImportStatus.PENDING
    )
    item_id: Mapped[int | None] = mapped_column(BigInteger)
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- VAT returns --------------------------------------------------------------------------


class VatReturn(AuditedMixin, CompanyScopedMixin, Base):
    """A filed return (decision 12). Immutable once filed — trigger, not convention.

    The return is a **query over `journal_lines`** and nothing else, so it ties to the VAT
    accounts by construction. What makes a filed return stay filed is `high_water_entry_id`:
    journal entries are append-only with monotonic ids, so "the lines of this return" is
    "entries dated in the range whose id is at or below the mark", and an entry posted into a
    filed month afterwards lands in the *next* return under late entries. The filed figures
    never change because nothing about them is recomputed.
    """

    __tablename__ = "vat_returns"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_vat_returns_company_id_id"),
        UniqueConstraint("company_id", "number", name="uq_vat_returns_company_number"),
        ForeignKeyConstraint(
            ["company_id", "journal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_vat_returns_journal_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reversal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_vat_returns_reversal_entry",
            ondelete="RESTRICT",
        ),
        CheckConstraint("period_to >= period_from", name="range_is_forward"),
        Index("ix_vat_returns_company_range", "company_id", "period_from", "period_to"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    #: The whole return as filed. A JSONB snapshot rather than a table of lines because it is
    #: evidence, not a model: what matters is that it is byte-for-byte what was submitted.
    figures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    high_water_entry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    journal_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    reversal_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    output_vat: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    input_vat: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    #: Positive is payable to the authority, negative is a credit carried forward. One number
    #: on one account, which is the balance an accountant recognises.
    net_payable: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[VatReturnStatus] = mapped_column(
        vat_return_status_type, nullable=False, default=VatReturnStatus.POSTED
    )
    filed_by: Mapped[int | None] = mapped_column(BigInteger)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    idempotency_hash: Mapped[str | None] = mapped_column(String(64))


# --- Unrealized FX ------------------------------------------------------------------------


class FxRevaluation(AuditedMixin, CompanyScopedMixin, Base):
    """A revaluation run over open foreign-currency partner documents (decision 13).

    **Never the control accounts.** `1200` and `2100` are subledger-only and their balance is
    the sum of open items at booking rates — P4's invariant, which a revaluation posting into
    them would break. The other side goes to dedicated revaluation accounts instead, and the
    balance sheet reads the two together.

    The run posts its entry **and its mirror** in one transaction: the entry at the
    revaluation date, the reversal the following day. The balance sheet at the date carries
    the revaluation and the next period does not, and realized FX at allocation stays P4's.
    """

    __tablename__ = "fx_revaluations"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_fx_revaluations_company_id_id"),
        UniqueConstraint("company_id", "number", name="uq_fx_revaluations_company_number"),
        ForeignKeyConstraint(
            ["company_id", "journal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_fx_revaluations_journal_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "mirror_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_fx_revaluations_mirror_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reversal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_fx_revaluations_reversal_entry",
            ondelete="RESTRICT",
        ),
        Index("ix_fx_revaluations_company_date", "company_id", "revaluation_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    revaluation_date: Mapped[date] = mapped_column(Date, nullable=False)
    role: Mapped[FxRevaluationRole] = mapped_column(fx_revaluation_role_type, nullable=False)
    journal_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    #: The next-day reversal posted in the same transaction as the entry above.
    mirror_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    #: Set when the whole run is taken back out through `module_reversal("gl")`.
    reversal_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[FxRevaluationStatus] = mapped_column(
        fx_revaluation_status_type, nullable=False, default=FxRevaluationStatus.POSTED
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    idempotency_hash: Mapped[str | None] = mapped_column(String(64))


class FxRevaluationLine(AuditedMixin, CompanyScopedMixin, Base):
    """One open document in a run: what it was booked at, what it is worth at the date, and
    the difference. The arithmetic is kept rather than recomputed because the rate it used is
    the rate that stood on the day, and a later correction to `exchange_rates` must not
    silently restate a posted revaluation."""

    __tablename__ = "fx_revaluation_lines"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_fx_revaluation_lines_company_id_id"),
        ForeignKeyConstraint(
            ["company_id", "revaluation_id"],
            ["fx_revaluations.company_id", "fx_revaluations.id"],
            name="fk_fx_revaluation_lines_revaluation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "document_id"],
            ["partner_documents.company_id", "partner_documents.id"],
            name="fk_fx_revaluation_lines_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "currency_id"],
            ["currencies.company_id", "currencies.id"],
            name="fk_fx_revaluation_lines_currency",
            ondelete="RESTRICT",
        ),
        Index("ix_fx_revaluation_lines_run", "company_id", "revaluation_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    revaluation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    document_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    open_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    booking_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    carrying_base: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    rate_at_date: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    revalued_base: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    difference: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
