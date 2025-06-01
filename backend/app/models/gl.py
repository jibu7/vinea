from sqlalchemy import Column, Integer, String, ForeignKey, DECIMAL, Date, Text, Boolean, func, CheckConstraint
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.base import BaseModel
from .accounting_period import AccountingPeriod
from .user import User
from .company import Company

class GLAccount(BaseModel):
    __tablename__ = "gl_accounts"

    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    account_code = Column(String(20), nullable=False)
    account_name = Column(String(255), nullable=False)
    account_type = Column(String(20), nullable=False)  # ASSET, LIABILITY, EQUITY, INCOME, EXPENSE
    parent_account_id = Column(Integer, ForeignKey("gl_accounts.id"), nullable=True)
    current_balance = Column(DECIMAL(15, 2), default=0.00, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_system_account = Column(Boolean, default=False, nullable=False)

    company = relationship("Company")
    parent_account = relationship("GLAccount", remote_side="GLAccount.id")

    __table_args__ = (
        CheckConstraint(account_type.in_(['ASSET', 'LIABILITY', 'EQUITY', 'INCOME', 'EXPENSE']), name='ck_gl_account_type'),
    )

class GLTransaction(BaseModel):
    __tablename__ = "gl_transactions"

    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    journal_entry_id = Column(String(50), nullable=False)
    account_id = Column(Integer, ForeignKey("gl_accounts.id"), nullable=False)
    transaction_date = Column(Date, nullable=False)
    period_id = Column(Integer, ForeignKey("accounting_periods.id"), nullable=True)
    description = Column(Text, nullable=True)
    debit_amount = Column(DECIMAL(15, 2), default=0.00, nullable=False)
    credit_amount = Column(DECIMAL(15, 2), default=0.00, nullable=False)
    reference = Column(String(100), nullable=True)
    source_module = Column(String(20), nullable=True)
    source_document_id = Column(Integer, nullable=True)
    posted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    is_reversed = Column(Boolean, default=False, nullable=False)

    company = relationship("Company")
    account = relationship("GLAccount")
    accounting_period = relationship("AccountingPeriod")
    posted_by_user = relationship("User")

    __table_args__ = (
        CheckConstraint(
            (debit_amount == 0.00) & (credit_amount > 0.00) |
            (debit_amount > 0.00) & (credit_amount == 0.00),
            name='ck_gl_transaction_amounts'
        ),
    ) 