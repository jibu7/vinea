"""Test script for Phase 7: Order Entry Module"""

import requests
import json
from datetime import datetime
from decimal import Decimal

# Base URL for the API
BASE_URL = "http://localhost:8000/api/v1"

# Test user credentials
TEST_USER = "admin"
TEST_PASSWORD = "admin123"

# Global variables to store created resources
auth_token = None
customer_id = None
supplier_id = None
item_id = None
so_doc_type_id = None
po_doc_type_id = None
grv_doc_type_id = None
sales_order_id = None
purchase_order_id = None
grv_id = None


def print_section(title):
    """Print a section header"""
    print(f"\n{'=' * 60}")
    print(f"{title}")
    print(f"{'=' * 60}")


def print_response(response):
    """Print formatted response"""
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200 or response.status_code == 201:
        print("Response:", json.dumps(response.json(), indent=2))
    else:
        print("Error:", response.text)


def login():
    """Login and get authentication token"""
    global auth_token
    print_section("1. Authentication")
    
    response = requests.post(
        f"{BASE_URL}/auth/login",
        data={
            "username": TEST_USER,
            "password": TEST_PASSWORD
        }
    )
    
    if response.status_code == 200:
        auth_token = response.json()["access_token"]
        print("✅ Login successful")
        return True
    else:
        print("❌ Login failed")
        print_response(response)
        return False


def get_headers():
    """Get headers with authentication token"""
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json"
    }


def test_oe_document_types():
    """Test OE document type endpoints"""
    global so_doc_type_id, po_doc_type_id, grv_doc_type_id
    print_section("2. Testing OE Document Types")
    
    # Get all document types
    print("\n2.1 Getting all OE document types:")
    response = requests.get(
        f"{BASE_URL}/oe/document-types",
        headers=get_headers()
    )
    print_response(response)
    
    if response.status_code == 200:
        doc_types = response.json()
        for doc_type in doc_types:
            if doc_type["code"] == "SO":
                so_doc_type_id = doc_type["id"]
            elif doc_type["code"] == "PO":
                po_doc_type_id = doc_type["id"]
            elif doc_type["code"] == "GRV":
                grv_doc_type_id = doc_type["id"]
    
    # Get sales document types
    print("\n2.2 Getting sales document types:")
    response = requests.get(
        f"{BASE_URL}/oe/document-types?document_class=SALES",
        headers=get_headers()
    )
    print_response(response)
    
    # Get specific document type
    print(f"\n2.3 Getting SO document type (ID: {so_doc_type_id}):")
    response = requests.get(
        f"{BASE_URL}/oe/document-types/{so_doc_type_id}",
        headers=get_headers()
    )
    print_response(response)


def setup_test_data():
    """Create test customer, supplier, and inventory item"""
    global customer_id, supplier_id, item_id
    print_section("3. Setting up test data")
    
    # Create customer
    print("\n3.1 Creating test customer:")
    response = requests.post(
        f"{BASE_URL}/customers",
        headers=get_headers(),
        json={
            "code": "CUST001",
            "name": "Test Customer",
            "address": "123 Test Street",
            "phone": "123-456-7890",
            "email": "test@customer.com",
            "credit_limit": 10000,
            "payment_terms_days": 30
        }
    )
    if response.status_code == 201:
        customer_id = response.json()["id"]
        print(f"✅ Customer created (ID: {customer_id})")
    else:
        # Try to get existing customer
        response = requests.get(
            f"{BASE_URL}/customers",
            headers=get_headers()
        )
        if response.status_code == 200:
            customers = response.json()
            if customers:
                customer_id = customers[0]["id"]
                print(f"✅ Using existing customer (ID: {customer_id})")
    
    # Create supplier
    print("\n3.2 Creating test supplier:")
    response = requests.post(
        f"{BASE_URL}/suppliers",
        headers=get_headers(),
        json={
            "code": "SUPP001",
            "name": "Test Supplier",
            "address": "456 Supplier Ave",
            "phone": "987-654-3210",
            "email": "test@supplier.com",
            "payment_terms_days": 45
        }
    )
    if response.status_code == 201:
        supplier_id = response.json()["id"]
        print(f"✅ Supplier created (ID: {supplier_id})")
    else:
        # Try to get existing supplier
        response = requests.get(
            f"{BASE_URL}/suppliers",
            headers=get_headers()
        )
        if response.status_code == 200:
            suppliers = response.json()
            if suppliers:
                supplier_id = suppliers[0]["id"]
                print(f"✅ Using existing supplier (ID: {supplier_id})")
    
    # Get inventory item
    print("\n3.3 Getting inventory item:")
    response = requests.get(
        f"{BASE_URL}/inventory/items",
        headers=get_headers()
    )
    if response.status_code == 200:
        items = response.json()
        if items:
            item_id = items[0]["id"]
            print(f"✅ Using inventory item (ID: {item_id})")
        else:
            print("❌ No inventory items found")


