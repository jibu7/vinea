# vinea Core ERP - AI Development Instructions

## 🎯 Project Overview

You are building **vinea**, a web-based ERP system inspired by Sage Evolution. This document provides comprehensive instructions for AI coding agents to implement the system correctly.

**Core References:**
- Requirements: `PRD_rwanly_Core_ERP.md` (All REQ-* and NFR-* codes)
- Technical Design: `TECHNICAL_ARCHITECTURE.md` 
- Implementation Guide: `GETTING_STARTED.md`
- Development Phases: `DEVELOPMENT_ROADMAP.md`

## 🏗️ Technology Stack (Mandatory)

```
Frontend: Next.js 13+ (App Router) + React + TypeScript + Tailwind CSS
Backend: FastAPI + SQLAlchemy + Pydantic
Database: PostgreSQL 14+
Authentication: JWT (httpOnly cookies)
State Management: React Query + React Context
Forms: React Hook Form + Zod validation
```

## 📁 Project Structure (Create Exactly)

```
vinea/
├── frontend/
│   ├── src/
│   │   ├── app/                    # Next.js 13+ App Router
│   │   │   ├── (auth)/            # Auth pages (login, register)
│   │   │   ├── (dashboard)/       # Protected pages
│   │   │   │   ├── layout.tsx     # Dashboard layout with sidebar
│   │   │   │   ├── page.tsx       # Dashboard home
│   │   │   │   ├── users/         # User management
│   │   │   │   ├── companies/     # Company setup
│   │   │   │   ├── gl/            # General Ledger
│   │   │   │   ├── ar/            # Accounts Receivable
│   │   │   │   ├── ap/            # Accounts Payable
│   │   │   │   ├── inventory/     # Inventory
│   │   │   │   └── oe/            # Order Entry
│   │   │   ├── globals.css
│   │   │   ├── layout.tsx
│   │   │   └── page.tsx
│   │   ├── components/
│   │   │   ├── ui/                # Base components
│   │   │   ├── forms/             # Form components
│   │   │   ├── layout/            # Layout components
│   │   │   └── modules/           # Module-specific
│   │   ├── hooks/
│   │   ├── lib/
│   │   ├── types/
│   │   └── styles/
│   ├── public/
│   ├── package.json
│   └── tailwind.config.js
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py            # Authentication endpoints
│   │   │   ├── users.py           # User management
│   │   │   ├── companies.py       # Company management
│   │   │   ├── gl/                # GL module endpoints
│   │   │   ├── ar/                # AR module endpoints
│   │   │   ├── ap/                # AP module endpoints
│   │   │   ├── inventory/         # Inventory endpoints
│   │   │   └── oe/                # Order Entry endpoints
│   │   ├── core/
│   │   │   ├── config.py          # Settings
│   │   │   ├── security.py        # JWT, password hashing
│   │   │   └── database.py        # DB connection
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── user.py
│   │   │   ├── company.py
│   │   │   ├── gl.py
│   │   │   ├── ar.py
│   │   │   ├── ap.py
│   │   │   ├── inventory.py
│   │   │   └── oe.py
│   │   ├── schemas/                # Pydantic schemas
│   │   ├── services/               # Business logic
│   │   ├── dependencies.py         # FastAPI dependencies
│   │   └── main.py
│   ├── alembic/
│   ├── tests/
│   └── pyproject.toml
├── docker/
├── docs/
└── README.md
```

## 🗄️ Database Schema Implementation

### Phase 1: Core Tables (Implement First)

```sql
-- 1. Companies (REQ-SYS-COMP-*)
CREATE TABLE companies (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    address TEXT,
    contact_info JSONB,
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Users (REQ-SYS-UM-*)
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    is_active BOOLEAN DEFAULT TRUE,
    last_login TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Roles (REQ-SYS-RBAC-*)
CREATE TABLE roles (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    permissions JSONB NOT NULL DEFAULT '[]',
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    is_system BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, company_id)
);

-- 4. User Roles
CREATE TABLE user_roles (
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    role_id INTEGER REFERENCES roles(id) ON DELETE CASCADE,
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    assigned_by INTEGER REFERENCES users(id),
    PRIMARY KEY (user_id, role_id)
);

-- 5. Accounting Periods (REQ-SYS-PERIOD-*)
CREATE TABLE accounting_periods (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    period_name VARCHAR(100) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    is_closed BOOLEAN DEFAULT FALSE,
    financial_year INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, start_date, end_date)
);
```

### Phase 2: General Ledger Tables (REQ-GL-*)

