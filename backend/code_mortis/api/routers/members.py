from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from code_mortis.api.dependencies import DB, SESSION_ADMIN
from code_mortis.api_tokens import service as pat_service
from code_mortis.integrations import github
from code_mortis.models import AppConfig, LoginSession, Membership, ReviewRun, User
from code_mortis.schemas.common import MemberInput

router = APIRouter()


def lock_memberships(db: Session, user: User) -> dict[int, Membership]:
    with db.no_autoflush:
        # Serialize membership changes before reading the current administrator roles.
        db.scalar(select(AppConfig).where(AppConfig.id == 1).with_for_update())
        memberships = {row.github_id: row for row in db.scalars(
            select(Membership).execution_options(populate_existing=True)
        )}
    if user.id not in memberships or memberships[user.id].role != "admin":
        raise HTTPException(403, "Administrator access is required. Refresh the page and retry.")
    return memberships


def retain_administrator(memberships: dict[int, Membership], membership: Membership | None):
    if membership and membership.role == "admin" and sum(row.role == "admin" for row in memberships.values()) <= 1:
        raise HTTPException(409, "At least one administrator must remain. Add another administrator first.")


@router.post("/members")
def add_member(body: MemberInput, db: DB, user: SESSION_ADMIN) -> dict:
    memberships = lock_memberships(db, user)
    identity = github.request("GET", f"/users/{body.login}", github.user_token(db, user))
    if identity.get("type") != "User":
        raise HTTPException(422, "Select an individual GitHub user.")
    membership = memberships.get(identity["id"])
    if body.role != "admin":
        retain_administrator(memberships, membership)
    if identity["id"] == user.id and body.role != "admin":
        raise HTTPException(409, "You cannot remove your own administrator access.")
    if membership is None:
        membership = Membership(github_id=identity["id"], login=identity["login"], role=body.role)
        db.add(membership)
    membership.role = body.role
    existing = db.get(User, identity["id"])
    if existing:
        existing.role, existing.enabled = body.role, True
    return {"ok": True}


@router.delete("/members/{user_id}")
def remove_member(user_id: int, db: DB, user: SESSION_ADMIN) -> dict:
    from code_mortis.reviews.service import stop_review
    from code_mortis.security_scans.service import cancel_access

    memberships = lock_memberships(db, user)
    membership = memberships.get(user_id)
    retain_administrator(memberships, membership)
    if user.id == user_id:
        raise HTTPException(409, "You cannot remove your own account.")
    if membership:
        db.delete(membership)
    existing = db.get(User, user_id)
    if existing:
        existing.enabled, existing.access_token, existing.refresh_token = False, "", ""
    # Permanent: re-adding the account later cannot resurrect these credentials.
    pat_service.revoke_for_user(db, user_id, reason="membership_removed", actor_id=user.id)
    cancel_access(db, actor_id=user_id, reason="Security scan requester access was removed.")
    for run in db.scalars(
        select(ReviewRun)
        .where(
            or_(ReviewRun.actor_id == user_id, ReviewRun.owner_id == user_id),
            ReviewRun.status.in_({"queued", "running", "blocked"}),
        )
        .with_for_update()
    ):
        run.cancel_requested = True
        stop_review(db, run, "cancelled", "The requesting user's access was removed.")
    db.execute(delete(LoginSession).where(LoginSession.user_id == user_id))
    return {"ok": True}
