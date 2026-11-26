from unittest.mock import Mock, call

import httpx
import pytest

from code_mortis.integrations import github
from code_mortis.models import User


@pytest.fixture(autouse=True)
def clear_conditional_responses():
    github._get_cache.clear()
    yield
    github._get_cache.clear()


def test_conditional_get_revalidates_and_does_not_share_mutated_results(monkeypatch):
    request = Mock(side_effect=[
        httpx.Response(200, headers={"ETag": '"first"'}, json={"permissions": {"push": True}}),
        httpx.Response(304),
        httpx.Response(200, headers={"ETag": '"second"'}, json={"permissions": {"push": False}}),
        httpx.Response(304),
    ])
    monkeypatch.setattr(github.http_client(), "request", request)
    path = "/repos/team/project"
    first = github.request("GET", path, "user-token")
    first["permissions"]["push"] = False
    assert github.request("GET", path, "user-token")["permissions"]["push"]
    assert not github.request("GET", path, "user-token")["permissions"]["push"]
    assert not github.request("GET", path, "user-token")["permissions"]["push"]
    assert [call.kwargs["headers"].get("If-None-Match") for call in request.call_args_list] == [
        None, '"first"', '"first"', '"second"',
    ]


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_conditional_get_never_falls_back_to_cached_access_on_error(monkeypatch, status):
    request = Mock(side_effect=[
        httpx.Response(200, headers={"ETag": '"access"'}, json={"permissions": {"pull": True}}),
        httpx.Response(status, headers={"Retry-After": "60"}),
        httpx.Response(200, json={"permissions": {"pull": False}}),
    ])
    monkeypatch.setattr(github.http_client(), "request", request)
    github.request("GET", "/repos/team/project", "user-token")
    with pytest.raises(github.GitHubError) as caught:
        github.request("GET", "/repos/team/project", "user-token")
    assert caught.value.status == status
    assert not github.request("GET", "/repos/team/project", "user-token")["permissions"]["pull"]
    assert "If-None-Match" not in request.call_args.kwargs["headers"]


def test_conditional_get_is_scoped_to_token_and_url(monkeypatch):
    request = Mock(return_value=httpx.Response(200, headers={"ETag": '"one"'}, json={"id": 1}))
    monkeypatch.setattr(github.http_client(), "request", request)
    for path, token in [("/repos/team/a", "one"), ("/repos/team/a", "two"), ("/repos/team/b", "one")]:
        github.request("GET", path, token)
    assert all("If-None-Match" not in call.kwargs["headers"] for call in request.call_args_list)


def test_conditional_get_still_rejects_revoked_write_permission(monkeypatch):
    request = Mock(side_effect=[
        httpx.Response(200, headers={"ETag": '"write"'}, json={"permissions": {"pull": True, "push": True}}),
        httpx.Response(304),
        httpx.Response(200, headers={"ETag": '"read"'}, json={"permissions": {"pull": True, "push": False}}),
    ])
    monkeypatch.setattr(github.http_client(), "request", request)
    monkeypatch.setattr(github, "user_token", lambda *_: "user-token")
    user = User(id=7)
    github.authorize_repo(None, user, "team/project", write=True)
    github.authorize_repo(None, user, "team/project", write=True)
    with pytest.raises(github.GitHubError, match="requires repository write access"):
        github.authorize_repo(None, user, "team/project", write=True)
    assert request.call_count == 3


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"id": 1}),
    httpx.Response(200, headers={"ETag": '"large"'}, json={"body": "x" * 1024 * 1024}),
])
def test_conditional_get_skips_responses_without_etags_or_over_the_size_limit(monkeypatch, response):
    request = Mock(return_value=response)
    monkeypatch.setattr(github.http_client(), "request", request)
    for _ in range(2):
        github.request("GET", "/repos/team/project", "user-token")
    assert not github._get_cache
    assert all("If-None-Match" not in call.kwargs["headers"] for call in request.call_args_list)


@pytest.mark.parametrize("kwargs", [{"params": {"page": 2}}, {"headers": {"Accept": "custom"}}])
def test_conditional_get_does_not_reuse_a_different_request_variant(monkeypatch, kwargs):
    request = Mock(return_value=httpx.Response(200, headers={"ETag": '"one"'}, json={"id": 1}))
    monkeypatch.setattr(github.http_client(), "request", request)
    github.request("GET", "/repos/team/a", "token")
    github.request("GET", "/repos/team/a", "token", **kwargs)
    assert "If-None-Match" not in request.call_args.kwargs["headers"]


def test_conditional_get_cache_is_bounded(monkeypatch):
    request = Mock(return_value=httpx.Response(200, headers={"ETag": '"one"'}, json={"id": 1}))
    monkeypatch.setattr(github.http_client(), "request", request)
    for index in range(130):
        github.request("GET", f"/repos/team/{index}", "token")
    assert len(github._get_cache) == 128
    github.request("GET", "/repos/team/0", "token")
    assert "If-None-Match" not in request.call_args.kwargs["headers"]


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
    monkeypatch.setattr(github.http_client(), "request", request)
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
    monkeypatch.setattr(github.http_client(), "request", request)
    monkeypatch.setattr(github.time, "sleep", sleep)

    with pytest.raises(github.GitHubError) as caught:
        github.request("GET", "/repos/team/project/pulls/3", "installation-token")

    assert request.call_count == 2
    sleep.assert_called_once_with(1)
    assert caught.value.status == 503 and caught.value.__cause__ is failure
    assert "private-provider-key" not in str(caught.value)


