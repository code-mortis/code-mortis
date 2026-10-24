from fastapi import APIRouter
from sqlalchemy import select

from code_mortis.api.dependencies import ADMIN, DB
from code_mortis.core.config import settings
from code_mortis.integrations import github
from code_mortis.models import Installation, Job, Membership

router = APIRouter()


@router.get("/settings")
def instance_settings(db: DB, user: ADMIN) -> dict:
    config = github.app_config(db)
    return {
        "github_app": {
            "id": config.github_app_id,
            "slug": config.github_slug,
            "install_url": "/api/v1/installations/start",
        },
        "installations": [
            {"id": row.id, "account": row.account, "active": row.active}
            for row in db.scalars(select(Installation).where(Installation.archived.is_(False)))
        ],
        "members": [
            {"id": row.github_id, "login": row.login, "role": row.role}
            for row in db.scalars(select(Membership))
        ],
        "failed_jobs": [
            {
                "id": row.id, "kind": row.kind, "error": row.error,
                "available_at": row.available_at, "attempts": row.attempts,
            }
            for row in db.scalars(
                select(Job).where(Job.status == "failed").order_by(Job.available_at.desc()).limit(20)
            )
        ],
        "public_url": settings().public_url,
        "webhook_url": settings().github_webhook_url,
        "configuration_errors": settings().diagnostics(),
        "registration": {
            "callback_url": settings().public_url + "/api/v1/auth/callback",
            "setup_url": settings().public_url + "/api/v1/installations/callback",
            "request_oauth_on_install": False,
        },
    }
