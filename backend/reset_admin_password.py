#!/usr/bin/env python3
import asyncio
import sys
import os

# Add the current directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.database import get_db
from app.models.user import User
from app.core.security import get_password_hash, verify_password, create_access_token
from sqlalchemy import select, update

async def reset_admin_password():
    print("Resetting admin password...")
    
    try:
        async for db in get_db():
            # Find admin user
            result = await db.execute(select(User).where(User.username == 'admin'))
            admin = result.scalar_one_or_none()
            
            if not admin:
                print("✗ Admin user not found")
                return
            
            print(f"Found admin user: {admin.username} (ID: {admin.id})")
            
            # Generate new password hash
            new_password = "admin123"
            try:
                new_hash = get_password_hash(new_password)
                print("✓ Generated new password hash")
                
                # Update the password hash
                await db.execute(
                    update(User)
                    .where(User.id == admin.id)
                    .values(password_hash=new_hash)
                )
                await db.commit()
                print("✓ Updated admin password in database")
                
                # Test the new password
                if verify_password(new_password, new_hash):
                    print("✓ Password verification works with new hash")
                    
                    # Test token generation
                    token = create_access_token(data={"sub": str(admin.id)})
                    print("✓ Token generation works")
                    print(f"Sample token: {token[:50]}...")
                    
                else:
                    print("✗ Password verification still failed")
                    
            except Exception as e:
                print(f"✗ Error with password operations: {e}")
                await db.rollback()
            
            break
            
    except Exception as e:
        print(f"✗ Database connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(reset_admin_password()) 