def test_sales_orders():
    """Test sales order endpoints"""
    global sales_order_id
    print_section("4. Testing Sales Orders")
    
    # Create sales order
    print("\n4.1 Creating sales order:")
    response = requests.post(
        f"{BASE_URL}/oe/sales-orders",
        headers=get_headers(),
        json={
            "customer_id": customer_id,
            "document_type_id": so_doc_type_id,
            "customer_po_number": "PO-2024-001",
            "notes": "Test sales order",
            "line_items": [
                {
                    "item_id": item_id,
                    "quantity": "5",
                    "unit_price": "100.00",
                    "discount_percent": "10",
                    "tax_percent": "15"
                }
            ]
        }
    )
    print_response(response)
    
    if response.status_code == 201:
        sales_order_id = response.json()["id"]
        print(f"✅ Sales order created (ID: {sales_order_id})")
    
    # Get sales order
    print(f"\n4.2 Getting sales order (ID: {sales_order_id}):")
    response = requests.get(
        f"{BASE_URL}/oe/sales-orders/{sales_order_id}",
        headers=get_headers()
    )
    print_response(response)
    
    # Update sales order
    print("\n4.3 Updating sales order:")
    response = requests.put(
        f"{BASE_URL}/oe/sales-orders/{sales_order_id}",
        headers=get_headers(),
        json={
            "notes": "Updated test sales order"
        }
    )
    print_response(response)
    
    # Confirm sales order
    print("\n4.4 Confirming sales order:")
    response = requests.post(
        f"{BASE_URL}/oe/sales-orders/{sales_order_id}/confirm",
        headers=get_headers()
    )
    print_response(response)
    
    # Get all sales orders
    print("\n4.5 Getting all sales orders:")
    response = requests.get(
        f"{BASE_URL}/oe/sales-orders",
        headers=get_headers()
    )
    print_response(response)


def test_purchase_orders():
    """Test purchase order endpoints"""
    global purchase_order_id
    print_section("5. Testing Purchase Orders")
    
    # Create purchase order
    print("\n5.1 Creating purchase order:")
    response = requests.post(
        f"{BASE_URL}/oe/purchase-orders",
        headers=get_headers(),
        json={
            "supplier_id": supplier_id,
            "document_type_id": po_doc_type_id,
            "supplier_ref": "SUP-REF-001",
            "delivery_address": "789 Warehouse St",
            "notes": "Test purchase order",
            "line_items": [
                {
                    "item_id": item_id,
                    "quantity": "10",
                    "unit_price": "80.00",
                    "discount_percent": "5",
                    "tax_percent": "15"
                }
            ]
        }
    )
    print_response(response)
    
    if response.status_code == 201:
        purchase_order_id = response.json()["id"]
        print(f"✅ Purchase order created (ID: {purchase_order_id})")
    
    # Get purchase order
    print(f"\n5.2 Getting purchase order (ID: {purchase_order_id}):")
    response = requests.get(
        f"{BASE_URL}/oe/purchase-orders/{purchase_order_id}",
        headers=get_headers()
    )
    print_response(response)
    
    # Confirm purchase order
    print("\n5.3 Confirming purchase order:")
    response = requests.post(
        f"{BASE_URL}/oe/purchase-orders/{purchase_order_id}/confirm",
        headers=get_headers()
    )
    print_response(response)
    
    # Get open PO lines
    print("\n5.4 Getting open purchase order lines:")
    response = requests.get(
        f"{BASE_URL}/oe/purchase-orders/open-lines",
        headers=get_headers()
    )
    print_response(response)


