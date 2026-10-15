from unittest.mock import Mock, call

import httpx
import pytest

from code_mortis import github


@pytest.mark.parametrize("method", ["GET", "get"])
@pytest.mark.parametrize("failure", [
    httpx.ConnectError("Connection interrupted"),
    httpx.ReadError("Read interrupted"),
    httpx.ConnectTimeout("Connection timed out"),
    httpx.ReadTimeout("Read timed out"),
    httpx.RemoteProtocolError("Server disconnected"),
    httpx.Response(500),
    httpx.Response(502),
    httpx.Response(503),
    httpx.Response(504),
])
def test_get_retries_transient_failure_with_the_same_request(monkeypatch, method, failure):
    request = Mock(side_effect=[failure, httpx.Response(200, json={"number": 3})])
    sleep = Mock()
    monkeypatch.setattr(github.httpx, "request", request)
    monkeypatch.setattr(github.time, "sleep", sleep)

    assert github.request(
        method, "/repos/team/project/pulls/3", "installation-token",
        headers={"X-Custom": "custom"}, params={"page": 2},
    ) == {"number": 3}

    expected = call(
        method, github.API + "/repos/team/project/pulls/3",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "Authorization": "Bearer installation-token",
            "X-Custom": "custom",
        },
        timeout=25, follow_redirects=False, params={"page": 2},
    )
    assert request.call_args_list == [expected, expected]
    sleep.assert_called_once_with(1)


def test_get_preserves_transport_cause_after_retry(monkeypatch):
    failure = httpx.RemoteProtocolError("private-provider-key")
    request = Mock(side_effect=failure)
    sleep = Mock()
    monkeypatch.setattr(github.httpx, "request", request)
    monkeypatch.setattr(github.time, "sleep", sleep)

    with pytest.raises(github.GitHubError) as caught:
        github.request("GET", "/repos/team/project/pulls/3", "installation-token")

    assert request.call_count == 2
    sleep.assert_called_once_with(1)
    assert caught.value.status == 503 and caught.value.__cause__ is failure
    assert "private-provider-key" not in str(caught.value)


def test_get_preserves_last_http_failure_after_retry(monkeypatch):
    request = Mock(side_effect=[
        httpx.Response(503), httpx.Response(502, json={"message": "private-provider-key"}),
    ])
    sleep = Mock()
    monkeypatch.setattr(github.httpx, "request", request)
    monkeypatch.setattr(github.time, "sleep", sleep)

    with pytest.raises(github.GitHubError) as caught:
        github.request("GET", "/repos/team/project/pulls/3", "installation-token")

    assert request.call_count == 2
    sleep.assert_called_once_with(1)
    assert caught.value.status == 502
    assert "private-provider-key" not in str(caught.value)


@pytest.mark.parametrize("retry_after,delay", [("120", 120), ("0", 0), ("", 0), ("invalid", 0)])
def test_get_defers_to_retry_after_without_sleeping(monkeypatch, retry_after, delay):
    request = Mock(return_value=httpx.Response(503, headers={"Retry-After": retry_after}))
    sleep = Mock()
    monkeypatch.setattr(github.httpx, "request", request)
    monkeypatch.setattr(github.time, "sleep", sleep)

    with pytest.raises(github.GitHubError) as caught:
        github.request("GET", "/repos/team/project/pulls/3", "installation-token")

    request.assert_called_once()
    sleep.assert_not_called()
    assert caught.value.status == 503 and caught.value.retry_after == delay


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 422, 429, 501])
def test_get_does_not_retry_other_http_errors(monkeypatch, status):
    request = Mock(return_value=httpx.Response(status))
    sleep = Mock()
    monkeypatch.setattr(github.httpx, "request", request)
    monkeypatch.setattr(github.time, "sleep", sleep)

    with pytest.raises(github.GitHubError) as caught:
        github.request("GET", "/repos/team/project/pulls/3", "installation-token")

    request.assert_called_once()
    sleep.assert_not_called()
    assert caught.value.status == status


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@pytest.mark.parametrize("failure", [httpx.RemoteProtocolError("Response lost"), httpx.Response(503)])
def test_other_methods_are_not_replayed(monkeypatch, method, failure):
    request = Mock(side_effect=[failure])
    sleep = Mock()
    monkeypatch.setattr(github.httpx, "request", request)
    monkeypatch.setattr(github.time, "sleep", sleep)

    with pytest.raises(github.GitHubError) as caught:
        github.request(method, "/graphql", "installation-token", json={"query": "mutation {}"})

    request.assert_called_once()
    sleep.assert_not_called()
    assert caught.value.status == 503


@pytest.mark.parametrize("failure", [httpx.LocalProtocolError("Invalid request"), httpx.DecodingError("Invalid body")])
def test_get_does_not_retry_other_httpx_errors(monkeypatch, failure):
    request = Mock(side_effect=failure)
    sleep = Mock()
    monkeypatch.setattr(github.httpx, "request", request)
    monkeypatch.setattr(github.time, "sleep", sleep)

    with pytest.raises(github.GitHubError) as caught:
        github.request("GET", "/repos/team/project/pulls/3", "installation-token")

    request.assert_called_once()
    sleep.assert_not_called()
    assert caught.value.status == 503 and caught.value.__cause__ is failure


@pytest.mark.parametrize("response,result", [(httpx.Response(200, json={"number": 3}), {"number": 3}), (httpx.Response(204), None)])
def test_successful_get_does_not_retry(monkeypatch, response, result):
    request = Mock(return_value=response)
    sleep = Mock()
    monkeypatch.setattr(github.httpx, "request", request)
    monkeypatch.setattr(github.time, "sleep", sleep)

    assert github.request("GET", "/repos/team/project/pulls/3", "installation-token") == result

    request.assert_called_once()
    sleep.assert_not_called()
