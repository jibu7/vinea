"""General Ledger API (P2): COA, exchange rates, manual journal & cashbook entries, reversal,
enquiries, periods and year-end. Routers hold no business logic — everything posts through
`app.kernel`."""

from datetime import date

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from app.api import idempotency
from app.api.deps import AuthContext, get_tenant_context
from app.banking import accounts as bank_accounts
from app.core import permissions
from app.core.errors import NotFoundError, PermissionDeniedError
from app.db import get_db
from app.kernel import accounts as accounts_service
from app.kernel import balances, enquiries, masters, posting, year_end
from app.kernel import periods as periods_service
from app.kernel.errors import LedgerStateError
from app.kernel.events import (
    CashbookEntry,
    CashbookKind,
    CashbookLineSpec,
    LineSpec,
    ManualJournal,
)
from app.models.audit import AuditLog
from app.models.banking import BankAccount as BankAccountModel
from app.models.currency import Currency, ExchangeRate
from app.models.fiscal import AccountingPeriod, FiscalYear, PeriodStatus
from app.models.fiscalization import FxRevaluation, FxRevaluationRole, VatReturn
from app.models.gl import AccountClass, GLSettings
from app.models.inventory import INVENTORY_MODULE, InventoryDocument
from app.models.journal import JournalEntry
from app.models.order_entry import LandedCostDocument
from app.models.partner import Partner, PartnerRole
from app.models.subledger import PartnerDocument
from app.order_entry import sources as order_sources
from app.schemas.common import Page
from app.schemas.gl import (
    AccountAuditRead,
    AccountTransactionRead,
    AccountTransactionsRead,
    BranchCreate,
    BranchRead,
    BranchUpdate,
    CashbookEntryCreate,
    CurrencyCreate,
    CurrencyRead,
    CurrencyUpdate,
    ExchangeRateCreate,
    ExchangeRateRead,
    FiscalYearCreate,
    FiscalYearRead,
    FxRevaluationCreate,
    FxRevaluationDetail,
    FxRevaluationLineRead,
    FxRevaluationPreview,
    FxRevaluationRead,
    FxRevaluationReverse,
    GLAccountCreate,
    GLAccountRead,
    GLAccountUpdate,
    GLSettingsRead,
    GLSettingsUpdate,
    JournalEntryCreate,
    JournalEntryRead,
    JournalEntrySummary,
    PeriodBalanceDriftRead,
    PeriodRead,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
    ReasonBody,
    ReversalCreate,
    TaxCodeCreate,
    TaxCodeRead,
    TaxCodeUpdate,
    TransactionTypeCreate,
    TransactionTypeRead,
    TransactionTypeUpdate,
    TrialBalanceRead,
    TrialBalanceRowRead,
)
from app.subledger import revaluation as revaluation_service

router = APIRouter(prefix="/gl", tags=["general-ledger"])

IdempotencyKey = idempotency.IdempotencyKey
_fingerprint = idempotency.fingerprint


def _entry_read(db: Session, entry: JournalEntry) -> JournalEntryRead:
    Original = aliased(JournalEntry)
    Reversing = aliased(JournalEntry)
    row = db.execute(
        select(
            JournalEntry,
            Original.number.label("reverses_entry_number"),
            Reversing.id.label("reversed_by_entry_id"),
            Reversing.number.label("reversed_by_number"),
        )
        .outerjoin(
            Original,
            and_(
                Original.company_id == JournalEntry.company_id,
                Original.id == JournalEntry.reverses_entry_id,
            ),
        )
        .outerjoin(
            Reversing,
            and_(
                Reversing.company_id == JournalEntry.company_id,
                Reversing.reverses_entry_id == JournalEntry.id,
            ),
        )
        .options(selectinload(JournalEntry.lines))
        .where(JournalEntry.id == entry.id)
    ).first()
    if row is None:
        raise NotFoundError("Journal entry not found")
    loaded, rev_num, rvd_by_id, rvd_by_num = row
    data = JournalEntryRead.model_validate(loaded)
    data.reverses_entry_number = rev_num
    data.reversed_by_entry_id = rvd_by_id
    data.reversed_by_number = rvd_by_num
    _resolve_landed_cost_pair(db, loaded, data)
    _resolve_p7_document_pair(db, loaded, data)
    (
        data.module_document_id,
        data.module_document_number,
        data.module_document_target,
    ) = _module_document(db, loaded)
    return data


def _resolve_landed_cost_pair(
    db: Session, entry: JournalEntry, data: JournalEntryRead
) -> None:
    """Fill the reversal pair for an `LCA-` entry, which the kernel's own column cannot.

    **Why this one doc type needs its own resolution.** Both halves of the join above hang off
    `journal_entries.reverses_entry_id`, which the kernel writes only when a reversal *mirrors*
    the entry it reverses. A landed cost does not reverse that way: the value it posted leaves
    through cost of sales as the goods are sold, so its reversal takes each share back from
    wherever it now sits, and is a split rather than a mirror (see
    `app/order_entry/landed_cost.py::reverse_landed_cost`). The kernel therefore leaves the
    column null, deliberately, and the link lives on the document —
    `landed_cost_documents.journal_entry_id` and `.reversal_entry_id`. Clause 9 of
    `assert_order_invariants` proves that link from both ends, which is what makes it safe to
    read here.

    Without this the entry page showed a reversal saying nothing about what it reversed, and an
    allocation saying nothing about having been reversed — the P4 review's failure mode exactly
    (rule 13): the row renders, the link is missing, and no test notices. Step 8 draws the
    resulting pair; the endpoint returns it now so the screen has something to draw.

    One document, two entries, so one query serves both directions: the document that names
    this entry as its posting has the reversal, and the document that names it as its reversal
    has the original.
    """
    if entry.source_doc_type != order_sources.LANDED_COST_DOCUMENT:
        return
    document = db.scalar(
        select(LandedCostDocument).where(
            LandedCostDocument.company_id == entry.company_id,
            or_(
                LandedCostDocument.journal_entry_id == entry.id,
                LandedCostDocument.reversal_entry_id == entry.id,
            ),
        )
    )
    if document is None:
        return
    if document.journal_entry_id == entry.id and document.reversal_entry_id is not None:
        # This is the allocation; the document names what took it back out.
        data.reversed_by_entry_id = document.reversal_entry_id
        data.reversed_by_number = _entry_number(db, entry.company_id, document.reversal_entry_id)
    elif document.reversal_entry_id == entry.id and document.journal_entry_id is not None:
        # This is the reversal; the document names what it reversed.
        data.reverses_entry_id = document.journal_entry_id
        data.reverses_entry_number = _entry_number(
            db, entry.company_id, document.journal_entry_id
        )