```sql
-- GL Accounts (REQ-GL-COA-*)
CREATE TABLE gl_accounts (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    account_code VARCHAR(20) NOT NULL,
    account_name VARCHAR(255) NOT NULL,
    account_type VARCHAR(20) NOT NULL CHECK (account_type IN ('ASSET', 'LIABILITY', 'EQUITY', 'INCOME', 'EXPENSE')),
    parent_account_id INTEGER REFERENCES gl_accounts(id),
    current_balance DECIMAL(15,2) DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    is_system_account BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, account_code)
);

-- GL Transactions (REQ-GL-JE-*)
CREATE TABLE gl_transactions (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    journal_entry_id VARCHAR(50) NOT NULL,
    account_id INTEGER REFERENCES gl_accounts(id),
    transaction_date DATE NOT NULL,
    period_id INTEGER REFERENCES accounting_periods(id),
    description TEXT,
    debit_amount DECIMAL(15,2) DEFAULT 0,
    credit_amount DECIMAL(15,2) DEFAULT 0,
    reference VARCHAR(100),
    source_module VARCHAR(20), -- 'GL', 'AR', 'AP', 'INV', 'OE'
    source_document_id INTEGER,
    posted_by INTEGER REFERENCES users(id),
    posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_reversed BOOLEAN DEFAULT FALSE,
    CONSTRAINT check_amounts CHECK (
        (debit_amount = 0 AND credit_amount > 0) OR 
        (debit_amount > 0 AND credit_amount = 0)
    )
);

CREATE INDEX idx_gl_transactions_date ON gl_transactions(transaction_date);
CREATE INDEX idx_gl_transactions_account ON gl_transactions(account_id);
```

### Phase 3: Business Module Tables

```sql
-- Customers (REQ-AR-CUST-*)
CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    customer_code VARCHAR(20) NOT NULL,
    name VARCHAR(255) NOT NULL,
    address JSONB,
    contact_info JSONB,
    payment_terms INTEGER DEFAULT 30,
    credit_limit DECIMAL(15,2) DEFAULT 0,
    current_balance DECIMAL(15,2) DEFAULT 0,
    ar_account_id INTEGER REFERENCES gl_accounts(id),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, customer_code)
);

-- Similar structure for suppliers, inventory_items, etc.
-- See TECHNICAL_ARCHITECTURE.md for complete schema
```

## 🔧 Implementation Instructions by Phase

### PHASE 1: Foundation (Week 1-2)
**Reference:** `GETTING_STARTED.md` Steps 1-4

#### Backend Setup:
1. Initialize FastAPI project with exact structure above
2. Implement `app/core/config.py`:
```python
from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    PROJECT_NAME: str = "vinea Core ERP"
    VERSION: str = "1.0.0"
    API_PREFIX: str = "/api"
    
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000"]
    
    # Security
    BCRYPT_ROUNDS: int = 12
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
```

3. Implement JWT authentication (`app/core/security.py`):
   - Use `python-jose` for JWT
   - Use `passlib` with bcrypt for password hashing
   - Implement `create_access_token()` and `verify_token()` functions

4. Create SQLAlchemy models for Phase 1 tables
5. Implement authentication endpoints (REQ-SYS-UM-001 to 005):
   - POST `/api/auth/login`
   - POST `/api/auth/logout`
   - POST `/api/auth/refresh`
   - GET `/api/auth/me`

#### Frontend Setup:
1. Initialize Next.js with TypeScript and Tailwind
2. Create authentication context with JWT handling
3. Implement login/register pages
4. Create protected route wrapper
5. Build basic dashboard layout with sidebar navigation

### PHASE 2: Core System (Week 3-4)
**Requirements:** REQ-SYS-* series

1. **User Management (REQ-SYS-UM-*):**
   - CRUD endpoints for users
   - Password complexity validation (min 8 chars, mixed case, numbers, special chars)
   - User listing with pagination
   - User activation/deactivation

2. **RBAC Implementation (REQ-SYS-RBAC-*):**
   - Define permission structure in JSON:
   ```json
   {
     "gl": ["view", "create", "edit", "delete", "post"],
     "ar": ["view", "create", "edit", "delete", "post", "allocate"],
     "users": ["view", "create", "edit", "delete", "assign_roles"]
   }
   ```
   - Create permission checking decorator for FastAPI
   - Implement role management UI

3. **Company Setup (REQ-SYS-COMP-*):**
   - Company creation and configuration
   - Multi-company selection on login
   - Company-specific data isolation

4. **Accounting Periods (REQ-SYS-PERIOD-*):**
   - Period creation with date validation
   - Period closing functionality
   - Transaction date validation against open periods

### PHASE 3: General Ledger (Week 5-6)
**Requirements:** REQ-GL-* series

