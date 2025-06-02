from sqlalchemy.orm import Session
from typing import List, Optional
from fastapi import HTTPException, status
from app.models import OEDocumentType
from app.schemas.oe_document_type import OEDocumentTypeCreate, OEDocumentTypeUpdate


class OEDocumentTypeService:
    
    @staticmethod
    def create_document_type(db: Session, doc_type_data: OEDocumentTypeCreate) -> OEDocumentType:
        # Check if code already exists
        existing = db.query(OEDocumentType).filter(
            OEDocumentType.code == doc_type_data.code
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Document type with code {doc_type_data.code} already exists"
            )
        
        # Validate document class and transaction type
        valid_classes = ['SALES', 'PURCHASE']
        valid_types = ['ORDER', 'INVOICE', 'CREDIT_NOTE', 'GRV']
        
        if doc_type_data.document_class not in valid_classes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid document class. Must be one of: {valid_classes}"
            )
        
        if doc_type_data.transaction_type not in valid_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid transaction type. Must be one of: {valid_types}"
            )
        
        # Create document type
        doc_type = OEDocumentType(**doc_type_data.dict())
        db.add(doc_type)
        db.commit()
        db.refresh(doc_type)
        return doc_type
    
    @staticmethod
    def get_document_type(db: Session, doc_type_id: int) -> OEDocumentType:
        doc_type = db.query(OEDocumentType).filter(OEDocumentType.id == doc_type_id).first()
        if not doc_type:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document type not found"
            )
        return doc_type
    
    @staticmethod
    def get_document_type_by_code(db: Session, code: str) -> OEDocumentType:
        doc_type = db.query(OEDocumentType).filter(OEDocumentType.code == code).first()
        if not doc_type:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document type not found"
            )
        return doc_type
    
    @staticmethod
    def get_document_types(
        db: Session,
        document_class: Optional[str] = None,
        transaction_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[OEDocumentType]:
        query = db.query(OEDocumentType)
        
        if document_class:
            query = query.filter(OEDocumentType.document_class == document_class)
        
        if transaction_type:
            query = query.filter(OEDocumentType.transaction_type == transaction_type)
        
        return query.offset(skip).limit(limit).all()
    
    @staticmethod
    def update_document_type(
        db: Session,
        doc_type_id: int,
        doc_type_update: OEDocumentTypeUpdate
    ) -> OEDocumentType:
        doc_type = OEDocumentTypeService.get_document_type(db, doc_type_id)
        
        update_data = doc_type_update.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(doc_type, field, value)
        
        db.commit()
        db.refresh(doc_type)
        return doc_type
    
    @staticmethod
    def delete_document_type(db: Session, doc_type_id: int) -> None:
        doc_type = OEDocumentTypeService.get_document_type(db, doc_type_id)
        
        # Check if document type is in use
        # TODO: Add checks for sales orders, purchase orders, etc.
        
        db.delete(doc_type)
        db.commit() 