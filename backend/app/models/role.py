from sqlalchemy import Column, String, Text, JSON, Boolean, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.models.base import BaseModel
from app.models.user import user_roles

class Role(BaseModel):
    """Role model (REQ-SYS-RBAC-*)"""
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint('name', 'company_id', name='_role_company_uc'),
    )
    
    name = Column(String(100), nullable=False)
    description = Column(Text)
    permissions = Column(JSON, nullable=False, default=list)
    company_id = Column(Integer, ForeignKey('companies.id', ondelete='CASCADE'))
    is_system = Column(Boolean, default=False)
    
    # Relationships
    company = relationship("Company", back_populates="roles")
    users = relationship(
        "User", 
        secondary=user_roles, 
        back_populates="roles",
        primaryjoin="Role.id == user_roles.c.role_id",
        secondaryjoin="User.id == user_roles.c.user_id"
    )
