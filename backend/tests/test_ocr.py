import base64
import json
import os
import sys
from pathlib import Path
from uuid import UUID

import pytest

from code_mortis.core.config import settings
from code_mortis.core.security import safe_endpoint
from code_mortis.integrations import ocr


def report(state="complete"):
    item = {"item_id": "file-1", "path": "main.py"}
    return {
        "manifest": {
            "schema_version": "ocr.run-manifest/v1",
            "terminal_state": state,
            "coverage": {"selected": [item], "completed": [item], "reused": [], "failed": [], "waived": []},
        },
        "comments": [
            {
                "path": "main.py",
                "content": "Fix this with secret-key",
                "start_line": 1,
                "end_line": 1,
                "thinking": "private reasoning",
            }
        ],
    }


def test_coverage_controls_outcome_and_private_reasoning_is_removed():
    data = report()
    state, saved, comments = ocr.parse_report(json.dumps(data), 0, ("secret-key",))
    assert state == "completed" and "thinking" not in comments[0]
    assert "secret-key" not in json.dumps(comments) and "[redacted]" in comments[0]["content"]
    second = {"item_id": "file-2", "path": "other.py"}
    data["manifest"]["coverage"]["selected"].append(second)
    data["manifest"]["coverage"]["waived"].append(second)
    assert ocr.parse_report(json.dumps(data), 0)[0] == "partial"
    assert ocr.parse_report(json.dumps(report()), 1)[0] == "failed"
    data["manifest"]["coverage"]["waived"] = []
    with pytest.raises(ValueError, match="missing or overlapping file results"):
        ocr.parse_report(json.dumps(data), 0)


@pytest.mark.parametrize("state", ["complete", "partial", "failed", "skipped"])
def test_review_limit_warning_preserves_results_without_reporting_completion(state):
    data = report(state)
    data["warnings"] = [{"type": "review_limit_reached", "file": "main.py",
                         "message": "reached the aggregate token budget; the grace round completed the task"}]
    status, saved, comments = ocr.parse_report(json.dumps(data), 0)
    assert status == ("partial" if state == "complete" else state)
    assert saved["manifest"] == data["manifest"]
    assert saved["warnings"] == data["warnings"]
    assert len(comments) == 1


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "../secret", ".git/config", "src/../../secret", "src\\secret"]
)
def test_invalid_findings_fail_safely(path):
    data = report()
    data["comments"][0]["path"] = path
    with pytest.raises(ValueError, match="invalid path") as error:
        ocr.parse_report(json.dumps(data), 0)
    assert "secret-key" not in str(error.value)


def test_diff_anchors_and_fingerprint():
    patch = "@@ -2,3 +2,4 @@\n context\n-removed\n+added\n+next\n context\n"
    assert ocr.diff_lines(patch) == {2, 3, 4, 5}
    comment = report()["comments"][0]
    assert ocr.fingerprint(comment) == ocr.fingerprint({**comment, "content": "  Fix this with secret-key  "})
    assert ocr.fingerprint(comment) != ocr.fingerprint({**comment, "path": "different.py"})


