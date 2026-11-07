"""Private runner transport. Never returns native authentication credentials."""

import httpx

from code_mortis.core.config import settings
from code_mortis.core.errors import RunnerBlocked


def request(method: str, path: str, body=None, timeout=35):
    if method == "POST" and ("/reviews/" in path or path.endswith("/auth")) or method == "GET" and (path.endswith("/models") or "/models?" in path or "/auth" in path):
        raise RunnerBlocked("retired")
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
    raise RunnerBlocked("retired")