def test_grvs():
    """Test goods received voucher endpoints"""
    global grv_id
    print_section("6. Testing Goods Received Vouchers")
    
    # Get PO lines for GRV
    print("\n6.1 Getting purchase order lines:")
    response = requests.get(
        f"{BASE_URL}/oe/purchase-orders/{purchase_order_id}",
        headers=get_headers()
    )
    po_data = response.json()
    po_line_id = po_data["line_items"][0]["id"]
    
    # Create GRV
    print("\n6.2 Creating GRV:")
    response = requests.post(
        f"{BASE_URL}/oe/grvs",
        headers=get_headers(),
        json={
            "purchase_order_id": purchase_order_id,
            "document_type_id": grv_doc_type_id,
            "delivery_note_number": "DN-001",
            "notes": "Test GRV",
            "line_items": [
                {
                    "po_line_id": po_line_id,
                    "received_quantity": "8",
                    "location": "Warehouse A",
                    "quality_status": "PASSED"
                }
            ]
        }
    )
    print_response(response)
    
    if response.status_code == 201:
        grv_id = response.json()["id"]
        print(f"✅ GRV created (ID: {grv_id})")
    
    # Get GRV
    print(f"\n6.3 Getting GRV (ID: {grv_id}):")
    response = requests.get(
        f"{BASE_URL}/oe/grvs/{grv_id}",
        headers=get_headers()
    )
    print_response(response)
    
    # Post GRV to inventory
    print("\n6.4 Posting GRV to inventory:")
    response = requests.post(
        f"{BASE_URL}/oe/grvs/{grv_id}/post-to-inventory",
        headers=get_headers()
    )
    print_response(response)
    
    # Get all GRVs
    print("\n6.5 Getting all GRVs:")
    response = requests.get(
        f"{BASE_URL}/oe/grvs",
        headers=get_headers()
    )
    print_response(response)


def test_order_to_invoice_conversion():
    """Test converting orders to invoices"""
    print_section("7. Testing Order to Invoice Conversion")
    
    # Convert sales order to invoice
    print("\n7.1 Converting sales order to AR invoice:")
    response = requests.post(
        f"{BASE_URL}/oe/sales-orders/convert-to-invoice",
        headers=get_headers(),
        json={
            "sales_order_id": sales_order_id
        }
    )
    print_response(response)
    
    # Convert GRV to supplier invoice
    print("\n7.2 Converting GRV to AP invoice:")
    response = requests.post(
        f"{BASE_URL}/oe/grvs/convert-to-invoice",
        headers=get_headers(),
        json={
            "grv_id": grv_id
        }
    )
    print_response(response)


def test_reporting():
    """Test order entry reports"""
    print_section("8. Testing Order Entry Reports")
    
    # Sales orders by customer
    print(f"\n8.1 Sales orders for customer {customer_id}:")
    response = requests.get(
        f"{BASE_URL}/oe/sales-orders?customer_id={customer_id}",
        headers=get_headers()
    )
    print_response(response)
    
    # Purchase orders by supplier
    print(f"\n8.2 Purchase orders for supplier {supplier_id}:")
    response = requests.get(
        f"{BASE_URL}/oe/purchase-orders?supplier_id={supplier_id}",
        headers=get_headers()
    )
    print_response(response)
    
    # GRVs by status
    print("\n8.3 Posted GRVs:")
    response = requests.get(
        f"{BASE_URL}/oe/grvs?status=POSTED",
        headers=get_headers()
    )
    print_response(response)


def main():
    """Main test function"""
    print("=" * 60)
    print("Phase 7: Order Entry Module Test Script")
    print("=" * 60)
    
    if not login():
        print("❌ Authentication failed. Exiting.")
        return
    
    # Run tests
    test_oe_document_types()
    setup_test_data()
    
    if customer_id and supplier_id and item_id:
        test_sales_orders()
        test_purchase_orders()
        
        if purchase_order_id:
            test_grvs()
            
            if sales_order_id and grv_id:
                test_order_to_invoice_conversion()
        
        test_reporting()
    else:
        print("❌ Failed to set up test data. Some tests skipped.")
    
    print("\n" + "=" * 60)
    print("✅ Order Entry Module testing completed!")
    print("=" * 60)


if __name__ == "__main__":
    main() 