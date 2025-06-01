import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from decimal import Decimal

from app.core.database import AsyncSessionLocal
from app.models import (
    Company, GLAccount, InventoryTransactionType, InventoryItem, AccountingPeriod
)


async def create_inventory_gl_accounts(db: AsyncSession, company_id: int):
    """Create default GL accounts for inventory if they don't exist"""
    
    # Define default inventory-related GL accounts
    inventory_accounts = [
        {
            "account_code": "1300",
            "account_name": "Inventory",
            "account_type": "ASSET",
            "current_balance": Decimal("0.00"),
            "is_system_account": True
        },
        {
            "account_code": "5100",
            "account_name": "Cost of Goods Sold",
            "account_type": "EXPENSE",
            "current_balance": Decimal("0.00"),
            "is_system_account": True
        },
        {
            "account_code": "5200",
            "account_name": "Inventory Adjustments",
            "account_type": "EXPENSE",
            "current_balance": Decimal("0.00"),
            "is_system_account": True
        }
    ]
    
    created_accounts = {}
    
    for account_data in inventory_accounts:
        # Check if account already exists
        result = await db.execute(
            select(GLAccount).where(
                and_(
                    GLAccount.company_id == company_id,
                    GLAccount.account_code == account_data["account_code"]
                )
            )
        )
        existing_account = result.scalar_one_or_none()
        
        if not existing_account:
            account = GLAccount(
                company_id=company_id,
                **account_data
            )
            db.add(account)
            await db.flush()  # Get the ID without committing
            created_accounts[account_data["account_code"]] = account
            print(f"Created GL account: {account.account_code} - {account.account_name}")
        else:
            created_accounts[account_data["account_code"]] = existing_account
            print(f"GL account already exists: {existing_account.account_code} - {existing_account.account_name}")
    
    return created_accounts


async def create_inventory_transaction_types(db: AsyncSession, company_id: int, gl_accounts: dict):
    """Create default inventory transaction types"""
    
    # Define default transaction types
    transaction_types = [
        {
            "code": "INV-ADJ-INC",
            "description": "Inventory Adjustment - Increase",
            "is_increase": True,
            "gl_account_id": gl_accounts["1300"].id,  # Inventory account
            "contra_gl_account_id": gl_accounts["5200"].id,  # Inventory Adjustments
            "is_system": True
        },
        {
            "code": "INV-ADJ-DEC",
            "description": "Inventory Adjustment - Decrease",
            "is_increase": False,
            "gl_account_id": gl_accounts["1300"].id,  # Inventory account
            "contra_gl_account_id": gl_accounts["5200"].id,  # Inventory Adjustments
            "is_system": True
        },
        {
            "code": "INV-RECEIPT",
            "description": "Inventory Receipt from Purchase",
            "is_increase": True,
            "gl_account_id": gl_accounts["1300"].id,  # Inventory account
            "is_system": True
        },
        {
            "code": "INV-ISSUE",
            "description": "Inventory Issue to Sales",
            "is_increase": False,
            "gl_account_id": gl_accounts["1300"].id,  # Inventory account
            "contra_gl_account_id": gl_accounts["5100"].id,  # COGS
            "is_system": True
        }
    ]
    
    for tt_data in transaction_types:
        # Check if transaction type already exists
        result = await db.execute(
            select(InventoryTransactionType).where(
                and_(
                    InventoryTransactionType.company_id == company_id,
                    InventoryTransactionType.code == tt_data["code"]
                )
            )
        )
        existing_tt = result.scalar_one_or_none()
        
        if not existing_tt:
            tt = InventoryTransactionType(
                company_id=company_id,
                **tt_data
            )
            db.add(tt)
            print(f"Created inventory transaction type: {tt.code} - {tt.description}")
        else:
            print(f"Inventory transaction type already exists: {existing_tt.code}")


async def create_sample_inventory_items(db: AsyncSession, company_id: int):
    """Create some sample inventory items for testing"""
    
    sample_items = [
        {
            "item_code": "WIDGET-001",
            "description": "Standard Widget",
            "item_type": "Stock",
            "unit_of_measure": "EACH",
            "cost_price": Decimal("10.00"),
            "selling_price": Decimal("15.00"),
            "quantity_on_hand": Decimal("100.0000"),
            "costing_method": "Weighted Average",
            "is_active": True
        },
        {
            "item_code": "SERVICE-001",
            "description": "Consulting Service",
            "item_type": "Service",
            "unit_of_measure": "HOUR",
            "cost_price": Decimal("50.00"),
            "selling_price": Decimal("100.00"),
            "quantity_on_hand": Decimal("0.0000"),
            "costing_method": "Weighted Average",
            "is_active": True
        },
        {
            "item_code": "PART-001",
            "description": "Replacement Part A",
            "item_type": "Stock",
            "unit_of_measure": "EACH",
            "cost_price": Decimal("25.50"),
            "selling_price": Decimal("35.00"),
            "quantity_on_hand": Decimal("50.0000"),
            "costing_method": "Weighted Average",
            "is_active": True
        }
    ]
    
    for item_data in sample_items:
        # Check if item already exists
        result = await db.execute(
            select(InventoryItem).where(
                and_(
                    InventoryItem.company_id == company_id,
                    InventoryItem.item_code == item_data["item_code"]
                )
            )
        )
        existing_item = result.scalar_one_or_none()
        
        if not existing_item:
            item = InventoryItem(
                company_id=company_id,
                **item_data
            )
            db.add(item)
            print(f"Created sample inventory item: {item.item_code} - {item.description}")
        else:
            print(f"Inventory item already exists: {existing_item.item_code}")


async def init_inventory_defaults():
    """Initialize inventory defaults for all companies"""
    async with AsyncSessionLocal() as db:
        # Get all companies
        result = await db.execute(select(Company))
        companies = result.scalars().all()
        
        if not companies:
            print("No companies found. Please run init_db.py first.")
            return
        
        for company in companies:
            print(f"\nInitializing inventory defaults for company: {company.name}")
            
            # Create GL accounts
            gl_accounts = await create_inventory_gl_accounts(db, company.id)
            
            # Create transaction types
            await create_inventory_transaction_types(db, company.id, gl_accounts)
            
            # Create sample items (optional)
            create_samples = input("\nCreate sample inventory items? (y/n): ").lower()
            if create_samples == 'y':
                await create_sample_inventory_items(db, company.id)
            
            # Commit all changes for this company
            await db.commit()
            print(f"Inventory initialization complete for {company.name}")


if __name__ == "__main__":
    print("Initializing Inventory Module Defaults...")
    asyncio.run(init_inventory_defaults()) 