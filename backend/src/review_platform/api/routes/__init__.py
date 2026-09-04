"""Central HTTP router registry."""

from fastapi import APIRouter

from review_platform.api.routes.homeworks import router as homeworks_router
from review_platform.api.routes.identity_courses import router as identity_courses_router
from review_platform.api.routes.operations import router as operations_router


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")
    router.include_router(identity_courses_router)
    router.include_router(homeworks_router)
    router.include_router(operations_router)
    return router


__all__ = ["build_api_router"]
