# vinea Core ERP - Technical Architecture

## 🏗️ System Architecture Overview

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Frontend      │    │    Backend      │    │    Database     │
│   (Next.js)     │◄──►│   (FastAPI)     │◄──►│  (PostgreSQL)   │
│                 │    │                 │    │                 │
│ • React + TS    │    │ • Python 3.9+  │    │ • Tables        │
│ • Tailwind CSS  │    │ • SQLAlchemy    │    │ • Indexes       │
│ • React Query   │    │ • Pydantic      │    │ • Constraints   │
│ • Auth Context  │    │ • JWT Auth      │    │ • Triggers      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## 🎯 Module Architecture (Based on PRD Requirements)

### Core System Modules
```
┌────────────────────────────────────────────────────────────┐
│                    Core System Layer                       │
├────────────────────────────────────────────────────────────┤
│ • User Management (REQ-SYS-UM-*)                          │
│ • Role-Based Access Control (REQ-SYS-RBAC-*)              │
│ • Company Setup (REQ-SYS-COMP-*)                          │
│ • Accounting Periods (REQ-SYS-PERIOD-*)                   │
│ • Configuration & Defaults (REQ-SYS-CONFIG-*)             │
│ • Master File Management (REQ-SYS-MF-*)                   │
│ • Reporting Framework (REQ-SYS-REPORT-*)                  │
└────────────────────────────────────────────────────────────┘
```

### Business Modules
```
┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│ General     │ │ Accounts    │ │ Accounts    │ │ Inventory   │ │ Order Entry │
│ Ledger (GL) │ │ Receivable  │ │ Payable     │ │ Management  │ │ (OE)        │
│             │ │ (AR)        │ │ (AP)        │ │             │ │             │
│ REQ-GL-*    │ │ REQ-AR-*    │ │ REQ-AP-*    │ │ REQ-INV-*   │ │ REQ-OE-*    │
└─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘
```

### Cross-Module Integration
```
┌────────────────────────────────────────────────────────────┐
│               Integration Layer (REQ-CROSS-*)               │
├────────────────────────────────────────────────────────────┤
│ • OE → AR Integration (Sales Orders to Invoices)          │
│ • OE → AP Integration (Purchase Orders to Supplier Bills)  │
│ • OE → Inventory (GRV updates stock levels)               │
│ • All Modules → GL (Financial postings)                   │
└────────────────────────────────────────────────────────────┘
```

## 🗄️ Database Schema Design

### Core Tables Structure

```sql
-- Company Management
companies
├── id (PK)
├── name
├── address
├── contact_info (JSONB)
├── settings (JSONB)
└── created_at

-- User Management (REQ-SYS-UM-*)
users
├── id (PK)
├── username (UNIQUE)
├── email (UNIQUE)
├── password_hash
├── company_id (FK → companies.id)
├── is_active
├── first_name
├── last_name
└── created_at

-- Role-Based Access Control (REQ-SYS-RBAC-*)
roles
├── id (PK)
├── name
├── description
├── permissions (JSONB)
├── company_id (FK → companies.id)
└── created_at

user_roles (Many-to-Many)
├── user_id (FK → users.id)
└── role_id (FK → roles.id)

-- Accounting Periods (REQ-SYS-PERIOD-*)
accounting_periods
├── id (PK)
├── company_id (FK → companies.id)
├── period_name
├── start_date
├── end_date
├── is_closed
└── financial_year

-- General Ledger (REQ-GL-*)
gl_accounts
├── id (PK)
├── company_id (FK → companies.id)
├── account_code (UNIQUE per company)
├── account_name
├── account_type (Assets/Liabilities/Equity/Income/Expense)
├── parent_account_id (FK → gl_accounts.id)
├── current_balance (DECIMAL)
├── is_active
└── created_at

gl_transactions
├── id (PK)
├── company_id (FK → companies.id)
├── journal_entry_id
├── account_id (FK → gl_accounts.id)
├── transaction_date
├── description
├── debit_amount (DECIMAL)
├── credit_amount (DECIMAL)
├── reference
├── posted_by (FK → users.id)
└── created_at

-- Customers (REQ-AR-CUST-*)
customers
├── id (PK)
├── company_id (FK → companies.id)
├── customer_code (UNIQUE per company)
├── name
├── address (JSONB)
├── contact_info (JSONB)
├── payment_terms
├── credit_limit (DECIMAL)
├── current_balance (DECIMAL)
├── is_active
└── created_at

-- Suppliers (REQ-AP-SUPP-*)
suppliers
├── id (PK)
├── company_id (FK → companies.id)
├── supplier_code (UNIQUE per company)
├── name
├── address (JSONB)
├── contact_info (JSONB)
├── payment_terms
├── current_balance (DECIMAL)
├── is_active
└── created_at

-- Inventory Items (REQ-INV-ITEM-*)
inventory_items
├── id (PK)
├── company_id (FK → companies.id)
├── item_code (UNIQUE per company)
├── description
├── item_type (Stock/Service)
├── unit_of_measure
├── cost_price (DECIMAL)
├── selling_price (DECIMAL)
├── quantity_on_hand (DECIMAL)
├── costing_method (Weighted Average)
├── is_active
└── created_at
```

## 🔌 API Design Structure

### Authentication Endpoints
```
POST   /api/auth/login
POST   /api/auth/logout
POST   /api/auth/refresh
GET    /api/auth/me
```

