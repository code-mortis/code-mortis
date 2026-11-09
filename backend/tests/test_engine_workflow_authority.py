"""Production workflow authority boundaries with synthetic transport and GitHub."""

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select
from test_engine_evaluation import runtime
from test_mortis_review import scenario as source_case  # noqa: F401
from test_mortis_workflow import workflow as workflow

from code_mortis.core.db import transaction
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisTask,
    Finding,
    InferenceAdmission,
    Occurrence,
    PullRequest,
    ReviewRun,
    User,
)


@pytest.mark.parametrize("change", ["cancel", "revocation", "head"])
def test_full_mortis_workflow_discards_paid_results_after_authority_changes(workflow, monkeypatch, change):
    from code_mortis.integrations import llm
    from code_mortis.reviews import service

    send = llm.send

    async def changed_after_response(*args, **kwargs):
        result = await send(*args, **kwargs)
        with transaction() as db:
            if change == "cancel":
                db.get(ReviewRun, "mortis-run").cancel_requested = True
            elif change == "revocation":
                db.get(User, 7).enabled = False
            else:
                db.get(PullRequest, "pr").head_sha = "f" * 40
                workflow["head"] = "f" * 40
        return result

    monkeypatch.setattr(llm, "send", changed_after_response)
    service.perform_review("mortis-run", "mortis-job:1")
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        attempts = list(db.scalars(select(AnalysisAttempt)))
        assert run.status != "completed"
        assert not list(db.scalars(select(Finding))) and not list(db.scalars(select(Occurrence)))
        assert attempts and all(row.usage_state == "known" and row.usage_tokens == 12 for row in attempts)
        assert all(row.result is None for row in db.scalars(select(AnalysisTask)))
        assert not list(db.scalars(select(InferenceAdmission).where(InferenceAdmission.status == "active")))
        assert not workflow["reviews"]
        evidence = {"fault": change, "run": runtime.row_data(run),
                    "attempts": [runtime.row_data(row) for row in attempts],
                    "synthetic_http_requests": workflow["requests"], "github_publications": 0}
    if destination := os.environ.get("CM_WORKFLOW_FAULT_EVIDENCE_DIR"):
        (Path(destination) / ("workflow-" + change + ".json")).write_text(json.dumps(evidence, indent=2,
            default=lambda value: value.isoformat()))

