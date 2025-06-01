#!/usr/bin/env python3
"""
Test script for Phase 4: Accounts Receivable module
Tests customers, AR transaction types, transactions, allocations, and reports
"""

import asyncio
import httpx
from datetime import datetime, date, timedelta
from decimal import Decimal
import json

BASE_URL = "http://localhost:8000/api"

# Test credentials
USERNAME = "admin"
PASSWORD = "admin123"

# Global variables to store test data
auth_token = None
test_company_id = None
test_customer_id = None
test_ar_account_id = None
test_revenue_account_id = None
test_invoice_type_id = None
test_payment_type_id = None
test_invoice_id = None
test_payment_id = None


async def login():
    """Login and get authentication token"""
    global auth_token
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BASE_URL}/auth/login",
            data={"username": USERNAME, "password": PASSWORD}
        )
        if response.status_code == 200:
            auth_token = response.json()["access_token"]
            print("✓ Login successful")
            return True
        else:
            print(f"✗ Login failed: {response.text}")
            return False


async def get_headers():
    """Get headers with authentication token"""
    return {"Authorization": f"Bearer {auth_token}"}


async def test_customers():
    """Test customer CRUD operations"""
    global test_customer_id
    print("\n=== Testing Customers ===")
    
    async with httpx.AsyncClient() as client:
        headers = await get_headers()
        
        # Create customer
        customer_data = {
            "customer_code": "CUST001",
            "name": "Test Customer Ltd",
            "address": {
                "street": "123 Main Street",
                "city": "Test City",
                "postal_code": "12345"
            },
            "contact_info": {
                "phone": "+1234567890",
                "email": "test@customer.com"
            },
            "payment_terms": 30,
            "credit_limit": "10000.00"
        }
        
        response = await client.post(
            f"{BASE_URL}/customers",
            json=customer_data,
            headers=headers
        )
        
        if response.status_code == 200:
            customer = response.json()
            test_customer_id = customer["id"]
            print(f"✓ Customer created: {customer['name']} (ID: {test_customer_id})")
        else:
            print(f"✗ Failed to create customer: {response.text}")
            return False
        
        # List customers
        response = await client.get(f"{BASE_URL}/customers", headers=headers)
        if response.status_code == 200:
            customers = response.json()
            print(f"✓ Listed {len(customers)} customers")
        else:
            print(f"✗ Failed to list customers: {response.text}")
        
        # Get customer balance
        response = await client.get(
            f"{BASE_URL}/customers/{test_customer_id}/balance",
            headers=headers
        )
        if response.status_code == 200:
            balance_info = response.json()
            print(f"✓ Customer balance: {balance_info['current_balance']}")
        else:
            print(f"✗ Failed to get customer balance: {response.text}")
        
        return True


async def setup_gl_accounts():
    """Create GL accounts needed for AR testing"""
    global test_ar_account_id, test_revenue_account_id
    print("\n=== Setting up GL Accounts ===")
    
    async with httpx.AsyncClient() as client:
        headers = await get_headers()
        
        # Create AR Control Account
        ar_account = {
            "account_code": "1200",
            "account_name": "Accounts Receivable",
            "account_type": "ASSET"
        }
        
        response = await client.post(
            f"{BASE_URL}/gl/accounts",
            json=ar_account,
            headers=headers
        )
        
        if response.status_code == 200:
            test_ar_account_id = response.json()["id"]
            print(f"✓ AR Control account created (ID: {test_ar_account_id})")
        else:
            print(f"✗ Failed to create AR account: {response.text}")
        
        # Create Revenue Account
        revenue_account = {
            "account_code": "4000",
            "account_name": "Sales Revenue",
            "account_type": "INCOME"
        }
        
        response = await client.post(
            f"{BASE_URL}/gl/accounts",
            json=revenue_account,
            headers=headers
        )
        
        if response.status_code == 200:
            test_revenue_account_id = response.json()["id"]
            print(f"✓ Revenue account created (ID: {test_revenue_account_id})")
        else:
            print(f"✗ Failed to create revenue account: {response.text}")
        
        return test_ar_account_id and test_revenue_account_id


