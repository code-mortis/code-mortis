import json
import os
import sys
from pathlib import Path

import pytest

from code_mortis import ocr
from code_mortis.config import settings
from code_mortis.security import safe_endpoint


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
    with pytest.raises(ValueError, match="account for every"):
        ocr.parse_report(json.dumps(data), 0)


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
        "options": {"timeout_sec": 30},
    }
    with ocr.configuration(connection, {"api_key": "secret"}, {"language": "Spanish"}):
        path = tmp_path / ".opencodereview/config.json"
        data = json.loads(path.read_text())
        assert data["mcp_servers"] == {} and data["provider"] == "mortis"
        assert data["custom_providers"]["mortis"]["protocol"] == protocol
        assert path.stat().st_mode & 0o777 == 0o600
    assert not path.exists()
    path.write_text('{"provider":"operator"}')
    with pytest.raises(ValueError, match="overwrite"):
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


def test_endpoints_reject_credentials_and_metadata(monkeypatch):
    with pytest.raises(ValueError, match="credentials"):
        safe_endpoint("https://token@example.com/v1")
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 443))])
    with pytest.raises(ValueError, match="Metadata"):
        safe_endpoint("https://metadata.example")


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
