#!/usr/bin/env python3
"""
Test script for Phase 2 implementation
Run with: python test_phase2.py
"""

import asyncio
import httpx
from datetime import date, timedelta

# Base URL for the API
BASE_URL = "http://localhost:8000/api"

# Test credentials
TEST_USERNAME = "admin"
TEST_PASSWORD = "Admin@123"
TEST_EMAIL = "admin@vinea.com"


async def create_test_company(client: httpx.AsyncClient, token: str):
    """Create a test company"""
    headers = {"Authorization": f"Bearer {token}"}
    response = await client.post(
        f"{BASE_URL}/companies/",
        json={
            "name": "Test Company",
            "address": "123 Test Street",
            "contact_info": {
                "phone": "555-1234",
                "email": "info@testcompany.com"
            }
        },
        headers=headers
    )
    if response.status_code != 201:
        print(f"❌ Failed to create company: {response.status_code} - {response.text}")
        return None
    return response.json()


async def create_test_role(client: httpx.AsyncClient, token: str, company_id: int):
    """Create a test role"""
    headers = {"Authorization": f"Bearer {token}"}
    response = await client.post(
        f"{BASE_URL}/roles/",
        json={
            "name": "Accountant",
            "description": "Accountant role with GL and AR permissions",
            "permissions": ["gl.view", "gl.create", "gl.edit", "ar.view", "ar.create"],
            "company_id": company_id
        },
        headers=headers
    )
    if response.status_code != 201:
        print(f"❌ Failed to create role: {response.status_code} - {response.text}")
        return None
    return response.json()


async def create_test_period(client: httpx.AsyncClient, token: str, company_id: int):
    """Create a test accounting period"""
    headers = {"Authorization": f"Bearer {token}"}
    today = date.today()
    start_date = date(today.year, 1, 1)
    end_date = date(today.year, 1, 31)
    
    response = await client.post(
        f"{BASE_URL}/accounting-periods/",
        json={
            "period_name": f"January {today.year}",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "financial_year": today.year,
            "company_id": company_id
        },
        headers=headers
    )
    if response.status_code != 201:
        print(f"❌ Failed to create accounting period: {response.status_code} - {response.text}")
        return None
    return response.json()


async def test_phase2():
    """Test Phase 2 implementation"""
    async with httpx.AsyncClient() as client:
        print("🚀 Testing Phase 2 Implementation")
        print("=" * 50)
        
        # 1. Login with existing admin user
        print("\n1. Logging in with existing admin user...")
        response = await client.post(
            f"{BASE_URL}/auth/login",
            json={
                "username": TEST_USERNAME,
                "password": TEST_PASSWORD
            }
        )
        if response.status_code == 200:
            token = response.json()["access_token"]
            print("✅ Login successful")
        else:
            print(f"❌ Login failed: {response.text}")
            return
        
        # 2. Create company
        print("\n2. Creating company...")
        company = await create_test_company(client, token)
        if company:
            print(f"✅ Company created: {company['name']}")
        else:
            print("❌ Company creation failed")
            return
        
        # 3. Create role
        print("\n3. Creating role...")
        role = await create_test_role(client, token, company['id'])
        if role:
            print(f"✅ Role created: {role['name']}")
        else:
            print("❌ Role creation failed")
            return
        
        # 4. Create accounting period
        print("\n4. Creating accounting period...")
        period = await create_test_period(client, token, company['id'])
        if period:
            print(f"✅ Accounting period created: {period['period_name']}")
        else:
            print("❌ Accounting period creation failed")
            return
        
        # 5. List users
        print("\n5. Listing users...")
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.get(f"{BASE_URL}/users/", headers=headers)
        users = response.json()
        print(f"✅ Found {users['total']} users")
        
        # 6. List roles
        print("\n6. Listing roles...")
        response = await client.get(f"{BASE_URL}/roles/", headers=headers)
        roles = response.json()
        print(f"✅ Found {roles['total']} roles")
        
        # 7. List accounting periods
        print("\n7. Listing accounting periods...")
        response = await client.get(f"{BASE_URL}/accounting-periods/", headers=headers)
        periods = response.json()
        print(f"✅ Found {periods['total']} accounting periods")
        
        print("\n" + "=" * 50)
        print("✨ Phase 2 Testing Complete!")
        print("\nYou can now:")
        print("- Access the frontend at http://localhost:3000")
        print("- Login with username: admin, password: Admin@123")
        print("- Manage users, roles, companies, and accounting periods")


if __name__ == "__main__":
    asyncio.run(test_phase2()) 