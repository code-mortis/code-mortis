"""Model-only context contracts; stored review results keep their existing shape."""

import copy
from types import SimpleNamespace

from code_mortis.core.security import digest

TASK_CONTRACT = "review-task/v2"
SELECTION_INSTRUCTIONS = (
    " Select evidence using only source_id, start_line and end_line from citation_sources. "
    "Code Mortis constructs quotes, paths and revisions. Select finding_id only from supplied "
    "history references; never copy an ID from prior_candidates."
)


def correctable(feedback):
    if not isinstance(feedback, dict) or not feedback:
        return False
    if "kind" in feedback:
        return feedback["kind"] in {"schema", "citation_selection", "history_selection", "delivery", "evidence"}
    return "field" in feedback or "errors" in feedback


def sanitize(value, credentials, stopped=lambda: False, *, usage=None):
    from code_mortis.integrations import ocr
    from code_mortis.security_scans import engine as security_engine

    values = credentials.setdefault("redaction_values", [])
    values.extend(value for value in (credentials.get("api_key", ""), *credentials.get("extra_headers", {}).values())
                  if value and value not in values)

    def redact(text):
        if stopped():
            raise ocr.ReviewStopped("Review stopped before sanitization.")
        return security_engine.redact_secrets(text, values, credentials.get("redaction_rules", []), stopped=stopped)

    try:
        return security_engine.redacted_context(SimpleNamespace(redact=redact), value)
    except security_engine.verification.VerificationOutputError as exc:
        if usage is not None:
            exc.usage = usage
        raise


def source_id(revision, path):
    return "source_" + digest(revision + "\0" + path)[:32]


def quote_matches(source, start_line, end_line, quote):
    # Selections retain original endings; legacy quoted responses use the rendered LF source view.
    original = "".join(source.splitlines(keepends=True)[start_line - 1:end_line])
    rendered = "\n".join(source.splitlines()[start_line - 1:end_line])
    return quote in original or quote in rendered


def selection_context(context, schema):
    from code_mortis.schemas.security import SecurityEvidenceSelection
    from code_mortis.security_scans.engine import model_schema

    supplied, schema = copy.deepcopy(context), copy.deepcopy(schema)
    if supplied.get("code_graph", {}).get("reason") != "used":
        supplied.pop("code_graph", None)
    supplied["task_contract"] = TASK_CONTRACT
    supplied["citation_sources"] = {
        source_id(revision, path): {"revision": revision, "path": path, "line_count": len(source.splitlines())}
        for revision, sources in sorted(context["sources"].items()) for path, source in sorted(sources.items())
    }
    definition = model_schema(SecurityEvidenceSelection)
    definition["properties"]["source_id"]["enum"] = list(supplied["citation_sources"]) or ["unavailable"]
    schema.setdefault("$defs", {})["SelectedEvidence"] = definition
    schema["properties"]["evidence"]["items"] = {"$ref": "#/$defs/SelectedEvidence"}
    history_ids = {f"history_{index + 1}": item["id"] for index, item in enumerate(context.get("history", []))}
    if "history" in supplied:
        supplied["history"] = [{**item, "id": ref} for ref, item in zip(history_ids, supplied["history"], strict=True)]
        schema["properties"]["finding_id"] = {"anyOf": [
            {"type": "string", "enum": list(history_ids)}, {"type": "null"}
        ]} if history_ids else {"type": "null"}
    return supplied, schema, history_ids


def selected_result(raw, supplied, history_ids):
    from code_mortis.reviews import verification
    from code_mortis.schemas.security import SecurityEvidenceSelection
    from code_mortis.security_scans import engine as security_engine

    try:
        if not isinstance(raw, dict) or not isinstance(raw.get("evidence"), list):
            raise ValueError("Evidence selections must be a list.")
        result = dict(raw)
        evidence = [SecurityEvidenceSelection.model_validate(item, strict=True).model_dump()
                    for item in result["evidence"]]
    except (ValueError, TypeError) as exc:
        raise verification.ResultValidationError("Invalid source selection.",
            {"kind": "citation_selection", "reason": "Use only source_id, start_line and end_line."}) from exc
    result["evidence"] = security_engine.selected_citations(evidence, supplied)
    if result.get("finding_id") is not None:
        if result["finding_id"] not in history_ids:
            raise verification.ResultValidationError("Select an established history reference.",
                {"kind": "history_selection", "allowed_values": [*history_ids, None]})
        result["finding_id"] = history_ids[result["finding_id"]]
    return result


def evidence_feedback(paths, field):
    return {"kind": "evidence", "field": field, "required_paths": sorted(paths),
            "reason": "These complete files are already supplied, including empty files. "
                      "Use their source blocks and request only absent paths."}


def unavailable_evidence_feedback(paths, field):
    return {"field": field, "required_paths": sorted(paths),
            "reason": "These paths are excluded or unsupported by the configured review scope; their contents "
                      "cannot be supplied. Do not request them again. Use the permitted sources or request other "
                      "necessary permitted paths. If this evidence is indispensable, return uncertain, explain "
                      "what is unavailable, and leave the requested-source list empty."}


def execution_credentials(db, repository_id, credentials, token):
    from sqlalchemy import select

    from code_mortis.models import SecurityFinding

    rules = [dict(row.evidence, path=row.path) for row in db.scalars(select(SecurityFinding).where(
        SecurityFinding.repository_id == repository_id, SecurityFinding.kind == "secret"))]
    return {**credentials, "redaction_values": [token], "redaction_rules": rules}


def prepare_ocr_redaction(repo, env, base, head, credentials, stopped):
    from code_mortis.integrations import ocr

    if not credentials.get("redaction_rules"):
        return
    sanitize("", credentials, stopped)
    changed = ocr.git(repo, env, "diff", "--name-only", "-z", f"{base}...{head}", stopped=stopped).strip("\0").split("\0")
    paths = set(filter(None, changed)) | {rule["path"] for rule in credentials["redaction_rules"]}
    for path in sorted(paths):
        ocr.ReviewComment.relative_path(path)
        for sha in (base, head):
            try:
                source = ocr.git(repo, env, "show", f"{sha}:{path}", stopped=stopped)
            except ValueError:
                continue
            sanitize(source, credentials, stopped)
