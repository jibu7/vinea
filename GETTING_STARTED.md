# Getting Started with vinea Core ERP

## 🎯 Quick Start Guide

Based on your PRD, here's exactly where to begin building your ERP system.

## Step 1: Environment Setup

### Prerequisites
```bash
# Install required software
- Node.js (v18+)
- Python (v3.9+)
- PostgreSQL (v14+)
- Git
```

### Development Tools
```bash
# Install package managers
npm install -g pnpm  # For frontend
pip install poetry   # For backend dependency management

# Install VS Code extensions (recommended)
- Python
- TypeScript
- Tailwind CSS IntelliSense
- PostgreSQL
- REST Client
```

## Step 2: Project Structure Creation

Create the following directory structure:

```
vinea/
├── frontend/                 # Next.js + React + TypeScript
│   ├── src/
│   │   ├── app/             # Next.js 13+ app directory
│   │   ├── components/      # Reusable UI components
│   │   ├── hooks/           # Custom React hooks
│   │   ├── lib/             # Utilities and configurations
│   │   ├── types/           # TypeScript type definitions
│   │   └── styles/          # Global styles
│   ├── public/              # Static assets
│   └── package.json
├── backend/                  # FastAPI + SQLAlchemy
│   ├── app/
│   │   ├── api/             # API routes
│   │   ├── core/            # Core functionality (auth, config)
│   │   ├── models/          # SQLAlchemy models
│   │   ├── schemas/         # Pydantic schemas
│   │   ├── services/        # Business logic
│   │   └── main.py          # FastAPI app entry point
│   ├── alembic/             # Database migrations
│   ├── tests/               # Backend tests
│   └── pyproject.toml       # Python dependencies
├── database/
│   ├── migrations/          # SQL migration scripts
│   ├── seeds/               # Initial data
│   └── schema.sql           # Database schema
├── docker/
│   ├── Dockerfile.frontend
│   ├── Dockerfile.backend
│   └── docker-compose.yml
├── docs/                    # Additional documentation
└── README.md
```

## Step 3: Backend Setup (FastAPI + PostgreSQL)

### 3.1 Initialize Backend Project
```bash
cd backend
poetry init
poetry add fastapi uvicorn sqlalchemy psycopg2-binary alembic python-jose[cryptography] passlib[bcrypt] python-multipart
poetry add --group dev pytest httpx black isort mypy
```

### 3.2 Create Core Backend Files

**app/main.py** (FastAPI entry point):
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import auth, users, companies
from app.core.config import settings

