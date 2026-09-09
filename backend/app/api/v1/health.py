from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.config import settings
from app.core.logging import get_logger
from app.providers.registry import get_routing_provider
from app.services.cache import get_redis

router = APIRouter(tags=["operations"])
logger = get_logger(__name__)


@router.get("/health")
async def health() -> dict:
    """Liveness. Deliberately dependency-free.

    If this checked Postgres, a database blip would make the orchestrator kill
    and restart healthy application containers, turning a short outage into a
    long one. Readiness is where dependencies belong.
    """
    return {
        "status": "ok",
        "environment": settings.ENVIRONMENT,
        "service": settings.APP_NAME,
    }


@router.get("/health/ready")
async def readiness(session: DbSession, response: Response) -> dict:
    checks: dict[str, dict] = {}
    ready = True

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        checks["database"] = {"status": "error", "detail": str(exc)[:200]}
        ready = False

    try:
        await get_redis().ping()
        checks["redis"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        # Redis down means no cache and no live dashboard updates, but
        # serviceability decisions still work. Degraded, not unready.
        checks["redis"] = {"status": "degraded", "detail": str(exc)[:200]}

    # The routing provider is NOT probed here. A readiness probe runs every few
    # seconds; calling a billed API that often would be expensive and would
    # make an upstream incident cycle our own pods. Provider health lives on
    # /api/v1/admin/routing/health and in Prometheus alerts.
    checks["routing_provider"] = {
        "status": "not_probed",
        "provider": get_routing_provider().name,
        "note": "Probe via /api/v1/admin/routing/health",
    }

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "checks": checks}
