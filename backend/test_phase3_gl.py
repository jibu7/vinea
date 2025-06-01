#!/usr/bin/env python3
"""
Phase 3 (General Ledger) Test Script
Tests all GL endpoints and requirements
"""

import asyncio
import httpx
from datetime import date, datetime
from decimal import Decimal
import json
from typing import Dict, List, Optional

# Test configuration
BASE_URL = "http://localhost:8000/api"
TEST_USERNAME = "admin"
TEST_PASSWORD = "admin123"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

class GLTester:
    def __init__(self):
        self.client = httpx.AsyncClient(base_url=BASE_URL)
        self.token = None
        self.user_id = None
        self.company_id = None
        self.created_accounts = []
        self.created_journal_entries = []
        
    async def __aenter__(self):
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()
        
    def print_result(self, test_name: str, success: bool, details: str = ""):
        status = f"{Colors.GREEN}✓ PASS{Colors.RESET}" if success else f"{Colors.RED}✗ FAIL{Colors.RESET}"
        print(f"{status} {test_name}")
        if details:
            print(f"  {Colors.YELLOW}→{Colors.RESET} {details}")
            
    async def login(self):
        """Authenticate and get token"""
        print(f"\n{Colors.BLUE}=== Authentication ==={Colors.RESET}")
        
        response = await self.client.post("/auth/login", json={
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD
        })
        
        if response.status_code == 200:
            data = response.json()
            self.token = data["access_token"]
            self.user_id = data["user"]["id"]
            self.company_id = data["user"]["company_id"]
            self.client.headers["Authorization"] = f"Bearer {self.token}"
            self.print_result("Login", True, f"User ID: {self.user_id}, Company ID: {self.company_id}")
            return True
        else:
            self.print_result("Login", False, f"Status: {response.status_code}")
            return False
            
    async def test_chart_of_accounts(self):
        """Test Chart of Accounts CRUD operations (REQ-GL-COA-*)"""
        print(f"\n{Colors.BLUE}=== Chart of Accounts Tests ==={Colors.RESET}")
        
        # Test 1: Create GL Account
        timestamp = datetime.now().strftime("%H%M%S")
        account_data = {
            "account_code": f"1000-{timestamp}",
            "account_name": "Cash and Bank",
            "account_type": "ASSET",
            "is_active": True
        }
        
        response = await self.client.post("/gl/accounts", json=account_data)
        if response.status_code == 201:
            account = response.json()
            self.created_accounts.append(account["id"])
            self.print_result("Create GL Account", True, f"Account ID: {account['id']}")
        else:
            self.print_result("Create GL Account", False, f"Status: {response.status_code}, Error: {response.text}")
            return
            
        # Test 2: Create child account (hierarchy test)
        child_account_data = {
            "account_code": f"1010-{timestamp}",
            "account_name": "Petty Cash",
            "account_type": "ASSET",
            "parent_account_id": account["id"],
            "is_active": True
        }
        
        response = await self.client.post("/gl/accounts", json=child_account_data)
        if response.status_code == 201:
            child_account = response.json()
            self.created_accounts.append(child_account["id"])
            self.print_result("Create Child Account", True, f"Parent: {account['id']}, Child: {child_account['id']}")
        else:
            self.print_result("Create Child Account", False, f"Status: {response.status_code}")
            
        # Test 3: List GL Accounts
        response = await self.client.get("/gl/accounts")
        if response.status_code == 200:
            accounts = response.json()
            self.print_result("List GL Accounts", True, f"Total accounts: {len(accounts)}")
        else:
            self.print_result("List GL Accounts", False, f"Status: {response.status_code}")
            
        # Test 4: Get single account
        response = await self.client.get(f"/gl/accounts/{account['id']}")
        self.print_result("Get GL Account", response.status_code == 200, 
                         f"Account: {response.json()['account_code'] if response.status_code == 200 else 'Failed'}")
        
        # Test 5: Update account
        update_data = {
            "account_name": "Cash and Bank Accounts",
            "is_active": True
        }
        response = await self.client.put(f"/gl/accounts/{account['id']}", json=update_data)
        self.print_result("Update GL Account", response.status_code == 200,
                         f"Updated name: {response.json()['account_name'] if response.status_code == 200 else 'Failed'}")
        
        # Create more accounts for journal entries
        expense_account = await self._create_account(f"5000-{timestamp}", "Office Expenses", "EXPENSE")
        income_account = await self._create_account(f"4000-{timestamp}", "Sales Revenue", "INCOME")
        
        return account["id"], expense_account, income_account
        
    async def _create_account(self, code: str, name: str, account_type: str) -> Optional[int]:
        """Helper to create an account"""
        response = await self.client.post("/gl/accounts", json={
            "account_code": code,
            "account_name": name,
            "account_type": account_type,
            "is_active": True
        })
        if response.status_code == 201:
            account_id = response.json()["id"]
            self.created_accounts.append(account_id)
            return account_id
        return None
        
    async def test_journal_entries(self, cash_account_id: int, expense_account_id: int):
        """Test Journal Entry operations (REQ-GL-JE-*)"""
        print(f"\n{Colors.BLUE}=== Journal Entry Tests ==={Colors.RESET}")
        
        # Test 1: Create journal entry
        journal_data = {
            "transaction_date": date.today().isoformat(),
            "journal_entry_id": f"JE-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "reference": "OFFICE-001",
            "description": "Office supplies purchase",
            "lines": [
                {
                    "account_id": expense_account_id,
                    "description": "Office supplies",
                    "debit_amount": 100.00,
                    "credit_amount": 0.00
                },
                {
                    "account_id": cash_account_id,
                    "description": "Cash payment",
                    "debit_amount": 0.00,
                    "credit_amount": 100.00
                }
            ]
        }
        
        response = await self.client.post("/gl/journal-entries", json=journal_data)
        if response.status_code == 201:
            transactions = response.json()
            self.created_journal_entries.append(journal_data["journal_entry_id"])
            self.print_result("Create Journal Entry", True, 
                            f"Entry ID: {journal_data['journal_entry_id']}, Lines: {len(transactions)}")
        else:
            self.print_result("Create Journal Entry", False, f"Status: {response.status_code}, Error: {response.text}")
            return
            
        # Test 2: Unbalanced entry (should fail)
        unbalanced_data = {
            "transaction_date": date.today().isoformat(),
            "journal_entry_id": f"JE-UNBAL-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "reference": "UNBAL-001",
            "description": "Unbalanced entry",
            "lines": [
                {
                    "account_id": expense_account_id,
                    "debit_amount": 100.00,
                    "credit_amount": 0.00
                },
                {
                    "account_id": cash_account_id,
                    "debit_amount": 0.00,
                    "credit_amount": 50.00  # Intentionally unbalanced
                }
            ]
        }
        
        response = await self.client.post("/gl/journal-entries", json=unbalanced_data)
        self.print_result("Reject Unbalanced Entry", response.status_code == 400,
                         f"Correctly rejected: {'Debits must equal credits' in response.text if response.status_code == 400 else 'Failed'}")
        
        # Test 3: Reverse journal entry
        response = await self.client.post(
            f"/gl/journal-entries/{journal_data['journal_entry_id']}/reverse",
            json={
                "reversal_date": date.today().isoformat(),
                "reversal_reference": "REV-001"
            }
        )
        self.print_result("Reverse Journal Entry", response.status_code == 201,
                         f"Reversal created: {len(response.json()) if response.status_code == 201 else 'Failed'} transactions")
        
    async def test_reports(self, account_id: int):
        """Test GL Reports (REQ-GL-REPORT-*)"""
        print(f"\n{Colors.BLUE}=== GL Reports Tests ==={Colors.RESET}")
        
        # Test 1: Trial Balance
        response = await self.client.get("/gl/reports/trial-balance", params={
            "report_date": date.today().isoformat()
        })
        if response.status_code == 200:
            trial_balance = response.json()
            total_debit = sum(float(line.get("debit", 0)) for line in trial_balance)
            total_credit = sum(float(line.get("credit", 0)) for line in trial_balance)
            is_balanced = abs(total_debit - total_credit) < 0.01
            self.print_result("Trial Balance Report", True, 
                            f"Accounts: {len(trial_balance)}, Balanced: {is_balanced}")
        else:
            self.print_result("Trial Balance Report", False, f"Status: {response.status_code}")
            
        # Test 2: GL Detail Report
        response = await self.client.get("/gl/reports/gl-detail", params={
            "account_id": account_id,
            "start_date": date(date.today().year, 1, 1).isoformat(),
            "end_date": date.today().isoformat()
        })
        if response.status_code == 200:
            gl_detail = response.json()
            self.print_result("GL Detail Report", True, f"Transactions: {len(gl_detail)}")
        else:
            self.print_result("GL Detail Report", False, f"Status: {response.status_code}")
            
        # Test 3: Export functionality
        response = await self.client.get("/gl/reports/trial-balance/export", params={
            "report_date": date.today().isoformat(),
            "format": "csv"
        })
        self.print_result("Export Trial Balance", response.status_code == 200,
                         f"Content-Type: {response.headers.get('content-type', 'Unknown')}")
        
    async def test_delete_operations(self):
        """Test delete operations"""
        print(f"\n{Colors.BLUE}=== Delete Operations Tests ==={Colors.RESET}")
        
        if self.created_accounts:
            # Try to delete account with transactions (should soft delete)
            account_id = self.created_accounts[0]
            response = await self.client.delete(f"/gl/accounts/{account_id}")
            self.print_result("Soft Delete Account", response.status_code == 204,
                            "Account marked as inactive due to transactions")
            
    async def run_all_tests(self):
        """Run all Phase 3 tests"""
        print(f"{Colors.BLUE}{'=' * 50}{Colors.RESET}")
        print(f"{Colors.BLUE}Phase 3 (General Ledger) Test Suite{Colors.RESET}")
        print(f"{Colors.BLUE}{'=' * 50}{Colors.RESET}")
        
        # Login first
        if not await self.login():
            print(f"\n{Colors.RED}Cannot proceed without authentication{Colors.RESET}")
            return
            
        # Run tests
        try:
            # Chart of Accounts tests
            accounts = await self.test_chart_of_accounts()
            if accounts:
                cash_id, expense_id, income_id = accounts
                
                # Journal Entry tests
                await self.test_journal_entries(cash_id, expense_id)
                
                # Report tests
                await self.test_reports(cash_id)
                
                # Delete tests
                await self.test_delete_operations()
                
        except Exception as e:
            print(f"\n{Colors.RED}Test execution error: {e}{Colors.RESET}")
            
        print(f"\n{Colors.BLUE}{'=' * 50}{Colors.RESET}")
        print(f"{Colors.BLUE}Test Suite Complete{Colors.RESET}")
        print(f"{Colors.BLUE}{'=' * 50}{Colors.RESET}")

async def main():
    async with GLTester() as tester:
        await tester.run_all_tests()

if __name__ == "__main__":
    asyncio.run(main()) 