@pytest.mark.parametrize("protocol", ["openai", "openai-responses", "anthropic", "anthropic-bedrock"])
def test_selected_provider_is_isolated_and_configuration_removed(protocol, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(settings(), "ocr_isolated_runtime", True)
    monkeypatch.setattr(ocr, "safe_endpoint", lambda value: value)
    connection = {
        "protocol": protocol,
        "model": "model",
        "base_url": "https://example.com/v1",
        "options": {"timeout_sec": 30, "prompt_token_ceiling": 200000},
    }
    with ocr.configuration(connection, {"api_key": "secret"}, {"language": "Spanish"}):
        path = tmp_path / ".opencodereview/config.json"
        data = json.loads(path.read_text())
        assert data["mcp_servers"] == {} and data["provider"] == "mortis"
        assert data["custom_providers"]["mortis"]["protocol"] == protocol
        assert data["custom_providers"]["mortis"]["timeout_sec"] == 30
        assert "prompt_token_ceiling" not in data["custom_providers"]["mortis"]
        assert path.stat().st_mode & 0o777 == 0o600
    assert not path.exists()
    path.write_text('{"provider":"operator"}')
    with pytest.raises(ValueError, match="cannot replace it safely"):
        with ocr.configuration(connection, {}, {}):
            pass
    assert json.loads(path.read_text())["provider"] == "operator"


def test_subprocess_credentials_limits_and_cancellation(tmp_path, monkeypatch):
    monkeypatch.setenv("CM_DATABASE_URL", "secret-database")
    monkeypatch.setenv("GH_TOKEN", "secret-github")
    assert "GH_TOKEN" not in ocr.child_environment() and "CM_DATABASE_URL" not in ocr.child_environment()
    with pytest.raises(ocr.ReviewStopped):
        ocr.command(
            [sys.executable, "-c", "import time;time.sleep(30)"],
            tmp_path,
            os.environ.copy(),
            stopped=lambda: True,
        )
    monkeypatch.setattr(ocr, "MAX_OUTPUT", 20)
    with pytest.raises(ValueError, match="output exceeded"):
        ocr.command([sys.executable, "-c", "print('x'*100)"], tmp_path, os.environ.copy())


@pytest.mark.parametrize("protocol", ["openai", "openai-responses", "anthropic", "anthropic-bedrock"])
@pytest.mark.parametrize("structured", [True, False])
def test_structured_responses_enforces_schema_without_changing_saved_options(protocol, structured, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(settings(), "ocr_isolated_runtime", True)
    monkeypatch.setattr(ocr, "safe_endpoint", lambda value: value)
    options = {"extra_body": {"keep": "value", "tools.0.strict": False}}
    connection = {"protocol": protocol, "model": "model", "base_url": "https://example.com/v1", "options": options}
    with ocr.configuration(connection, {}, {}, structured_result=structured):
        data = json.loads((tmp_path / ".opencodereview/config.json").read_text())
        body = data["custom_providers"]["mortis"]["extra_body"]
        assert body == {"keep": "value", "tools.0.strict": structured and protocol == "openai-responses"}
    assert options == {"extra_body": {"keep": "value", "tools.0.strict": False}}


@pytest.mark.parametrize(
    "url,is_go",
    [
        ("https://opencode.ai/zen/go/v1/", True),
        ("https://opencode.ai/zen/v1", False),
        ("https://opencode.ai.example/zen/go/v1", False),
    ],
)
def test_opencode_go_headers_are_scoped_and_session_changes_per_invocation(url, is_go, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(settings(), "ocr_isolated_runtime", True)
    monkeypatch.setattr(ocr, "safe_endpoint", lambda value: value)
    connection = {"protocol": "openai-responses", "model": "model", "base_url": url, "options": {}}
    credentials = {"extra_headers": {"user-agent": "old-client", "X-OpenCode-Session": "old", "X-Test": "keep"}}
    sessions = []
    for _ in range(2):
        with ocr.configuration(connection, credentials, {}):
            data = json.loads((tmp_path / ".opencodereview/config.json").read_text())
            headers = data["custom_providers"]["mortis"]["extra_headers"]
            if is_go:
                assert headers["User-Agent"] == "code-mortis/0.1.0"
                sessions.append(UUID(headers["x-opencode-session"]))
                assert headers["X-Test"] == "keep" and len(headers) == 3
            else:
                assert headers == credentials["extra_headers"]
    if is_go:
        assert sessions[0] != sessions[1]
    assert credentials["extra_headers"]["X-OpenCode-Session"] == "old"


def test_shared_opencode_configuration_uses_native_conversation_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(settings(), "ocr_isolated_runtime", True)
    monkeypatch.setattr(ocr, "safe_endpoint", lambda value: value)
    connection = {"protocol": "openai-responses", "model": "same-model",
                  "base_url": "https://opencode.ai/zen/go/v1", "options": {}}
    credentials = {"api_key": "fake", "extra_headers": {"X-Test": "keep"}}
    with ocr.configuration(connection, credentials, {}, session_per_task=True):
        config = json.loads((tmp_path / ".opencodereview/config.json").read_text())
        provider = config["custom_providers"]["mortis"]
        assert provider["model"] == "same-model"
        assert provider["extra_headers"] == {"User-Agent": "code-mortis/0.1.0",
                                            "x-opencode-session": "{ocr_session_key}", "X-Test": "keep"}
    assert not (tmp_path / ".opencodereview/config.json").exists()
    assert credentials["extra_headers"] == {"X-Test": "keep"}


def test_endpoints_reject_credentials_and_metadata(monkeypatch):
    with pytest.raises(ValueError, match="credentials"):
        safe_endpoint("https://token@example.com/v1")
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 443))])
    with pytest.raises(ValueError, match="protected system"):
        safe_endpoint("https://metadata.example")