app = FastAPI(title="vinea Core ERP API", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_HOSTS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["authentication"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(companies.router, prefix="/api/companies", tags=["companies"])

@app.get("/")
def read_root():
    return {"message": "vinea Core ERP API"}
```

### 3.3 Database Configuration
```python
# app/core/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://username:password@localhost/vinea_db"
    SECRET_KEY: str = "your-secret-key-here"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    ALLOWED_HOSTS: list = ["http://localhost:3000"]
    
    class Config:
        env_file = ".env"

settings = Settings()
```

## Step 4: Frontend Setup (Next.js + TypeScript)

### 4.1 Initialize Frontend Project
```bash
cd frontend
npx create-next-app@latest . --typescript --tailwind --eslint --app --src-dir
pnpm add @tanstack/react-query axios react-hook-form @hookform/resolvers zod lucide-react
pnpm add --save-dev @types/node
```

### 4.2 Configure Authentication Context
```typescript
// src/lib/auth.tsx
'use client'
import { createContext, useContext, useState } from 'react'

interface AuthContextType {
  user: User | null
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  isAuthenticated: boolean
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within AuthProvider')
  return context
}
```

## Step 5: Database Schema Design

### 5.1 Core Tables (Start with these)

Based on your PRD requirements, create these essential tables first:

```sql
-- Companies
CREATE TABLE companies (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    address TEXT,
    contact_info JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Users (Agents)
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    company_id INTEGER REFERENCES companies(id),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Roles
CREATE TABLE roles (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    permissions JSONB,
    company_id INTEGER REFERENCES companies(id)
);

-- User Roles (Many-to-Many)
CREATE TABLE user_roles (
    user_id INTEGER REFERENCES users(id),
    role_id INTEGER REFERENCES roles(id),
    PRIMARY KEY (user_id, role_id)
);

-- Chart of Accounts
CREATE TABLE gl_accounts (
    id SERIAL PRIMARY KEY,
    account_code VARCHAR(20) NOT NULL,
    account_name VARCHAR(255) NOT NULL,
    account_type VARCHAR(50) NOT NULL, -- Assets, Liabilities, Equity, Income, Expense
    parent_account_id INTEGER REFERENCES gl_accounts(id),
    balance DECIMAL(15,2) DEFAULT 0,
    company_id INTEGER REFERENCES companies(id),
    is_active BOOLEAN DEFAULT TRUE
);
```

## Step 6: Implementation Priority Order

### Week 1-2: Foundation
1. **Set up the project structure above**
2. **Implement basic authentication** (REQ-SYS-UM-001 to 005)
3. **Create user management API** (CRUD operations)
4. **Build login/logout UI components**

### Week 3-4: Core System
1. **Implement RBAC system** (REQ-SYS-RBAC-001 to 003)
2. **Company setup functionality** (REQ-SYS-COMP-001 to 003)
3. **Basic admin dashboard**
4. **User role management interface**

### Week 5-6: General Ledger
1. **Chart of Accounts management** (REQ-GL-COA-001 to 003)
2. **Journal entry creation** (REQ-GL-JE-001 to 003)
3. **Trial Balance report** (REQ-GL-REPORT-001)

## Step 7: Development Commands

### Backend Development
```bash
cd backend
poetry shell                 # Activate virtual environment
uvicorn app.main:app --reload # Start development server
pytest                       # Run tests
alembic revision --autogenerate -m "message"  # Create migration
alembic upgrade head          # Apply migrations
```

### Frontend Development
```bash
cd frontend
pnpm dev                     # Start development server
pnpm build                   # Build for production
pnpm test                    # Run tests
```

### Database Setup
```bash
# Create PostgreSQL database
createdb vinea_db

# Run initial migrations
cd backend
alembic upgrade head
```

## Step 8: First Sprint Goals

**Sprint 1 (Week 1-2) - "Authentication & Foundation"**

**Backend Tasks:**
- [ ] FastAPI project setup with basic structure
- [ ] PostgreSQL connection and basic models
- [ ] JWT authentication endpoints
- [ ] User CRUD operations
- [ ] Basic RBAC implementation

**Frontend Tasks:**
- [ ] Next.js project with TypeScript
- [ ] Authentication pages (login/register)
- [ ] Protected route wrapper
- [ ] Basic layout with navigation
- [ ] User management interface

**Definition of Done:**
- Users can register, login, and logout
- Admin can create and manage users
- Basic role assignment works
- API documentation is auto-generated
- All core infrastructure is tested

## 🎯 Success Metrics for Sprint 1

- [ ] Authentication flow completely functional
- [ ] User management CRUD operations work
- [ ] Frontend communicates with backend APIs
- [ ] Database schema for users/roles implemented
- [ ] Basic security (password hashing, JWT) working

## 📚 Recommended Learning Resources

1. **FastAPI Documentation**: https://fastapi.tiangolo.com/
2. **Next.js App Router**: https://nextjs.org/docs/app
3. **SQLAlchemy ORM**: https://docs.sqlalchemy.org/
4. **React Query**: https://tanstack.com/query/latest
5. **Tailwind CSS**: https://tailwindcss.com/docs

---

**Start with Step 1 and work through each step systematically. This approach ensures you build a solid foundation before moving to the complex ERP modules.**
