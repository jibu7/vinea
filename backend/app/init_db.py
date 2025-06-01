import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import engine, Base, AsyncSessionLocal
from app.core.security import get_password_hash
from app.models import Company, User, Role

async def create_initial_data():
    """Create initial data for the application"""
    async with AsyncSessionLocal() as db:
        # Check if any company exists
        result = await db.execute(select(Company))
        existing_company = result.scalar_one_or_none()
        
        if not existing_company:
            # Create default company
            company = Company(
                name="Default Company",
                address="123 Main St",
                contact_info={
                    "phone": "+1-555-0123",
                    "email": "info@defaultcompany.com"
                },
                settings={}
            )
            db.add(company)
            await db.commit()
            await db.refresh(company)
            print(f"Created company: {company.name}")
        else:
            company = existing_company
            print(f"Company already exists: {company.name}")
        
        # Check if admin user exists
        result = await db.execute(select(User).where(User.username == "admin"))
        existing_user = result.scalar_one_or_none()
        
        if not existing_user:
            # Create admin user
            admin_user = User(
                username="admin",
                email="admin@vinea.com",
                password_hash=get_password_hash("Admin@123"),
                first_name="System",
                last_name="Administrator",
                company_id=company.id,
                is_active=True,
                is_superuser=True
            )
            db.add(admin_user)
            await db.commit()
            print("Created admin user:")
            print("  Username: admin")
            print("  Password: Admin@123")
            print("  Email: admin@vinea.com")
        else:
            print("Admin user already exists")
        
        # Create default roles
        roles_data = [
            {
                "name": "Administrator",
                "description": "Full system access",
                "permissions": ["*"],
                "is_system": True
            },
            {
                "name": "User",
                "description": "Basic user access",
                "permissions": ["read"],
                "is_system": True
            }
        ]
        
        for role_data in roles_data:
            result = await db.execute(
                select(Role).where(
                    Role.name == role_data["name"],
                    Role.company_id == company.id
                )
            )
            existing_role = result.scalar_one_or_none()
            
            if not existing_role:
                role = Role(
                    company_id=company.id,
                    **role_data
                )
                db.add(role)
                await db.commit()
                print(f"Created role: {role.name}")
            else:
                print(f"Role already exists: {existing_role.name}")

async def init_db():
    """Initialize the database"""
    print("Creating database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables created.")
    
    print("\nCreating initial data...")
    await create_initial_data()
    print("\nDatabase initialization complete!")

if __name__ == "__main__":
    asyncio.run(init_db())
