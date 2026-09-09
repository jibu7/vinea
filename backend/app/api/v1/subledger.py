"""AR/AP API (P4). `role` is a path segment (`ar` / `ap`), so there is exactly one router,
one set of schemas and one service behind both modules."""

from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, Request, Response, status
from fastapi.responses import Response as RawResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import AuthContext, get_tenant_context
from app.api.idempotency import IdempotencyKey, fingerprint
from app.core import permissions
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.db import get_db
from app.kernel.posting import gl_settings_for
from app.models.job import JobStatus
from app.models.partner import PartnerRole
from app.models.subledger import Allocation, DocumentKind, PartnerDocument
from app.schemas.common import Page
from app.schemas.subledger import (
    AgeingBucketAmount,
    AgeingReport,
    AgeingRow,
    AllocationCreate,
    AllocationPreview,
    AllocationPreviewLine,
    AllocationPreviewPosting,
    AllocationRead,
    ArApDefaultsRead,
    ArApDefaultsUpdate,
    AutoAllocateCreate,
    BucketRead,
    BucketSetCreate,
    BucketSetRead,
    BucketSetUpdate,
    ContactCreate,
    ContactRead,
    ContactUpdate,
    DocumentCreate,
    DocumentRead,
    DocumentSummary,
    JobRead,
    JobSweepResult,
    MaturityRunRequest,
    MaturityRunResult,
    OpenItemRead,
    PartnerAllocationRead,
    PartnerCreate,
    PartnerEnquiry,
    PartnerEnquiryEntry,
    PartnerRead,
    PartnerUpdate,
    PaymentTermsRead,
    PaymentTermsWrite,
    ReversalRequest,
    RoleSettingsRead,
    RoleSettingsWrite,
    SalesRepCreate,
    SalesRepRead,
    SalesRepUpdate,
    StatementRequest,
)
from app.services import jobs as jobs_service
from app.subledger import ageing as ageing_service
from app.subledger import allocations as allocations_service
from app.subledger import documents as documents_service
from app.subledger import enquiries as enquiries_service
from app.subledger import masters
from app.subledger.statements import STATEMENT_JOB

router = APIRouter(prefix="/subledger", tags=["accounts-receivable-payable"])

RolePath = Path(description="`ar` for customers, `ap` for suppliers")

SETUP_PERMISSION = {
    PartnerRole.AR: permissions.AR_SETUP_MANAGE,
    PartnerRole.AP: permissions.AP_SETUP_MANAGE,
}
POST_PERMISSION = {
    PartnerRole.AR: permissions.AR_TRANSACTIONS_POST,
    PartnerRole.AP: permissions.AP_TRANSACTIONS_POST,
}
VIEW_PERMISSION = {
    PartnerRole.AR: permissions.AR_REPORTS_VIEW,
    PartnerRole.AP: permissions.AP_REPORTS_VIEW,
}


def _require(auth: AuthContext, mapping: dict[PartnerRole, str], role: PartnerRole) -> None:
    needed = mapping[role]
    if needed not in auth.permissions:
        raise PermissionDeniedError(f"Missing required permission(s): {needed}")


def _require_any(auth: AuthContext, *needed: str) -> None:
    if not any(permission in auth.permissions for permission in needed):
        raise PermissionDeniedError(f"Missing required permission(s): {' or '.join(needed)}")


# --- Partners ---------------------------------------------------------------------------


