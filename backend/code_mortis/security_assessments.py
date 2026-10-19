"""Read-only assessment history projected from existing security checkpoints."""

import json
from collections import Counter

from sqlalchemy import select

from .models import SecurityOccurrence
from .security import digest
from .security_schemas import (
    SecurityAssessment,
    SecurityAssessmentOut,
    SecurityAssessmentSummary,
    SecurityProposal,
)


def read(db, scan):
    tasks = scan.progress.get("tasks", {})
    proposals = []
    # The worker assigns assessment positions after file analysis finishes, in inventory order.
    if scan.stages.get("analysis", {}).get("status") not in {"pending", "running"}:
        paths = list(scan.coverage.get("inventory", {}))
        paths.extend(key.removeprefix("analysis:") for key in tasks
                     if key.startswith("analysis:") and key.removeprefix("analysis:") not in paths)
        for path in paths:
            task = tasks.get("analysis:" + path, {})
            if task.get("status") == "completed":
                proposals.extend(task.get("result", {}).get("findings", []))
    proposals = {str(position): proposal for position, proposal in enumerate(proposals)}
    for key, task in tasks.items():
        if key.startswith("assessment:") and key.removeprefix("assessment:").isdigit():
            proposals.setdefault(key.removeprefix("assessment:"), task.get("proposal", {}))
    observations, fingerprints = {}, {}
    for row in db.scalars(select(SecurityOccurrence).where(SecurityOccurrence.scan_id == scan.id)):
        observations[row.finding_id] = row.finding_id
        evidence = row.snapshot.get("evidence", {})
        for value in (evidence, evidence.get("ai_analysis", {})):
            if fingerprint := value.get("fingerprint"):
                fingerprints[fingerprint] = row.finding_id
    running = scan.status in {"queued", "running", "blocked"}
    items = []
    for position in sorted(proposals, key=int):
        proposal = proposals[position]
        task = tasks.get(f"assessment:{position}", {})
        proposal = {key: value for key, value in task.get("proposal", proposal).items() if key in SecurityProposal.model_fields}
        result = {key: value for key, value in task.get("result", {}).items() if key in SecurityAssessment.model_fields} or None
        status = task.get("status", "queued" if running else "interrupted")
        if status not in {"queued", "running", "completed", "failed", "interrupted"}:
            status = "interrupted"
        if not running and status in {"queued", "running"}:
            status = "interrupted"
        finding_id = None
        if result and result.get("verdict") == "confirmed":
            finding_id = observations.get(result.get("finding_id"))
            if not finding_id and proposal.get("evidence"):
                fingerprint = digest(json.dumps(["code", proposal["path"], proposal["category"],
                                                proposal["evidence"][0]["quote"], proposal["title"]]))
                finding_id = fingerprints.get(fingerprint)
        verdict = result.get("verdict") if result else None
        if status in {"queued", "running"}:
            outcome = "pending"
        elif status == "completed":
            outcome = verdict if verdict in {"confirmed", "rejected", "uncertain"} else "withheld"
            if outcome == "confirmed" and not finding_id:
                outcome = "pending" if running else "withheld"
        else:
            outcome = status
        phase = "validating" if status == "completed" and outcome == "pending" else "checking" if status == "running" else "queued" if status == "queued" else "finished"
        if phase == "validating":
            status = "running"
        row = SecurityAssessmentOut(id=str(position), proposal=proposal, status=status, outcome=outcome,
            progress_phase=phase, result=result, error=task.get("error"), tokens=task.get("tokens"), reused=bool(task.get("reused")), finding_id=finding_id)
        items.append(row)
    summary = SecurityAssessmentSummary(total=len(items), status_counts=dict(Counter(row.status for row in items)),
        outcome_counts=dict(Counter(row.outcome for row in items)),
        revision=digest(json.dumps([row.model_dump() for row in items], sort_keys=True)))
    for row in items:
        row.revision = summary.revision
    return items, summary