1. **Chart of Accounts (REQ-GL-COA-*):**
   - Account CRUD with hierarchical structure
   - Account type validation
   - Balance calculation logic
   - Account listing with tree view

2. **Journal Entries (REQ-GL-JE-*):**
   - Multi-line journal entry form
   - Debit/Credit balance validation
   - Posting to GL with transaction creation
   - Entry reversal functionality

3. **GL Reports (REQ-GL-REPORT-*):**
   - Trial Balance generation
   - Account detail report
   - Export to CSV/Excel

### PHASE 4-7: Business Modules
**Follow similar pattern for AR, AP, Inventory, and OE modules**
**Reference:** Requirements in PRD_rwanly_Core_ERP.md

## 🔐 Security Implementation (NFR-SEC-*)

1. **API Security:**
   - All endpoints must check JWT token
   - Implement rate limiting
   - Log all API access with user/timestamp
   - Use HTTPS in production

2. **RBAC Enforcement:**
```python
# Example permission checker
from functools import wraps
from fastapi import HTTPException, Depends

def require_permission(module: str, action: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, current_user: User = Depends(get_current_user), **kwargs):
            if not has_permission(current_user, module, action):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            return await func(*args, current_user=current_user, **kwargs)
        return wrapper
    return decorator

# Usage
@router.post("/gl/journal-entries")
@require_permission("gl", "post")
async def create_journal_entry(...):
    pass
```

3. **Data Security:**
   - Hash passwords with bcrypt (12 rounds)
   - Store JWT in httpOnly cookies
   - Implement CORS properly
   - Validate all inputs with Pydantic

## 🎨 Frontend Implementation Guidelines

1. **Component Structure:**
   - Use TypeScript for all components
   - Implement proper error boundaries
   - Use React Query for data fetching
   - Implement optimistic updates

2. **UI/UX Requirements (NFR-USABILITY-*):**
   - Responsive design for all screen sizes
   - Loading states for all async operations
   - Clear error messages
   - Consistent navigation patterns

3. **Form Handling:**
```typescript
// Example form with React Hook Form + Zod
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import * as z from 'zod'

const customerSchema = z.object({
  name: z.string().min(1, "Name is required"),
  customer_code: z.string().regex(/^[A-Z0-9]+$/, "Invalid code format"),
  email: z.string().email().optional(),
  credit_limit: z.number().min(0)
})

type CustomerForm = z.infer<typeof customerSchema>
```

## 📊 Testing Requirements

1. **Backend Tests:**
   - Unit tests for all services
   - Integration tests for API endpoints
   - Test RBAC for all protected endpoints

2. **Frontend Tests:**
   - Component tests with React Testing Library
   - Form validation tests
   - Authentication flow tests

## 🚀 Performance Requirements (NFR-PERF-*)

1. **Database Optimization:**
   - Create indexes as specified in TECHNICAL_ARCHITECTURE.md
   - Use database connection pooling
   - Implement query pagination

2. **Frontend Optimization:**
   - Implement code splitting by route
   - Use React.memo for expensive components
   - Implement virtual scrolling for large lists

## 📝 Module Integration Rules (REQ-CROSS-*)

1. **GL Posting Rules:**
   - Every financial transaction MUST post to GL
   - Maintain audit trail with source module/document
   - Ensure balanced entries (debits = credits)

2. **Document Flow:**
   - Sales Order → AR Invoice → GL Posting
   - Purchase Order → GRV → AP Invoice → GL Posting
   - All inventory movements → GL adjustment

## 🎯 Success Criteria

Before marking any phase complete, ensure:
1. All REQ-* requirements for that phase are implemented
2. All NFR-* requirements are met
3. Tests are written and passing
4. API documentation is updated
5. UI is responsive and follows design system
6. RBAC is properly enforced
7. Data integrity is maintained

## 💡 AI Agent Guidelines

1. **Always Reference Requirements:**
   - Every feature must trace to a REQ-* or NFR-* code
   - Check PRD_rwanly_Core_ERP.md for exact requirements

2. **Follow Architecture:**
   - Use exact folder structure provided
   - Follow naming conventions consistently
   - Implement patterns from TECHNICAL_ARCHITECTURE.md

3. **Security First:**
   - Never expose sensitive data
   - Always validate inputs
   - Check permissions before operations

4. **Test Everything:**
   - Write tests alongside implementation
   - Test edge cases and error scenarios
   - Verify cross-module integrations

5. **Documentation:**
   - Comment complex business logic
   - Update API docs automatically
   - Maintain README files

---

**Start with PHASE 1 and complete each phase before moving to the next. Use DEVELOPMENT_ROADMAP.md for detailed timelines.** 