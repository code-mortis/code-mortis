from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from code_mortis.api.dependencies import DB, USER, pr_access
from code_mortis.models import Activity, Finding, FindingVerification
from code_mortis.reviews.service import enqueue, request_finding_status
from code_mortis.schemas.common import (
    ActivityOut,
    FindingAction,
    FindingDetail,
    FindingOut,
    VerificationOut,
)

router = APIRouter()


@router.get("/findings/{finding_id}", response_model=FindingDetail)
def finding_detail(finding_id: str, db: DB, user: USER):
    finding = db.get(Finding, finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found.")
    pr_access(db, user, finding.pr_id)
    return FindingDetail(
        finding=FindingOut.model_validate(finding),
        activities=[
            ActivityOut.model_validate(activity)
            for activity in db.scalars(
                select(Activity).where(Activity.finding_id == finding.id).order_by(Activity.created_at)
            )
        ],
        verifications=[VerificationOut.model_validate(row) for row in db.scalars(
            select(FindingVerification).where(FindingVerification.finding_id == finding.id)
            .order_by(FindingVerification.created_at)
        )],
    )


@router.patch("/findings/{finding_id}", response_model=FindingOut, status_code=202)
def update_finding(finding_id: str, body: FindingAction, db: DB, user: USER):
    finding = db.get(Finding, finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found.")
    pr_access(db, user, finding.pr_id, write=True)
    finding = db.scalar(select(Finding).where(Finding.id == finding_id).with_for_update().execution_options(populate_existing=True))
    request_finding_status(db, finding, body.status, user, body.reason)
    return FindingOut.model_validate(finding)


@router.post("/findings/{finding_id}/sync", status_code=202)
def retry_finding_sync(finding_id: str, db: DB, user: USER) -> dict:
    finding = db.get(Finding, finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found.")
    pr_access(db, user, finding.pr_id, write=True)
    finding = db.scalar(select(Finding).where(Finding.id == finding_id).with_for_update().execution_options(populate_existing=True))
    if not finding.desired_status:
        raise HTTPException(409, "This finding has no GitHub status update waiting to be retried. Refresh the page to see its current status.")
    if not finding.sync_intent:
        # Compatibility with manual intents queued before the migration.
        finding.sync_intent = {
            "finding_id": finding.id, "version": finding.sync_version,
            "actor_id": user.id, "reason": "Retry pending synchronization.",
        }
    finding.sync_error = None
    # Explicit retry adopts the initiating session (or its PAT), never the stored origin.
    from code_mortis.api_tokens import provenance as _pat

    finding.sync_intent = _pat.adopt_intent(finding.sync_intent)
    job = enqueue(db, "resolve", finding.sync_intent)
    return {"job_id": job.id}
