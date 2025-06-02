from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas.oe_document_type import OEDocumentType, OEDocumentTypeCreate, OEDocumentTypeUpdate
from app.services.oe_document_type_service import OEDocumentTypeService

router = APIRouter()


@router.post("/", response_model=OEDocumentType)
def create_document_type(
    doc_type_data: OEDocumentTypeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new OE document type"""
    return OEDocumentTypeService.create_document_type(db, doc_type_data)


@router.get("/", response_model=List[OEDocumentType])
def get_document_types(
    document_class: Optional[str] = Query(None, description="Filter by document class (SALES or PURCHASE)"),
    transaction_type: Optional[str] = Query(None, description="Filter by transaction type"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get list of OE document types"""
    return OEDocumentTypeService.get_document_types(
        db, document_class, transaction_type, skip, limit
    )


@router.get("/{doc_type_id}", response_model=OEDocumentType)
def get_document_type(
    doc_type_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get a specific OE document type"""
    return OEDocumentTypeService.get_document_type(db, doc_type_id)


@router.get("/code/{code}", response_model=OEDocumentType)
def get_document_type_by_code(
    code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get a specific OE document type by code"""
    return OEDocumentTypeService.get_document_type_by_code(db, code)


@router.put("/{doc_type_id}", response_model=OEDocumentType)
def update_document_type(
    doc_type_id: int,
    doc_type_update: OEDocumentTypeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update an OE document type"""
    return OEDocumentTypeService.update_document_type(db, doc_type_id, doc_type_update)


@router.delete("/{doc_type_id}")
def delete_document_type(
    doc_type_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete an OE document type"""
    OEDocumentTypeService.delete_document_type(db, doc_type_id)
    return {"message": "Document type deleted successfully"} 