### Core System APIs
```
# User Management (REQ-SYS-UM-*)
GET    /api/users
POST   /api/users
GET    /api/users/{id}
PUT    /api/users/{id}
DELETE /api/users/{id}
POST   /api/users/{id}/assign-role

# Role Management (REQ-SYS-RBAC-*)
GET    /api/roles
POST   /api/roles
GET    /api/roles/{id}
PUT    /api/roles/{id}
DELETE /api/roles/{id}

# Company Management (REQ-SYS-COMP-*)
GET    /api/companies
POST   /api/companies
GET    /api/companies/{id}
PUT    /api/companies/{id}
```

### Business Module APIs
```
# General Ledger (REQ-GL-*)
GET    /api/gl/accounts
POST   /api/gl/accounts
GET    /api/gl/accounts/{id}
PUT    /api/gl/accounts/{id}
POST   /api/gl/journal-entries
GET    /api/gl/trial-balance

# Accounts Receivable (REQ-AR-*)
GET    /api/ar/customers
POST   /api/ar/customers
POST   /api/ar/transactions
POST   /api/ar/allocations
GET    /api/ar/aging-report

# Similar patterns for AP, Inventory, and OE
```

## 🎨 Frontend Architecture

### Component Structure
```
src/
├── app/                    # Next.js 13+ App Router
│   ├── (auth)/            # Auth-related pages
│   ├── (dashboard)/       # Main application pages
│   ├── globals.css
│   ├── layout.tsx
│   └── page.tsx
├── components/
│   ├── ui/                # Base UI components
│   │   ├── Button.tsx
│   │   ├── Input.tsx
│   │   ├── Table.tsx
│   │   └── Modal.tsx
│   ├── forms/             # Form components
│   │   ├── UserForm.tsx
│   │   ├── CustomerForm.tsx
│   │   └── JournalEntryForm.tsx
│   ├── layout/            # Layout components
│   │   ├── Navbar.tsx
│   │   ├── Sidebar.tsx
│   │   └── PageHeader.tsx
│   └── modules/           # Module-specific components
│       ├── gl/
│       ├── ar/
│       ├── ap/
│       ├── inventory/
│       └── oe/
├── hooks/                 # Custom React hooks
│   ├── useAuth.ts
│   ├── useApi.ts
│   └── usePermissions.ts
├── lib/                   # Utilities and configs
│   ├── api.ts            # API client configuration
│   ├── auth.ts           # Authentication utilities
│   ├── permissions.ts    # RBAC utilities
│   └── utils.ts          # General utilities
├── types/                # TypeScript type definitions
│   ├── auth.ts
│   ├── api.ts
│   └── business.ts
└── styles/               # Global styles
```

### State Management Strategy
```typescript
// Use React Query for server state
import { useQuery, useMutation } from '@tanstack/react-query'

// Use React Context for global app state
interface AppState {
  user: User | null
  company: Company | null
  permissions: Permission[]
}

// Use React Hook Form for form state
import { useForm } from 'react-hook-form'
```

## 🔒 Security Implementation

### Authentication Flow (NFR-SEC-*)
```
1. User Login → Backend validates → JWT token generated
2. Frontend stores JWT in httpOnly cookie
3. All API requests include JWT in Authorization header
4. Backend validates JWT on each request
5. RBAC permissions checked per endpoint
```

### RBAC Implementation
```typescript
// Permission checking utility
export const hasPermission = (
  userPermissions: string[],
  requiredPermission: string
): boolean => {
  return userPermissions.includes(requiredPermission)
}

// React component protection
export const ProtectedComponent = ({ 
  permission, 
  children 
}: {
  permission: string
  children: React.ReactNode
}) => {
  const { permissions } = useAuth()
  
  if (!hasPermission(permissions, permission)) {
    return <div>Access Denied</div>
  }
  
  return <>{children}</>
}
```

## 📊 Performance Considerations (NFR-PERF-*)

### Database Optimization
```sql
-- Essential indexes for performance
CREATE INDEX idx_users_company_id ON users(company_id);
CREATE INDEX idx_gl_transactions_account_date ON gl_transactions(account_id, transaction_date);
CREATE INDEX idx_customers_company_id ON customers(company_id);
CREATE INDEX idx_gl_accounts_company_code ON gl_accounts(company_id, account_code);
```

### Frontend Optimization
```typescript
// Code splitting by route
const GLModule = lazy(() => import('./modules/gl'))
const ARModule = lazy(() => import('./modules/ar'))

// Data fetching optimization
const { data, isLoading } = useQuery({
  queryKey: ['customers', companyId],
  queryFn: () => api.getCustomers(companyId),
  staleTime: 5 * 60 * 1000, // 5 minutes
})
```

## 🚀 Deployment Architecture

### Production Stack
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Load Balancer │    │   Web Server    │    │    Database     │
│   (Nginx)       │◄──►│   (Docker)      │◄──►│  (PostgreSQL)   │
│                 │    │                 │    │   + Backups     │
│ • SSL/TLS       │    │ • Frontend      │    │                 │
│ • Rate Limiting │    │ • Backend API   │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

### Docker Configuration
```yaml
# docker-compose.yml
version: '3.8'
services:
  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    environment:
      - NEXT_PUBLIC_API_URL=http://backend:8000
  
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://user:pass@db:5432/vinea
    depends_on:
      - db
  
  db:
    image: postgres:14
    environment:
      - POSTGRES_DB=vinea
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=pass
    volumes:
      - postgres_data:/var/lib/postgresql/data
```

## 📈 Monitoring & Logging

### Application Monitoring
```python
# Backend logging
import logging
from fastapi import Request

logger = logging.getLogger(__name__)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    
    logger.info(
        f"{request.method} {request.url} - "
        f"{response.status_code} - {process_time:.2f}s"
    )
    return response
```

This technical architecture provides the foundation for implementing all requirements from your PRD systematically and scalably.
