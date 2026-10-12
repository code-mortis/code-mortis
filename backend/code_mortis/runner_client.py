"""Private runner transport. Never returns native authentication credentials."""

import time

import httpx

from .config import settings


class RunnerBlocked(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(
            {
                "busy": "This personal connection is in use. The review will wait.",
                "quota": "The subscription limit was reached. Retry when usage is available.",
                "authentication": "This personal account needs to sign in again. Reconnect it in Settings, then retry.",
                "prompt_limit": "The subscription prompt exceeds its conservative per-prompt token limit. Increase Prompt ceiling on the AI connection or reduce the supplied context.",
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
    from .ocr import ReviewStopped

    path = f"/connections/{connection_id}/reviews/{run_id}"
    request("POST", path, payload)
    try:
        while True:
            if stopped():
                raise ReviewStopped()
            state = request("GET", path)
            if state["status"] == "completed":
                return state["result"]
            if state["status"] == "failed":
                raise RunnerBlocked(state["reason"])
            time.sleep(1)
    finally:
        # The runner also enforces finite deadlines and expires unpolled unlimited work.
        try:
            request("DELETE", path)
        except RunnerBlocked:
            pass
