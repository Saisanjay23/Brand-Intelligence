from __future__ import annotations

from fastapi import APIRouter, Response

from backend.controllers import health_controller

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict:
    return await health_controller.live()


@router.get("/health/ready")
async def ready(response: Response) -> dict:
    body, ok = await health_controller.ready()
    if not ok:
        response.status_code = 503
    return body


@router.get("/health/platforms")
async def platforms() -> dict:
    return await health_controller.platforms()


@router.get("/health/collection-sizes")
async def collection_sizes() -> dict:
    return await health_controller.collection_sizes()


@router.get("/health/data-integrity")
async def data_integrity() -> dict:
    return await health_controller.data_integrity()


@router.get("/metrics")
async def metrics() -> Response:
    body, content_type = await health_controller.metrics()
    return Response(content=body, media_type=content_type)
