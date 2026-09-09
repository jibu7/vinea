from fastapi import APIRouter

from app.api.v1 import auth, company, gl, invitations, memberships, operator, subledger

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(company.router)
api_router.include_router(invitations.router)
api_router.include_router(memberships.router)
api_router.include_router(operator.router)
api_router.include_router(gl.router)
api_router.include_router(subledger.router)