def _entry_number(db: Session, company_id: int, entry_id: int) -> str | None:
    return db.scalar(
        select(JournalEntry.number).where(
            JournalEntry.company_id == company_id, JournalEntry.id == entry_id
        )
    )


# --- The two P7 documents that post from outside a subledger --------------------------------
#
# A filed VAT return and an FX revaluation run both post through the kernel and neither has a
# row in `MODULE_DOCUMENT_TABLES`: `tax` has no document table at all, and `gl` is every manual
# journal ever posted, so keying on the module would buy a wasted query on every ordinary
# journal entry anybody opens. They are keyed on the **doc type** instead, which is exactly the
# two families of entry that can belong to one.
#
# Each table names the columns that point at an entry. `mirror_entry_id` is FX's alone: a
# revaluation posts its entry *and* the next-day mirror in one transaction (decision 13), and
# the mirror is an `FXR-` entry a person can open like any other.

#: `doc_type` → (table, the columns naming an entry, the routing key).
_P7_DOCUMENTS: dict[str, tuple[type, tuple[str, ...], str]] = {
    "VAT": (VatReturn, ("journal_entry_id", "reversal_entry_id"), order_sources.VAT_RETURN),
    "FXR": (
        FxRevaluation,
        ("journal_entry_id", "mirror_entry_id", "reversal_entry_id"),
        order_sources.FX_REVALUATION,
    ),
}


def _p7_document(db: Session, entry: JournalEntry):  # noqa: ANN202 - the row or None
    """The return or run an entry belongs to, or `None`.

    **Two hops, and the second is what makes it complete.** Four of the six entries these two
    documents can produce are named by a column on the document itself. The other two are not:
    reversing an FX run posts a counter-entry *and a mirror of that counter*, and only the
    counter is stored (`fx_revaluations.reversal_entry_id`). That mirror is an `FXR-` numbered
    entry with no source link and no column pointing at it — so it is resolved through the one
    thing it does carry, `reverses_entry_id`, on the rule that a mirror belongs wherever its
    original belongs.
    """
    table, columns, _target = _P7_DOCUMENTS[entry.doc_type]
    candidates = [entry.id]
    if entry.reverses_entry_id is not None:
        candidates.append(entry.reverses_entry_id)
    return db.scalar(
        select(table).where(
            table.company_id == entry.company_id,
            or_(*(getattr(table, column).in_(candidates) for column in columns)),
        )
    )


def _resolve_p7_document_pair(
    db: Session, entry: JournalEntry, data: JournalEntryRead
) -> None:
    """Fill the reversal pair for a `VATR-` or `FXR-` entry that the kernel's column cannot.

    Same hole as `_resolve_landed_cost_pair`, one phase later and only on one side of it.

    A **VAT return** reverses as a true mirror (`ReversalRequested` under
    `module_reversal("tax")`), so `journal_entries.reverses_entry_id` is written by the kernel
    and both directions already stand. Nothing here touches it, and
    `test_a_filed_return_pairs_without_the_document` is what keeps that true rather than
    assumed.

    An **FX revaluation** does not. Reversing a run posts a counter-entry of *negated lines*
    rather than a mirror — see `app/subledger/revaluation.py::reverse_revaluation` — because
    the run's own next-day mirror already occupies the `reverses_entry_id` slot on the entry it
    reverses, and one entry cannot be mirrored twice (`uq_journal_entries_reverses_entry_id`).
    So the counter-entry is a reversal the ledger renders with nothing to say about what it
    reversed, which is the P4 failure mode rule 13 is written against: the row is there, the
    link is not, and nothing fails.

    **Filled only where the column is null**, which is the whole difference from the landed-cost
    resolver and is not a detail. The run's entry already has a `reversed_by` — its mirror, the
    next-day frozen-base reversal — and that is the honest answer to "what took this back out
    of the balance sheet". Overwriting it with the counter-entry would replace a true statement
    with a different true statement and lose the first.
    """
    if entry.doc_type not in _P7_DOCUMENTS:
        return
    document = _p7_document(db, entry)
    if document is None:
        return
    if data.reverses_entry_id is None and document.reversal_entry_id == entry.id:
        if document.journal_entry_id is not None:
            data.reverses_entry_id = document.journal_entry_id
            data.reverses_entry_number = _entry_number(
                db, entry.company_id, document.journal_entry_id
            )
    elif data.reversed_by_entry_id is None and document.journal_entry_id == entry.id:
        if document.reversal_entry_id is not None:
            data.reversed_by_entry_id = document.reversal_entry_id
            data.reversed_by_number = _entry_number(
                db, entry.company_id, document.reversal_entry_id
            )


#: Where each module keeps the documents it posts, keyed by the module that posted the entry.
#: Both tables have carried `journal_entry_id` since they were created, which is why this
#: direction answers for every entry ever posted — including the ones from before
#: `journal_entries.source_doc_id` was written at all.
MODULE_DOCUMENT_TABLES: dict[str, type] = {
    "inv": InventoryDocument,
    "ar": PartnerDocument,
    "ap": PartnerDocument,
}

#: The routing key for each module table. A partner document splits by role, which
#: `_module_document` settles from the row it found rather than from the entry's module — the
#: same rule `sources.resolve` applies, and for the same reason: which subledger a document
#: belongs to is a property of the document.
_MODULE_TABLE_TARGET = {"inv": order_sources.INVENTORY_DOCUMENT}


