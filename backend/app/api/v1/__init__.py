from fastapi import APIRouter

from app.api.v1 import auth, gl, invitations, operator

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(invitations.router)
api_router.include_router(operator.router)
api_router.include_router(gl.router)
