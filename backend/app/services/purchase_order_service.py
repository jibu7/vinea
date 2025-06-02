from sqlalchemy.orm import Session
from typing import List, Optional
from fastapi import HTTPException, status
from datetime import datetime
from decimal import Decimal
from app.models import (
    PurchaseOrder, PurchaseOrderLine, Supplier, InventoryItem, 
    OEDocumentType
)
from app.schemas.purchase_order import PurchaseOrderCreate, PurchaseOrderUpdate


class PurchaseOrderService:
    
    @staticmethod
    def generate_order_number(db: Session) -> str:
        """Generate unique purchase order number"""
        current_date = datetime.now()
        prefix = f"PO{current_date.strftime('%y%m')}"
        
        # Get the last order number for this month
        last_order = db.query(PurchaseOrder).filter(
            PurchaseOrder.order_number.like(f"{prefix}%")
        ).order_by(PurchaseOrder.order_number.desc()).first()
        
        if last_order:
            last_sequence = int(last_order.order_number[-4:])
            new_sequence = last_sequence + 1
        else:
            new_sequence = 1
        
        return f"{prefix}{new_sequence:04d}"
    
    @staticmethod
    def calculate_line_totals(
        quantity: Decimal,
        unit_price: Decimal,
        discount_percent: Decimal,
        tax_percent: Decimal
    ) -> dict:
        """Calculate line item totals"""
        line_total = quantity * unit_price
        discount_amount = line_total * (discount_percent / 100)
        subtotal = line_total - discount_amount
        tax_amount = subtotal * (tax_percent / 100)
        net_amount = subtotal + tax_amount
        
        return {
            'line_total': line_total,
            'discount_amount': discount_amount,
            'tax_amount': tax_amount,
            'net_amount': net_amount
        }
    
    @staticmethod
    def create_purchase_order(
        db: Session,
        order_data: PurchaseOrderCreate,
        created_by: int
    ) -> PurchaseOrder:
        # Validate supplier
        supplier = db.query(Supplier).filter(Supplier.id == order_data.supplier_id).first()
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Supplier not found"
            )
        
        # Validate document type
        doc_type = db.query(OEDocumentType).filter(
            OEDocumentType.id == order_data.document_type_id
        ).first()
        if not doc_type or doc_type.document_class != 'PURCHASE' or doc_type.transaction_type != 'ORDER':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid document type for purchase order"
            )
        
        # Create order header
        order_dict = order_data.dict(exclude={'line_items'})
        order = PurchaseOrder(
            **order_dict,
            order_number=PurchaseOrderService.generate_order_number(db),
            supplier_name=supplier.name,
            supplier_address=supplier.address,
            created_by=created_by,
            order_date=order_data.order_date or datetime.utcnow()
        )
        
        # Process line items
        total_amount = Decimal('0')
        total_discount = Decimal('0')
        total_tax = Decimal('0')
        
        for idx, line_data in enumerate(order_data.line_items):
            # Validate item
            item = db.query(InventoryItem).filter(
                InventoryItem.id == line_data.item_id
            ).first()
            if not item:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Item {line_data.item_id} not found"
                )
            
            # Calculate line totals
            totals = PurchaseOrderService.calculate_line_totals(
                line_data.quantity,
                line_data.unit_price,
                line_data.discount_percent,
                line_data.tax_percent
            )
            
            # Create line item
            line = PurchaseOrderLine(
                line_number=idx + 1,
                item_id=item.id,
                item_code=item.code,
                item_description=item.description,
                quantity=line_data.quantity,
                unit_price=line_data.unit_price,
                discount_percent=line_data.discount_percent,
                tax_percent=line_data.tax_percent,
                gl_account_id=line_data.gl_account_id or item.purchase_gl_account_id,
                **totals
            )
            
            order.line_items.append(line)
            
            # Update order totals
            total_amount += totals['line_total']
            total_discount += totals['discount_amount']
            total_tax += totals['tax_amount']
        
        # Set order totals
        order.total_amount = total_amount
        order.discount_amount = total_discount
        order.tax_amount = total_tax
        order.net_amount = total_amount - total_discount + total_tax
        
        db.add(order)
        db.commit()
        db.refresh(order)
        return order
    
    @staticmethod
    def get_purchase_order(db: Session, order_id: int) -> PurchaseOrder:
        order = db.query(PurchaseOrder).filter(PurchaseOrder.id == order_id).first()
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Purchase order not found"
            )
        return order
    
    @staticmethod
    def get_purchase_orders(
        db: Session,
        supplier_id: Optional[int] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[PurchaseOrder]:
        query = db.query(PurchaseOrder)
        
        if supplier_id:
            query = query.filter(PurchaseOrder.supplier_id == supplier_id)
        
        if status:
            query = query.filter(PurchaseOrder.status == status)
        
        return query.order_by(PurchaseOrder.order_date.desc()).offset(skip).limit(limit).all()
    
    @staticmethod
    def update_purchase_order(
        db: Session,
        order_id: int,
        order_update: PurchaseOrderUpdate
    ) -> PurchaseOrder:
        order = PurchaseOrderService.get_purchase_order(db, order_id)
        
        # Check if order can be updated
        if order.status not in ['DRAFT', 'CONFIRMED']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot update order with status " + order.status
            )
        
        update_data = order_update.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(order, field, value)
        
        db.commit()
        db.refresh(order)
        return order
    
    @staticmethod
    def confirm_purchase_order(db: Session, order_id: int) -> PurchaseOrder:
        order = PurchaseOrderService.get_purchase_order(db, order_id)
        
        if order.status != 'DRAFT':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only draft orders can be confirmed"
            )
        
        order.status = 'CONFIRMED'
        db.commit()
        db.refresh(order)
        return order
    
    @staticmethod
    def get_open_po_lines(db: Session, supplier_id: Optional[int] = None) -> List[PurchaseOrderLine]:
        """Get purchase order lines that have not been fully received"""
        query = db.query(PurchaseOrderLine).join(PurchaseOrder)
        
        # Only confirmed orders
        query = query.filter(PurchaseOrder.status == 'CONFIRMED')
        
        if supplier_id:
            query = query.filter(PurchaseOrder.supplier_id == supplier_id)
        
        # Get lines where received quantity is less than ordered quantity
        lines = query.all()
        open_lines = []
        
        for line in lines:
            if line.received_quantity < line.quantity:
                open_lines.append(line)
        
        return open_lines
    
    @staticmethod
    def cancel_purchase_order(db: Session, order_id: int) -> PurchaseOrder:
        order = PurchaseOrderService.get_purchase_order(db, order_id)
        
        if order.status in ['RECEIVED', 'INVOICED']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot cancel order with receipts or invoices"
            )
        
        order.status = 'CANCELLED'
        db.commit()
        db.refresh(order)
        return order 