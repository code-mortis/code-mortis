"""Public errors explain recovery without returning submitted secrets."""

import pytest
from fastapi.testclient import TestClient

from code_mortis import github
from code_mortis.main import app


@pytest.mark.parametrize("field,value,message", [
    ("token_budget", 5000001, "Settings / token budget: Enter a value no greater than 5,000,000."),
    ("timeout_seconds", 2, "Settings / Timeout (seconds): Enter a value of at least 60."),
    ("language", "   ", "Settings / language: Use at least 1 character."),
    ("max_files", 1.5, "Settings / max files: Enter a whole number."),
])
def test_validation_names_the_field_and_constraint(seed, github_stub, client, field, value, message):
    response = client.put("/api/v1/repositories/22", json={"enabled": False, "settings": {field: value}})
    assert response.status_code == 422
    assert response.json()["detail"] == message
    assert "None" not in response.json()["detail"] and "body." not in response.json()["detail"]


def test_validation_does_not_repeat_secrets_in_unknown_fields_or_invalid_json(seed, client):
    secret = "private-provider-key"
    response = client.post("/api/v1/connections", json={secret: secret})
    assert response.status_code == 422 and secret not in response.text
    assert "unsupported field" in response.json()["detail"]
    response = client.post("/api/v1/connections", content='{"api_key":"' + secret + '",', headers={"content-type": "application/json"})
    assert response.status_code == 422 and secret not in response.text
    assert response.json()["detail"] == "The request contains invalid JSON. Check quotes, commas, and braces."


def test_unexpected_api_error_has_a_safe_message_and_request_reference(seed, client, monkeypatch):
    def failed(*args):
        raise RuntimeError("private-provider-key")

    monkeypatch.setattr(github, "visible_repositories", failed)
    with TestClient(app, raise_server_exceptions=False) as browser:
        browser.cookies.update(client.cookies)
        response = browser.get("/api/v1/repositories")
    assert response.status_code == 500
    assert "private-provider-key" not in response.text
    assert "Code Mortis could not complete this request" in response.json()["detail"]
    assert response.headers["x-request-id"] in response.json()["detail"]
    assert response.headers["cache-control"] == "no-store"
