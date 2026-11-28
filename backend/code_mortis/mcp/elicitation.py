"""Stateless, authenticated input rounds for three MCP commands."""

import json

from fastapi import HTTPException
from mcp.types import ElicitResult, InputRequiredResult
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from code_mortis.api.dependencies import repo_access
from code_mortis.api.routers.security import finding_access
from code_mortis.core.db import transaction
from code_mortis.core.security import saved_credential_redactor
from code_mortis.mcp.auth import rate_limit, tool_access
from code_mortis.mcp.skills import MODERN
from code_mortis.mcp.tools import StartScanInput, TriageInput
from code_mortis.models import Connection


def input_round(key, state, **params):
    return InputRequiredResult.model_validate({"requestState": state, "inputRequests": {
        key: {"method": "elicitation/create", "params": params}}})


def scan_options(params):
    with transaction() as db:
        user, grant = tool_access(db, "security", "security:run", params.repository_id)
        repo_access(db, user, params.repository_id, write=True)
        rate_limit("elicitation", grant.id)
        rows = db.execute(select(Connection.id, Connection.name).where(Connection.kind == "api")
                          .order_by(Connection.name, Connection.id).limit(101)).all()
        if not rows:
            raise HTTPException(422, "Configure a shared API connection before starting a scan.")
        if len(rows) > 100:
            raise HTTPException(422, "Supply connection_id explicitly to choose from this instance's connections.")
        redact = saved_credential_redactor(db, [params.repository_id])
        return [{"const": row.id, "title": redact(row.name)} for row in rows]


def triage_message(params):
    with transaction() as db:
        user, grant = tool_access(db, "security", "security:write")
        finding, _, _ = finding_access(db, user, params.finding_id, write=True)
        if finding.version != params.version:
            raise HTTPException(409, "The finding changed. Refresh it before updating its state.")
        rate_limit("elicitation", grant.id)
        return (f"Give your reason to change finding {finding.id} (version {finding.version}) "
                f"from {finding.state} to {params.state}.")


def supports(context, mode):
    if not context or context.protocol_version not in MODERN or not context.client_capabilities:
        return False
    cap = context.client_capabilities.elicitation
    if cap is None:
        return False
    return cap.url is not None if mode == "url" else cap.form is not None or cap.url is None


def answer(context, key):
    response = (context.input_responses or {}).get(key)
    if not isinstance(response, ElicitResult):
        raise HTTPException(422, "Return the requested input through the MCP client.")
    if response.action != "accept":
        raise HTTPException(409, "The requested action was not performed.")
    return response.content or {}


async def interactive_call(name, arguments, context, invoke):
    field = {"security_start_scan": "connection_id", "security_triage_finding": "reason"}.get(name)
    params = arguments.get("params")
    if not isinstance(params, dict) or not context:
        return await invoke(name, arguments, context)
    if context.input_responses and context.request_state is None:
        raise HTTPException(422, "Missing MCP continuation state.")
    if field and params.get(field) is None and supports(context, "form"):
        if context.request_state is None:
            if field == "connection_id":
                choices = await run_in_threadpool(scan_options, StartScanInput.model_validate(params))
                message = "Choose the shared API connection for this repository scan."
                schema = {"type": "string", "oneOf": choices}
                state = json.dumps([choice["const"] for choice in choices])
            else:
                message = await run_in_threadpool(triage_message, TriageInput.model_validate(params))
                schema = {"type": "string", "minLength": 1, "maxLength": 4000}
                state = field
            return input_round(field, state, mode="form", message=message, requestedSchema={
                "type": "object", "properties": {field: schema}, "required": [field]})
        value = answer(context, field).get(field)
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(422, "Provide the requested value.")
        if field == "connection_id" and value not in json.loads(context.request_state):
            raise HTTPException(422, "Choose one of the offered shared API connections.")
        arguments = {**arguments, "params": {**params, field: value}}

    if name == "security_create_issue" and supports(context, "url"):
        if context.request_state is not None:
            answer(context, "approve_issue")
            arguments = {**arguments, "params": {**params, "approval_id": context.request_state}}
        result = await invoke(name, arguments, context)
        pending = result.structured_content or {}
        if pending.get("status") == "approval_required" and context.request_state is None:
            return input_round("approve_issue", pending["approval_id"], mode="url",
                               message="Review and approve the exact GitHub issue draft in Code Mortis.",
                               url=pending["approval_url"])
        return result
    return await invoke(name, arguments, context)
