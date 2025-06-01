import asyncio
from app.core.database import AsyncSessionLocal
from app.core.security import verify_password
from app.models.user import User
from sqlalchemy import select

async def test_login_credentials():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == 'admin'))
        user = result.scalar_one_or_none()
        
        if user:
            print(f'✓ User found: {user.username}')
            print(f'✓ User active: {user.is_active}')
            print(f'✓ Password hash exists: {bool(user.password_hash)}')
            
            # Test the exact credentials the user provided
            password_valid = verify_password('Admin@123', user.password_hash)
            print(f'✓ Password "Admin@123" valid: {password_valid}')
            
            if not password_valid:
                # Let's also test some common variations
                test_passwords = ['admin123', 'admin', 'password', 'test123']
                for pwd in test_passwords:
                    valid = verify_password(pwd, user.password_hash)
                    if valid:
                        print(f'✓ Found working password: {pwd}')
                        break
                else:
                    print('✗ None of the common passwords work')
                    
        else:
            print('✗ Admin user not found')

if __name__ == "__main__":
    asyncio.run(test_login_credentials()) 