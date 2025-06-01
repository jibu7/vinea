# vinea Core ERP - Quick Reference for AI Agents

## 🚀 Project Initialization Commands

### Backend Setup
```bash
# Create backend structure
mkdir -p backend/app/{api,core,models,schemas,services}
cd backend
poetry init -n
poetry add fastapi uvicorn[standard] sqlalchemy psycopg2-binary alembic pydantic pydantic-settings python-jose[cryptography] passlib[bcrypt] python-multipart httpx

# Dev dependencies
poetry add --group dev pytest pytest-asyncio pytest-cov black isort mypy sqlalchemy-stubs

# Create initial files
touch app/__init__.py app/main.py
touch app/core/{__init__.py,config.py,security.py,database.py}
touch app/models/{__init__.py,base.py,user.py,company.py}
touch app/schemas/{__init__.py,user.py,auth.py}
touch app/api/{__init__.py,auth.py,users.py,companies.py}
```

### Frontend Setup
```bash
# Create Next.js app with specific options
npx create-next-app@latest frontend --typescript --tailwind --eslint --app --src-dir --import-alias "@/*"
cd frontend

# Install required dependencies
pnpm add @tanstack/react-query @tanstack/react-query-devtools
pnpm add axios react-hook-form @hookform/resolvers zod
pnpm add lucide-react clsx tailwind-merge
pnpm add js-cookie
pnpm add --save-dev @types/js-cookie

# Create folder structure
mkdir -p src/{components/{ui,forms,layout,modules},hooks,lib,types,styles}
mkdir -p src/app/\(auth\)/{login,register}
mkdir -p src/app/\(dashboard\)/{users,companies,gl,ar,ap,inventory,oe}
```

### Database Setup
```bash
# Create database
createdb vinea_db

# Initialize Alembic
cd backend
alembic init alembic

# Update alembic.ini
# sqlalchemy.url = postgresql://user:password@localhost/vinea_db

# Create first migration
alembic revision --autogenerate -m "Initial schema"
alembic upgrade head
```

## 💻 Code Templates

### Backend: FastAPI Main App
```python
# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.database import engine, Base
from app.api import auth, users, companies, gl, ar, ap, inventory, oe

# Create tables on startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Shutdown

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_PREFIX}/openapi.json",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix=f"{settings.API_PREFIX}/auth", tags=["Authentication"])
app.include_router(users.router, prefix=f"{settings.API_PREFIX}/users", tags=["Users"])
app.include_router(companies.router, prefix=f"{settings.API_PREFIX}/companies", tags=["Companies"])

@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": settings.VERSION}
```

### Backend: Database Configuration
```python
# backend/app/core/database.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings

# Use async PostgreSQL URL
DATABASE_URL = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(DATABASE_URL, echo=settings.DEBUG)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
```

### Backend: JWT Security
```python
# backend/app/core/security.py
from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import jwt, JWTError
from passlib.context import CryptContext
from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt
```

### Frontend: Authentication Context
```typescript
// frontend/src/lib/auth-context.tsx
'use client'
import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'
import Cookies from 'js-cookie'

interface User {
  id: number
  username: string
  email: string
  roles: string[]
}

interface AuthContextType {
  user: User | null
  isLoading: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  checkAuth: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const router = useRouter()

  const login = async (username: string, password: string) => {
    const response = await axios.post('/api/auth/login', { username, password })
    const { access_token, user } = response.data
    Cookies.set('access_token', access_token, { secure: true, sameSite: 'strict' })
    setUser(user)
    router.push('/dashboard')
  }

  const logout = async () => {
    await axios.post('/api/auth/logout')
    Cookies.remove('access_token')
    setUser(null)
    router.push('/login')
  }

  const checkAuth = async () => {
    try {
      const response = await axios.get('/api/auth/me')
      setUser(response.data)
    } catch {
      setUser(null)
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    checkAuth()
  }, [])

  return (
    <AuthContext.Provider value={{ user, isLoading, login, logout, checkAuth }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within AuthProvider')
  return context
}
```

### Frontend: Protected Route Wrapper
```typescript
// frontend/src/components/auth/protected-route.tsx
'use client'
import { useAuth } from '@/lib/auth-context'
import { useRouter } from 'next/navigation'
import { useEffect } from 'react'

export function ProtectedRoute({ 
  children, 
  requiredPermission 
}: { 
  children: React.ReactNode
  requiredPermission?: string 
}) {
  const { user, isLoading } = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (!isLoading && !user) {
      router.push('/login')
    }
  }, [user, isLoading, router])

  if (isLoading) {
    return <div>Loading...</div>
  }

  if (!user) {
    return null
  }

  if (requiredPermission && !hasPermission(user, requiredPermission)) {
    return <div>Access Denied</div>
  }

  return <>{children}</>
}
```

