"""Read model metadata without sending inference requests or exposing credentials."""

import json
import time

import httpx

from code_mortis.core.security import safe_endpoint, unseal
from code_mortis.schemas.common import ModelCatalog, ModelChoice

MAX_MODELS = 5000
MAX_BYTES = 2_000_000


def choices(rows, id_key="id", name_key="display_name"):
    if not isinstance(rows, list) or len(rows) > MAX_MODELS:
        raise ValueError("Invalid model list")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid model entry")
        identity = row.get(id_key)
        if (
            not isinstance(identity, str)
            or not identity.strip()
            or len(identity) > 150
            or any(ord(char) < 32 for char in identity)
        ):
            raise ValueError("Invalid model identifier")
        name = row.get(name_key) or row.get("name") or identity
        if not isinstance(name, str) or len(name) > 250:
            raise ValueError("Invalid model name")
        result[identity] = ModelChoice(id=identity, name=name)
    return list(result.values())


def credentials_for(body, connection=None):
    old = json.loads(unseal(connection.secret)) if connection else {}
    # A draft can point anywhere an admin allows, but saved secrets belong to one endpoint.
    if connection and body.base_url.rstrip("/") != connection.base_url.rstrip("/"):
        if body.api_key is None and old.get("api_key"):
            raise ValueError("Enter a replacement API key before loading models from a different endpoint.")
        if body.extra_headers is None and old.get("extra_headers"):
            raise ValueError(
                "Replace or clear stored headers before loading models from a different endpoint."
            )
        old = {}
    return {
        "api_key": body.api_key if body.api_key is not None else old.get("api_key", ""),
        "extra_headers": body.extra_headers
        if body.extra_headers is not None
        else old.get("extra_headers", {}),
    }


def api_models(connection, credentials):
    if connection.protocol == "anthropic-bedrock":
        return ModelCatalog(
            source="provider",
            status="unsupported",
            message="Model discovery is not supported for Bedrock. Enter a custom model ID.",
        )
    base = safe_endpoint(connection.base_url)
    anthropic = connection.protocol == "anthropic"
    url = base + ("/v1/models" if anthropic and not base.endswith("/v1") else "/models")
    key = credentials.get("api_key", "")
    if not key:
        return ModelCatalog(
            source="provider",
            status="disconnected",
            message="Enter an API key to load models. Local endpoints can use a placeholder.",
        )
    header = connection.options.get("auth_header") or ("x-api-key" if anthropic else "Authorization")
    deadline, size, models, cursors = time.monotonic() + 20, 0, {}, set()
    params = {"limit": 1000} if anthropic else {}
    try:
        headers = httpx.Headers({header: f"Bearer {key}" if header.lower() == "authorization" else key})
        if anthropic:
            headers["anthropic-version"] = "2023-06-01"
        headers.update(credentials.get("extra_headers", {}))
        with httpx.Client(follow_redirects=False) as client:
            for _ in range(20):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                with client.stream(
                    "GET", url, headers=headers, params=params, timeout=min(10, remaining)
                ) as response:
                    if response.status_code in {401, 403}:
                        return ModelCatalog(
                            source="provider",
                            status="disconnected",
                            message="The provider denied model discovery. Check the connection credentials and model-list permissions.",
                        )
                    if response.status_code in {404, 405, 501}:
                        return ModelCatalog(
                            source="provider",
                            status="unsupported",
                            message="This endpoint does not support model discovery. Enter a custom model ID.",
                        )
                    if response.status_code != 200:
                        raise ValueError("Provider rejected discovery")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES or time.monotonic() > deadline:
                            raise ValueError("Model discovery exceeded its limits")
                        content.extend(chunk)
                data = json.loads(content)
                if not isinstance(data, dict):
                    raise ValueError("Invalid model response")
                models.update((model.id, model) for model in choices(data.get("data")))
                if len(models) > MAX_MODELS:
                    raise ValueError("Too many models")
                if not data.get("has_more"):
                    return ModelCatalog(source="provider", models=list(models.values()))
                cursor = data.get("last_id")
                if not isinstance(cursor, str) or not cursor or len(cursor) > 1000 or cursor in cursors:
                    raise ValueError("Invalid model cursor")
                cursors.add(cursor)
                params["after_id" if anthropic else "after"] = cursor
    except (httpx.HTTPError, ValueError, TimeoutError, UnicodeError):
        pass
    return ModelCatalog(
        source="provider",
        status="unavailable",
        message="Could not load models from this endpoint. Retry or enter a custom model ID.",
    )