@router.get("/{role}/partners")
def list_partners(
    role: PartnerRole = RolePath,
    search: str | None = None,
    include_inactive: bool = False,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[PartnerRead]:
    _require(auth, VIEW_PERMISSION, role)
    rows = masters.list_partners(
        db, auth.company_id, role=role, search=search, include_inactive=include_inactive
    )
    return [PartnerRead.model_validate(row) for row in rows]


@router.post("/{role}/partners", status_code=status.HTTP_201_CREATED)
def create_partner(
    payload: PartnerCreate,
    request: Request,
    role: PartnerRole = RolePath,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PartnerRead:
    _require(auth, SETUP_PERMISSION, role)
    partner = masters.create_partner(
        db,
        auth.company_id,
        masters.PartnerInput(
            name=payload.name,
            customer_code=payload.customer_code,
            supplier_code=payload.supplier_code,
            tin=payload.tin,
            email=payload.email,
            phone=payload.phone,
            address=payload.address,
            notes=payload.notes,
            currency_id=payload.currency_id,
        ),
        actor=auth.user,
        request=request,
    )
    db.commit()
    return PartnerRead.model_validate(partner)


@router.get("/{role}/partners/{partner_id}")
def get_partner(
    partner_id: int,
    role: PartnerRole = RolePath,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PartnerRead:
    _require(auth, VIEW_PERMISSION, role)
    return PartnerRead.model_validate(masters.get_partner(db, auth.company_id, partner_id))


@router.patch("/{role}/partners/{partner_id}")
def update_partner(
    partner_id: int,
    payload: PartnerUpdate,
    request: Request,
    role: PartnerRole = RolePath,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PartnerRead:
    _require(auth, SETUP_PERMISSION, role)
    partner = masters.get_partner(db, auth.company_id, partner_id)
    customer_code: object = ...
    if payload.clear_customer_code:
        customer_code = None
    elif payload.customer_code is not None:
        customer_code = payload.customer_code
    supplier_code: object = ...
    if payload.clear_supplier_code:
        supplier_code = None
    elif payload.supplier_code is not None:
        supplier_code = payload.supplier_code
    currency: object = ...
    if payload.clear_currency:
        currency = None
    elif payload.currency_id is not None:
        currency = payload.currency_id
    masters.update_partner(
        db,
        partner,
        name=payload.name,
        customer_code=customer_code,
        supplier_code=supplier_code,
        tin=payload.tin,
        email=payload.email,
        phone=payload.phone,
        address=payload.address,
        notes=payload.notes,
        currency_id=currency,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return PartnerRead.model_validate(partner)


@router.get("/{role}/partners/{partner_id}/settings")
def get_partner_settings(
    partner_id: int,
    role: PartnerRole = RolePath,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> RoleSettingsRead | None:
    _require(auth, VIEW_PERMISSION, role)
    settings = masters.get_role_settings(db, auth.company_id, partner_id, role)
    return None if settings is None else RoleSettingsRead.model_validate(settings)


@router.put("/{role}/partners/{partner_id}/settings")
def put_partner_settings(
    partner_id: int,
    payload: RoleSettingsWrite,
    request: Request,
    role: PartnerRole = RolePath,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> RoleSettingsRead:
    _require(auth, SETUP_PERMISSION, role)
    partner = masters.get_partner(db, auth.company_id, partner_id)
    settings = masters.upsert_role_settings(
        db,
        auth.company_id,
        partner,
        role,
        masters.RoleSettingsInput(**payload.model_dump()),
        actor=auth.user,
        request=request,
    )
    db.commit()
    return RoleSettingsRead.model_validate(settings)


@router.get("/{role}/partners/{partner_id}/contacts")
def list_contacts(
    partner_id: int,
    role: PartnerRole = RolePath,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[ContactRead]:
    _require(auth, VIEW_PERMISSION, role)
    return [
        ContactRead.model_validate(row)
        for row in masters.list_contacts(db, auth.company_id, partner_id)
    ]


@router.post("/{role}/partners/{partner_id}/contacts", status_code=status.HTTP_201_CREATED)
def create_contact(
    partner_id: int,
    payload: ContactCreate,
    request: Request,
    role: PartnerRole = RolePath,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ContactRead:
    _require(auth, SETUP_PERMISSION, role)
    partner = masters.get_partner(db, auth.company_id, partner_id)
    contact = masters.create_contact(
        db,
        auth.company_id,
        partner,
        name=payload.name,
        role=payload.role,
        email=payload.email,
        phone=payload.phone,
        notes=payload.notes,
        is_primary=payload.is_primary,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return ContactRead.model_validate(contact)


@router.patch("/{role}/contacts/{contact_id}")
def update_contact(
    contact_id: int,
    payload: ContactUpdate,
    request: Request,
    role: PartnerRole = RolePath,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ContactRead:
    _require(auth, SETUP_PERMISSION, role)
    contact = masters.get_contact(db, auth.company_id, contact_id)
    masters.update_contact(
        db,
        contact,
        name=payload.name,
        role=payload.role,
        email=payload.email,
        phone=payload.phone,
        notes=payload.notes,
        is_primary=payload.is_primary,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return ContactRead.model_validate(contact)


# --- Shared masters ---------------------------------------------------------------------


@router.get("/sales-reps")
def list_sales_reps(
    include_inactive: bool = False,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[SalesRepRead]:
    _require_any(auth, permissions.AR_REPORTS_VIEW, permissions.AR_SETUP_MANAGE)
    return [
        SalesRepRead.model_validate(row)
        for row in masters.list_sales_reps(db, auth.company_id, include_inactive=include_inactive)
    ]


@router.post("/sales-reps", status_code=status.HTTP_201_CREATED)
def create_sales_rep(
    payload: SalesRepCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.AR_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> SalesRepRead:
    rep = masters.create_sales_rep(
        db,
        auth.company_id,
        code=payload.code,
        name=payload.name,
        email=payload.email,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return SalesRepRead.model_validate(rep)


@router.patch("/sales-reps/{rep_id}")
def update_sales_rep(
    rep_id: int,
    payload: SalesRepUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.AR_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> SalesRepRead:
    rep = masters.get_sales_rep(db, auth.company_id, rep_id)
    masters.update_sales_rep(
        db,
        rep,
        name=payload.name,
        email=payload.email,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return SalesRepRead.model_validate(rep)


@router.get("/payment-terms")
def list_payment_terms(
    include_inactive: bool = False,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[PaymentTermsRead]:
    _require_any(
        auth,
        permissions.AR_REPORTS_VIEW,
        permissions.AP_REPORTS_VIEW,
        permissions.AR_SETUP_MANAGE,
        permissions.AP_SETUP_MANAGE,
    )
    return [
        PaymentTermsRead.model_validate(row)
        for row in masters.list_payment_terms(
            db, auth.company_id, include_inactive=include_inactive
        )
    ]


@router.post("/payment-terms", status_code=status.HTTP_201_CREATED)
def create_payment_terms(
    payload: PaymentTermsWrite,
    request: Request,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PaymentTermsRead:
    _require_any(auth, permissions.AR_SETUP_MANAGE, permissions.AP_SETUP_MANAGE)
    terms = masters.create_payment_terms(
        db,
        auth.company_id,
        masters.PaymentTermsInput(
            code=payload.code,
            name=payload.name,
            due_basis=payload.due_basis,
            due_days=payload.due_days,
            due_day_of_month=payload.due_day_of_month,
            discount_percent=payload.discount_percent,
            discount_days=payload.discount_days,
        ),
        actor=auth.user,
        request=request,
    )
    db.commit()
    return PaymentTermsRead.model_validate(terms)


@router.put("/payment-terms/{terms_id}")
def update_payment_terms(
    terms_id: int,
    payload: PaymentTermsWrite,
    request: Request,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PaymentTermsRead:
    _require_any(auth, permissions.AR_SETUP_MANAGE, permissions.AP_SETUP_MANAGE)
    terms = masters.get_payment_terms(db, auth.company_id, terms_id)
    masters.update_payment_terms(
        db,
        terms,
        masters.PaymentTermsInput(
            code=payload.code,
            name=payload.name,
            due_basis=payload.due_basis,
            due_days=payload.due_days,
            due_day_of_month=payload.due_day_of_month,
            discount_percent=payload.discount_percent,
            discount_days=payload.discount_days,
        ),
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return PaymentTermsRead.model_validate(terms)


def _bucket_set_read(db: Session, bucket_set) -> BucketSetRead:  # noqa: ANN001
    data = BucketSetRead.model_validate(bucket_set)
    data.buckets = [
        BucketRead.model_validate(bucket) for bucket in masters.buckets_of(db, bucket_set)
    ]
    return data


@router.get("/ageing-bucket-sets")
def list_bucket_sets(
    include_inactive: bool = False,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[BucketSetRead]:
    _require_any(auth, permissions.AR_REPORTS_VIEW, permissions.AP_REPORTS_VIEW)
    return [
        _bucket_set_read(db, bucket_set)
        for bucket_set in masters.list_bucket_sets(
            db, auth.company_id, include_inactive=include_inactive
        )
    ]


@router.post("/ageing-bucket-sets", status_code=status.HTTP_201_CREATED)
def create_bucket_set(
    payload: BucketSetCreate,
    request: Request,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> BucketSetRead:
    _require_any(auth, permissions.AR_SETUP_MANAGE, permissions.AP_SETUP_MANAGE)
    bucket_set = masters.create_bucket_set(
        db,
        auth.company_id,
        code=payload.code,
        name=payload.name,
        basis=payload.basis,
        buckets=[
            masters.BucketInput(b.label, b.from_days, b.to_days) for b in payload.buckets
        ],
        is_default=payload.is_default,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return _bucket_set_read(db, bucket_set)


@router.patch("/ageing-bucket-sets/{set_id}")
def update_bucket_set(
    set_id: int,
    payload: BucketSetUpdate,
    request: Request,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> BucketSetRead:
    _require_any(auth, permissions.AR_SETUP_MANAGE, permissions.AP_SETUP_MANAGE)
    bucket_set = masters.get_bucket_set(db, auth.company_id, set_id)
    masters.update_bucket_set(
        db,
        bucket_set,
        name=payload.name,
        basis=payload.basis,
        buckets=(
            None
            if payload.buckets is None
            else [masters.BucketInput(b.label, b.from_days, b.to_days) for b in payload.buckets]
        ),
        is_default=payload.is_default,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return _bucket_set_read(db, bucket_set)


@router.get("/defaults")
def get_defaults(
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ArApDefaultsRead:
    _require_any(auth, permissions.AR_REPORTS_VIEW, permissions.AP_REPORTS_VIEW)
    return ArApDefaultsRead.model_validate(gl_settings_for(db, auth.company_id))


@router.patch("/defaults")
def update_defaults(
    payload: ArApDefaultsUpdate,
    auth: AuthContext = permissions.require(permissions.GL_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> ArApDefaultsRead:
    settings = gl_settings_for(db, auth.company_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(settings, field, value)
    db.commit()
    return ArApDefaultsRead.model_validate(settings)


# --- Documents --------------------------------------------------------------------------


def _document_read(db: Session, document: PartnerDocument) -> DocumentRead:
    loaded = db.scalar(
        select(PartnerDocument)
        .options(selectinload(PartnerDocument.lines))
        .where(PartnerDocument.id == document.id)
    )
    return DocumentRead.model_validate(loaded)


@router.post("/{role}/documents", status_code=status.HTTP_201_CREATED)
def post_document(
    payload: DocumentCreate,
    request: Request,
    response: Response,
    role: PartnerRole = RolePath,
    idempotency_key: str = IdempotencyKey,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> DocumentRead:
    _require(auth, POST_PERMISSION, role)
    data = documents_service.DocumentInput(
        kind=DocumentKind(payload.kind),
        partner_id=payload.partner_id,
        document_date=payload.document_date,
        description=payload.description,
        due_date=payload.due_date,
        currency_id=payload.currency_id,
        exchange_rate=payload.exchange_rate,
        branch_id=payload.branch_id,
        project_id=payload.project_id,
        payment_terms_id=payload.payment_terms_id,
        sales_rep_id=payload.sales_rep_id,
        tax_mode=payload.tax_mode,
        reference=payload.reference,
        lines=tuple(
            documents_service.LineInput(
                unit_price=line.unit_price,
                quantity=line.quantity,
                discount_percent=line.discount_percent,
                description=line.description,
                gl_account_id=line.gl_account_id,
                transaction_type=line.transaction_type,
                tax_code_id=line.tax_code_id,
                branch_id=line.branch_id,
                project_id=line.project_id,
            )
            for line in payload.lines
        ),
        amount=payload.amount,
        cash_account_id=payload.cash_account_id,
        instrument_type=payload.instrument_type,
        maturity_date=payload.maturity_date,
    )
    document, replayed = documents_service.post_document(
        db,
        auth.company_id,
        role,
        data,
        actor=auth.user,
        permissions=auth.permissions,
        idempotency_key=idempotency_key,
        idempotency_hash=fingerprint(f"{role.value}_document", payload),
        request=request,
    )
    db.commit()
    response.status_code = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED
    return _document_read(db, document)


@router.get("/{role}/documents")
def list_documents(
    role: PartnerRole = RolePath,
    partner_id: int | None = None,
    kind: DocumentKind | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    open_only: bool = False,
    cursor: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Page[DocumentSummary]:
    _require(auth, VIEW_PERMISSION, role)
    rows, next_cursor = documents_service.list_documents(
        db,
        auth.company_id,
        role=role,
        partner_id=partner_id,
        kind=kind,
        date_from=date_from,
        date_to=date_to,
        open_only=open_only,
        cursor=cursor,
        limit=limit,
    )
    return Page(
        items=[DocumentSummary.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.get("/{role}/documents/{document_id}")
def get_document(
    document_id: int,
    role: PartnerRole = RolePath,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> DocumentRead:
    _require(auth, VIEW_PERMISSION, role)
    return _document_read(
        db, documents_service.get_document(db, auth.company_id, document_id)
    )


@router.post("/{role}/documents/{document_id}/reverse", status_code=status.HTTP_201_CREATED)
def reverse_document(
    document_id: int,
    payload: ReversalRequest,
    request: Request,
    role: PartnerRole = RolePath,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> DocumentRead:
    _require(auth, POST_PERMISSION, role)
    document = documents_service.get_document(db, auth.company_id, document_id)
    documents_service.reverse_document(
        db,
        document,
        on_date=payload.on_date,
        reason=payload.reason,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return _document_read(db, document)


@router.post("/{role}/instruments/mature")
def mature_instruments(
    payload: MaturityRunRequest,
    request: Request,
    role: PartnerRole = RolePath,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> MaturityRunResult:
    """Post-dated instruments reaching maturity move from the post-dated account to the
    bank. Exposed as an endpoint and driven by a scheduled job."""
    _require(auth, POST_PERMISSION, role)
    matured = documents_service.mature_instruments(
        db, auth.company_id, as_of=payload.as_of, actor=auth.user, role=role, request=request
    )
    db.commit()
    return MaturityRunResult(
        as_of=payload.as_of,
        matured_document_ids=[document.id for document in matured],
        journal_entry_ids=[
            document.matured_entry_id for document in matured if document.matured_entry_id
        ],
    )


# --- Allocations ------------------------------------------------------------------------


def _preview_read(db: Session, preview) -> AllocationPreview:  # noqa: ANN001
    return AllocationPreview(
        currency_id=preview.currency.id,
        lines=[
            AllocationPreviewLine(
                debit_document_id=pair.debit.id,
                debit_number=pair.debit.number,
                credit_document_id=pair.credit.id,
                credit_number=pair.credit.number,
                amount=pair.amount,
                discount_amount=pair.discount_amount,
                fx_base_amount=pair.fx_base_amount,
            )
            for pair in preview.pairs
        ],
        postings=[
            AllocationPreviewPosting(
                gl_account_id=item.gl_account_id,
                description=item.description,
                base_amount=item.base_amount,
            )
            for item in preview.postings
        ],
        total_allocated=preview.total_allocated,
        total_discount=preview.total_discount,
        total_fx_base=preview.total_fx_base,
    )


@router.post("/{role}/allocations/preview")
def preview_allocation(
    payload: AllocationCreate,
    role: PartnerRole = RolePath,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> AllocationPreview:
    """Realized FX and discount postings, before Post."""
    _require(auth, VIEW_PERMISSION, role)
    preview = allocations_service.prepare(
        db,
        auth.company_id,
        role,
        partner_id=payload.partner_id,
        allocation_date=payload.allocation_date,
        pairs=[
            allocations_service.PairInput(
                debit_document_id=pair.debit_document_id,
                credit_document_id=pair.credit_document_id,
                amount=pair.amount,
                discount_amount=pair.discount_amount,
            )
            for pair in payload.pairs
        ],
    )
    return _preview_read(db, preview)


@router.post("/{role}/allocations/auto")
def auto_allocate_preview(
    payload: AutoAllocateCreate,
    role: PartnerRole = RolePath,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> AllocationPreview:
    """Oldest-first suggestion; the client posts it through `POST /allocations`."""
    _require(auth, VIEW_PERMISSION, role)
    pairs = allocations_service.auto_allocate_pairs(
        db,
        auth.company_id,
        role,
        partner_id=payload.partner_id,
        allocation_date=payload.allocation_date,
        credit_document_id=payload.credit_document_id,
    )
    preview = allocations_service.prepare(
        db,
        auth.company_id,
        role,
        partner_id=payload.partner_id,
        allocation_date=payload.allocation_date,
        pairs=pairs,
    )
    return _preview_read(db, preview)


@router.post("/{role}/allocations", status_code=status.HTTP_201_CREATED)
def post_allocation(
    payload: AllocationCreate,
    request: Request,
    response: Response,
    role: PartnerRole = RolePath,
    idempotency_key: str = IdempotencyKey,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> AllocationRead:
    _require(auth, POST_PERMISSION, role)
    allocation, replayed = allocations_service.allocate(
        db,
        auth.company_id,
        role,
        partner_id=payload.partner_id,
        allocation_date=payload.allocation_date,
        pairs=[
            allocations_service.PairInput(
                debit_document_id=pair.debit_document_id,
                credit_document_id=pair.credit_document_id,
                amount=pair.amount,
                discount_amount=pair.discount_amount,
            )
            for pair in payload.pairs
        ],
        description=payload.description,
        actor=auth.user,
        idempotency_key=idempotency_key,
        idempotency_hash=fingerprint(f"{role.value}_allocation", payload),
        request=request,
    )
    db.commit()
    response.status_code = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED
    return _allocation_read(db, allocation)


@router.post("/{role}/allocations/{allocation_id}/unallocate", status_code=status.HTTP_201_CREATED)
def unallocate(
    allocation_id: int,
    payload: ReversalRequest,
    request: Request,
    role: PartnerRole = RolePath,
    idempotency_key: str = IdempotencyKey,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> AllocationRead:
    _require(auth, POST_PERMISSION, role)
    allocation = db.get(Allocation, allocation_id)
    if allocation is None or allocation.company_id != auth.company_id:
        raise NotFoundError("Allocation not found")
    reversal = allocations_service.unallocate(
        db,
        allocation,
        on_date=payload.on_date,
        reason=payload.reason,
        actor=auth.user,
        idempotency_key=idempotency_key,
        idempotency_hash=fingerprint(f"{role.value}_unallocate_{allocation_id}", payload),
        request=request,
    )
    db.commit()
    return _allocation_read(db, reversal)


def _allocation_read(db: Session, allocation: Allocation) -> AllocationRead:
    db.refresh(allocation)
    return AllocationRead.model_validate(allocation)


@router.get("/{role}/allocations")
def list_allocations(
    role: PartnerRole = RolePath,
    partner_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[PartnerAllocationRead]:
    _require(auth, VIEW_PERMISSION, role)
    entries = enquiries_service.partner_allocations(
        db,
        auth.company_id,
        role,
        partner_id=partner_id,
        date_from=date_from,
        date_to=date_to,
    )
    return [
        PartnerAllocationRead(
            allocation_id=entry.allocation.id,
            number=entry.allocation.number,
            allocation_date=entry.allocation.allocation_date,
            debit_document_id=entry.line.debit_document_id,
            debit_number=entry.debit_number,
            credit_document_id=entry.line.credit_document_id,
            credit_number=entry.credit_number,
            amount=entry.line.amount,
            discount_amount=entry.line.discount_amount,
            fx_base_amount=entry.line.fx_base_amount,
        )
        for entry in entries
    ]


# --- Enquiries, ageing, statements ------------------------------------------------------


@router.get("/{role}/enquiry/{partner_id}")
def partner_enquiry(
    partner_id: int,
    role: PartnerRole = RolePath,
    as_of: date | None = None,
    date_from: date | None = None,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PartnerEnquiry:
    _require(auth, VIEW_PERMISSION, role)
    resolved = as_of or date.today()
    enquiry = enquiries_service.partner_enquiry(
        db, auth.company_id, role, partner_id, as_of=resolved, date_from=date_from
    )
    return PartnerEnquiry(
        role=role.value,
        partner_id=enquiry.partner.id,
        partner_name=enquiry.partner.name,
        partner_code=enquiry.partner.code_for(role),
        as_of=resolved,
        balance_base=enquiry.balance_base,
        credit_limit=enquiry.credit_limit,
        credit_available=enquiry.credit_available,
        entries=[
            PartnerEnquiryEntry(
                document_id=entry.document.id,
                number=entry.document.number,
                kind=str(entry.document.kind),
                document_date=entry.document.document_date,
                due_date=entry.document.due_date,
                currency_id=entry.document.currency_id,
                total_amount=entry.document.total_amount,
                open_amount=entry.document.open_amount,
                direction=entry.document.direction,
                journal_entry_id=entry.document.journal_entry_id,
                running_base=entry.running_base,
                status=str(entry.document.status),
            )
            for entry in enquiry.entries
        ],
        open_items=[
            OpenItemRead(
                document_id=item.document.id,
                number=item.document.number,
                kind=str(item.document.kind),
                document_date=item.document.document_date,
                due_date=item.document.due_date,
                currency_id=item.document.currency_id,
                total_amount=item.document.total_amount,
                open_amount=item.open_amount,
                open_base_amount=item.open_base_amount,
                direction=item.document.direction,
                days_overdue=(
                    max((resolved - item.document.due_date).days, 0)
                    if item.document.due_date
                    else 0
                ),
            )
            for item in enquiry.open_items
        ],
    )


@router.get("/{role}/ageing")
def age_analysis(
    role: PartnerRole = RolePath,
    as_of: date | None = None,
    bucket_set_id: int | None = None,
    partner_id: int | None = None,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> AgeingReport:
    _require(auth, VIEW_PERMISSION, role)
    resolved = as_of or date.today()
    report = ageing_service.age_analysis(
        db,
        auth.company_id,
        role,
        as_of=resolved,
        bucket_set_id=bucket_set_id,
        partner_id=partner_id,
    )
    return AgeingReport(
        role=role.value,
        as_of=resolved,
        bucket_set_id=report.bucket_set.id,
        bucket_set_code=report.bucket_set.code,
        basis=report.bucket_set.basis,
        rows=[
            AgeingRow(
                partner_id=row.partner_id,
                partner_code=row.partner_code,
                partner_name=row.partner_name,
                total=row.total,
                buckets=[
                    AgeingBucketAmount(
                        label=cell.label,
                        from_days=cell.from_days,
                        to_days=cell.to_days,
                        amount=cell.amount,
                    )
                    for cell in row.buckets
                ],
            )
            for row in report.rows
        ],
        totals=[
            AgeingBucketAmount(
                label=cell.label,
                from_days=cell.from_days,
                to_days=cell.to_days,
                amount=cell.amount,
            )
            for cell in report.totals
        ],
        grand_total=report.grand_total,
    )


@router.post("/{role}/statements", status_code=status.HTTP_202_ACCEPTED)
def queue_statement(
    payload: StatementRequest,
    background: BackgroundTasks,
    role: PartnerRole = RolePath,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> JobRead:
    """Statements are jobs: this queues one and returns immediately (decision 10)."""
    _require(auth, VIEW_PERMISSION, role)
    job = jobs_service.enqueue(
        db,
        auth.company_id,
        STATEMENT_JOB,
        {
            "role": role.value,
            "partner_ids": payload.partner_ids,
            "as_of": payload.as_of.isoformat(),
            "variant": payload.variant,
            "date_from": payload.date_from.isoformat() if payload.date_from else None,
        },
        actor=auth.user,
    )
    db.commit()
    background.add_task(jobs_service.run_job, job.id, auth.company_id, auth.user.id)
    return JobRead.model_validate(job)


@router.get("/jobs")
def list_jobs(
    kind: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[JobRead]:
    """Listings never select the artifact bytes — `Job.artifact` is a deferred column."""
    _require_any(auth, permissions.AR_REPORTS_VIEW, permissions.AP_REPORTS_VIEW)
    return [
        JobRead.model_validate(job)
        for job in jobs_service.list_jobs(db, auth.company_id, kind=kind, limit=limit)
    ]


@router.post("/jobs/sweep")
def sweep_jobs(
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> JobSweepResult:
    """Retention + reaper: fails jobs abandoned by a restarted process and deletes expired
    ones. Runs on every enqueue as well; this endpoint is for the scheduler.

    It **deletes rows**, so it is gated on the setup permissions, not the reports ones: a
    clerk who may read a statement may not reap other people's jobs."""
    _require_any(auth, permissions.AR_SETUP_MANAGE, permissions.AP_SETUP_MANAGE)
    abandoned, deleted = jobs_service.sweep(db, auth.company_id)
    db.commit()
    return JobSweepResult(abandoned=abandoned, deleted=deleted)


@router.get("/jobs/{job_id}")
def get_job(
    job_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> JobRead:
    _require_any(auth, permissions.AR_REPORTS_VIEW, permissions.AP_REPORTS_VIEW)
    return JobRead.model_validate(jobs_service.get_job(db, auth.company_id, job_id))


@router.get("/jobs/{job_id}/artifact")
def download_artifact(
    job_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> RawResponse:
    _require_any(auth, permissions.AR_REPORTS_VIEW, permissions.AP_REPORTS_VIEW)
    job = jobs_service.get_job(db, auth.company_id, job_id)
    if job.status != JobStatus.SUCCEEDED:
        raise ConflictError("The job has not produced an artifact yet", code="job_not_ready")
    artifact = jobs_service.load_artifact(db, auth.company_id, job_id)
    if artifact is None:
        raise ConflictError("The job has not produced an artifact yet", code="job_not_ready")
    return RawResponse(
        content=artifact,
        media_type=job.artifact_content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{job.artifact_name}"'},
    )
