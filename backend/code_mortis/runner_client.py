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
                "authentication": "Reconnect this personal account before retrying the review.",
                "unavailable": "The official client runner is unavailable. Check its health and retry.",
            }.get(
                reason,
                "The official client could not complete this review. Retry after checking the connection.",
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
        # Cancel on worker-side failure too. The runner also enforces its own deadline.
        try:
            request("DELETE", path)
        except RunnerBlocked:
            pass
