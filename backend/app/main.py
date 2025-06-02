from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.database import engine, Base
from app.api import auth, users, companies, roles, accounting_periods, gl, customers, ar, suppliers, ap, inventory, oe

# Create tables on startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application lifecycle events"""
    # Startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Shutdown
    await engine.dispose()

# Create FastAPI app
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_PREFIX}/openapi.json",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(
    auth.router, 
    prefix=f"{settings.API_PREFIX}/auth", 
    tags=["Authentication"]
)
app.include_router(
    users.router, 
    prefix=f"{settings.API_PREFIX}/users", 
    tags=["Users"]
)
app.include_router(
    companies.router, 
    prefix=f"{settings.API_PREFIX}/companies", 
    tags=["Companies"]
)
app.include_router(
    roles.router, 
    prefix=f"{settings.API_PREFIX}/roles", 
    tags=["Roles"]
)
app.include_router(
    accounting_periods.router, 
    prefix=f"{settings.API_PREFIX}/accounting-periods", 
    tags=["Accounting Periods"]
)
app.include_router(
    gl.router, 
    prefix=f"{settings.API_PREFIX}/gl", 
    tags=["General Ledger"]
)
app.include_router(
    customers.router, 
    prefix=f"{settings.API_PREFIX}/customers", 
    tags=["Customers"]
)
app.include_router(
    ar.router, 
    prefix=f"{settings.API_PREFIX}/ar", 
    tags=["Accounts Receivable"]
)
app.include_router(
    suppliers.router, 
    prefix=f"{settings.API_PREFIX}/suppliers", 
    tags=["Suppliers"]
)
app.include_router(
    ap.router, 
    prefix=f"{settings.API_PREFIX}/ap", 
    tags=["Accounts Payable"]
)
app.include_router(
    inventory.router, 
    prefix=f"{settings.API_PREFIX}/inventory", 
    tags=["Inventory"]
)
app.include_router(
    oe.router, 
    prefix=f"{settings.API_PREFIX}/oe", 
    tags=["Order Entry"]
)

# Root endpoint
@app.get("/")
async def root():
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "running"
    }

# Health check endpoint
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": settings.VERSION
    }
