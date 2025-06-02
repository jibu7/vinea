"""Initialize default Order Entry document types and configurations"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models import OEDocumentType, ARTransactionType, APTransactionType
from datetime import datetime


def init_oe_document_types(db: Session):
    """Create default OE document types"""
    print("Creating OE document types...")
    
    # Get AR/AP transaction types
    ar_invoice_type = db.query(ARTransactionType).filter(
        ARTransactionType.code == "INV"
    ).first()
    
    ap_invoice_type = db.query(APTransactionType).filter(
        APTransactionType.code == "SINV"
    ).first()
    
    document_types = [
        # Sales document types
        {
            "code": "SO",
            "name": "Sales Order",
            "document_class": "SALES",
            "transaction_type": "ORDER",
            "ar_transaction_type_id": None,
            "updates_inventory": False,
            "creates_ar_transaction": False,
            "creates_ap_transaction": False
        },
        {
            "code": "SI",
            "name": "Sales Invoice",
            "document_class": "SALES",
            "transaction_type": "INVOICE",
            "ar_transaction_type_id": ar_invoice_type.id if ar_invoice_type else None,
            "updates_inventory": True,
            "creates_ar_transaction": True,
            "creates_ap_transaction": False
        },
        {
            "code": "SCN",
            "name": "Sales Credit Note",
            "document_class": "SALES",
            "transaction_type": "CREDIT_NOTE",
            "ar_transaction_type_id": None,  # Should link to credit note type
            "updates_inventory": True,
            "creates_ar_transaction": True,
            "creates_ap_transaction": False
        },
        # Purchase document types
        {
            "code": "PO",
            "name": "Purchase Order",
            "document_class": "PURCHASE",
            "transaction_type": "ORDER",
            "ap_transaction_type_id": None,
            "updates_inventory": False,
            "creates_ar_transaction": False,
            "creates_ap_transaction": False
        },
        {
            "code": "GRV",
            "name": "Goods Received Voucher",
            "document_class": "PURCHASE",
            "transaction_type": "GRV",
            "ap_transaction_type_id": ap_invoice_type.id if ap_invoice_type else None,
            "updates_inventory": True,
            "creates_ar_transaction": False,
            "creates_ap_transaction": False
        },
        {
            "code": "PI",
            "name": "Purchase Invoice",
            "document_class": "PURCHASE",
            "transaction_type": "INVOICE",
            "ap_transaction_type_id": ap_invoice_type.id if ap_invoice_type else None,
            "updates_inventory": False,
            "creates_ar_transaction": False,
            "creates_ap_transaction": True
        },
        {
            "code": "PCN",
            "name": "Purchase Credit Note",
            "document_class": "PURCHASE",
            "transaction_type": "CREDIT_NOTE",
            "ap_transaction_type_id": None,  # Should link to credit note type
            "updates_inventory": False,
            "creates_ar_transaction": False,
            "creates_ap_transaction": True
        }
    ]
    
    for doc_type_data in document_types:
        # Check if document type already exists
        existing = db.query(OEDocumentType).filter(
            OEDocumentType.code == doc_type_data["code"]
        ).first()
        
        if not existing:
            doc_type = OEDocumentType(**doc_type_data)
            db.add(doc_type)
            print(f"Created document type: {doc_type.name} ({doc_type.code})")
        else:
            print(f"Document type already exists: {existing.name} ({existing.code})")
    
    db.commit()
    print("OE document types initialized successfully!")


def main():
    """Main initialization function"""
    db = SessionLocal()
    try:
        print("Initializing Order Entry defaults...")
        init_oe_document_types(db)
        print("\nOrder Entry initialization completed!")
    except Exception as e:
        print(f"Error during initialization: {str(e)}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main() 