def _module_document(
    db: Session, entry: JournalEntry
) -> tuple[int | None, str | None, str | None]:
    """The document a module-owned entry belongs to, and what kind of page opens it.

    **Two resolutions, in this order, and the order is the point.**

    The module's own table is asked first. `journal_entries.source_doc_id` was only populated
    from P5 step 9 and cannot be back-filled — a posted entry is immutable in the database, and
    rewriting one would cost the guarantee that makes the ledger worth trusting (rule 10) — so
    an entry posted before then has a null source link and a perfectly findable document. Going
    this way round is what gives every entry, old or new, the link the screen needs, and
    `test_an_entry_resolves_its_document_even_with_no_source_link` pins it.

    What that cannot answer is **P6**, and this is why the second resolution exists. A goods
    receipt, a landed cost, an inventory adjustment and the companion stock entry of a partner
    document are all posted by the `inv` module and live on four different screens. Three of
    them are not `inventory_documents` rows at all, so the table above returns nothing for
    them and the entry page was left offering a disabled Reverse and no way onward — the
    P4 failure mode rule 13 exists for. `sources.resolve` knows all four kinds, and every P6
    entry carries the source link it needs, because every one of them was posted after step 9.

    The order between the two is proven by
    `tests/order_entry/test_entry_drill.py::test_the_module_table_is_asked_before_the_source_link`,
    which builds the one state where they disagree — an entry with a module-table row *and* a
    source link naming something else — and swaps the answer when the blocks are swapped.
    """
    table = MODULE_DOCUMENT_TABLES.get(entry.module)
    if table is PartnerDocument:
        found = db.execute(
            select(PartnerDocument.id, PartnerDocument.number, PartnerDocument.role).where(
                PartnerDocument.company_id == entry.company_id,
                PartnerDocument.journal_entry_id == entry.id,
            )
        ).first()
        if found is not None:
            target = "ar_document" if found[2] == PartnerRole.AR else "ap_document"
            return found[0], found[1], target
    elif table is not None:
        found = db.execute(
            select(table.id, table.number).where(
                table.company_id == entry.company_id, table.journal_entry_id == entry.id
            )
        ).first()
        if found is not None:
            return found[0], found[1], _MODULE_TABLE_TARGET.get(entry.module)

    if entry.source_doc_type is not None and entry.source_doc_id is not None:
        ref = (entry.source_doc_type, int(entry.source_doc_id))
        resolved = order_sources.resolve(db, entry.company_id, [ref]).get(ref)
        if resolved is not None:
            return resolved.source_doc_id, resolved.number, resolved.target

    # P7's two, last, and the order matters here too. A settlement entry and a run entry both
    # carry a source link and would be answered above; the entries that reach this line are the
    # **mirrors and the counter-entry**, posted as `ReversalRequested` or with no source of
    # their own, which have no source link to follow and a perfectly findable document.
    # Without it, half of every `VATR-`/`FXR-` pair opened from the GL was a page that named
    # its own reversal and could not say what document either of them belonged to.
    if entry.doc_type in _P7_DOCUMENTS:
        document = _p7_document(db, entry)
        if document is not None:
            return document.id, document.number, _P7_DOCUMENTS[entry.doc_type][2]
    return None, None, None


def _posted_response(
    db: Session, response: Response, entry: JournalEntry, *, replayed: bool
) -> JournalEntryRead:
    db.commit()
    response.status_code = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED
    return _entry_read(db, entry)


# --- Chart of accounts -------------------------------------------------------------------


