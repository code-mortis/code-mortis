from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from code_mortis.api.dependencies import DB, SESSION_USER, pr_access
from code_mortis.core import activity
from code_mortis.core.db import now
from code_mortis.models import Job, ReviewRun
from code_mortis.schemas.activity import ActivityFeed, ActivityRead, SummaryAttemptOut

router = APIRouter()
Cursor = Annotated[str | None, Query(min_length=1, max_length=256)]


@router.get("/activity", response_model=ActivityFeed)
def activity_feed(db: DB, user: SESSION_USER, running_cursor: Cursor = None, updates_cursor: Cursor = None):
    return activity.feed(db, user, running_cursor, updates_cursor)


@router.post("/activity/read", status_code=204)
def read_activity(body: ActivityRead, db: DB, user: SESSION_USER):
    activity.mark_read(db, user, body, now())
    return Response(status_code=204)


@router.get("/runs/{run_id}/summary/attempts/{job_id}", response_model=SummaryAttemptOut)
def summary_attempt(run_id: str, job_id: str, db: DB, user: SESSION_USER):
    run = db.get(ReviewRun, run_id)
    if run is None:
        raise HTTPException(404, "Review run not found.")
    pr_access(db, user, run.pr_id)
    job = db.get(Job, job_id)
    if job is None or job.kind != "generate_summary" or job.payload.get("run_id") != run.id:
        raise HTTPException(404, "Summary attempt not found.")
    return SummaryAttemptOut(id=job.id, status=job.status, finished_at=job.finished_at)
