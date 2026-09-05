"""Central HTTP router registry."""

from fastapi import APIRouter

from review_platform.api.routes.agents import router as agents_router
from review_platform.api.routes.ai_reviews import router as ai_reviews_router
from review_platform.api.routes.deliveries import router as deliveries_router
from review_platform.api.routes.homeworks import router as homeworks_router
from review_platform.api.routes.identity_courses import router as identity_courses_router
from review_platform.api.routes.operations import router as operations_router
from review_platform.api.routes.reviews import router as reviews_router
from review_platform.api.routes.submissions import router as submissions_router


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")
    router.include_router(agents_router)
    router.include_router(ai_reviews_router)
    router.include_router(deliveries_router)
    router.include_router(identity_courses_router)
    router.include_router(homeworks_router)
    router.include_router(submissions_router)
    router.include_router(reviews_router)
    router.include_router(operations_router)
    return router


__all__ = ["build_api_router"]