def test_review_fetch_credentials_need_no_executable_and_do_not_reach_ocr(tmp_path, monkeypatch):
    from contextlib import nullcontext

    monkeypatch.setattr(settings(), "work_dir", tmp_path)
    monkeypatch.setattr(ocr, "check_version", lambda *a: None)
    monkeypatch.setattr(ocr, "configuration", lambda *a: nullcontext())
    base, head = "a" * 40, "b" * 40
    fetches = []

    def git(directory, env, *args, **kwargs):
        assert not (directory.parent / "askpass").exists()
        assert "GIT_ASKPASS" not in env and "CM_GIT_TOKEN" not in env
        if args[0] == "fetch":
            assert env["GIT_CONFIG_COUNT"] == "1"
            assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
            encoded = env["GIT_CONFIG_VALUE_0"].removeprefix("Authorization: Basic ")
            assert base64.b64decode(encoded).decode() == "x-access-token:installation-secret"
            fetches.append(args)
        else:
            assert "GIT_CONFIG_VALUE_0" not in env
        return head if args[0] == "rev-parse" else base

    def model_command(args, directory, env, **kwargs):
        assert "GIT_CONFIG_VALUE_0" not in env and "installation-secret" not in str(env)
        if "--preview" in args:
            return 0, json.dumps({"files": [{"path": "main.py", "will_review": True}], "reviewable_count": 1}), ""
        data = report()
        data["comments"] = []
        return 0, json.dumps(data), ""

    monkeypatch.setattr(ocr, "git", git)
    monkeypatch.setattr(ocr, "command", model_command)
    state, _, _ = ocr.review(
        "owner/repo", 1, base, head, {}, {},
        {"effort": "low", "concurrency": 1, "token_budget": 1000, "timeout_seconds": 60},
        "installation-secret", lambda: False,
    )
    assert state == "completed" and len(fetches) == 1


def test_provider_probe_does_not_claim_unverified_tools(monkeypatch, tmp_path):
    from contextlib import nullcontext

    monkeypatch.setattr(ocr, "configuration", lambda *a: nullcontext())
    monkeypatch.setattr(ocr, "check_version", lambda *a: None)
    monkeypatch.setattr(
        ocr,
        "command",
        lambda *a, **k: (
            0,
            "✓ Connection test successful\n! Tool-call round trip unverified: the model did not call the test tool",
            "",
        ),
    )
    with pytest.raises(ValueError, match="not verified"):
        ocr.test_connection({}, {})

    monkeypatch.setattr(
        ocr, "command", lambda *a, **k: (1, "", "400 MissingSessionID key=private-key header=private-header")
    )
    with pytest.raises(ValueError, match="400 MissingSessionID") as error:
        ocr.test_connection({}, {"api_key": "private-key", "extra_headers": {"X-Token": "private-header"}})
    assert "private-key" not in str(error.value) and "private-header" not in str(error.value)


def test_private_redaction_material_refreshes_and_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(settings(), "ocr_isolated_runtime", True)
    monkeypatch.setattr(ocr, "safe_endpoint", lambda value: value)
    connection = {"protocol": "openai", "model": "test", "options": {}, "base_url": "https://provider.test"}
    credentials = {"api_key": "synthetic-key", "redaction_values": []}
    redaction = tmp_path / ".opencodereview/redaction.json"
    with ocr.configuration(connection, credentials, {}) as refresh:
        assert redaction.stat().st_mode & 0o777 == 0o600
        credentials["redaction_values"].append("recovered-historical-value")
        refresh()
        assert "recovered-historical-value" in json.loads(redaction.read_text())
    assert not redaction.exists()
