"""Private runner transport. Never returns native authentication credentials."""

import time

import httpx

from .config import settings


class RunnerBlocked(ValueError):
    def __init__(self, reason: str, usage=None):
        self.reason = reason
        tokens = usage.get("tokens") if isinstance(usage, dict) else None
        self.usage = {"tokens": tokens if type(tokens) is int and tokens >= 0 else None}
        super().__init__(
            {
                "busy": "This personal connection is in use. The review will wait.",
                "quota": "The subscription limit was reached. Retry when usage is available.",
                "authentication": "This personal account needs to sign in again. Reconnect it in Settings, then retry.",
                "prompt_limit": "The subscription prompt exceeds its conservative per-prompt token limit. Increase Prompt ceiling on the AI connection or reduce the supplied context.",
                "token_limit": "The subscription request exceeded the remaining Token budget. Increase Token budget, choose Unlimited, or reduce the review scope.",
                "usage_unknown": "The native client did not report valid token usage. Further AI checks were stopped because the remaining Token budget cannot be verified. Start a new review.",
                "unavailable": "The service that runs ChatGPT and Claude Code is unavailable. Ask the administrator to check the runner service, then try again.",
            }.get(
                reason,
                "ChatGPT or Claude Code could not complete this request. Check the AI connection account status, then try again.",
            )
        )


def request(method: str, path: str, body=None, timeout=35):
    if not settings().runner_token:
        raise RunnerBlocked("unavailable")
    try:
        response = httpx.request(
            method,
            settings().runner_url + path,
            json=body,
            timeout=timeout,
            headers={"Authorization": "Bearer " + settings().runner_token},
        )
        if response.status_code >= 400:
            reason = response.json().get("detail", "unavailable")
            raise RunnerBlocked(reason if reason in {"busy", "quota", "authentication"} else "unavailable")
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, RunnerBlocked):
            raise
        raise RunnerBlocked("unavailable") from exc


def review(connection_id: str, run_id: str, payload: dict, stopped):
    from . import review_context, verification
    from .ocr import ReviewStopped

    payload = dict(payload)
    context = payload.pop("_evidence_context", None)
    instructions = payload.pop("_instructions", None)
    history_ids = {}
    if context is not None:
        supplied, schema, history_ids = review_context.selection_context(context, payload["output_schema"])
        payload.update(prompt=instructions + review_context.SELECTION_INSTRUCTIONS + "\n\n" + verification.evidence_bundle(supplied),
                       output_schema=schema)
    path = f"/connections/{connection_id}/reviews/{run_id}"
    if stopped():
        raise ReviewStopped("Review stopped before native dispatch.")
    request("POST", path, payload)
    try:
        while True:
            if stopped():
                raise ReviewStopped()
            state = request("GET", path)
            if state["status"] == "completed":
                usage = state.get("usage")
                tokens = usage.get("tokens") if isinstance(usage, dict) else None
                if type(tokens) is not int or tokens < 0:
                    raise RunnerBlocked("usage_unknown")
                result = state["result"]
                if context is not None:
                    try:
                        result = review_context.selected_result(result, supplied, history_ids)
                    except verification.ResultValidationError as exc:
                        raise verification.VerificationOutputError(str(exc), usage, feedback=exc.feedback) from exc
                return result, usage
            if state["status"] == "failed":
                raise RunnerBlocked(state["reason"], state.get("usage"))
            time.sleep(1)
    finally:
        # The runner also enforces finite deadlines and expires unpolled unlimited work.
        try:
            request("DELETE", path)
        except RunnerBlocked:
            pass