@router.get("/accounts")
def list_accounts(
    include_inactive: bool = False,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[GLAccountRead]:
    rows = accounts_service.list_accounts(db, auth.company_id, include_inactive=include_inactive)
    return [GLAccountRead.model_validate(row) for row in rows]


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_account(
    payload: GLAccountCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.GL_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> GLAccountRead:
    account = accounts_service.create_account(
        db,
        auth.company_id,
        accounts_service.AccountInput(
            code=payload.code,
            name=payload.name,
            class_=payload.class_,
            parent_id=payload.parent_id,
            is_postable=payload.is_postable,
            control_type=payload.control_type,
        ),
        actor=auth.user,
        request=request,
    )
    # P8 decision 2: a `bank` / `cash` control account gets its master row in the **same
    # transaction** as the account. The hook lives here rather than in
    # `app/kernel/accounts.py` because the kernel may not import the banking package; a path
    # that forgets it is caught by `assert_bank_invariants` clause 6, not by review.
    bank_accounts.ensure_row(db, account)
    db.commit()
    return GLAccountRead.model_validate(account)


@router.get("/accounts/{account_id}")
def get_account(
    account_id: int,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> GLAccountRead:
    return GLAccountRead.model_validate(
        accounts_service.get_account(db, auth.company_id, account_id)
    )


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: int,
    payload: GLAccountUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.GL_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> GLAccountRead:
    account = accounts_service.get_account(db, auth.company_id, account_id)
    parent: int | None | object = ...
    if payload.clear_parent:
        parent = None
    elif payload.parent_id is not None:
        parent = payload.parent_id
    accounts_service.update_account(
        db,
        account,
        code=payload.code,
        name=payload.name,
        parent_id=parent,
        is_postable=payload.is_postable,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return GLAccountRead.model_validate(account)


@router.get("/accounts/{account_id}/history")
def get_account_history(
    account_id: int,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[AccountAuditRead]:
    account = accounts_service.get_account(db, auth.company_id, account_id)
    statement = (
        select(AuditLog)
        .where(
            AuditLog.company_id == auth.company_id,
            AuditLog.entity == "gl_accounts",
            AuditLog.entity_id == str(account.id),
        )
        # `at` is the transaction clock, so two rows written by one request tie on it;
        # the id breaks the tie in write order.
        .order_by(AuditLog.at.desc(), AuditLog.id.desc())
    )
    rows = db.scalars(statement).all()
    return [AccountAuditRead.model_validate(row) for row in rows]


@router.get("/settings")
def get_settings(
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> GLSettingsRead:
    return GLSettingsRead.model_validate(posting.gl_settings_for(db, auth.company_id))


@router.put("/settings")
def update_settings(
    payload: GLSettingsUpdate,
    auth: AuthContext = permissions.require(permissions.GL_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> GLSettingsRead:
    settings: GLSettings = posting.gl_settings_for(db, auth.company_id)
    for field, label, classes in _SETTING_ACCOUNT_RULES:
        value = getattr(payload, field)
        if value is None:
            continue
        setattr(settings, field, _postable_account(db, auth.company_id, value, label, classes))
    if payload.clear_fiscal_default_purchase_class_code:
        settings.fiscal_default_purchase_class_code = None
    elif payload.fiscal_default_purchase_class_code is not None:
        settings.fiscal_default_purchase_class_code = (
            payload.fiscal_default_purchase_class_code.strip() or None
        )
    db.commit()
    return GLSettingsRead.model_validate(settings)


#: (field, the label a refusal names it by, the account classes it may hold).
#:
#: The class restriction is P7's, and it is the same rule the pickers on the Defaults screen
#: filter by — a settings key that could hold any postable account is a key an operator can
#: point at the wrong side of the balance sheet, and neither the VAT filing nor the
#: revaluation would notice until it had posted. The revaluation contras are deliberately
#: split: `1290` carries what the AR control account may not (decision 13), so it is an
#: **asset**, and `2190` is its liability twin. None of them may be a control account, which
#: `_postable_account` has always refused.
_SETTING_ACCOUNT_RULES: tuple[tuple[str, str, tuple[AccountClass, ...] | None], ...] = (
    ("retained_earnings_account_id", "retained earnings", None),
    ("rounding_difference_account_id", "rounding difference", None),
    ("vat_settlement_account_id", "VAT settlement", (AccountClass.LIABILITY,)),
    ("ar_revaluation_account_id", "AR revaluation", (AccountClass.ASSET,)),
    ("ap_revaluation_account_id", "AP revaluation", (AccountClass.LIABILITY,)),
    (
        "unrealized_fx_gain_account_id",
        "unrealized FX gain",
        (AccountClass.INCOME, AccountClass.EXPENSE),
    ),
    (
        "unrealized_fx_loss_account_id",
        "unrealized FX loss",
        (AccountClass.INCOME, AccountClass.EXPENSE),
    ),
    # P8. `1130` is an **asset** and is deliberately not the bank account itself (decision 8):
    # a base-only line on a bank account is a ledger line the statement can never show, and
    # the reconciliation would carry it forever. The other two are the drawer's defaults, and
    # a fee is an expense while interest is income — but either may legitimately be the other
    # (a bank refunding charges, a penalty), so both classes are allowed on both.
    ("bank_revaluation_account_id", "bank revaluation", (AccountClass.ASSET,)),
    (
        "bank_charges_account_id",
        "bank charges",
        (AccountClass.INCOME, AccountClass.EXPENSE),
    ),
    (
        "bank_interest_account_id",
        "bank interest",
        (AccountClass.INCOME, AccountClass.EXPENSE),
    ),
)


def _postable_account(
    db: Session,
    company_id: int,
    account_id: int,
    label: str,
    classes: tuple[AccountClass, ...] | None = None,
) -> int:
    account = accounts_service.get_account(db, company_id, account_id)
    if not account.is_postable or account.is_control or not account.is_active:
        raise LedgerStateError(
            f"The {label} account must be active, postable and not a control account",
            code="invalid_gl_setting_account",
        )
    if classes is not None and account.class_ not in classes:
        wanted = " or ".join(str(item) for item in classes)
        raise LedgerStateError(
            f"The {label} account must be {wanted}; {account.code} is {account.class_}",
            code="invalid_gl_setting_account_class",
        )
    return account.id


# --- Exchange rates ----------------------------------------------------------------------


@router.get("/exchange-rates")
def list_exchange_rates(
    currency_id: int | None = None,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[ExchangeRateRead]:
    statement = select(ExchangeRate).where(ExchangeRate.company_id == auth.company_id)
    if currency_id is not None:
        statement = statement.where(ExchangeRate.currency_id == currency_id)
    rows = db.scalars(statement.order_by(ExchangeRate.currency_id, ExchangeRate.valid_from))
    return [ExchangeRateRead.model_validate(row) for row in rows]


@router.post("/exchange-rates", status_code=status.HTTP_201_CREATED)
def create_exchange_rate(
    payload: ExchangeRateCreate,
    auth: AuthContext = permissions.require(permissions.COMMON_SETUP_CURRENCIES),
    db: Session = Depends(get_db),
) -> ExchangeRateRead:
    row = accounts_service.add_exchange_rate(
        db,
        auth.company_id,
        currency_id=payload.currency_id,
        valid_from=payload.valid_from,
        rate=payload.rate,
        actor=auth.user,
    )
    db.commit()
    return ExchangeRateRead.model_validate(row)


# --- Branches, tax codes, currencies ----------------------------------------------------


@router.get("/branches")
def list_branches(
    include_inactive: bool = False,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[BranchRead]:
    rows = masters.list_branches(db, auth.company_id, include_inactive=include_inactive)
    return [BranchRead.model_validate(row) for row in rows]


@router.post("/branches", status_code=status.HTTP_201_CREATED)
def create_branch(
    payload: BranchCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.COMMON_SETUP_BRANCHES),
    db: Session = Depends(get_db),
) -> BranchRead:
    branch = masters.create_branch(
        db,
        auth.company_id,
        code=payload.code,
        name=payload.name,
        is_main=payload.is_main,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return BranchRead.model_validate(branch)


@router.patch("/branches/{branch_id}")
def update_branch(
    branch_id: int,
    payload: BranchUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.COMMON_SETUP_BRANCHES),
    db: Session = Depends(get_db),
) -> BranchRead:
    branch = masters.get_branch(db, auth.company_id, branch_id)
    masters.update_branch(
        db,
        branch,
        name=payload.name,
        is_main=payload.is_main,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return BranchRead.model_validate(branch)


@router.get("/tax-codes")
def list_tax_codes(
    include_inactive: bool = False,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[TaxCodeRead]:
    rows = masters.list_tax_codes(db, auth.company_id, include_inactive=include_inactive)
    return [TaxCodeRead.model_validate(row) for row in rows]


@router.post("/tax-codes", status_code=status.HTTP_201_CREATED)
def create_tax_code(
    payload: TaxCodeCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.COMMON_SETUP_TAXES),
    db: Session = Depends(get_db),
) -> TaxCodeRead:
    tax_code = masters.create_tax_code(
        db,
        auth.company_id,
        code=payload.code,
        name=payload.name,
        nature=payload.nature,
        rate_pct=payload.rate_pct,
        gl_account_id=payload.gl_account_id,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        fiscal_tax_type=payload.fiscal_tax_type,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return TaxCodeRead.model_validate(tax_code)


@router.patch("/tax-codes/{code_id}")
def update_tax_code(
    code_id: int,
    payload: TaxCodeUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.COMMON_SETUP_TAXES),
    db: Session = Depends(get_db),
) -> TaxCodeRead:
    tax_code = masters.get_tax_code(db, auth.company_id, code_id)
    gl_acc: int | None | object = ...
    if payload.clear_gl_account:
        gl_acc = None
    elif payload.gl_account_id is not None:
        gl_acc = payload.gl_account_id
    tax_type: object = ...
    if payload.clear_fiscal_tax_type:
        tax_type = None
    elif payload.fiscal_tax_type is not None:
        tax_type = payload.fiscal_tax_type
    masters.update_tax_code(
        db,
        tax_code,
        name=payload.name,
        rate_pct=payload.rate_pct,
        gl_account_id=gl_acc,
        valid_to=payload.valid_to if payload.valid_to is not None else ...,
        fiscal_tax_type=tax_type,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return TaxCodeRead.model_validate(tax_code)


@router.get("/currencies")
def list_currencies(
    include_inactive: bool = False,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[CurrencyRead]:
    rows = masters.list_currencies(db, auth.company_id, include_inactive=include_inactive)
    return [CurrencyRead.model_validate(row) for row in rows]


@router.post("/currencies", status_code=status.HTTP_201_CREATED)
def create_currency(
    payload: CurrencyCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.COMMON_SETUP_CURRENCIES),
    db: Session = Depends(get_db),
) -> CurrencyRead:
    currency = masters.create_currency(
        db,
        auth.company_id,
        code=payload.code,
        name=payload.name,
        symbol=payload.symbol,
        decimal_places=payload.decimal_places,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return CurrencyRead.model_validate(currency)


@router.patch("/currencies/{currency_id}")
def update_currency(
    currency_id: int,
    payload: CurrencyUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.COMMON_SETUP_CURRENCIES),
    db: Session = Depends(get_db),
) -> CurrencyRead:
    currency = masters.get_currency(db, auth.company_id, currency_id)
    masters.update_currency(
        db,
        currency,
        name=payload.name,
        symbol=payload.symbol,
        decimal_places=payload.decimal_places,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return CurrencyRead.model_validate(currency)


# --- Journal & cashbook entries ----------------------------------------------------------


@router.post("/journal-entries", status_code=status.HTTP_201_CREATED)
def create_journal_entry(
    payload: JournalEntryCreate,
    response: Response,
    idempotency_key: str = IdempotencyKey,
    auth: AuthContext = permissions.require(permissions.GL_JOURNAL_POST),
    db: Session = Depends(get_db),
) -> JournalEntryRead:
    request_hash = _fingerprint("journal-entry", payload)
    existing = posting.replay(db, auth.company_id, idempotency_key, request_hash)
    if existing is not None:
        return _posted_response(db, response, existing, replayed=True)
    event = ManualJournal(
        entry_date=payload.entry_date,
        description=payload.description,
        reference=payload.reference,
        branch_id=payload.branch_id,
        idempotency_key=idempotency_key,
        idempotency_hash=request_hash,
        lines=tuple(
            LineSpec(
                amount=line.signed_amount,
                gl_account_id=line.gl_account_id,
                transaction_type=line.transaction_type,
                currency_id=line.currency_id,
                exchange_rate=line.exchange_rate,
                branch_id=line.branch_id,
                project_id=line.project_id,
                partner_type=line.partner_type,
                partner_id=line.partner_id,
                item_id=line.item_id,
                tax_code_id=line.tax_code_id,
                tax_amount=line.tax_amount,
                description=line.description,
            )
            for line in payload.lines
        ),
    )
    entry = posting.post(db, event, company_id=auth.company_id, actor=auth.user)
    assert entry is not None
    return _posted_response(db, response, entry, replayed=False)


@router.post("/cashbook-entries", status_code=status.HTTP_201_CREATED)
def create_cashbook_entry(
    payload: CashbookEntryCreate,
    response: Response,
    idempotency_key: str = IdempotencyKey,
    auth: AuthContext = permissions.require(permissions.GL_JOURNAL_POST),
    db: Session = Depends(get_db),
) -> JournalEntryRead:
    request_hash = _fingerprint("cashbook-entry", payload)
    existing = posting.replay(db, auth.company_id, idempotency_key, request_hash)
    if existing is not None:
        return _posted_response(db, response, existing, replayed=True)
    event = CashbookEntry(
        entry_date=payload.entry_date,
        description=payload.description,
        branch_id=payload.branch_id,
        idempotency_key=idempotency_key,
        idempotency_hash=request_hash,
        cash_account_id=payload.cash_account_id,
        kind=CashbookKind(payload.kind),
        currency_id=payload.currency_id,
        exchange_rate=payload.exchange_rate,
        reference=payload.reference,
        lines=tuple(
            CashbookLineSpec(
                gl_account_id=line.gl_account_id,
                transaction_type=line.transaction_type,
                amount=line.amount,
                tax_code_id=line.tax_code_id,
                tax_inclusive=line.tax_inclusive,
                branch_id=line.branch_id,
                project_id=line.project_id,
                partner_type=line.partner_type,
                partner_id=line.partner_id,
                description=line.description,
            )
            for line in payload.lines
        ),
    )
    entry = posting.post(db, event, company_id=auth.company_id, actor=auth.user)
    assert entry is not None
    return _posted_response(db, response, entry, replayed=False)


@router.get("/journal-entries")
def list_journal_entries(
    cursor: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    date_from: date | None = None,
    date_to: date | None = None,
    reference: str | None = None,
    query: str | None = None,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> Page[JournalEntrySummary]:
    Reversing = aliased(JournalEntry)
    statement = (
        select(
            JournalEntry,
            Reversing.id.label("reversed_by_entry_id"),
            Reversing.number.label("reversed_by_number"),
        )
        .outerjoin(
            Reversing,
            and_(
                Reversing.company_id == JournalEntry.company_id,
                Reversing.reverses_entry_id == JournalEntry.id,
            ),
        )
        .where(JournalEntry.company_id == auth.company_id)
    )
    if date_from is not None:
        statement = statement.where(JournalEntry.entry_date >= date_from)
    if date_to is not None:
        statement = statement.where(JournalEntry.entry_date <= date_to)
    if reference is not None and reference.strip():
        statement = statement.where(JournalEntry.reference.ilike(f"%{reference.strip()}%"))
    if query is not None and query.strip():
        q = f"%{query.strip()}%"
        statement = statement.where(
            (JournalEntry.number.ilike(q))
            | (JournalEntry.description.ilike(q))
            | (JournalEntry.reference.ilike(q))
        )
    if cursor is not None:
        statement = statement.where(JournalEntry.id > cursor)
    rows = db.execute(statement.order_by(JournalEntry.id).limit(limit + 1)).all()
    items = []
    for row in rows[:limit]:
        entry_row, rvd_by_id, rvd_by_num = row
        item = JournalEntrySummary.model_validate(entry_row)
        item.reversed_by_entry_id = rvd_by_id
        item.reversed_by_number = rvd_by_num
        items.append(item)
    return Page(items=items, next_cursor=items[-1].id if len(rows) > limit else None)


@router.get("/journal-entries/{entry_id}")
def get_journal_entry(
    entry_id: int,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> JournalEntryRead:
    entry = db.get(JournalEntry, entry_id)
    if entry is None or entry.company_id != auth.company_id:
        raise NotFoundError("Journal entry not found")
    return _entry_read(db, entry)



@router.post("/journal-entries/{entry_id}/reverse", status_code=status.HTTP_201_CREATED)
def reverse_journal_entry(
    entry_id: int,
    payload: ReversalCreate,
    response: Response,
    idempotency_key: str = IdempotencyKey,
    auth: AuthContext = permissions.require(permissions.GL_JOURNAL_POST),
    db: Session = Depends(get_db),
) -> JournalEntryRead:
    request_hash = _fingerprint(f"reverse:{entry_id}", payload)
    existing = posting.replay(db, auth.company_id, idempotency_key, request_hash)
    if existing is not None:
        return _posted_response(db, response, existing, replayed=True)
    entry = posting.reverse(
        db,
        entry_id,
        company_id=auth.company_id,
        on_date=payload.entry_date,
        reason=payload.reason,
        actor=auth.user,
        idempotency_key=idempotency_key,
        idempotency_hash=request_hash,
    )
    return _posted_response(db, response, entry, replayed=False)


# --- Enquiries ---------------------------------------------------------------------------


@router.get("/trial-balance")
def get_trial_balance(
    as_of: date,
    branch_id: int | None = None,
    project_id: int | None = None,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> TrialBalanceRead:
    report = enquiries.trial_balance(
        db, auth.company_id, as_of=as_of, branch_id=branch_id, project_id=project_id
    )
    return TrialBalanceRead(
        as_of=report.as_of,
        branch_id=report.branch_id,
        project_id=report.project_id,
        rows=[
            TrialBalanceRowRead(
                gl_account_id=row.gl_account_id,
                code=row.code,
                name=row.name,
                class_=row.class_,
                debit=row.debit,
                credit=row.credit,
                net=row.net,
            )
            for row in report.rows
        ],
        total_debit=report.total_debit,
        total_credit=report.total_credit,
        foots=report.foots,
    )


@router.get("/accounts/{account_id}/transactions")
def get_account_transactions(
    account_id: int,
    date_from: date,
    date_to: date,
    branch_id: int | None = None,
    project_id: int | None = None,
    cursor: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> AccountTransactionsRead:
    report = enquiries.account_transactions(
        db,
        auth.company_id,
        account_id,
        date_from=date_from,
        date_to=date_to,
        branch_id=branch_id,
        project_id=project_id,
        cursor=cursor,
        limit=limit,
    )
    return AccountTransactionsRead(
        gl_account_id=report.gl_account_id,
        date_from=report.date_from,
        date_to=report.date_to,
        opening_base=report.opening_base,
        items=[AccountTransactionRead(**vars(item)) for item in report.items],
        next_cursor=report.next_cursor,
    )


@router.get("/period-balances/verify")
def verify_period_balances(
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[PeriodBalanceDriftRead]:
    drift = balances.verify_period_balances(db, auth.company_id)
    return [PeriodBalanceDriftRead(**vars(item)) for item in drift]


# --- Periods & fiscal years --------------------------------------------------------------


@router.get("/fiscal-years")
def list_fiscal_years(
    auth: AuthContext = permissions.require(permissions.COMPANY_READ),
    db: Session = Depends(get_db),
) -> list[FiscalYearRead]:
    rows = db.scalars(
        select(FiscalYear)
        .where(FiscalYear.company_id == auth.company_id)
        .order_by(FiscalYear.start_date)
    )
    return [FiscalYearRead.model_validate(row) for row in rows]


@router.post("/fiscal-years", status_code=status.HTTP_201_CREATED)
def create_fiscal_year(
    payload: FiscalYearCreate,
    auth: AuthContext = permissions.require(permissions.ACCOUNTING_PERIODS_MANAGE),
    db: Session = Depends(get_db),
) -> FiscalYearRead:
    year = periods_service.create_fiscal_year(
        db,
        auth.company_id,
        name=payload.name,
        start_date=payload.start_date,
        end_date=payload.end_date,
        open_through=payload.open_through,
    )
    db.commit()
    return FiscalYearRead.model_validate(year)


@router.post("/fiscal-years/{year_id}/close")
def close_fiscal_year(
    year_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.ACCOUNTING_PERIODS_MANAGE),
    db: Session = Depends(get_db),
) -> FiscalYearRead:
    year = year_end.close_fiscal_year(
        db, auth.company_id, year_id, actor=auth.user, request=request
    )
    db.commit()
    return FiscalYearRead.model_validate(year)


@router.post("/fiscal-years/{year_id}/reopen")
def reopen_fiscal_year(
    year_id: int,
    payload: ReasonBody,
    request: Request,
    auth: AuthContext = permissions.require(
        permissions.ACCOUNTING_PERIODS_MANAGE, permissions.ACCOUNTING_PERIODS_REOPEN
    ),
    db: Session = Depends(get_db),
) -> FiscalYearRead:
    year = year_end.reopen_fiscal_year(
        db, auth.company_id, year_id, actor=auth.user, reason=payload.reason, request=request
    )
    db.commit()
    return FiscalYearRead.model_validate(year)


@router.get("/periods")
def list_periods(
    fiscal_year_id: int | None = None,
    auth: AuthContext = permissions.require(permissions.COMPANY_READ),
    db: Session = Depends(get_db),
) -> list[PeriodRead]:
    statement = select(AccountingPeriod).where(AccountingPeriod.company_id == auth.company_id)
    if fiscal_year_id is not None:
        statement = statement.where(AccountingPeriod.fiscal_year_id == fiscal_year_id)
    rows = db.scalars(statement.order_by(AccountingPeriod.start_date))
    return [PeriodRead.model_validate(row) for row in rows]


def _transition(
    db: Session,
    auth: AuthContext,
    request: Request,
    period_id: int,
    target: PeriodStatus,
    reason: str | None = None,
) -> PeriodRead:
    period = periods_service.get_period(db, auth.company_id, period_id)
    periods_service.transition_period(
        db, period, target, actor=auth.user, reason=reason, request=request
    )
    db.commit()
    return PeriodRead.model_validate(period)


@router.post("/periods/{period_id}/open")
def open_period(
    period_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.ACCOUNTING_PERIODS_MANAGE),
    db: Session = Depends(get_db),
) -> PeriodRead:
    return _transition(db, auth, request, period_id, PeriodStatus.OPEN)


@router.post("/periods/{period_id}/close")
def close_period(
    period_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.ACCOUNTING_PERIODS_MANAGE),
    db: Session = Depends(get_db),
) -> PeriodRead:
    return _transition(db, auth, request, period_id, PeriodStatus.CLOSED)


@router.post("/periods/{period_id}/lock")
def lock_period(
    period_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.ACCOUNTING_PERIODS_MANAGE),
    db: Session = Depends(get_db),
) -> PeriodRead:
    return _transition(db, auth, request, period_id, PeriodStatus.LOCKED)


@router.post("/periods/{period_id}/reopen")
def reopen_period(
    period_id: int,
    payload: ReasonBody,
    request: Request,
    auth: AuthContext = permissions.require(
        permissions.ACCOUNTING_PERIODS_MANAGE, permissions.ACCOUNTING_PERIODS_REOPEN
    ),
    db: Session = Depends(get_db),
) -> PeriodRead:
    """closed → open, or locked → closed (audited)."""
    period = periods_service.get_period(db, auth.company_id, period_id)
    target = PeriodStatus.OPEN if period.status == PeriodStatus.CLOSED else PeriodStatus.CLOSED
    return _transition(db, auth, request, period_id, target, payload.reason)


# --- Transaction types (determination-chain defaults) ---------------------------------------

# One table, one `module` discriminator (P2) — so the AR and AP maintenance screens read and
# write their own rows through these endpoints. A module-scoped call therefore passes on the
# GL permission *or* that module's own: a Sales Manager holds `ar:setup_manage` and no GL
# rights at all, and must still be able to maintain AR transaction types.
MODULE_VIEW_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "ar": (permissions.AR_REPORTS_VIEW, permissions.AR_SETUP_MANAGE),
    "ap": (permissions.AP_REPORTS_VIEW, permissions.AP_SETUP_MANAGE),
    INVENTORY_MODULE: (permissions.INV_REPORTS_VIEW, permissions.INV_SETUP_MANAGE),
}
MODULE_SETUP_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "ar": (permissions.AR_SETUP_MANAGE,),
    "ap": (permissions.AP_SETUP_MANAGE,),
    INVENTORY_MODULE: (permissions.INV_SETUP_MANAGE,),
}


def _require_module_permission(
    auth: AuthContext,
    module: str | None,
    *,
    gl_permission: str,
    by_module: dict[str, tuple[str, ...]],
) -> None:
    """An unscoped call (`module=None`, i.e. every module at once) stays GL-only."""
    allowed = (gl_permission, *by_module.get(module or "", ()))
    if not any(permission in auth.permissions for permission in allowed):
        raise PermissionDeniedError(f"Missing required permission(s): {' or '.join(allowed)}")


@router.get("/transaction-types")
def list_transaction_types(
    module: str | None = None,
    include_inactive: bool = False,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[TransactionTypeRead]:
    _require_module_permission(
        auth,
        module,
        gl_permission=permissions.GL_REPORTS_VIEW,
        by_module=MODULE_VIEW_PERMISSIONS,
    )
    rows = masters.list_transaction_types(
        db, auth.company_id, module=module, include_inactive=include_inactive
    )
    return [TransactionTypeRead.model_validate(row) for row in rows]


@router.post("/transaction-types", status_code=status.HTTP_201_CREATED)
def create_transaction_type(
    payload: TransactionTypeCreate,
    request: Request,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TransactionTypeRead:
    _require_module_permission(
        auth,
        payload.module,
        gl_permission=permissions.GL_SETUP_MANAGE,
        by_module=MODULE_SETUP_PERMISSIONS,
    )
    row = masters.create_transaction_type(
        db,
        auth.company_id,
        module=payload.module,
        code=payload.code,
        name=payload.name,
        kind=payload.kind,
        default_gl_account_id=payload.default_gl_account_id,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return TransactionTypeRead.model_validate(row)


@router.patch("/transaction-types/{type_id}")
def update_transaction_type(
    type_id: int,
    payload: TransactionTypeUpdate,
    request: Request,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TransactionTypeRead:
    transaction_type = masters.get_transaction_type(db, auth.company_id, type_id)
    _require_module_permission(
        auth,
        transaction_type.module,
        gl_permission=permissions.GL_SETUP_MANAGE,
        by_module=MODULE_SETUP_PERMISSIONS,
    )
    default_account: int | None | object = ...
    if payload.clear_default_account:
        default_account = None
    elif payload.default_gl_account_id is not None:
        default_account = payload.default_gl_account_id
    masters.update_transaction_type(
        db,
        transaction_type,
        name=payload.name,
        default_gl_account_id=default_account,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return TransactionTypeRead.model_validate(transaction_type)


# --- Projects (costing dimension, D8) --------------------------------------------------------


@router.get("/projects")
def list_projects(
    include_inactive: bool = False,
    auth: AuthContext = permissions.require(permissions.PROJECTS_READ),
    db: Session = Depends(get_db),
) -> list[ProjectRead]:
    rows = masters.list_projects(db, auth.company_id, include_inactive=include_inactive)
    return [ProjectRead.model_validate(row) for row in rows]


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.PROJECTS_MANAGE),
    db: Session = Depends(get_db),
) -> ProjectRead:
    project = masters.create_project(
        db,
        auth.company_id,
        code=payload.code,
        name=payload.name,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return ProjectRead.model_validate(project)


@router.get("/projects/{project_id}")
def get_project(
    project_id: int,
    auth: AuthContext = permissions.require(permissions.PROJECTS_READ),
    db: Session = Depends(get_db),
) -> ProjectRead:
    return ProjectRead.model_validate(masters.get_project(db, auth.company_id, project_id))


@router.patch("/projects/{project_id}")
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.PROJECTS_MANAGE),
    db: Session = Depends(get_db),
) -> ProjectRead:
    project = masters.get_project(db, auth.company_id, project_id)
    masters.update_project(
        db,
        project,
        code=payload.code,
        name=payload.name,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return ProjectRead.model_validate(project)


# --- Unrealized FX revaluation (P7 decision 13) ---------------------------------------------
#
# The screen arrives at **step 7** — General Ledger → Period end → FX revaluation, with the
# preview, Post and Reverse — so the three mutating endpoints carry a `GAP (P7, step 7)` line
# in `tests/test_api_has_a_caller.py` naming the step that deletes them.


def _revaluation_line(line: revaluation_service.RevaluationLine) -> FxRevaluationLineRead:
    return FxRevaluationLineRead(
        scope=line.scope,
        document_id=line.document_id,
        document_number=line.document_number,
        role=str(line.role) if line.role is not None else None,
        partner_id=line.partner_id,
        partner_name=line.partner_name,
        bank_account_id=line.bank_account_id,
        bank_account_code=line.bank_account_code,
        bank_account_name=line.bank_account_name,
        currency_id=line.currency_id,
        currency_code=line.currency_code,
        open_amount=line.open_amount,
        booking_rate=line.booking_rate,
        carrying_base=line.carrying_base,
        rate_at_date=line.rate_at_date,
        revalued_base=line.revalued_base,
        difference=line.difference,
    )


@router.get("/fx-revaluations/preview")
def preview_fx_revaluation(
    revaluation_date: date = Query(...),
    role: FxRevaluationRole = Query(default=FxRevaluationRole.BOTH),
    auth: AuthContext = permissions.require(permissions.GL_FX_REVALUE),
    db: Session = Depends(get_db),
) -> FxRevaluationPreview:
    """What a run would post, document by document, without posting it.

    Deliberately permissive about the date: a preview of a day that could not be posted is
    still worth reading, and the refusals belong where they can be acted on.
    """
    view = revaluation_service.preview(
        db, auth.company_id, revaluation_date=revaluation_date, role=role
    )
    return FxRevaluationPreview(
        revaluation_date=view.revaluation_date,
        role=view.role,
        total_difference=view.total_difference,
        lines=[_revaluation_line(line) for line in view.lines],
    )


@router.get("/fx-revaluations")
def list_fx_revaluations(
    auth: AuthContext = permissions.require(permissions.GL_FX_REVALUE),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[FxRevaluationRead]:
    rows = db.scalars(
        select(FxRevaluation)
        .where(FxRevaluation.company_id == auth.company_id)
        .order_by(FxRevaluation.revaluation_date.desc(), FxRevaluation.id.desc())
        .limit(limit)
    )
    return [FxRevaluationRead.model_validate(row) for row in rows]


def _stored_revaluation_line(  # noqa: ANN202
    line,  # noqa: ANN001
    documents: dict,
    partners: dict,
    currencies: dict,
    bank_rows: dict,
) -> FxRevaluationLineRead:
    """A stored line, document or bank, with the names its screen drills through.

    One function rather than two branches inline, because the difference between the two kinds is
    exactly which half of the row is null and every other field is shared.
    """
    document = documents.get(line.document_id) if line.document_id is not None else None
    row = bank_rows.get(line.bank_account_id) if line.bank_account_id is not None else None
    partner = partners.get(document.partner_id) if document is not None else None
    return FxRevaluationLineRead(
        scope="bank" if row is not None else str(document.role) if document else "",
        document_id=line.document_id,
        document_number=document.number if document else None,
        role=str(document.role) if document else None,
        partner_id=document.partner_id if document else None,
        partner_name=partner.name if partner else None,
        bank_account_id=line.bank_account_id,
        bank_account_code=row.code if row else None,
        bank_account_name=row.name if row else None,
        currency_id=line.currency_id,
        currency_code=currencies[line.currency_id].code,
        open_amount=line.open_amount,
        booking_rate=line.booking_rate,
        carrying_base=line.carrying_base,
        rate_at_date=line.rate_at_date,
        revalued_base=line.revalued_base,
        difference=line.difference,
    )


@router.get("/fx-revaluations/{revaluation_id}")
def read_fx_revaluation(
    revaluation_id: int,
    auth: AuthContext = permissions.require(permissions.GL_FX_REVALUE),
    db: Session = Depends(get_db),
) -> FxRevaluationDetail:
    run = db.scalar(
        select(FxRevaluation).where(
            FxRevaluation.company_id == auth.company_id, FxRevaluation.id == revaluation_id
        )
    )
    if run is None:
        raise NotFoundError("FX revaluation not found")
    stored = revaluation_service.lines_of(db, auth.company_id, run.id)
    documents = {
        document.id: document
        for document in db.scalars(
            select(PartnerDocument).where(
                PartnerDocument.company_id == auth.company_id,
                PartnerDocument.id.in_(
                    [line.document_id for line in stored if line.document_id is not None] or [0]
                ),
            )
        )
    }
    # P8 decision 8: a run may carry bank lines, which have no document and no partner. Loaded
    # here for the same reason the partners are — the report drills to the workspace from a bank
    # line's code, and an id with no code on it is a cell nobody can follow.
    bank_rows = {
        row.id: row
        for row in db.scalars(
            select(BankAccountModel).where(
                BankAccountModel.company_id == auth.company_id,
                BankAccountModel.id.in_(
                    [line.bank_account_id for line in stored if line.bank_account_id is not None]
                    or [0]
                ),
            )
        )
    }
    currencies = {
        currency.id: currency
        for currency in db.scalars(
            select(Currency).where(Currency.company_id == auth.company_id)
        )
    }
    # Decision 13: the lines carry the partner **so the report drills**. Loading the documents
    # and leaving the name blank would render a screen of empty cells and satisfy nothing.
    partners = {
        partner.id: partner
        for partner in db.scalars(
            select(Partner).where(
                Partner.company_id == auth.company_id,
                Partner.id.in_([document.partner_id for document in documents.values()] or [0]),
            )
        )
    }
    lines = [
        _stored_revaluation_line(line, documents, partners, currencies, bank_rows)
        for line in stored
    ]
    # Built with its lines rather than validated and then filled: `lines` is required, so
    # `model_validate(run)` on the ORM row alone raises — which is what every call to this
    # endpoint did until a test opened it.
    return FxRevaluationDetail(**FxRevaluationRead.model_validate(run).model_dump(), lines=lines)


@router.post("/fx-revaluations", status_code=status.HTTP_201_CREATED)
def post_fx_revaluation(
    payload: FxRevaluationCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.GL_FX_REVALUE),
    db: Session = Depends(get_db),
    idempotency_key: str = idempotency.IdempotencyKey,
) -> FxRevaluationRead:
    """Post the run and its mirror in one transaction."""
    run = revaluation_service.post_revaluation(
        db,
        auth.company_id,
        revaluation_date=payload.revaluation_date,
        role=payload.role,
        actor=auth.user,
        idempotency_key=idempotency_key,
        request=request,
    )
    db.commit()
    return FxRevaluationRead.model_validate(run)


@router.post("/fx-revaluations/{revaluation_id}/reverse")
def reverse_fx_revaluation(
    revaluation_id: int,
    payload: FxRevaluationReverse,
    request: Request,
    auth: AuthContext = permissions.require(permissions.GL_FX_REVALUE),
    db: Session = Depends(get_db),
) -> FxRevaluationRead:
    run = revaluation_service.reverse_revaluation(
        db,
        auth.company_id,
        revaluation_id,
        reason=payload.reason,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return FxRevaluationRead.model_validate(run)
