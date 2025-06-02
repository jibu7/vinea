from sqlalchemy.orm import Session
from typing import List, Optional
from fastapi import HTTPException, status
from datetime import datetime
from decimal import Decimal
from app.models import (
    SalesOrder, SalesOrderLine, Customer, InventoryItem, 
    OEDocumentType, ARTransaction, ARTransactionType
)
from app.schemas.sales_order import SalesOrderCreate, SalesOrderUpdate, SalesOrderToInvoice


class SalesOrderService:
    
    @staticmethod
    def generate_order_number(db: Session) -> str:
        """Generate unique sales order number"""
        current_date = datetime.now()
        prefix = f"SO{current_date.strftime('%y%m')}"
        
        # Get the last order number for this month
        last_order = db.query(SalesOrder).filter(
            SalesOrder.order_number.like(f"{prefix}%")
        ).order_by(SalesOrder.order_number.desc()).first()
        
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
    def create_sales_order(
        db: Session,
        order_data: SalesOrderCreate,
        created_by: int
    ) -> SalesOrder:
        # Validate customer
        customer = db.query(Customer).filter(Customer.id == order_data.customer_id).first()
        if not customer:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Customer not found"
            )
        
        # Validate document type
        doc_type = db.query(OEDocumentType).filter(
            OEDocumentType.id == order_data.document_type_id
        ).first()
        if not doc_type or doc_type.document_class != 'SALES' or doc_type.transaction_type != 'ORDER':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid document type for sales order"
            )
        
        # Create order header
        order_dict = order_data.dict(exclude={'line_items'})
        order = SalesOrder(
            **order_dict,
            order_number=SalesOrderService.generate_order_number(db),
            customer_name=customer.name,
            customer_address=customer.address,
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
            totals = SalesOrderService.calculate_line_totals(
                line_data.quantity,
                line_data.unit_price,
                line_data.discount_percent,
                line_data.tax_percent
            )
            
            # Create line item
            line = SalesOrderLine(
                line_number=idx + 1,
                item_id=item.id,
                item_code=item.code,
                item_description=item.description,
                quantity=line_data.quantity,
                unit_price=line_data.unit_price,
                discount_percent=line_data.discount_percent,
                tax_percent=line_data.tax_percent,
                gl_account_id=line_data.gl_account_id or item.sales_gl_account_id,
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
    def get_sales_order(db: Session, order_id: int) -> SalesOrder:
        order = db.query(SalesOrder).filter(SalesOrder.id == order_id).first()
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sales order not found"
            )
        return order
    
    @staticmethod
    def get_sales_orders(
        db: Session,
        customer_id: Optional[int] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[SalesOrder]:
        query = db.query(SalesOrder)
        
        if customer_id:
            query = query.filter(SalesOrder.customer_id == customer_id)
        
        if status:
            query = query.filter(SalesOrder.status == status)
        
        return query.order_by(SalesOrder.order_date.desc()).offset(skip).limit(limit).all()
    
    @staticmethod
    def update_sales_order(
        db: Session,
        order_id: int,
        order_update: SalesOrderUpdate
    ) -> SalesOrder:
        order = SalesOrderService.get_sales_order(db, order_id)
        
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
    def confirm_sales_order(db: Session, order_id: int) -> SalesOrder:
        order = SalesOrderService.get_sales_order(db, order_id)
        
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
    def convert_to_invoice(
        db: Session,
        invoice_data: SalesOrderToInvoice,
        posted_by: int
    ) -> ARTransaction:
        """Convert sales order to AR invoice"""
        order = SalesOrderService.get_sales_order(db, invoice_data.sales_order_id)
        
        # Check if order can be invoiced
        if order.status != 'CONFIRMED':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only confirmed orders can be invoiced"
            )
        
        # Get invoice document type
        doc_type = db.query(OEDocumentType).filter(
            OEDocumentType.id == order.document_type_id
        ).first()
        
        if not doc_type or not doc_type.ar_transaction_type_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order document type is not configured for AR invoice creation"
            )
        
        # Generate invoice number
        current_date = datetime.now()
        prefix = f"INV{current_date.strftime('%y%m')}"
        
        # Get the last invoice number for this month
        last_invoice = db.query(ARTransaction).filter(
            ARTransaction.transaction_number.like(f"{prefix}%")
        ).order_by(ARTransaction.transaction_number.desc()).first()
        
        if last_invoice:
            last_sequence = int(last_invoice.transaction_number[-4:])
            new_sequence = last_sequence + 1
        else:
            new_sequence = 1
        
        invoice_number = f"{prefix}{new_sequence:04d}"
        
        # Create AR transaction directly
        ar_transaction = ARTransaction(
            customer_id=order.customer_id,
            transaction_type_id=doc_type.ar_transaction_type_id,
            transaction_date=invoice_data.invoice_date or datetime.utcnow(),
            transaction_number=invoice_number,
            reference=order.order_number,
            description=f"Invoice from SO {order.order_number}",
            amount=order.net_amount,
            source_module='OE',
            source_document_id=order.id,
            is_posted=False,
            is_allocated=False,
            allocated_amount=Decimal('0')
        )
        db.add(ar_transaction)
        
        # Update order status
        order.status = 'INVOICED'
        db.commit()
        db.refresh(ar_transaction)
        
        return ar_transaction
    
    @staticmethod
    def cancel_sales_order(db: Session, order_id: int) -> SalesOrder:
        order = SalesOrderService.get_sales_order(db, order_id)
        
        if order.status == 'INVOICED':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot cancel invoiced order"
            )
        
        order.status = 'CANCELLED'
        db.commit()
        db.refresh(order)
        return order 