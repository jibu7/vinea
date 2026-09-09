"""AR/AP masters: partners and their per-role settings, contacts, sales reps, payment terms
and ageing bucket sets.

Every function takes a `PartnerRole` rather than living in a customer module and a supplier
copy. Masters referenced by posted documents are deactivated, never deleted.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import ConflictError, NotFoundError
from app.kernel.errors import LedgerStateError
from app.models.partner import (
    ROLE_SETTINGS,
    AgeingBasis,
    AgeingBucket,
    AgeingBucketSet,
    DueBasis,
    Partner,
    PartnerApSettings,
    PartnerArSettings,
    PartnerContact,
    PartnerRole,
    PaymentTerms,
    SalesRep,
    TaxMode,
)
from app.models.user import User
from app.subledger.common import audit

RoleSettings = PartnerArSettings | PartnerApSettings

DEFAULT_AGEING_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("Current", 0, 30),
    ("31 - 60", 31, 60),
    ("61 - 90", 61, 90),
    ("91 - 120", 91, 120),
    ("120+", 121, None),
)


# --- Partners ------------------------------------------------------------------------------


@dataclass(frozen=True)
class PartnerInput:
    name: str
    customer_code: str | None = None
    supplier_code: str | None = None
    tin: str | None = None
    email: str | None = None
    phone: str | None = None
    address: dict | None = None
    notes: str | None = None
    currency_id: int | None = None


def list_partners(
    db: Session,
    company_id: int,
    *,
    role: PartnerRole | None = None,
    search: str | None = None,
    include_inactive: bool = False,
) -> list[Partner]:
    statement = select(Partner).where(Partner.company_id == company_id)
    if role == PartnerRole.AR:
        statement = statement.where(Partner.is_customer)
    elif role == PartnerRole.AP:
        statement = statement.where(Partner.is_supplier)
    if search:
        pattern = f"%{search}%"
        statement = statement.where(
            Partner.name.ilike(pattern)
            | Partner.customer_code.ilike(pattern)
            | Partner.supplier_code.ilike(pattern)
        )
    if not include_inactive:
        statement = statement.where(Partner.is_active)
    return list(db.scalars(statement.order_by(Partner.name)))


def get_partner(db: Session, company_id: int, partner_id: int) -> Partner:
    partner = db.get(Partner, partner_id)
    if partner is None or partner.company_id != company_id:
        raise NotFoundError("Partner not found")
    return partner


def _assert_code_free(db: Session, company_id: int, field: str, code: str | None) -> None:
    if code is None:
        return
    column = Partner.customer_code if field == "customer_code" else Partner.supplier_code
    clash = db.scalar(select(Partner.id).where(Partner.company_id == company_id, column == code))
    if clash is not None:
        raise ConflictError(
            f"{code} is already in use", code="partner_code_taken", field_errors={field: ["taken"]}
        )


def create_partner(
    db: Session, company_id: int, data: PartnerInput, *, actor: User, request: Request | None = None
) -> Partner:
    if data.customer_code is None and data.supplier_code is None:
        raise LedgerStateError(
            "A partner needs a customer code, a supplier code, or both",
            code="partner_role_required",
        )
    _assert_code_free(db, company_id, "customer_code", data.customer_code)
    _assert_code_free(db, company_id, "supplier_code", data.supplier_code)
    partner = Partner(
        company_id=company_id,
        name=data.name,
        customer_code=data.customer_code,
        supplier_code=data.supplier_code,
        is_customer=data.customer_code is not None,
        is_supplier=data.supplier_code is not None,
        tin=data.tin,
        email=data.email,
        phone=data.phone,
        address=data.address,
        notes=data.notes,
        currency_id=data.currency_id,
        is_active=True,
    )
    db.add(partner)
    db.flush()
    audit(
        db,
        company_id,
        "partner.created",
        "partners",
        partner.id,
        actor=actor,
        after={
            "name": partner.name,
            "customer_code": partner.customer_code,
            "supplier_code": partner.supplier_code,
        },
        request=request,
    )
    return partner


def update_partner(
    db: Session,
    partner: Partner,
    *,
    name: str | None = None,
    customer_code: str | None | object = ...,
    supplier_code: str | None | object = ...,
    tin: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    address: dict | None = None,
    notes: str | None = None,
    currency_id: int | None | object = ...,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> Partner:
    """Codes are renameable — history hangs off `partner_id`, never off the code (the
    "Rename customer/supplier" screen in Appendix C)."""
    before = {
        "name": partner.name,
        "customer_code": partner.customer_code,
        "supplier_code": partner.supplier_code,
        "is_active": partner.is_active,
    }
    if name is not None:
        partner.name = name
    for field, value in (("customer_code", customer_code), ("supplier_code", supplier_code)):
        if value is ...:
            continue
        if value is not None and value != getattr(partner, field):
            _assert_code_free(db, partner.company_id, field, str(value))
        setattr(partner, field, value)
    partner.is_customer = partner.customer_code is not None
    partner.is_supplier = partner.supplier_code is not None
    if not partner.is_customer and not partner.is_supplier:
        raise LedgerStateError(
            "A partner needs a customer code, a supplier code, or both",
            code="partner_role_required",
        )
    if tin is not None:
        partner.tin = tin
    if email is not None:
        partner.email = email
    if phone is not None:
        partner.phone = phone
    if address is not None:
        partner.address = address
    if notes is not None:
        partner.notes = notes
    if currency_id is not ...:
        partner.currency_id = currency_id  # type: ignore[assignment]
    if is_active is not None:
        partner.is_active = is_active
    db.flush()
    after = {
        "name": partner.name,
        "customer_code": partner.customer_code,
        "supplier_code": partner.supplier_code,
        "is_active": partner.is_active,
    }
    if after != before:
        renamed = (
            after["customer_code"] != before["customer_code"]
            or after["supplier_code"] != before["supplier_code"]
        )
        audit(
            db,
            partner.company_id,
            "partner.renamed" if renamed else "partner.updated",
            "partners",
            partner.id,
            actor=actor,
            before=before,
            after=after,
            request=request,
        )
    return partner


# --- Per-role settings -----------------------------------------------------------------------


@dataclass(frozen=True)
class RoleSettingsInput:
    control_account_id: int | None = None
    payment_terms_id: int | None = None
    credit_limit: Decimal | None = None
    sales_rep_id: int | None = None
    default_tax_code_id: int | None = None
    default_branch_id: int | None = None
    default_project_id: int | None = None
    default_gl_account_id: int | None = None
    tax_mode: TaxMode = TaxMode.EXCLUSIVE
    is_on_hold: bool = False


def get_role_settings(
    db: Session, company_id: int, partner_id: int, role: PartnerRole
) -> RoleSettings | None:
    model = ROLE_SETTINGS[role]
    return db.scalar(
        select(model).where(model.company_id == company_id, model.partner_id == partner_id)  # type: ignore[attr-defined]
    )


def role_settings_or_default(
    db: Session, company_id: int, partner_id: int, role: PartnerRole
) -> RoleSettings:
    """Absent settings behave as "all defaults" — a partner is usable the moment it exists."""
    existing = get_role_settings(db, company_id, partner_id, role)
    if existing is not None:
        return existing
    model = ROLE_SETTINGS[role]
    return model(company_id=company_id, partner_id=partner_id, tax_mode=TaxMode.EXCLUSIVE)  # type: ignore[operator]


def upsert_role_settings(
    db: Session,
    company_id: int,
    partner: Partner,
    role: PartnerRole,
    data: RoleSettingsInput,
    *,
    actor: User,
    request: Request | None = None,
) -> RoleSettings:
    if not partner.has_role(role):
        raise LedgerStateError(
            f"Partner {partner.name} does not have the {role.value.upper()} role",
            code="partner_role_missing",
        )
    if role == PartnerRole.AP and data.sales_rep_id is not None:
        raise LedgerStateError(
            "Sales representatives apply to customers only", code="sales_rep_not_applicable"
        )
    model = ROLE_SETTINGS[role]
    settings = get_role_settings(db, company_id, partner.id, role)
    before = None if settings is None else _settings_snapshot(settings)
    if settings is None:
        settings = model(company_id=company_id, partner_id=partner.id)  # type: ignore[operator]
        db.add(settings)
    for field in (
        "control_account_id",
        "payment_terms_id",
        "credit_limit",
        "sales_rep_id",
        "default_tax_code_id",
        "default_branch_id",
        "default_project_id",
        "default_gl_account_id",
        "tax_mode",
        "is_on_hold",
    ):
        setattr(settings, field, getattr(data, field))
    db.flush()
    audit(
        db,
        company_id,
        f"partner_{role.value}_settings.updated",
        f"partner_{role.value}_settings",
        settings.id,
        actor=actor,
        before=before,
        after=_settings_snapshot(settings),
        request=request,
    )
    return settings


def _settings_snapshot(settings: RoleSettings) -> dict:
    return {
        "control_account_id": settings.control_account_id,
        "payment_terms_id": settings.payment_terms_id,
        "credit_limit": None if settings.credit_limit is None else str(settings.credit_limit),
        "sales_rep_id": settings.sales_rep_id,
        "default_tax_code_id": settings.default_tax_code_id,
        "default_branch_id": settings.default_branch_id,
        "default_project_id": settings.default_project_id,
        "default_gl_account_id": settings.default_gl_account_id,
        "tax_mode": str(settings.tax_mode),
        "is_on_hold": settings.is_on_hold,
    }


# --- Contacts ------------------------------------------------------------------------------


def list_contacts(db: Session, company_id: int, partner_id: int) -> list[PartnerContact]:
    return list(
        db.scalars(
            select(PartnerContact)
            .where(
                PartnerContact.company_id == company_id, PartnerContact.partner_id == partner_id
            )
            .order_by(PartnerContact.is_primary.desc(), PartnerContact.name)
        )
    )


def get_contact(db: Session, company_id: int, contact_id: int) -> PartnerContact:
    contact = db.get(PartnerContact, contact_id)
    if contact is None or contact.company_id != company_id:
        raise NotFoundError("Contact not found")
    return contact


def create_contact(
    db: Session,
    company_id: int,
    partner: Partner,
    *,
    name: str,
    role: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    notes: str | None = None,
    is_primary: bool = False,
    actor: User,
    request: Request | None = None,
) -> PartnerContact:
    if is_primary:
        _clear_primary(db, company_id, partner.id)
    contact = PartnerContact(
        company_id=company_id,
        partner_id=partner.id,
        name=name,
        role=role,
        email=email,
        phone=phone,
        notes=notes,
        is_primary=is_primary,
        is_active=True,
    )
    db.add(contact)
    db.flush()
    audit(
        db,
        company_id,
        "partner_contact.created",
        "partner_contacts",
        contact.id,
        actor=actor,
        after={"partner_id": partner.id, "name": name},
        request=request,
    )
    return contact


def update_contact(
    db: Session,
    contact: PartnerContact,
    *,
    name: str | None = None,
    role: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    notes: str | None = None,
    is_primary: bool | None = None,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> PartnerContact:
    if is_primary:
        _clear_primary(db, contact.company_id, contact.partner_id)
    for field, value in (
        ("name", name),
        ("role", role),
        ("email", email),
        ("phone", phone),
        ("notes", notes),
        ("is_primary", is_primary),
        ("is_active", is_active),
    ):
        if value is not None:
            setattr(contact, field, value)
    db.flush()
    audit(
        db,
        contact.company_id,
        "partner_contact.updated",
        "partner_contacts",
        contact.id,
        actor=actor,
        after={"name": contact.name, "is_active": contact.is_active},
        request=request,
    )
    return contact


def _clear_primary(db: Session, company_id: int, partner_id: int) -> None:
    for other in db.scalars(
        select(PartnerContact).where(
            PartnerContact.company_id == company_id,
            PartnerContact.partner_id == partner_id,
            PartnerContact.is_primary,
        )
    ):
        other.is_primary = False


# --- Sales representatives -------------------------------------------------------------------


def list_sales_reps(
    db: Session, company_id: int, *, include_inactive: bool = False
) -> list[SalesRep]:
    statement = select(SalesRep).where(SalesRep.company_id == company_id)
    if not include_inactive:
        statement = statement.where(SalesRep.is_active)
    return list(db.scalars(statement.order_by(SalesRep.code)))


def get_sales_rep(db: Session, company_id: int, rep_id: int) -> SalesRep:
    rep = db.get(SalesRep, rep_id)
    if rep is None or rep.company_id != company_id:
        raise NotFoundError("Sales representative not found")
    return rep


def create_sales_rep(
    db: Session,
    company_id: int,
    *,
    code: str,
    name: str,
    email: str | None = None,
    actor: User,
    request: Request | None = None,
) -> SalesRep:
    clash = db.scalar(
        select(SalesRep.id).where(SalesRep.company_id == company_id, SalesRep.code == code)
    )
    if clash is not None:
        raise ConflictError(
            f"Sales representative {code} already exists",
            code="sales_rep_code_taken",
            field_errors={"code": ["already in use"]},
        )
    rep = SalesRep(company_id=company_id, code=code, name=name, email=email, is_active=True)
    db.add(rep)
    db.flush()
    audit(
        db,
        company_id,
        "sales_rep.created",
        "sales_reps",
        rep.id,
        actor=actor,
        after={"code": code, "name": name},
        request=request,
    )
    return rep


def update_sales_rep(
    db: Session,
    rep: SalesRep,
    *,
    name: str | None = None,
    email: str | None = None,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> SalesRep:
    for field, value in (("name", name), ("email", email), ("is_active", is_active)):
        if value is not None:
            setattr(rep, field, value)
    db.flush()
    audit(
        db,
        rep.company_id,
        "sales_rep.updated",
        "sales_reps",
        rep.id,
        actor=actor,
        after={"name": rep.name, "is_active": rep.is_active},
        request=request,
    )
    return rep


# --- Payment terms ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaymentTermsInput:
    code: str
    name: str
    due_basis: DueBasis = DueBasis.DAYS_FROM_DOCUMENT_DATE
    due_days: int = 0
    due_day_of_month: int | None = None
    discount_percent: Decimal = Decimal(0)
    discount_days: int = 0


def list_payment_terms(
    db: Session, company_id: int, *, include_inactive: bool = False
) -> list[PaymentTerms]:
    statement = select(PaymentTerms).where(PaymentTerms.company_id == company_id)
    if not include_inactive:
        statement = statement.where(PaymentTerms.is_active)
    return list(db.scalars(statement.order_by(PaymentTerms.code)))


def get_payment_terms(db: Session, company_id: int, terms_id: int) -> PaymentTerms:
    terms = db.get(PaymentTerms, terms_id)
    if terms is None or terms.company_id != company_id:
        raise NotFoundError("Payment terms not found")
    return terms


def _validate_terms(data: PaymentTermsInput) -> None:
    if data.due_basis == DueBasis.FIXED_DAY_OF_MONTH and not (
        data.due_day_of_month and 1 <= data.due_day_of_month <= 31
    ):
        raise LedgerStateError(
            "A fixed-day-of-month basis needs a day between 1 and 31",
            code="due_day_required",
        )
    if not (Decimal(0) <= data.discount_percent < Decimal(100)):
        raise LedgerStateError(
            "The settlement discount must be at least 0% and below 100%",
            code="invalid_discount_percent",
        )


def create_payment_terms(
    db: Session,
    company_id: int,
    data: PaymentTermsInput,
    *,
    actor: User,
    request: Request | None = None,
) -> PaymentTerms:
    _validate_terms(data)
    clash = db.scalar(
        select(PaymentTerms.id).where(
            PaymentTerms.company_id == company_id, PaymentTerms.code == data.code
        )
    )
    if clash is not None:
        raise ConflictError(
            f"Payment terms {data.code} already exist",
            code="payment_terms_code_taken",
            field_errors={"code": ["already in use"]},
        )
    terms = PaymentTerms(
        company_id=company_id,
        code=data.code,
        name=data.name,
        due_basis=data.due_basis,
        due_days=data.due_days,
        due_day_of_month=data.due_day_of_month,
        discount_percent=data.discount_percent,
        discount_days=data.discount_days,
        is_active=True,
    )
    db.add(terms)
    db.flush()
    audit(
        db,
        company_id,
        "payment_terms.created",
        "payment_terms",
        terms.id,
        actor=actor,
        after={"code": data.code, "due_basis": str(data.due_basis)},
        request=request,
    )
    return terms


def update_payment_terms(
    db: Session,
    terms: PaymentTerms,
    data: PaymentTermsInput,
    *,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> PaymentTerms:
    _validate_terms(data)
    before = {
        "due_basis": str(terms.due_basis),
        "due_days": terms.due_days,
        "discount_percent": str(terms.discount_percent),
        "discount_days": terms.discount_days,
    }
    terms.name = data.name
    terms.due_basis = data.due_basis
    terms.due_days = data.due_days
    terms.due_day_of_month = data.due_day_of_month
    terms.discount_percent = data.discount_percent
    terms.discount_days = data.discount_days
    if is_active is not None:
        terms.is_active = is_active
    db.flush()
    audit(
        db,
        terms.company_id,
        "payment_terms.updated",
        "payment_terms",
        terms.id,
        actor=actor,
        before=before,
        after={
            "due_basis": str(terms.due_basis),
            "due_days": terms.due_days,
            "discount_percent": str(terms.discount_percent),
            "discount_days": terms.discount_days,
        },
        request=request,
    )
    return terms


def due_date_for(terms: PaymentTerms | None, document_date: date) -> date:
    return document_date if terms is None else terms.due_date(document_date)


# --- Ageing bucket sets ----------------------------------------------------------------------


@dataclass(frozen=True)
class BucketInput:
    label: str
    from_days: int
    to_days: int | None


def list_bucket_sets(
    db: Session, company_id: int, *, include_inactive: bool = False
) -> list[AgeingBucketSet]:
    statement = select(AgeingBucketSet).where(AgeingBucketSet.company_id == company_id)
    if not include_inactive:
        statement = statement.where(AgeingBucketSet.is_active)
    return list(db.scalars(statement.order_by(AgeingBucketSet.code)))


def get_bucket_set(db: Session, company_id: int, set_id: int) -> AgeingBucketSet:
    bucket_set = db.get(AgeingBucketSet, set_id)
    if bucket_set is None or bucket_set.company_id != company_id:
        raise NotFoundError("Ageing bucket set not found")
    return bucket_set


def default_bucket_set(db: Session, company_id: int) -> AgeingBucketSet:
    bucket_set = db.scalar(
        select(AgeingBucketSet).where(
            AgeingBucketSet.company_id == company_id, AgeingBucketSet.is_default
        )
    )
    if bucket_set is None:
        raise NotFoundError("No default ageing bucket set is configured")
    return bucket_set


def buckets_of(db: Session, bucket_set: AgeingBucketSet) -> list[AgeingBucket]:
    return list(
        db.scalars(
            select(AgeingBucket)
            .where(
                AgeingBucket.company_id == bucket_set.company_id,
                AgeingBucket.bucket_set_id == bucket_set.id,
            )
            .order_by(AgeingBucket.sequence)
        )
    )


def _validate_buckets(buckets: list[BucketInput]) -> None:
    if not buckets:
        raise LedgerStateError("An ageing bucket set needs at least one bucket", code="no_buckets")
    if buckets[0].from_days != 0:
        raise LedgerStateError("The first ageing bucket must start at 0 days", code="bucket_gap")
    if buckets[-1].to_days is not None:
        raise LedgerStateError(
            "The last ageing bucket must be open-ended (no upper bound)", code="bucket_not_open"
        )
    for previous, current in zip(buckets, buckets[1:], strict=False):
        if previous.to_days is None or current.from_days != previous.to_days + 1:
            raise LedgerStateError(
                "Ageing buckets must be contiguous and non-overlapping", code="bucket_gap"
            )


def create_bucket_set(
    db: Session,
    company_id: int,
    *,
    code: str,
    name: str,
    basis: AgeingBasis,
    buckets: list[BucketInput],
    is_default: bool = False,
    actor: User,
    request: Request | None = None,
) -> AgeingBucketSet:
    _validate_buckets(buckets)
    clash = db.scalar(
        select(AgeingBucketSet.id).where(
            AgeingBucketSet.company_id == company_id, AgeingBucketSet.code == code
        )
    )
    if clash is not None:
        raise ConflictError(
            f"Ageing bucket set {code} already exists",
            code="bucket_set_code_taken",
            field_errors={"code": ["already in use"]},
        )
    if is_default:
        _clear_default_bucket_set(db, company_id)
    bucket_set = AgeingBucketSet(
        company_id=company_id,
        code=code,
        name=name,
        basis=basis,
        is_default=is_default,
        is_active=True,
    )
    db.add(bucket_set)
    db.flush()
    _replace_buckets(db, bucket_set, buckets)
    audit(
        db,
        company_id,
        "ageing_bucket_set.created",
        "ageing_bucket_sets",
        bucket_set.id,
        actor=actor,
        after={"code": code, "basis": str(basis), "buckets": len(buckets)},
        request=request,
    )
    return bucket_set


def update_bucket_set(
    db: Session,
    bucket_set: AgeingBucketSet,
    *,
    name: str | None = None,
    basis: AgeingBasis | None = None,
    buckets: list[BucketInput] | None = None,
    is_default: bool | None = None,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> AgeingBucketSet:
    if buckets is not None:
        _validate_buckets(buckets)
        _replace_buckets(db, bucket_set, buckets)
    if name is not None:
        bucket_set.name = name
    if basis is not None:
        bucket_set.basis = basis
    if is_default:
        _clear_default_bucket_set(db, bucket_set.company_id)
        bucket_set.is_default = True
    if is_active is not None:
        if not is_active and bucket_set.is_default:
            raise LedgerStateError(
                "The default ageing bucket set cannot be deactivated",
                code="default_bucket_set_required",
            )
        bucket_set.is_active = is_active
    db.flush()
    audit(
        db,
        bucket_set.company_id,
        "ageing_bucket_set.updated",
        "ageing_bucket_sets",
        bucket_set.id,
        actor=actor,
        after={"basis": str(bucket_set.basis), "is_default": bucket_set.is_default},
        request=request,
    )
    return bucket_set


def _clear_default_bucket_set(db: Session, company_id: int) -> None:
    for other in db.scalars(
        select(AgeingBucketSet).where(
            AgeingBucketSet.company_id == company_id, AgeingBucketSet.is_default
        )
    ):
        other.is_default = False
    db.flush()


def _replace_buckets(db: Session, bucket_set: AgeingBucketSet, buckets: list[BucketInput]) -> None:
    for existing in buckets_of(db, bucket_set):
        db.delete(existing)
    db.flush()
    db.add_all(
        [
            AgeingBucket(
                company_id=bucket_set.company_id,
                bucket_set_id=bucket_set.id,
                sequence=index,
                label=bucket.label,
                from_days=bucket.from_days,
                to_days=bucket.to_days,
            )
            for index, bucket in enumerate(buckets)
        ]
    )
    db.flush()
