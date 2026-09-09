# Import all model modules here so Base.metadata sees every table (P1+).
from app.db import Base
from app.models.audit import AuditLog
from app.models.company import Branch, Company, CompanyStatus
from app.models.currency import Currency, ExchangeRate
from app.models.fiscal import AccountingPeriod, FiscalYear, PeriodStatus
from app.models.gl import (
    AccountClass,
    ControlType,
    GLAccount,
    GLSettings,
    GLTransactionType,
    Project,
)
from app.models.job import Job, JobStatus
from app.models.journal import (
    DocumentSequence,
    JournalEntry,
    JournalLine,
    JournalStatus,
    PeriodBalance,
)
from app.models.membership import CompanyMembership, MembershipRole, MembershipStatus, Role
from app.models.partner import (
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
from app.models.subledger import (
    Allocation,
    AllocationLine,
    DocumentKind,
    DocumentStatus,
    InstrumentType,
    PartnerDocument,
    PartnerDocumentLine,
)
from app.models.tax import TaxCode, TaxNature
from app.models.user import RefreshToken, User, UserToken, UserTokenPurpose

__all__ = [
    "AccountClass",
    "AccountingPeriod",
    "AgeingBasis",
    "AgeingBucket",
    "AgeingBucketSet",
    "Allocation",
    "AllocationLine",
    "AuditLog",
    "Base",
    "Branch",
    "Company",
    "CompanyMembership",
    "CompanyStatus",
    "ControlType",
    "Currency",
    "DocumentSequence",
    "DocumentKind",
    "DocumentStatus",
    "DueBasis",
    "ExchangeRate",
    "FiscalYear",
    "GLAccount",
    "GLSettings",
    "GLTransactionType",
    "InstrumentType",
    "Job",
    "JobStatus",
    "JournalEntry",
    "JournalLine",
    "JournalStatus",
    "MembershipRole",
    "MembershipStatus",
    "Partner",
    "PartnerApSettings",
    "PartnerArSettings",
    "PartnerContact",
    "PartnerDocument",
    "PartnerDocumentLine",
    "PartnerRole",
    "PaymentTerms",
    "PeriodBalance",
    "PeriodStatus",
    "Project",
    "RefreshToken",
    "Role",
    "SalesRep",
    "TaxCode",
    "TaxMode",
    "TaxNature",
    "User",
    "UserToken",
    "UserTokenPurpose",
]
