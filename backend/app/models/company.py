from sqlalchemy import Column, String, Text, JSON
from sqlalchemy.orm import relationship
from app.models.base import BaseModel

class Company(BaseModel):
    """Company model (REQ-SYS-COMP-*)"""
    __tablename__ = "companies"
    
    name = Column(String(255), nullable=False)
    address = Column(Text)
    contact_info = Column(JSON)
    settings = Column(JSON, default={})
    
    # Relationships
    users = relationship("User", back_populates="company", cascade="all, delete-orphan")
    roles = relationship("Role", back_populates="company", cascade="all, delete-orphan")
    accounting_periods = relationship("AccountingPeriod", back_populates="company", cascade="all, delete-orphan")
    customers = relationship("Customer", back_populates="company", cascade="all, delete-orphan")