@pytest.mark.parametrize("scoped,status", [(False, 500), (True, 502)])
def test_installation_token_retries_server_failure_with_identical_scope(monkeypatch, scoped, status):
    request = Mock(side_effect=[httpx.Response(status), httpx.Response(201, json={"token": "issued"})])
    monkeypatch.setattr(github.http_client(), "request", request)
    monkeypatch.setattr(github.time, "sleep", Mock())
    monkeypatch.setattr(github, "app_token", lambda db: "app-jwt")

    if scoped:
        assert github.repository_read_token(None, 10, 22) == "issued"
        assert request.call_args.kwargs["json"] == {
            "repository_ids": [22], "permissions": {"contents": "read", "metadata": "read"},
        }
    else:
        assert github.installation_token_from_jwt("app-jwt", 10) == "issued"
    assert len(request.call_args_list) == 2
    assert request.call_args_list[0] == request.call_args_list[1]


@pytest.mark.parametrize("failure,status,delay", [
    (httpx.Response(503, headers={"Retry-After": "120"}), 503, 120),
    (httpx.Response(403), 403, 0),
    (httpx.ReadTimeout("response lost"), 503, 0),
])
def test_installation_token_defers_or_fails_without_replay(monkeypatch, failure, status, delay):
    request = Mock(side_effect=[failure])
    monkeypatch.setattr(github.http_client(), "request", request)
    with pytest.raises(github.GitHubError) as caught:
        github.installation_token_from_jwt("app-jwt", 10)
    assert (caught.value.status, caught.value.retry_after) == (status, delay)
    request.assert_called_once()


def test_thread_query_retries_server_failure_but_mutation_is_sent_once(monkeypatch):
    connection = {"pageInfo": {"hasNextPage": False}, "nodes": []}
    data = {"repository": {"pullRequest": {"reviewThreads": connection}}}
    request = Mock(side_effect=[httpx.Response(503), httpx.Response(200, json={"data": data}), httpx.Response(503)])
    monkeypatch.setattr(github.http_client(), "request", request)
    monkeypatch.setattr(github.time, "sleep", Mock())

    assert github.threads("token", "team/project", 3) == []
    assert request.call_count == 2 and request.call_args_list[0] == request.call_args_list[1]
    with pytest.raises(github.GitHubError) as caught:
        github.set_thread("token", "thread", True)
    assert caught.value.status == 503 and request.call_count == 3


def test_get_preserves_last_http_failure_after_retry(monkeypatch):
    request = Mock(side_effect=[
        httpx.Response(503), httpx.Response(502, json={"message": "private-provider-key"}),
    ])
    sleep = Mock()
    monkeypatch.setattr(github.http_client(), "request", request)
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
    monkeypatch.setattr(github.http_client(), "request", request)
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
    monkeypatch.setattr(github.http_client(), "request", request)
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
    monkeypatch.setattr(github.http_client(), "request", request)
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
    monkeypatch.setattr(github.http_client(), "request", request)
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
    monkeypatch.setattr(github.http_client(), "request", request)
    monkeypatch.setattr(github.time, "sleep", sleep)

    assert github.request("GET", "/repos/team/project/pulls/3", "installation-token") == result

    request.assert_called_once()
    sleep.assert_not_called()


def test_pool_reuses_connections_without_sharing_request_identity(monkeypatch):
    import json
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    observed = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            observed.append((self.client_address[1], self.headers.get("Authorization"), self.headers.get("Cookie")))
            body = json.dumps({"auth": self.headers.get("Authorization")}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Set-Cookie", "identity=private; Path=/")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    github.close_http_client()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(github, "API", f"http://127.0.0.1:{server.server_port}")
    try:
        client = github.http_client()
        assert github.request("GET", "/a", "alice") == {"auth": "Bearer alice"}
        assert github.request("GET", "/b", "bob", max_response_bytes=100) == {"auth": "Bearer bob"}
        assert observed[0][0] == observed[1][0]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda i: github.request("GET", "/c", str(i)), range(12)))
        assert results == [{"auth": f"Bearer {i}"} for i in range(12)]
        assert all(cookie is None for _, _, cookie in observed)
        assert not list(client.cookies.jar)
        github.close_http_client()
        assert client.is_closed and github.http_client() is not client
    finally:
        github.close_http_client()
        server.shutdown()
        server.server_close()
        thread.join()


def test_pool_is_reset_after_fork():
    import multiprocessing
    import os

    if not hasattr(os, "fork"):
        pytest.skip("Requires fork")
    parent_client = github.http_client()
    context = multiprocessing.get_context("fork")
    result = context.Queue()

    def child():
        result.put(github._client is None)

    process = context.Process(target=child)
    process.start()
    process.join(5)
    assert process.exitcode == 0 and result.get(timeout=1)
    assert github.http_client() is parent_client and not parent_client.is_closed
    result.close()


def test_api_lifespan_closes_github_pool():
    from fastapi.testclient import TestClient

    from code_mortis.main import app

    with TestClient(app):
        pooled = github.http_client()
    assert pooled.is_closed
