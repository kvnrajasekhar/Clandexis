from fastapi import APIRouter
from app.api.v1.endpoints import router as v1_router
from app.api.v1.dashboard_endpoints import router as dashboard_router

api_router = APIRouter()
api_router.include_router(v1_router, prefix="", tags=["Transaction Pipeline & Speech"])
api_router.include_router(dashboard_router, prefix="", tags=["Judge & Merchant Dashboard"])

