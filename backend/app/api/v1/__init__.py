from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    customers,
    geocoding,
    imports,
    map_dashboard,
    reports,
    service_locations,
    serviceability,
    stream,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(customers.router)
api_router.include_router(service_locations.router)
api_router.include_router(serviceability.router)
api_router.include_router(geocoding.router)
api_router.include_router(map_dashboard.router)
api_router.include_router(admin.router)
api_router.include_router(imports.router)
api_router.include_router(reports.router)
api_router.include_router(stream.router)

__all__ = ["api_router"]
