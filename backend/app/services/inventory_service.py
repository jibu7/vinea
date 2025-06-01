from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from sqlalchemy.exc import IntegrityError
from typing import List, Optional, Dict
from datetime import datetime
from decimal import Decimal
from fastapi import HTTPException, status

from app.models import (
    InventoryItem, InventoryTransactionType, InventoryTransaction,
    GLTransaction, AccountingPeriod
)
from app.schemas.inventory import (
    InventoryItemCreate, InventoryItemUpdate,
    InventoryTransactionTypeCreate, InventoryTransactionTypeUpdate,
    InventoryAdjustmentRequest
)
from app.services.gl_service import GLService


class InventoryService:
    """Service for handling inventory operations"""
    
    def __init__(self, db: Session):
        self.db = db
    
    # Inventory Item Management
    def create_item(self, company_id: int, item_data: InventoryItemCreate) -> InventoryItem:
        """Create a new inventory item"""
        # Check if item code already exists for this company
        existing = self.db.query(InventoryItem).filter(
            and_(
                InventoryItem.company_id == company_id,
                InventoryItem.item_code == item_data.item_code
            )
        ).first()
        
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Item code {item_data.item_code} already exists"
            )
        
        item = InventoryItem(
            company_id=company_id,
            **item_data.dict()
        )
        
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        
        return item
    
    def get_item(self, company_id: int, item_id: int) -> Optional[InventoryItem]:
        """Get a single inventory item"""
        return self.db.query(InventoryItem).filter(
            and_(
                InventoryItem.company_id == company_id,
                InventoryItem.id == item_id
            )
        ).first()
    
    def get_item_by_code(self, company_id: int, item_code: str) -> Optional[InventoryItem]:
        """Get an inventory item by code"""
        return self.db.query(InventoryItem).filter(
            and_(
                InventoryItem.company_id == company_id,
                InventoryItem.item_code == item_code.upper()
            )
        ).first()
    
    def list_items(
        self, 
        company_id: int, 
        skip: int = 0, 
        limit: int = 100,
        item_type: Optional[str] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None
    ) -> List[InventoryItem]:
        """List inventory items with filtering"""
        query = self.db.query(InventoryItem).filter(
            InventoryItem.company_id == company_id
        )
        
        if item_type:
            query = query.filter(InventoryItem.item_type == item_type)
        
        if is_active is not None:
            query = query.filter(InventoryItem.is_active == is_active)
        
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    InventoryItem.item_code.ilike(search_pattern),
                    InventoryItem.description.ilike(search_pattern)
                )
            )
        
        return query.order_by(InventoryItem.item_code).offset(skip).limit(limit).all()
    
    def update_item(
        self, 
        company_id: int, 
        item_id: int, 
        item_data: InventoryItemUpdate
    ) -> InventoryItem:
        """Update an inventory item"""
        item = self.get_item(company_id, item_id)
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Item not found"
            )
        
        # Update only provided fields
        update_data = item_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(item, field, value)
        
        self.db.commit()
        self.db.refresh(item)
        
        return item
    
    def delete_item(self, company_id: int, item_id: int) -> bool:
        """Delete an inventory item (soft delete by deactivating)"""
        item = self.get_item(company_id, item_id)
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Item not found"
            )
        
        # Check if item has transactions
        has_transactions = self.db.query(InventoryTransaction).filter(
            InventoryTransaction.item_id == item_id
        ).first()
        
        if has_transactions:
            # Soft delete - just deactivate
            item.is_active = False
            self.db.commit()
        else:
            # Hard delete if no transactions
            self.db.delete(item)
            self.db.commit()
        
        return True
    
    # Transaction Type Management
    def create_transaction_type(
        self, 
        company_id: int, 
        tt_data: InventoryTransactionTypeCreate
    ) -> InventoryTransactionType:
        """Create a new inventory transaction type"""
        # Check if code already exists
        existing = self.db.query(InventoryTransactionType).filter(
            and_(
                InventoryTransactionType.company_id == company_id,
                InventoryTransactionType.code == tt_data.code
            )
        ).first()
        
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Transaction type code {tt_data.code} already exists"
            )
        
        tt = InventoryTransactionType(
            company_id=company_id,
            **tt_data.dict()
        )
        
        self.db.add(tt)
        self.db.commit()
        self.db.refresh(tt)
        
        return tt
    
    def get_transaction_type(
        self, 
        company_id: int, 
        tt_id: int
    ) -> Optional[InventoryTransactionType]:
        """Get a single transaction type"""
        return self.db.query(InventoryTransactionType).filter(
            and_(
                InventoryTransactionType.company_id == company_id,
                InventoryTransactionType.id == tt_id
            )
        ).first()
    
    def list_transaction_types(
        self, 
        company_id: int
    ) -> List[InventoryTransactionType]:
        """List all inventory transaction types"""
        return self.db.query(InventoryTransactionType).filter(
            InventoryTransactionType.company_id == company_id
        ).order_by(InventoryTransactionType.code).all()
    
    def update_transaction_type(
        self, 
        company_id: int, 
        tt_id: int, 
        tt_data: InventoryTransactionTypeUpdate
    ) -> InventoryTransactionType:
        """Update a transaction type"""
        tt = self.get_transaction_type(company_id, tt_id)
        if not tt:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transaction type not found"
            )
        
        if tt.is_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot update system transaction types"
            )
        
        # Update only provided fields
        update_data = tt_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(tt, field, value)
        
        self.db.commit()
        self.db.refresh(tt)
        
        return tt
    
    def delete_transaction_type(self, company_id: int, tt_id: int) -> bool:
        """Delete a transaction type"""
        tt = self.get_transaction_type(company_id, tt_id)
        if not tt:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transaction type not found"
            )
        
        if tt.is_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete system transaction types"
            )
        
        # Check if used in transactions
        has_transactions = self.db.query(InventoryTransaction).filter(
            InventoryTransaction.transaction_type_id == tt_id
        ).first()
        
        if has_transactions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete transaction type that has been used"
            )
        
        self.db.delete(tt)
        self.db.commit()
        
        return True
    
    # Inventory Adjustment Processing
    def process_adjustment(
        self, 
        company_id: int, 
        user_id: int,
        adjustment: InventoryAdjustmentRequest
    ) -> InventoryTransaction:
        """Process an inventory adjustment with GL integration"""
        # Validate item
        item = self.get_item(company_id, adjustment.item_id)
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Item not found"
            )
        
        if not item.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot adjust inactive items"
            )
        
        # Validate transaction type
        tt = self.get_transaction_type(company_id, adjustment.transaction_type_id)
        if not tt:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transaction type not found"
            )
        
        # Check accounting period
        period = self.db.query(AccountingPeriod).filter(
            and_(
                AccountingPeriod.company_id == company_id,
                AccountingPeriod.start_date <= adjustment.transaction_date,
                AccountingPeriod.end_date >= adjustment.transaction_date,
                AccountingPeriod.is_closed == False
            )
        ).first()
        
        if not period:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No open accounting period for the transaction date"
            )
        
        # Calculate costs based on weighted average
        if tt.is_increase:
            # For increases, use the provided cost or current cost
            unit_cost = item.cost_price
            total_cost = unit_cost * adjustment.quantity
            
            # Update weighted average cost
            new_qty = item.quantity_on_hand + adjustment.quantity
            if new_qty > 0:
                current_value = item.quantity_on_hand * item.cost_price
                new_value = current_value + total_cost
                item.cost_price = new_value / new_qty
            
            item.quantity_on_hand = new_qty
        else:
            # For decreases, use current weighted average
            if adjustment.quantity > item.quantity_on_hand:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Insufficient quantity. Available: {item.quantity_on_hand}"
                )
            
            unit_cost = item.cost_price
            total_cost = unit_cost * adjustment.quantity
            item.quantity_on_hand -= adjustment.quantity
        
        # Create inventory transaction
        inv_trans = InventoryTransaction(
            company_id=company_id,
            transaction_type_id=tt.id,
            item_id=item.id,
            transaction_date=adjustment.transaction_date,
            reference=adjustment.reference,
            description=adjustment.description or f"Inventory adjustment for {item.item_code}",
            quantity=adjustment.quantity if tt.is_increase else -adjustment.quantity,
            unit_cost=unit_cost,
            total_cost=total_cost if tt.is_increase else -total_cost,
            source_module="INV",
            posted_by_id=user_id
        )
        
        self.db.add(inv_trans)
        
        # Create GL entries
        gl_entries = []
        
        if tt.is_increase:
            # Debit inventory, credit adjustment account
            gl_entries.append({
                'account_id': tt.gl_account_id,  # Inventory account
                'debit_amount': total_cost,
                'credit_amount': Decimal('0.00'),
                'description': f"Inventory increase: {item.item_code}"
            })
            
            contra_account = tt.contra_gl_account_id or tt.gl_account_id
            gl_entries.append({
                'account_id': contra_account,  # Adjustment account
                'debit_amount': Decimal('0.00'),
                'credit_amount': total_cost,
                'description': f"Inventory increase: {item.item_code}"
            })
        else:
            # Credit inventory, debit adjustment account
            gl_entries.append({
                'account_id': tt.gl_account_id,  # Inventory account
                'debit_amount': Decimal('0.00'),
                'credit_amount': total_cost,
                'description': f"Inventory decrease: {item.item_code}"
            })
            
            contra_account = tt.contra_gl_account_id or tt.gl_account_id
            gl_entries.append({
                'account_id': contra_account,  # Adjustment account
                'debit_amount': total_cost,
                'credit_amount': Decimal('0.00'),
                'description': f"Inventory decrease: {item.item_code}"
            })
        
        # Post to GL
        try:
            # Generate journal entry ID
            from datetime import datetime as dt
            timestamp = dt.now().strftime("%Y%m%d%H%M%S")
            journal_entry_id = f"JE-INV-{timestamp}"
            
            # Create GL transactions for each entry
            for entry in gl_entries:
                gl_transaction = GLTransaction(
                    company_id=company_id,
                    journal_entry_id=journal_entry_id,
                    account_id=entry['account_id'],
                    transaction_date=adjustment.transaction_date,
                    period_id=period.id,
                    description=entry.get('description', ''),
                    debit_amount=entry.get('debit_amount', Decimal('0')),
                    credit_amount=entry.get('credit_amount', Decimal('0')),
                    reference=adjustment.reference or f"INV-ADJ-{inv_trans.id}",
                    source_module="INV",
                    source_document_id=inv_trans.id,
                    posted_by_id=user_id,
                    is_reversed=False
                )
                self.db.add(gl_transaction)
        except Exception as e:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"GL posting failed: {str(e)}"
            )
        
        self.db.commit()
        self.db.refresh(inv_trans)
        
        return inv_trans
    
    # Reporting
    def get_stock_quantity_report(
        self,
        company_id: int,
        item_type: Optional[str] = None,
        show_zero_qty: bool = False,
        item_code_from: Optional[str] = None,
        item_code_to: Optional[str] = None
    ) -> List[Dict]:
        """Generate stock quantity report"""
        query = self.db.query(InventoryItem).filter(
            and_(
                InventoryItem.company_id == company_id,
                InventoryItem.is_active == True
            )
        )
        
        if item_type:
            query = query.filter(InventoryItem.item_type == item_type)
        else:
            # Default to stock items only
            query = query.filter(InventoryItem.item_type == "Stock")
        
        if not show_zero_qty:
            query = query.filter(InventoryItem.quantity_on_hand > 0)
        
        if item_code_from:
            query = query.filter(InventoryItem.item_code >= item_code_from.upper())
        
        if item_code_to:
            query = query.filter(InventoryItem.item_code <= item_code_to.upper())
        
        items = query.order_by(InventoryItem.item_code).all()
        
        report_data = []
        total_value = Decimal('0.00')
        
        for item in items:
            item_value = item.quantity_on_hand * item.cost_price
            total_value += item_value
            
            report_data.append({
                'item_code': item.item_code,
                'description': item.description,
                'item_type': item.item_type.value,
                'unit_of_measure': item.unit_of_measure,
                'quantity_on_hand': float(item.quantity_on_hand),
                'cost_price': float(item.cost_price),
                'selling_price': float(item.selling_price),
                'total_value': float(item_value),
                'is_active': item.is_active
            })
        
        return {
            'items': report_data,
            'summary': {
                'total_items': len(report_data),
                'total_value': float(total_value)
            }
        }
    
    def get_item_transactions(
        self,
        company_id: int,
        item_id: int,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None
    ) -> List[InventoryTransaction]:
        """Get transaction history for an item"""
        query = self.db.query(InventoryTransaction).filter(
            and_(
                InventoryTransaction.company_id == company_id,
                InventoryTransaction.item_id == item_id
            )
        )
        
        if date_from:
            query = query.filter(InventoryTransaction.transaction_date >= date_from)
        
        if date_to:
            query = query.filter(InventoryTransaction.transaction_date <= date_to)
        
        return query.order_by(InventoryTransaction.transaction_date.desc()).all() 