## 🗄️ Database Schema Quick Reference

### Core Tables Creation Order
1. companies
2. users
3. roles
4. user_roles
5. accounting_periods
6. gl_accounts
7. transaction_types
8. customers
9. suppliers
10. inventory_items

### Common SQLAlchemy Model Pattern
```python
# backend/app/models/base_model.py
from sqlalchemy import Column, Integer, DateTime, func
from app.core.database import Base

class BaseModel(Base):
    __abstract__ = True
    
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
```

## 🔌 API Endpoint Patterns

### CRUD Pattern
```python
# GET /api/resource - List all (paginated)
# POST /api/resource - Create new
# GET /api/resource/{id} - Get single
# PUT /api/resource/{id} - Update
# DELETE /api/resource/{id} - Delete

# Example implementation
@router.get("/", response_model=List[UserSchema])
async def list_users(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    result = await db.execute(
        select(User).offset(skip).limit(limit)
    )
    return result.scalars().all()
```

## 🧪 Testing Commands

### Backend Tests
```bash
# Run all tests
poetry run pytest

# Run with coverage
poetry run pytest --cov=app --cov-report=html

# Run specific test file
poetry run pytest tests/test_auth.py -v
```

### Frontend Tests
```bash
# Run all tests
pnpm test

# Run in watch mode
pnpm test:watch

# Run with coverage
pnpm test:coverage
```

## 📝 Common Validation Patterns

### Pydantic Schema Example
```python
# backend/app/schemas/customer.py
from pydantic import BaseModel, EmailStr, validator
from typing import Optional
from decimal import Decimal

class CustomerBase(BaseModel):
    customer_code: str
    name: str
    email: Optional[EmailStr] = None
    payment_terms: int = 30
    credit_limit: Decimal = Decimal("0.00")

    @validator('customer_code')
    def validate_customer_code(cls, v):
        if not v.replace('-', '').replace('_', '').isalnum():
            raise ValueError('Customer code must be alphanumeric')
        return v.upper()

    @validator('credit_limit')
    def validate_credit_limit(cls, v):
        if v < 0:
            raise ValueError('Credit limit cannot be negative')
        return v
```

### Zod Schema Example
```typescript
// frontend/src/lib/validations/customer.ts
import * as z from 'zod'

export const customerSchema = z.object({
  customer_code: z.string()
    .min(1, "Customer code is required")
    .regex(/^[A-Z0-9-_]+$/, "Must be uppercase alphanumeric"),
  name: z.string().min(1, "Name is required"),
  email: z.string().email().optional().or(z.literal("")),
  payment_terms: z.number().int().min(0).max(365),
  credit_limit: z.number().min(0)
})

export type CustomerFormData = z.infer<typeof customerSchema>
```

## 🚨 Common Gotchas

1. **CORS Issues**: Ensure backend allows frontend origin
2. **JWT Storage**: Use httpOnly cookies, not localStorage
3. **Decimal Handling**: Use Decimal type for money, not float
4. **Date/Time**: Always store in UTC, convert for display
5. **Transactions**: Use database transactions for multi-table updates
6. **Permissions**: Check at both API and UI level
7. **Pagination**: Always paginate list endpoints
8. **N+1 Queries**: Use eager loading for relationships

## 📊 Performance Checklist

- [ ] Database indexes created
- [ ] API responses paginated
- [ ] Frontend code splitting enabled
- [ ] Images optimized
- [ ] Database connection pooling configured
- [ ] Caching strategy implemented
- [ ] Query optimization done
- [ ] Lazy loading for large lists

## 🔐 Security Checklist

- [ ] Password hashing with bcrypt
- [ ] JWT tokens expire
- [ ] HTTPS enforced
- [ ] Input validation on all endpoints
- [ ] SQL injection prevention (use ORM)
- [ ] XSS prevention (React handles this)
- [ ] CSRF protection
- [ ] Rate limiting implemented
- [ ] Audit logging enabled
- [ ] Sensitive data encrypted

---

**Use this quick reference alongside the main vinea_dev_instructions.md for rapid development.** 