async def test_ar_transaction_types():
    """Test AR transaction type operations"""
    global test_invoice_type_id, test_payment_type_id
    print("\n=== Testing AR Transaction Types ===")
    
    async with httpx.AsyncClient() as client:
        headers = await get_headers()
        
        # Create Invoice type
        invoice_type = {
            "code": "INV",
            "name": "Customer Invoice",
            "description": "Standard customer invoice",
            "ar_control_account_id": test_ar_account_id,
            "revenue_account_id": test_revenue_account_id,
            "affects_balance": "debit",
            "is_payment": False
        }
        
        response = await client.post(
            f"{BASE_URL}/ar/transaction-types",
            json=invoice_type,
            headers=headers
        )
        
        if response.status_code == 200:
            test_invoice_type_id = response.json()["id"]
            print(f"✓ Invoice type created (ID: {test_invoice_type_id})")
        else:
            print(f"✗ Failed to create invoice type: {response.text}")
            return False
        
        # Create Payment type
        payment_type = {
            "code": "PMT",
            "name": "Customer Payment",
            "description": "Customer payment receipt",
            "ar_control_account_id": test_ar_account_id,
            "affects_balance": "credit",
            "is_payment": True
        }
        
        response = await client.post(
            f"{BASE_URL}/ar/transaction-types",
            json=payment_type,
            headers=headers
        )
        
        if response.status_code == 200:
            test_payment_type_id = response.json()["id"]
            print(f"✓ Payment type created (ID: {test_payment_type_id})")
        else:
            print(f"✗ Failed to create payment type: {response.text}")
            return False
        
        # List transaction types
        response = await client.get(f"{BASE_URL}/ar/transaction-types", headers=headers)
        if response.status_code == 200:
            types = response.json()
            print(f"✓ Listed {len(types)} AR transaction types")
        else:
            print(f"✗ Failed to list transaction types: {response.text}")
        
        return True


async def test_ar_transactions():
    """Test AR transaction operations"""
    global test_invoice_id, test_payment_id
    print("\n=== Testing AR Transactions ===")
    
    async with httpx.AsyncClient() as client:
        headers = await get_headers()
        
        # Get next transaction number
        response = await client.get(
            f"{BASE_URL}/ar/transactions/next-number/INV",
            headers=headers
        )
        if response.status_code == 200:
            invoice_number = response.json()["next_number"]
            print(f"✓ Got next invoice number: {invoice_number}")
        else:
            invoice_number = "INV000001"
        
        # Create invoice
        invoice_data = {
            "customer_id": test_customer_id,
            "transaction_type_id": test_invoice_type_id,
            "transaction_number": invoice_number,
            "transaction_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=30)).isoformat(),
            "reference": "PO12345",
            "description": "Services rendered for October 2024",
            "amount": "5000.00"
        }
        
        response = await client.post(
            f"{BASE_URL}/ar/transactions",
            json=invoice_data,
            headers=headers
        )
        
        if response.status_code == 200:
            invoice = response.json()
            test_invoice_id = invoice["id"]
            print(f"✓ Invoice created: {invoice['transaction_number']} (ID: {test_invoice_id})")
        else:
            print(f"✗ Failed to create invoice: {response.text}")
            return False
        
        # Post invoice
        response = await client.post(
            f"{BASE_URL}/ar/transactions/{test_invoice_id}/post",
            headers=headers
        )
        
        if response.status_code == 200:
            print(f"✓ Invoice posted successfully")
        else:
            print(f"✗ Failed to post invoice: {response.text}")
        
        # Create payment
        payment_number = "PMT000001"
        payment_data = {
            "customer_id": test_customer_id,
            "transaction_type_id": test_payment_type_id,
            "transaction_number": payment_number,
            "transaction_date": date.today().isoformat(),
            "reference": "CHQ123456",
            "description": "Payment received - Thank you",
            "amount": "3000.00"
        }
        
        response = await client.post(
            f"{BASE_URL}/ar/transactions",
            json=payment_data,
            headers=headers
        )
        
        if response.status_code == 200:
            payment = response.json()
            test_payment_id = payment["id"]
            print(f"✓ Payment created: {payment['transaction_number']} (ID: {test_payment_id})")
        else:
            print(f"✗ Failed to create payment: {response.text}")
            return False
        
        # Post payment
        response = await client.post(
            f"{BASE_URL}/ar/transactions/{test_payment_id}/post",
            headers=headers
        )
        
        if response.status_code == 200:
            print(f"✓ Payment posted successfully")
        else:
            print(f"✗ Failed to post payment: {response.text}")
        
        # List transactions
        response = await client.get(
            f"{BASE_URL}/ar/transactions?customer_id={test_customer_id}",
            headers=headers
        )
        if response.status_code == 200:
            transactions = response.json()
            print(f"✓ Listed {len(transactions)} AR transactions")
        else:
            print(f"✗ Failed to list transactions: {response.text}")
        
        return True


