#!/usr/bin/env python3
import asyncio
import sys
import os

# Add the current directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.database import get_db
from app.models.user import User
from app.core.security import verify_password, create_access_token
from sqlalchemy import select

async def check_admin_and_auth():
    print("Checking database connection and admin user...")
    
    try:
        async for db in get_db():
            # Check admin user
            result = await db.execute(select(User).where(User.username == 'admin'))
            admin = result.scalar_one_or_none()
            
            if admin:
                print(f"✓ Admin user found:")
                print(f"  - ID: {admin.id}")
                print(f"  - Username: {admin.username}")
                print(f"  - Active: {admin.is_active}")
                print(f"  - Company ID: {admin.company_id}")
                print(f"  - Password hash exists: {bool(admin.password_hash)}")
                
                # Test password verification
                if verify_password("admin123", admin.password_hash):
                    print("✓ Password verification works")
                    
                    # Test token creation
                    try:
                        token = create_access_token(data={"sub": str(admin.id)})
                        print("✓ Token generation works")
                        print(f"  Sample token: {token[:50]}...")
                    except Exception as e:
                        print(f"✗ Token generation failed: {e}")
                else:
                    print("✗ Password verification failed")
            else:
                print("✗ Admin user not found")
            
            break
            
    except Exception as e:
        print(f"✗ Database connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(check_admin_and_auth()) 