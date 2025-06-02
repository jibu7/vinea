from sqlalchemy.orm import Session
from typing import List, Optional
from fastapi import HTTPException, status
from datetime import datetime
from decimal import Decimal
from app.models import (
    GoodsReceivedVoucher, GRVLine, PurchaseOrder, PurchaseOrderLine,
    OEDocumentType, InventoryTransaction, APTransaction
)
from app.schemas.grv import GRVCreate, GRVUpdate, GRVToInvoice


class GRVService:
    
    @staticmethod
    def generate_grv_number(db: Session) -> str:
        """Generate unique GRV number"""
        current_date = datetime.now()
        prefix = f"GRV{current_date.strftime('%y%m')}"
        
        # Get the last GRV number for this month
        last_grv = db.query(GoodsReceivedVoucher).filter(
            GoodsReceivedVoucher.grv_number.like(f"{prefix}%")
        ).order_by(GoodsReceivedVoucher.grv_number.desc()).first()
        
        if last_grv:
            last_sequence = int(last_grv.grv_number[-4:])
            new_sequence = last_sequence + 1
        else:
            new_sequence = 1
        
        return f"{prefix}{new_sequence:04d}"
    
    @staticmethod
    def create_grv(
        db: Session,
        grv_data: GRVCreate,
        received_by: int
    ) -> GoodsReceivedVoucher:
        # Validate purchase order
        po = db.query(PurchaseOrder).filter(
            PurchaseOrder.id == grv_data.purchase_order_id
        ).first()
        if not po:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Purchase order not found"
            )
        
        if po.status != 'CONFIRMED':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Purchase order must be confirmed before creating GRV"
            )
        
        # Validate document type
        doc_type = db.query(OEDocumentType).filter(
            OEDocumentType.id == grv_data.document_type_id
        ).first()
        if not doc_type or doc_type.document_class != 'PURCHASE' or doc_type.transaction_type != 'GRV':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid document type for GRV"
            )
        
        # Create GRV header
        grv_dict = grv_data.dict(exclude={'line_items'})
        grv = GoodsReceivedVoucher(
            **grv_dict,
            grv_number=GRVService.generate_grv_number(db),
            supplier_id=po.supplier_id,
            supplier_name=po.supplier_name,
            received_by=received_by,
            grv_date=grv_data.grv_date or datetime.utcnow()
        )
        
        # Process line items
        for idx, line_data in enumerate(grv_data.line_items):
            # Validate PO line
            po_line = db.query(PurchaseOrderLine).filter(
                PurchaseOrderLine.id == line_data.po_line_id,
                PurchaseOrderLine.purchase_order_id == po.id
            ).first()
            if not po_line:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Purchase order line {line_data.po_line_id} not found"
                )
            
            # Check if quantity is valid
            remaining_qty = po_line.quantity - po_line.received_quantity
            if line_data.received_quantity > remaining_qty:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Received quantity ({line_data.received_quantity}) exceeds remaining quantity ({remaining_qty}) for item {po_line.item_code}"
                )
            
            # Create GRV line
            grv_line = GRVLine(
                line_number=idx + 1,
                po_line_id=po_line.id,
                item_id=po_line.item_id,
                item_code=po_line.item_code,
                item_description=po_line.item_description,
                ordered_quantity=po_line.quantity,
                received_quantity=line_data.received_quantity,
                unit_price=po_line.unit_price,
                location=line_data.location,
                quality_status=line_data.quality_status,
                quality_notes=line_data.quality_notes
            )
            
            grv.line_items.append(grv_line)
        
        db.add(grv)
        db.commit()
        db.refresh(grv)
        return grv
    
    @staticmethod
    def get_grv(db: Session, grv_id: int) -> GoodsReceivedVoucher:
        grv = db.query(GoodsReceivedVoucher).filter(
            GoodsReceivedVoucher.id == grv_id
        ).first()
        if not grv:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="GRV not found"
            )
        return grv
    
    @staticmethod
    def get_grvs(
        db: Session,
        purchase_order_id: Optional[int] = None,
        supplier_id: Optional[int] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[GoodsReceivedVoucher]:
        query = db.query(GoodsReceivedVoucher)
        
        if purchase_order_id:
            query = query.filter(GoodsReceivedVoucher.purchase_order_id == purchase_order_id)
        
        if supplier_id:
            query = query.filter(GoodsReceivedVoucher.supplier_id == supplier_id)
        
        if status:
            query = query.filter(GoodsReceivedVoucher.status == status)
        
        return query.order_by(GoodsReceivedVoucher.grv_date.desc()).offset(skip).limit(limit).all()
    
    @staticmethod
    def post_grv_to_inventory(
        db: Session,
        grv_id: int,
        posted_by: int
    ) -> GoodsReceivedVoucher:
        """Post GRV to inventory - updates stock quantities"""
        grv = GRVService.get_grv(db, grv_id)
        
        if grv.status != 'DRAFT':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only draft GRVs can be posted"
            )
        
        if grv.inventory_posted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GRV already posted to inventory"
            )
        
        # Get receipt transaction type (assuming ID 1 for now)
        # In a real system, this would be configured properly
        receipt_type_id = 1
        
        # Process each line item
        for line in grv.line_items:
            if line.quality_status == 'FAILED':
                continue  # Skip failed items
            
            # Get inventory item
            from app.models import InventoryItem
            item = db.query(InventoryItem).filter(
                InventoryItem.id == line.item_id
            ).first()
            
            if not item:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Inventory item {line.item_id} not found"
                )
            
            # Update inventory quantity and weighted average cost
            old_qty = item.quantity_on_hand
            old_value = old_qty * item.cost_price
            new_qty = old_qty + line.received_quantity
            new_value = old_value + (line.received_quantity * line.unit_price)
            
            if new_qty > 0:
                item.cost_price = new_value / new_qty
            item.quantity_on_hand = new_qty
            
            # Create inventory transaction
            inv_trans = InventoryTransaction(
                item_id=line.item_id,
                transaction_type_id=receipt_type_id,
                transaction_date=grv.grv_date,
                quantity=line.received_quantity,
                unit_cost=line.unit_price,
                total_cost=line.received_quantity * line.unit_price,
                reference=grv.grv_number,
                description=f"Receipt from GRV {grv.grv_number}",
                source_module='OE',
                source_document_id=grv.id,
                posted_by_id=posted_by
            )
            db.add(inv_trans)
            
            # Update PO line received quantity
            po_line = line.po_line
            po_line.received_quantity += line.received_quantity
        
        # Update GRV status
        grv.status = 'POSTED'
        grv.inventory_posted = True
        grv.inventory_posted_at = datetime.utcnow()
        
        # Check if PO is fully received
        po = grv.purchase_order
        fully_received = True
        for po_line in po.line_items:
            if po_line.received_quantity < po_line.quantity:
                fully_received = False
                break
        
        if fully_received:
            po.status = 'RECEIVED'
        
        db.commit()
        db.refresh(grv)
        return grv
    
    @staticmethod
    def convert_to_invoice(
        db: Session,
        invoice_data: GRVToInvoice,
        posted_by: int
    ) -> APTransaction:
        """Convert GRV to AP supplier invoice"""
        grv = GRVService.get_grv(db, invoice_data.grv_id)
        
        # Check if GRV can be invoiced
        if grv.status != 'POSTED':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only posted GRVs can be invoiced"
            )
        
        # Get invoice document type
        doc_type = db.query(OEDocumentType).filter(
            OEDocumentType.id == grv.document_type_id
        ).first()
        
        if not doc_type or not doc_type.ap_transaction_type_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GRV document type is not configured for AP invoice creation"
            )
        
        # Calculate invoice amount
        invoice_amount = Decimal('0')
        for line in grv.line_items:
            if line.quality_status != 'FAILED':
                line_amount = line.received_quantity * line.unit_price
                invoice_amount += line_amount
        
        # Generate invoice number
        current_date = datetime.now()
        prefix = f"SINV{current_date.strftime('%y%m')}"
        
        # Get the last invoice number for this month
        last_invoice = db.query(APTransaction).filter(
            APTransaction.transaction_number.like(f"{prefix}%")
        ).order_by(APTransaction.transaction_number.desc()).first()
        
        if last_invoice:
            last_sequence = int(last_invoice.transaction_number[-4:])
            new_sequence = last_sequence + 1
        else:
            new_sequence = 1
        
        invoice_number = f"{prefix}{new_sequence:04d}"
        
        # Create AP transaction directly
        ap_transaction = APTransaction(
            supplier_id=grv.supplier_id,
            transaction_type_id=doc_type.ap_transaction_type_id,
            transaction_date=invoice_data.invoice_date or datetime.utcnow(),
            transaction_number=invoice_number,
            reference=grv.grv_number,
            description=f"Invoice from GRV {grv.grv_number}",
            amount=invoice_amount,
            source_module='OE',
            source_document_id=grv.id,
            is_posted=False,
            is_allocated=False,
            allocated_amount=Decimal('0')
        )
        db.add(ap_transaction)
        
        # Update GRV status
        grv.status = 'INVOICED'
        
        # Update PO status if all GRVs are invoiced
        po = grv.purchase_order
        all_invoiced = True
        for po_grv in po.grvs:
            if po_grv.status != 'INVOICED':
                all_invoiced = False
                break
        
        if all_invoiced and po.status == 'RECEIVED':
            po.status = 'INVOICED'
        
        db.commit()
        db.refresh(ap_transaction)
        
        return ap_transaction
    
    @staticmethod
    def update_grv(
        db: Session,
        grv_id: int,
        grv_update: GRVUpdate
    ) -> GoodsReceivedVoucher:
        grv = GRVService.get_grv(db, grv_id)
        
        # Check if GRV can be updated
        if grv.status != 'DRAFT':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only draft GRVs can be updated"
            )
        
        update_data = grv_update.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(grv, field, value)
        
        db.commit()
        db.refresh(grv)
        return grv
    
    @staticmethod
    def cancel_grv(db: Session, grv_id: int) -> GoodsReceivedVoucher:
        grv = GRVService.get_grv(db, grv_id)
        
        if grv.status != 'DRAFT':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only draft GRVs can be cancelled"
            )
        
        grv.status = 'CANCELLED'
        db.commit()
        db.refresh(grv)
        return grv 