async def test_allocations():
    """Test payment allocation"""
    print("\n=== Testing Payment Allocations ===")
    
    async with httpx.AsyncClient() as client:
        headers = await get_headers()
        
        # Allocate payment to invoice
        allocation_data = {
            "from_transaction_id": test_payment_id,  # Payment
            "to_transaction_id": test_invoice_id,    # Invoice
            "allocated_amount": "3000.00"
        }
        
        response = await client.post(
            f"{BASE_URL}/ar/allocations",
            json=allocation_data,
            headers=headers
        )
        
        if response.status_code == 200:
            allocation = response.json()
            print(f"✓ Allocation created: ${allocation['allocated_amount']}")
        else:
            print(f"✗ Failed to create allocation: {response.text}")
            return False
        
        # List allocations
        response = await client.get(
            f"{BASE_URL}/ar/allocations?customer_id={test_customer_id}",
            headers=headers
        )
        if response.status_code == 200:
            allocations = response.json()
            print(f"✓ Listed {len(allocations)} allocations")
        else:
            print(f"✗ Failed to list allocations: {response.text}")
        
        return True


async def test_reports():
    """Test AR reports"""
    print("\n=== Testing AR Reports ===")
    
    async with httpx.AsyncClient() as client:
        headers = await get_headers()
        
        # Customer Ageing Report
        response = await client.get(
            f"{BASE_URL}/ar/reports/ageing?as_at_date={date.today().isoformat()}",
            headers=headers
        )
        
        if response.status_code == 200:
            ageing = response.json()
            print(f"✓ Ageing report generated for {len(ageing)} customers")
            if ageing:
                for customer_ageing in ageing:
                    print(f"  - {customer_ageing['customer_name']}: Total ${customer_ageing['total']}")
        else:
            print(f"✗ Failed to generate ageing report: {response.text}")
        
        # Customer Statement
        response = await client.get(
            f"{BASE_URL}/ar/reports/statement/{test_customer_id}",
            headers=headers
        )
        
        if response.status_code == 200:
            statement = response.json()
            print(f"✓ Statement generated with {len(statement['transactions'])} transactions")
            print(f"  - Closing balance: ${statement['closing_balance']}")
        else:
            print(f"✗ Failed to generate statement: {response.text}")
        
        return True


async def verify_gl_integration():
    """Verify GL integration"""
    print("\n=== Verifying GL Integration ===")
    
    async with httpx.AsyncClient() as client:
        headers = await get_headers()
        
        # Check GL transactions
        response = await client.get(
            f"{BASE_URL}/gl/transactions?source_module=AR",
            headers=headers
        )
        
        if response.status_code == 200:
            gl_transactions = response.json()
            print(f"✓ Found {len(gl_transactions)} GL transactions from AR module")
        else:
            print(f"✗ Failed to get GL transactions: {response.text}")
        
        # Check account balances
        response = await client.get(
            f"{BASE_URL}/gl/accounts/{test_ar_account_id}",
            headers=headers
        )
        
        if response.status_code == 200:
            ar_account = response.json()
            print(f"✓ AR Control account balance: ${ar_account['current_balance']}")
        else:
            print(f"✗ Failed to get AR account: {response.text}")
        
        return True


async def main():
    """Run all tests"""
    print("=== Phase 4: Accounts Receivable Test Suite ===")
    print(f"Testing against: {BASE_URL}")
    
    # Login
    if not await login():
        print("Failed to login. Exiting.")
        return
    
    # Run tests in sequence
    tests = [
        ("GL Account Setup", setup_gl_accounts),
        ("Customers", test_customers),
        ("AR Transaction Types", test_ar_transaction_types),
        ("AR Transactions", test_ar_transactions),
        ("Payment Allocations", test_allocations),
        ("AR Reports", test_reports),
        ("GL Integration", verify_gl_integration)
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        try:
            if await test_func():
                passed += 1
            else:
                failed += 1
                print(f"✗ {test_name} test failed")
        except Exception as e:
            failed += 1
            print(f"✗ {test_name} test error: {str(e)}")
    
    print("\n=== Test Summary ===")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Total: {len(tests)}")
    
    if failed == 0:
        print("\n✓ All tests passed! Phase 4 implementation is working correctly.")
    else:
        print(f"\n✗ {failed} tests failed. Please check the implementation.")


if __name__ == "__main__":
    asyncio.run(main()) 