import requests
import json
from datetime import datetime, date
from decimal import Decimal
import sys

# Configuration
BASE_URL = "http://localhost:8000/api"
USERNAME = "admin"
PASSWORD = "admin123"

# Colors for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def print_test_header(test_name):
    print(f"\n{BLUE}{'='*60}")
    print(f"Testing: {test_name}")
    print(f"{'='*60}{RESET}")

def print_success(message):
    print(f"{GREEN}✓ {message}{RESET}")

def print_error(message):
    print(f"{RED}✗ {message}{RESET}")

def print_info(message):
    print(f"{YELLOW}ℹ {message}{RESET}")

class InventoryTestSuite:
    def __init__(self):
        self.session = requests.Session()
        self.access_token = None
        self.company_id = None
        self.test_item_ids = []
        self.test_tt_ids = []
        
    def login(self):
        """Login and get access token"""
        print_test_header("Authentication")
        
        response = self.session.post(
            f"{BASE_URL}/auth/login",
            json={
                "username": USERNAME,
                "password": PASSWORD
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            self.access_token = data["access_token"]
            self.session.headers.update({
                "Authorization": f"Bearer {self.access_token}"
            })
            print_success(f"Logged in as {USERNAME}")
            
            # Get user info
            user_response = self.session.get(f"{BASE_URL}/auth/me")
            if user_response.status_code == 200:
                user_data = user_response.json()
                self.company_id = user_data["company_id"]
                print_success(f"Company ID: {self.company_id}")
            return True
        else:
            print_error(f"Login failed: {response.status_code} - {response.text}")
            return False
    
    def test_inventory_items(self):
        """Test inventory item CRUD operations"""
        print_test_header("Inventory Items CRUD")
        
        # 1. Create a new inventory item
        print_info("Creating new inventory item...")
        new_item = {
            "item_code": "TEST-ITEM-001",
            "description": "Test Inventory Item",
            "item_type": "Stock",
            "unit_of_measure": "EACH",
            "cost_price": 25.50,
            "selling_price": 45.00,
            "costing_method": "Weighted Average",
            "is_active": True
        }
        
        response = self.session.post(f"{BASE_URL}/inventory/items", json=new_item)
        if response.status_code == 200:
            item_data = response.json()
            self.test_item_ids.append(item_data["id"])
            print_success(f"Created item: {item_data['item_code']} - {item_data['description']}")
            print_info(f"Initial quantity: {item_data['quantity_on_hand']}")
        else:
            print_error(f"Failed to create item: {response.status_code} - {response.text}")
            return
        
        # 2. List inventory items
        print_info("\nListing inventory items...")
        response = self.session.get(f"{BASE_URL}/inventory/items")
        if response.status_code == 200:
            items = response.json()
            print_success(f"Found {len(items)} items")
            for item in items[:3]:  # Show first 3
                print(f"  - {item['item_code']}: {item['description']} (Qty: {item['quantity_on_hand']})")
        
        # 3. Get specific item
        print_info("\nGetting specific item...")
        response = self.session.get(f"{BASE_URL}/inventory/items/{self.test_item_ids[0]}")
        if response.status_code == 200:
            item = response.json()
            print_success(f"Retrieved item: {item['item_code']}")
        
        # 4. Update item
        print_info("\nUpdating item...")
        update_data = {
            "description": "Updated Test Item",
            "selling_price": 50.00
        }
        response = self.session.put(f"{BASE_URL}/inventory/items/{self.test_item_ids[0]}", json=update_data)
        if response.status_code == 200:
            updated = response.json()
            print_success(f"Updated item: {updated['description']}, Price: ${updated['selling_price']}")
        
        # 5. Search items
        print_info("\nSearching items...")
        response = self.session.get(f"{BASE_URL}/inventory/items", params={"search": "TEST"})
        if response.status_code == 200:
            items = response.json()
            print_success(f"Search found {len(items)} items matching 'TEST'")
    
    def test_transaction_types(self):
        """Test inventory transaction types"""
        print_test_header("Inventory Transaction Types")
        
        # List existing transaction types
        response = self.session.get(f"{BASE_URL}/inventory/transaction-types")
        if response.status_code == 200:
            tt_list = response.json()
            print_success(f"Found {len(tt_list)} transaction types:")
            for tt in tt_list:
                print(f"  - {tt['code']}: {tt['description']} ({'Increase' if tt['is_increase'] else 'Decrease'})")
            
            # Store IDs for adjustment testing
            self.adjustment_inc_tt = next(tt for tt in tt_list if tt['code'] == 'INV-ADJ-INC')
            self.adjustment_dec_tt = next(tt for tt in tt_list if tt['code'] == 'INV-ADJ-DEC')
    
    def test_inventory_adjustments(self):
        """Test inventory adjustments with GL integration"""
        print_test_header("Inventory Adjustments")
        
        if not self.test_item_ids:
            print_error("No test items available")
            return
        
        # Get item before adjustment
        response = self.session.get(f"{BASE_URL}/inventory/items/{self.test_item_ids[0]}")
        if response.status_code != 200:
            print_error("Failed to get item")
            return
        
        item_before = response.json()
        print_info(f"Item before adjustment: {item_before['item_code']}, Qty: {item_before['quantity_on_hand']}")
        
        # 1. Increase adjustment
        print_info("\nProcessing inventory increase...")
        adjustment = {
            "item_id": self.test_item_ids[0],
            "transaction_type_id": self.adjustment_inc_tt['id'],
            "transaction_date": datetime.now().isoformat(),
            "quantity": 50.0,
            "reference": "TEST-ADJ-001",
            "description": "Test inventory increase"
        }
        
        response = self.session.post(f"{BASE_URL}/inventory/adjustments", json=adjustment)
        if response.status_code == 200:
            adj_data = response.json()
            print_success(f"Inventory increased by {adj_data['quantity']}")
            
            # Check updated quantity
            item_response = self.session.get(f"{BASE_URL}/inventory/items/{self.test_item_ids[0]}")
            if item_response.status_code == 200:
                item_after = item_response.json()
                print_success(f"New quantity: {item_after['quantity_on_hand']}")
                print_info(f"Cost price updated: ${item_after['cost_price']}")
        else:
            print_error(f"Adjustment failed: {response.status_code} - {response.text}")
        
        # 2. Decrease adjustment
        print_info("\nProcessing inventory decrease...")
        adjustment = {
            "item_id": self.test_item_ids[0],
            "transaction_type_id": self.adjustment_dec_tt['id'],
            "transaction_date": datetime.now().isoformat(),
            "quantity": 10.0,
            "reference": "TEST-ADJ-002",
            "description": "Test inventory decrease"
        }
        
        response = self.session.post(f"{BASE_URL}/inventory/adjustments", json=adjustment)
        if response.status_code == 200:
            adj_data = response.json()
            print_success(f"Inventory decreased by {abs(adj_data['quantity'])}")
            
            # Check updated quantity
            item_response = self.session.get(f"{BASE_URL}/inventory/items/{self.test_item_ids[0]}")
            if item_response.status_code == 200:
                item_after = item_response.json()
                print_success(f"New quantity: {item_after['quantity_on_hand']}")
        
        # 3. Test insufficient quantity
        print_info("\nTesting insufficient quantity validation...")
        adjustment = {
            "item_id": self.test_item_ids[0],
            "transaction_type_id": self.adjustment_dec_tt['id'],
            "transaction_date": datetime.now().isoformat(),
            "quantity": 1000.0,  # More than available
            "reference": "TEST-ADJ-003",
            "description": "Test insufficient quantity"
        }
        
        response = self.session.post(f"{BASE_URL}/inventory/adjustments", json=adjustment)
        if response.status_code == 400:
            print_success("Correctly rejected adjustment with insufficient quantity")
        else:
            print_error("Should have rejected insufficient quantity adjustment")
    
    def test_transaction_history(self):
        """Test inventory transaction history"""
        print_test_header("Transaction History")
        
        if not self.test_item_ids:
            print_error("No test items available")
            return
        
        response = self.session.get(f"{BASE_URL}/inventory/items/{self.test_item_ids[0]}/transactions")
        if response.status_code == 200:
            transactions = response.json()
            print_success(f"Found {len(transactions)} transactions")
            for trans in transactions:
                print(f"  - Date: {trans['transaction_date'][:10]}, Qty: {trans['quantity']}, "
                      f"Cost: ${trans['total_cost']}, Ref: {trans['reference']}")
    
    def test_reports(self):
        """Test inventory reports"""
        print_test_header("Inventory Reports")
        
        # 1. Item Listing Report
        print_info("Generating item listing report...")
        response = self.session.get(f"{BASE_URL}/inventory/reports/item-listing")
        if response.status_code == 200:
            report = response.json()
            print_success(f"Item Listing Report - Total items: {report['summary']['total_items']}")
            print_info(f"Total inventory value: ${report['summary']['total_value']:,.2f}")
            
            # Show some items
            for item in report['data'][:3]:
                print(f"  - {item['item_code']}: {item['description']} "
                      f"(Qty: {item['quantity_on_hand']}, Value: ${item['total_value']:,.2f})")
        
        # 2. Stock Quantity Report
        print_info("\nGenerating stock quantity report...")
        report_params = {
            "item_type": "Stock",
            "show_zero_qty": False
        }
        response = self.session.post(f"{BASE_URL}/inventory/reports/stock-quantity", json=report_params)
        if response.status_code == 200:
            report = response.json()
            print_success(f"Stock Quantity Report - Total items: {report['summary']['total_items']}")
            print_info(f"Total stock value: ${report['summary']['total_value']:,.2f}")
    
    def verify_gl_integration(self):
        """Verify GL integration for inventory transactions"""
        print_test_header("GL Integration Verification")
        
        # Get GL transactions for today
        today = date.today().isoformat()
        response = self.session.get(f"{BASE_URL}/gl/transactions", params={
            "date_from": today,
            "date_to": today,
            "source_module": "INV"
        })
        
        if response.status_code == 200:
            transactions = response.json()
            inv_transactions = [t for t in transactions if t.get('source_module') == 'INV']
            print_success(f"Found {len(inv_transactions)} GL transactions from inventory module")
            
            for trans in inv_transactions[:5]:  # Show first 5
                print(f"  - Account: {trans['account_code']}, "
                      f"Dr: ${trans['debit_amount']}, Cr: ${trans['credit_amount']}, "
                      f"Desc: {trans['description']}")
    
    def cleanup(self):
        """Clean up test data"""
        print_test_header("Cleanup")
        
        # Delete test items (will be soft deleted if they have transactions)
        for item_id in self.test_item_ids:
            response = self.session.delete(f"{BASE_URL}/inventory/items/{item_id}")
            if response.status_code == 200:
                print_success(f"Deleted/deactivated test item ID: {item_id}")
    
    def run_all_tests(self):
        """Run all inventory tests"""
        print(f"\n{BLUE}{'='*60}")
        print("VINEA INVENTORY MODULE TEST SUITE")
        print(f"{'='*60}{RESET}")
        
        if not self.login():
            print_error("Failed to authenticate. Exiting.")
            return
        
        try:
            self.test_inventory_items()
            self.test_transaction_types()
            self.test_inventory_adjustments()
            self.test_transaction_history()
            self.test_reports()
            self.verify_gl_integration()
        except Exception as e:
            print_error(f"Test failed with error: {str(e)}")
            import traceback
            traceback.print_exc()
        finally:
            self.cleanup()
        
        print(f"\n{BLUE}{'='*60}")
        print("TEST SUITE COMPLETED")
        print(f"{'='*60}{RESET}")


if __name__ == "__main__":
    # Check if server is running
    try:
        response = requests.get("http://localhost:8000/health")
        if response.status_code != 200:
            print_error("Server is not responding. Please start the server first.")
            sys.exit(1)
    except requests.exceptions.ConnectionError:
        print_error("Cannot connect to server. Please ensure the server is running on http://localhost:8000")
        sys.exit(1)
    
    # Run tests
    test_suite = InventoryTestSuite()
    test_suite.run_all_tests() 