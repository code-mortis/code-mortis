from fastapi import APIRouter

from code_mortis.api.routers import (
    analytics,
    api_tokens,
    auth,
    connections,
    findings,
    installations,
    members,
    pulls,
    repositories,
    runs,
    security,
    settings,
    subscriptions,
    webhooks,
)

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(api_tokens.router)
router.include_router(repositories.router)
router.include_router(analytics.router)
router.include_router(pulls.router)
router.include_router(runs.router)
router.include_router(findings.router)
router.include_router(connections.router)
router.include_router(settings.router)
router.include_router(installations.router)
router.include_router(members.router)
router.include_router(webhooks.router)

router.include_router(subscriptions.router)
router.include_router(security.router)
